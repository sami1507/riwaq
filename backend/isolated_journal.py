"""Durable container cleanup obligations, outside the worker and its payload.

No automatic execution replay. Recovery touches only explicitly journaled owned
names after checking the current pinned daemon/image/client configuration.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from backend.isolated_worker import IsolatedError, IsolatedConfig, cleanup_owned, load_config
from backend.isolated_locks import LeaseError, worker_lease

VERSION = 'isolated-journal-v1'
MAX_RECORDS = 256
KEYS = {'version', 'container_name', 'container_id', 'daemon_id', 'image_id', 'runner',
        'config_sha256', 'created_at', 'updated_at', 'state'}


def _directory():
    from backend.db import ROOT
    base = Path(os.environ.get('RIWAQ_DATA_DIR', str(ROOT/'data')))
    folder = base/'isolated-runs'
    if folder.is_symlink(): raise IsolatedError('journal_path_invalid')
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _stamp(): return datetime.now(timezone.utc).isoformat()


def _fingerprint(config, runner):
    data = {'docker_executable': config.docker_executable, 'docker_sha256': config.docker_sha256,
            'endpoint': config.endpoint, 'daemon_id': config.daemon_id,
            'runner': runner, 'image_id': config.images.get(runner)}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _records(folder):
    items = []
    # Bounded traversal of one trusted application directory, never the engine.
    for index, item in enumerate(folder.iterdir()):
        if index >= MAX_RECORDS * 2: raise IsolatedError('journal_entry_limit')
        if item.suffix in {'.json','.tmp'}:
            if len(items) >= MAX_RECORDS: raise IsolatedError('journal_entry_limit')
            items.append(item)
    return items


def assert_ready():
    if _records(_directory()): raise IsolatedError('pending_container_recovery')


class Journal:
    def __init__(self, config: IsolatedConfig, runner: str, progress=None):
        self.config = config; self.runner = runner; self.progress = progress
        self.path = None; self.record = None

    def _write(self):
        temporary = self.path.with_suffix('.tmp')
        if self.path.is_symlink() or temporary.is_symlink(): raise IsolatedError('journal_path_invalid')
        raw = json.dumps(self.record, sort_keys=True, separators=(',', ':')).encode()
        if len(raw) > 4096: raise IsolatedError('journal_record_limit')
        with temporary.open('wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def event(self, value):
        if value.get('stage') == 'container_created':
            name, identity = value.get('container_name'), value.get('container_id')
            if not isinstance(name, str) or not re.fullmatch(r'riwaq-isolated-[0-9a-f]{32}', name): raise IsolatedError('journal_identity_invalid')
            if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{64}', identity): raise IsolatedError('journal_identity_invalid')
            folder = _directory()
            if _records(folder): raise IsolatedError('pending_container_recovery')
            self.path = folder/(name+'.json')
            if self.path.exists(): raise IsolatedError('journal_collision')
            self.record = {'version': VERSION, 'container_name': name, 'container_id': identity,
                'daemon_id': self.config.daemon_id, 'image_id': self.config.images[self.runner], 'runner': self.runner,
                'config_sha256': _fingerprint(self.config, self.runner), 'created_at': _stamp(),
                'updated_at': _stamp(), 'state': 'pending'}
            self._write()  # Must complete before the adapter is allowed to start.
        if self.progress: self.progress(value)

    def finish(self, receipt):
        if self.path is None: return
        self.record['updated_at'] = _stamp()
        self.record['state'] = 'cleaned' if receipt.get('cleanup_verified') is True else 'cleanup_required'
        self._write()
        if self.record['state'] == 'cleaned': self.path.unlink()


def recover_pending(config: IsolatedConfig | None = None):
    """Explicit startup recovery, suitable for asyncio.to_thread; never replay."""
    try:
        with worker_lease():
            return _recover_pending(config)
    except (LeaseError, OSError) as exc:
        return {'version': VERSION, 'checked': 0, 'cleaned': 0,
                'blocked': [{'reason_code': getattr(exc, 'code', 'journal_lock_unavailable')}],
                'execution_replayed': False}


def _recover_pending(config: IsolatedConfig | None = None):
    folder = _directory(); result = {'version': VERSION, 'checked': 0, 'cleaned': 0, 'blocked': [], 'execution_replayed': False}
    try: records = _records(folder)
    except IsolatedError as exc:
        result['blocked'].append({'reason_code': exc.code}); return result
    if not records: return result
    if config is None:
        try: config = load_config(folder.parent/'isolated-workers.json')
        except (OSError, IsolatedError, ValueError):
            result['blocked'].append({'reason_code': 'recovery_configuration_unavailable'}); return result
    for path in records:
        result['checked'] += 1
        try:
            if path.is_symlink() or path.stat().st_size > 4096: raise IsolatedError('journal_record_invalid')
            value = json.loads(path.read_bytes())
            if not isinstance(value, dict) or set(value) != KEYS or value.get('version') != VERSION: raise IsolatedError('journal_record_invalid')
            name = value['container_name']; runner = value['runner']
            if not isinstance(name, str) or not re.fullmatch(r'riwaq-isolated-[0-9a-f]{32}', name) or path.name not in {name+'.json',name+'.tmp'}: raise IsolatedError('journal_record_invalid')
            if not isinstance(value['container_id'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['container_id']): raise IsolatedError('journal_record_invalid')
            if runner not in config.images or value['image_id'] != config.images[runner] or value['daemon_id'] != config.daemon_id or value['config_sha256'] != _fingerprint(config, runner):
                raise IsolatedError('journal_configuration_mismatch')
            if value['state'] not in {'pending','cleanup_required','cleaned'}: raise IsolatedError('journal_record_invalid')
            if value['state'] != 'cleaned':
                receipt = cleanup_owned(config, name, expected_id=value['container_id'],expected_image=value['image_id'])
                if receipt.get('cleanup_verified') is not True: raise IsolatedError('cleanup_unverified')
            path.unlink(); result['cleaned'] += 1
        except Exception as exc:
            result['blocked'].append({'record': path.name, 'reason_code': getattr(exc, 'code', 'journal_recovery_failed')})
    return result
