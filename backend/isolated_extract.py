"""Parent-side protocol for the shared parser inside the pinned Linux image."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import threading

from backend.isolated_parsers import ParserError, VERSION, MAX_RESULT, MAX_EXTRACTED, MAX_PAGES, validate_metadata
from backend.isolated_worker import IsolatedError, execute, load_config
from backend.isolated_journal import Journal, assert_ready
from backend.isolated_locks import LeaseError, worker_lease


def extract(body: bytes, metadata: dict, stop=None):
    """Return (pages, supervisor receipt). Never falls back to a host parser."""
    try:
        with worker_lease():
            return _extract_locked(body, metadata, stop)
    except (LeaseError, OSError):
        raise ParserError(503, 'Isolated worker is busy or its lifecycle lock is unavailable') from None


def _extract_locked(body: bytes, metadata: dict, stop=None):
    validate_metadata(metadata)
    stop = stop or threading.Event()
    if stop.is_set(): raise ParserError(499, 'Document extraction cancelled')
    from backend.db import ROOT
    config_path = Path(os.environ.get('RIWAQ_DATA_DIR', str(ROOT/'data')))/'isolated-workers.json'
    try: config = load_config(config_path)
    except (IsolatedError, OSError, ValueError):
        raise ParserError(503, 'OS-isolated document parser is not configured; host fallback is disabled') from None
    if 'document_extract' not in config.images:
        raise ParserError(503, 'OS-isolated document parser image is not configured; host fallback is disabled')
    try: assert_ready()
    except (IsolatedError,OSError): raise ParserError(503,'Pending container cleanup must be recovered before extraction') from None
    metadata_raw = json.dumps(metadata, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('ascii')
    journal=Journal(config,'document_extract')
    result = execute({'input.bin': body, 'metadata.json': metadata_raw}, 'document_extract', ['input.bin'],
                     config=config, stop=stop, authorize=lambda: not stop.is_set(), timeout_seconds=45, memory_mb=768,progress=journal.event)
    try: journal.finish(result)
    except (OSError,RuntimeError): raise ParserError(503,'Document container cleanup journal could not be completed') from None
    reason = result.get('reason_code')
    if stop.is_set() or result.get('status') in {'cancelled', 'revoked'}: raise ParserError(499, 'Document extraction cancelled')
    if result.get('status') == 'timeout': raise ParserError(504, 'OS-isolated document parser timed out')
    if result.get('status') != 'completed' or not result.get('cleanup_verified') or not result.get('policy_inspected'):
        status = 503 if result.get('status') in {'blocked', 'cleanup_failed'} else 422
        raise ParserError(status, 'OS-isolated document parser failed; no host fallback or result was accepted')
    try:
        raw = result['stdout'].encode('utf-8')
        if len(raw) > MAX_RESULT: raise ValueError()
        value = json.loads(raw)
        if value.get('version') != VERSION or value.get('input_sha256') != hashlib.sha256(body).hexdigest(): raise ValueError()
        if value.get('success') is not True:
            code = value.get('error_status'); detail = value.get('error_detail')
            if type(code) is not int or code not in {400, 413, 415, 422, 503} or not isinstance(detail, str) or len(detail) > 500: raise ValueError()
            raise ParserError(code, detail)
        pages = value['pages']
        if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES: raise ValueError()
        total = 0; output = []
        for item in pages:
            if not isinstance(item, list) or len(item) != 2: raise ValueError()
            page, text = item
            if page is not None and (type(page) is not int or not 1 <= page <= MAX_PAGES): raise ValueError()
            if not isinstance(text, str) or '\x00' in text: raise ValueError()
            total += len(text)
            if total > MAX_EXTRACTED: raise ValueError()
            output.append((page, text))
        if not any(text.strip() for _, text in output): raise ValueError()
    except ParserError: raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise ParserError(422, 'OS-isolated parser returned no valid bounded text result') from None
    if stop.is_set(): raise ParserError(499, 'Document extraction cancelled before accepting result')
    # Parsed text is caller data, not duplicated in the supervisor provenance.
    resources = {k: v for k, v in result.items() if k not in {'stdout', 'stderr'}}
    resources['isolation'] = 'linux_container_shared_kernel_no_host_mounts_network_none'
    return output, resources
