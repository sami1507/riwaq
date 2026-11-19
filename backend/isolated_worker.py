"""Fail-closed local Docker adapter for bounded, explicit code snapshots.

Only the pinned Docker client runs on the host. User code goes through stdin to
a fixed bootstrap in a Linux container. No host source mount, shell, pull,
dependency installation or host fallback. Containers remain a shared-kernel
boundary, not a claim of complete protection against kernel/runtime exploits.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Callable, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator
from backend.isolated_bootstrap import RUNNERS
from backend.isolated_locks import LeaseError, worker_lease

VERSION = 'isolated-docker-v1'
MAX_FILES = 256
MAX_FILE = 1024 ** 2
MAX_INPUT = 10 * 1024 ** 2
MAX_PAYLOAD = 15_000_000
MAX_OUTPUT = 65536
_SLOT = threading.BoundedSemaphore(1)
_QUARANTINED = False  # A failed cleanup blocks further execution in this process.
_QUARANTINE_NAMES = set()
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
_ENDPOINTS = {'npipe:////./pipe/dockerDesktopLinuxEngine', 'npipe:////./pipe/docker_engine'}


class IsolatedError(RuntimeError):
    def __init__(self, code): self.code = code; super().__init__(code)


class IsolatedConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    version: Literal['isolated-docker-v1'] = VERSION
    docker_executable: str = Field(min_length=1, max_length=1024)
    docker_sha256: str
    endpoint: str
    daemon_id: str = Field(min_length=1, max_length=128)
    images: dict[Literal['python_unittest', 'node_test', 'document_extract', 'document_page', 'skill_transform', 'media_decode'], str] = Field(min_length=1, max_length=6)

    @field_validator('docker_sha256')
    @classmethod
    def hash_valid(cls, value):
        if not _HEX.fullmatch(value): raise ValueError('Docker executable SHA256 is required')
        return value

    @field_validator('endpoint')
    @classmethod
    def endpoint_local(cls, value):
        if value not in _ENDPOINTS: raise ValueError('Only fixed local Docker named pipes are supported')
        return value

    @field_validator('images')
    @classmethod
    def image_pins(cls, value):
        if any(not _IMAGE.fullmatch(v) for v in value.values()): raise ValueError('Local immutable image IDs required; tags are refused')
        return dict(value)


def load_config(path: Path | str) -> IsolatedConfig:
    p = Path(path)
    try:
        if p.is_symlink() or p.stat().st_size > 16384: raise IsolatedError('configuration_invalid')
        return IsolatedConfig.model_validate_json(p.read_bytes())
    except (ValueError, OSError): raise IsolatedError('configuration_invalid') from None


def _verified_executable(config):
    path = Path(config.docker_executable)
    if not path.is_absolute() or path.name.lower() != 'docker.exe' or path.is_symlink():
        raise IsolatedError('docker_executable_untrusted')
    try:
        if not path.is_file() or path.stat().st_size > 128 * 1024 ** 2: raise OSError()
        with path.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    except OSError: raise IsolatedError('docker_executable_unavailable') from None
    if digest != config.docker_sha256: raise IsolatedError('docker_executable_changed')
    return str(path.resolve())


def _relative(name):
    if not isinstance(name, str) or not name or len(name) > 512 or name.startswith('/'):
        raise IsolatedError('snapshot_path_invalid')
    if any(ord(c) < 32 or ord(c) == 127 or c in '\\:*?[];|&<>"' for c in name):
        raise IsolatedError('snapshot_path_invalid')
    parts = name.split('/')
    if any(p in {'', '.', '..'} or p.endswith((' ', '.')) for p in parts): raise IsolatedError('snapshot_path_invalid')
    denied = {'.git', '.hg', '.svn', '.aws', '.ssh', '.gnupg', '.azure', '.codex', '.agents', '.config', '.venv', 'venv', 'node_modules'}
    if any(p.casefold() in denied or p.casefold().startswith('.env') or re.search(r'(^|[._-])(credentials?|secrets?)([._-]|$)', p, re.I) for p in parts):
        raise IsolatedError('snapshot_path_forbidden')
    return name


def prepare_payload(files, runner, targets):
    if runner == 'media_decode':
        from backend import isolated_audio as protocol
        if not isinstance(files,dict) or set(files)!={'input.bin'} or targets!=['input.bin']:raise IsolatedError('audio_input_invalid')
        body=files['input.bin']
        try:protocol.kind(body)
        except (ValueError,TypeError):raise IsolatedError('audio_input_invalid') from None
        raw=protocol.dump({'version':protocol.VERSION,'body':base64.b64encode(body).decode('ascii')})
        if len(raw)>protocol.MAX_PAYLOAD:raise IsolatedError('audio_payload_limit')
        return raw,{'input.bin':hashlib.sha256(body).hexdigest()}
    if runner == 'skill_transform':
        from backend import skill_transform_protocol as protocol
        if not isinstance(files,dict) or set(files)!={'transform.json'} or targets!=['transform.json']:
            raise IsolatedError('transform_input_invalid')
        raw=files['transform.json']
        if not isinstance(raw,bytes) or not 1<=len(raw)<=protocol.MAX_PAYLOAD: raise IsolatedError('transform_input_limit')
        try: protocol.validate_payload(protocol.loads(raw))
        except (ValueError,TypeError,KeyError): raise IsolatedError('transform_metadata_invalid') from None
        return raw,{'transform.json':hashlib.sha256(raw).hexdigest()}
    if runner == 'document_page':
        from backend import isolated_document_page as document
        if not isinstance(files,dict) or set(files)!={'input.bin','metadata.json'} or targets!=['input.bin']:
            raise IsolatedError('document_input_invalid')
        body=files['input.bin'];raw=files['metadata.json']
        if not isinstance(body,bytes) or not 1<=len(body)<=document.MAX_INPUT or not isinstance(raw,bytes) or len(raw)>8192:
            raise IsolatedError('document_input_limit')
        try:metadata=document.validate_metadata(document.loads(raw))
        except (ValueError,TypeError,KeyError):raise IsolatedError('document_metadata_invalid') from None
        payload=document.dump({'body':base64.b64encode(body).decode('ascii'),'metadata':metadata})
        if len(payload)>document.MAX_PAYLOAD:raise IsolatedError('document_payload_limit')
        return payload,{name:hashlib.sha256(value).hexdigest() for name,value in files.items()}
    if runner == 'document_extract':
        from backend.isolated_parsers import validate_metadata, MAX_PUBLIC_INPUT, MAX_PAYLOAD as PARSER_PAYLOAD
        if not isinstance(files, dict) or set(files) != {'input.bin', 'metadata.json'} or targets != ['input.bin']:
            raise IsolatedError('parser_input_invalid')
        if not isinstance(files['input.bin'], bytes) or not 1 <= len(files['input.bin']) <= MAX_PUBLIC_INPUT:
            raise IsolatedError('parser_input_limit')
        if not isinstance(files['metadata.json'], bytes) or len(files['metadata.json']) > 16384:
            raise IsolatedError('parser_metadata_invalid')
        metadata = validate_metadata(json.loads(files['metadata.json']))
        raw = json.dumps({'body': base64.b64encode(files['input.bin']).decode('ascii'), 'metadata': metadata},
                         ensure_ascii=True, separators=(',', ':')).encode('ascii')
        if len(raw) > PARSER_PAYLOAD: raise IsolatedError('parser_input_limit')
        return raw, {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}
    if runner not in RUNNERS: raise IsolatedError('runner_unsupported')
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES: raise IsolatedError('snapshot_file_limit')
    if not isinstance(targets, list) or not 1 <= len(targets) <= 20: raise IsolatedError('target_limit')
    total = 0; encoded = {}; hashes = {}; folded = set()
    for name, value in files.items():
        _relative(name)
        if name.casefold() in folded: raise IsolatedError('snapshot_duplicate_path')
        folded.add(name.casefold())
        if not isinstance(value, bytes) or len(value) > MAX_FILE: raise IsolatedError('snapshot_file_limit')
        total += len(value)
        if total > MAX_INPUT: raise IsolatedError('snapshot_byte_limit')
        encoded[name] = base64.b64encode(value).decode('ascii'); hashes[name] = hashlib.sha256(value).hexdigest()
    # File/directory ambiguity is rejected before starting Docker.
    if any('/'.join(name.split('/')[:i]).casefold() in folded for name in files for i in range(1, len(name.split('/')))):
        raise IsolatedError('snapshot_path_conflict')
    extensions = ('.py',) if runner == 'python_unittest' else ('.js', '.mjs', '.cjs')
    if len({str(t).casefold() for t in targets}) != len(targets): raise IsolatedError('target_duplicate')
    if any(_relative(t) not in files or t.startswith('-') or not t.endswith(extensions) for t in targets): raise IsolatedError('target_not_in_snapshot')
    raw = json.dumps({'files': encoded, 'targets': targets}, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    if len(raw) > MAX_PAYLOAD: raise IsolatedError('snapshot_payload_limit')
    return raw, hashes


def _check(stop, authorize, deadline):
    if stop.is_set(): raise IsolatedError('cancelled')
    if time.monotonic() >= deadline: raise IsolatedError('timeout')
    try: allowed = authorize() is True
    except Exception: allowed = False
    if not allowed: raise IsolatedError('authorization_revoked')
    # Authorization may block on storage while cancellation or the deadline
    # arrives. Recheck immediately before the caller may dispatch a process.
    if stop.is_set(): raise IsolatedError('cancelled')
    if time.monotonic() >= deadline: raise IsolatedError('timeout')


@dataclass
class CLIResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class _Client:
    def __init__(self, config, folder, deadline=None):
        self.config = config; self.folder = Path(folder)
        self.deadline = deadline
        self.executable = _verified_executable(config)
        config_dir = self.folder / 'docker-config'; config_dir.mkdir()
        (config_dir / 'config.json').write_text('{}', encoding='ascii')
        self.base = [self.executable, '--config', str(config_dir), '--host', config.endpoint]
        self.env = {k: v for k, v in os.environ.items() if k.upper() in {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE'}}
        self.env.update(PATH=str(Path(self.executable).parent), TEMP=str(self.folder), TMP=str(self.folder))

    def command(self, args, *, payload=b'', seconds=10, stop=None, authorize=None, output_limit=MAX_OUTPUT):
        stop = stop or threading.Event(); authorize = authorize or (lambda: True)
        deadline = min(time.monotonic() + seconds, self.deadline or float('inf'))
        _check(stop, authorize, deadline)
        buffers = [bytearray(), bytearray()]; overflow = threading.Event(); lock = threading.Lock(); threads = []
        proc = None
        try:
            proc = subprocess.Popen(self.base + list(args), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=self.folder, env=self.env, shell=False, bufsize=0,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            def read(stream, index):
                while True:
                    part = stream.read(4096)
                    if not part: break
                    with lock:
                        remaining = output_limit - sum(len(b) for b in buffers)
                        buffers[index].extend(part[:max(0, remaining)])
                        if len(part) > remaining: overflow.set(); break
            def write():
                try:
                    for i in range(0, len(payload), 65536): proc.stdin.write(payload[i:i+65536])
                except (BrokenPipeError, OSError): pass
                finally:
                    try: proc.stdin.close()
                    except OSError: pass
            for stream, index in ((proc.stdout, 0), (proc.stderr, 1)):
                thread = threading.Thread(target=read, args=(stream, index), daemon=True); thread.start(); threads.append(thread)
            thread = threading.Thread(target=write, daemon=True); thread.start(); threads.append(thread)
            while proc.poll() is None:
                _check(stop, authorize, deadline)
                if overflow.is_set(): raise IsolatedError('output_limit')
                time.sleep(.025)
            for thread in threads: thread.join(timeout=1)
            if any(t.is_alive() for t in threads): raise IsolatedError('client_io_failed')
            if overflow.is_set(): raise IsolatedError('output_limit')
            _check(stop, authorize, deadline)
            return CLIResult(proc.returncode, bytes(buffers[0]), bytes(buffers[1]))
        except OSError: raise IsolatedError('docker_client_unavailable') from None
        finally:
            if proc:
                if proc.poll() is None: proc.kill()
                try: proc.wait(timeout=3)
                except subprocess.TimeoutExpired: raise IsolatedError('docker_client_cleanup_failed') from None
                for thread in threads: thread.join(timeout=1)
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    try: stream.close()
                    except OSError: pass


def _json(result):
    if result.returncode != 0: raise IsolatedError('docker_command_failed')
    try: return json.loads(result.stdout)
    except (ValueError, UnicodeError): raise IsolatedError('docker_response_invalid') from None


def _probe(client, runner, stop, authorize):
    info = _json(client.command(['info', '--format', '{{json .}}'], stop=stop, authorize=authorize))
    config = client.config
    if info.get('OSType') != 'linux' or info.get('ID') != config.daemon_id: raise IsolatedError('daemon_identity_mismatch')
    if not all(info.get(key) is True for key in ('MemoryLimit', 'SwapLimit', 'PidsLimit', 'CpuCfsQuota')):
        raise IsolatedError('daemon_resource_controls_unavailable')
    options = info.get('SecurityOptions', [])
    if not any(isinstance(x, str) and 'name=seccomp' in x and 'profile=unconfined' not in x for x in options):
        raise IsolatedError('seccomp_unavailable')
    if runner not in config.images: raise IsolatedError('runner_image_unconfigured')
    images = _json(client.command(['image', 'inspect', config.images[runner]], stop=stop, authorize=authorize))
    if not isinstance(images, list) or len(images) != 1: raise IsolatedError('image_unavailable')
    image = images[0]
    if image.get('Id') != config.images[runner] or image.get('Os') != 'linux' or image.get('Architecture') != 'amd64':
        raise IsolatedError('image_identity_mismatch')
    if image.get('Config', {}).get('Volumes'): raise IsolatedError('image_implicit_mounts_forbidden')
    return {'daemon_id': config.daemon_id, 'server_version': info.get('ServerVersion'), 'image_id': image['Id'],
            'security_options': options, 'resource_controls_reported': True}


def probe(config, runner='python_unittest'):
    """Explicit bounded local-daemon probe; no container is created or started."""
    config = IsolatedConfig.model_validate(config) if isinstance(config, dict) else config
    with tempfile.TemporaryDirectory(prefix='riwaq-isolated-probe-') as folder:
        evidence = _probe(_Client(config, folder), runner, threading.Event(), lambda: True)
    return {'available': True, 'tested': False, 'isolation_verified': False, 'evidence': evidence}


def create_arguments(config, runner, name, memory_mb, timeout_seconds):
    executable, bootstrap = RUNNERS[runner]
    return ['create', '--name', name, '--label', 'riwaq.isolated=' + VERSION, '--pull', 'never',
        '--network', 'none', '--read-only', '--user', '65534:65534', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges=true', '--pids-limit', '64', '--cpus', '1.0',
        '--memory', str(memory_mb * 1024 ** 2), '--memory-swap', str(memory_mb * 1024 ** 2),
        '--ulimit', 'nofile=256:256', '--ulimit', f'cpu={timeout_seconds}:{timeout_seconds}',
        '--ulimit', 'fsize=16777216:16777216', '--shm-size', '8388608', '--ipc', 'private',
        '--cgroupns', 'private', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=67108864,mode=1777',
        '--log-driver', 'none', '--restart', 'no', '--no-healthcheck', '--interactive',
        '--workdir', '/tmp', '--env', 'HOME=/tmp', '--env', 'LANG=C.UTF-8',
        '--env', 'PYTHONPATH=', '--env', 'PYTHONSTARTUP=', '--env', 'NODE_OPTIONS=',
        '--env', 'HTTP_PROXY=', '--env', 'HTTPS_PROXY=', '--env', 'ALL_PROXY=',
        '--entrypoint', executable, config.images[runner], *bootstrap]


def verify_container(value, config, runner, memory_mb):
    """Check daemon-applied policy before stdin or user code reaches a process."""
    if not isinstance(value, list) or len(value) != 1: raise IsolatedError('container_inspection_invalid')
    row = value[0]; host = row.get('HostConfig', {}); inner = row.get('Config', {})
    checks = [row.get('Image') == config.images[runner], row.get('State', {}).get('Status') == 'created',
        host.get('NetworkMode') == 'none', host.get('ReadonlyRootfs') is True, host.get('Privileged') is False,
        inner.get('User') == '65534:65534', inner.get('Entrypoint') == [RUNNERS[runner][0]],
        inner.get('Cmd') == RUNNERS[runner][1],
        inner.get('Healthcheck', {}).get('Test') == ['NONE'],
        inner.get('OpenStdin') is True, inner.get('Tty') is False,
        host.get('Memory') == memory_mb * 1024 ** 2, host.get('MemorySwap') == memory_mb * 1024 ** 2,
        host.get('PidsLimit') == 64, host.get('NanoCpus') == 1_000_000_000,
        set(host.get('CapDrop') or []) == {'ALL'}, not host.get('CapAdd'),
        host.get('IpcMode') == 'private', host.get('CgroupnsMode') == 'private',
        host.get('PidMode') in ('', 'private'), host.get('UTSMode') in ('', 'private'),
        host.get('RestartPolicy', {}).get('Name') == 'no', host.get('LogConfig', {}).get('Type') == 'none',
        not row.get('Mounts'), not host.get('Binds'), not host.get('Devices'), not host.get('DeviceRequests'),
        not host.get('VolumesFrom'), not host.get('PortBindings'), not host.get('PublishAllPorts'),
        set(host.get('Tmpfs') or {}) == {'/tmp'},
        any(v in ('no-new-privileges', 'no-new-privileges=true') for v in host.get('SecurityOpt') or []),
        not any('unconfined' in v for v in host.get('SecurityOpt') or [])]
    if not all(checks): raise IsolatedError('container_policy_mismatch')
    opts = set(host['Tmpfs']['/tmp'].split(','))
    if not {'rw', 'noexec', 'nosuid', 'nodev', 'size=67108864', 'mode=1777'} <= opts:
        raise IsolatedError('container_tmpfs_policy_mismatch')
    return row


def _cleanup(client, name):
    # Not subject to revoked source authority: cleanup must still stop own work.
    client.deadline = None
    result = client.command(['rm', '--force', '--volumes', name], seconds=5)
    remaining = client.command(['container', 'ls', '--all', '--quiet', '--filter', 'name=^/' + name + '$'], seconds=5)
    return remaining.returncode == 0 and remaining.stdout.strip() == b''


def _started(state):
    value = state.get('StartedAt')
    return state.get('Running') is True or (isinstance(value, str) and len(value) >= 20 and not value.startswith('0001-'))


def execute(files: dict[str, bytes], runner: str, targets: list[str], *, config: IsolatedConfig | dict,
            stop: threading.Event | None = None, authorize: Callable[[], bool], timeout_seconds=30,
            memory_mb=512, progress: Callable[[dict], None] | None = None):
    """Return a supervisor receipt. Never asserts that untrusted tests are honest.

    Callers own project ACL, secret scanning, and explicit file selection. The
    callback is rechecked before dispatch, during execution, and after execution.
    """
    global _QUARANTINED
    started = time.monotonic(); stop = stop or threading.Event()
    receipt = {'version': VERSION, 'adapter': 'docker-linux', 'status': 'blocked', 'reason_code': None,
        'executed': False, 'start_attempted': False, 'exit_code': None, 'runner_exit_zero': None, 'tests_passed': None,
        'test_count': None, 'test_count_verified': False, 'stdout': '', 'stderr': '',
        'host_fallback': False, 'automatic_download': False, 'isolation_verified': False,
        'policy_inspected': False, 'cleanup_verified': False, 'network_policy': 'none',
        'input_transport': 'bounded_stdin_no_host_mounts', 'runner': runner, 'targets': list(targets),
        'seconds': 0, 'notice': 'A container shares the engine kernel. Exit zero does not prove tests ran or claims are correct.'}
    client = None; name = None; acquired = False; lease = None
    try:
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120: raise IsolatedError('timeout_invalid')
        if type(memory_mb) is not int or not 128 <= memory_mb <= 2048: raise IsolatedError('memory_invalid')
        _check(stop, authorize, started + timeout_seconds)
        if _QUARANTINED: raise IsolatedError('cleanup_quarantine')
        config = IsolatedConfig.model_validate(config) if isinstance(config, dict) else config
        if not isinstance(config, IsolatedConfig): raise IsolatedError('configuration_invalid')
        payload, hashes = prepare_payload(files, runner, targets)
        output_limit = 6 * 2 * 1024**2 + 65536 if runner == 'document_extract' else MAX_OUTPUT
        if runner=='media_decode':
            from backend.isolated_audio import MAX_ENVELOPE
            output_limit=MAX_ENVELOPE
        if runner=='document_page':
            from backend.isolated_document_page import MAX_ENVELOPE,MAX_NORMALIZE_ENVELOPE,MAX_VISUAL_NORMALIZE_ENVELOPE,loads
            doc_meta=loads(files['metadata.json'])
            output_limit=(MAX_VISUAL_NORMALIZE_ENVELOPE if doc_meta.get('variant')=='visual' else MAX_NORMALIZE_ENVELOPE) if doc_meta.get('operation')=='normalize' else MAX_ENVELOPE
        if runner=='skill_transform':
            from backend.skill_transform_protocol import MAX_ENVELOPE
            output_limit=MAX_ENVELOPE
        receipt.update(snapshot_sha256=hashlib.sha256(payload).hexdigest(), file_hashes=hashes,
                       requested_limits={'seconds': timeout_seconds, 'memory_mb': memory_mb, 'output_bytes': output_limit})
        acquired = _SLOT.acquire(blocking=False)
        if not acquired: raise IsolatedError('busy')
        candidate_lease = worker_lease()
        candidate_lease.__enter__()
        lease = candidate_lease
        with tempfile.TemporaryDirectory(prefix='riwaq-isolated-') as folder:
            try:
                client = _Client(config, folder, deadline=started + timeout_seconds)
                receipt['runtime'] = _probe(client, runner, stop, authorize)
                _check(stop, authorize, started + timeout_seconds)
                name = 'riwaq-isolated-' + uuid.uuid4().hex
                created = client.command(create_arguments(config, runner, name, memory_mb, timeout_seconds), stop=stop, authorize=authorize)
                if created.returncode != 0: raise IsolatedError('container_create_failed')
                cid = created.stdout.decode('ascii', errors='strict').strip()
                if not _HEX.fullmatch(cid): raise IsolatedError('container_id_invalid')
                row = verify_container(_json(client.command(['inspect', name], stop=stop, authorize=authorize)), config, runner, memory_mb)
                if row.get('Id') != cid: raise IsolatedError('container_identity_mismatch')
                receipt['policy_inspected'] = True
                receipt.update(container_name=name, container_id=cid)
                if progress: progress({'stage': 'container_created', 'container_name': name, 'container_id': cid, 'executed': False})
                _check(stop, authorize, started + timeout_seconds)
                if progress: progress({'stage': 'running', 'adapter': 'docker-linux', 'executed': False})
                receipt['start_attempted'] = True
                result = client.command(['start', '--attach', '--interactive', name], payload=payload,
                    seconds=max(.001, started + timeout_seconds - time.monotonic()), stop=stop, authorize=authorize,
                    output_limit=output_limit)
                state = _json(client.command(['inspect', name], stop=stop, authorize=authorize))[0]['State']
                receipt['executed'] = _started(state)
                if state.get('Running') or state.get('Status') != 'exited': raise IsolatedError('container_state_unresolved')
                if not receipt['executed']: raise IsolatedError('container_start_unverified')
                code = state.get('ExitCode')
                if type(code) is not int: raise IsolatedError('container_state_unresolved')
                _check(stop, authorize, started + timeout_seconds)
                receipt.update(exit_code=code, runner_exit_zero=code == 0, status='completed' if code == 0 else 'failed',
                    reason_code='runner_exited' if code == 0 else 'runner_nonzero',
                    stdout=result.stdout.decode('utf-8', errors='replace'), stderr=result.stderr.decode('utf-8', errors='replace'))
                if state.get('OOMKilled'): receipt.update(status='resource_limit', reason_code='memory_limit', runner_exit_zero=False)
            finally:
                if client and name:
                    if receipt['start_attempted'] and not receipt['executed']:
                        try:
                            client.deadline = None
                            state = _json(client.command(['inspect', name], seconds=2))[0]['State']
                            receipt['executed'] = _started(state)
                        except Exception: pass
                    try: receipt['cleanup_verified'] = _cleanup(client, name)
                    except Exception: receipt['cleanup_verified'] = False
                    if not receipt['cleanup_verified']:
                        _QUARANTINED = True; _QUARANTINE_NAMES.add(name)
    except (IsolatedError, LeaseError) as exc:
        status = {'cancelled': 'cancelled', 'authorization_revoked': 'revoked', 'timeout': 'timeout', 'output_limit': 'output_limit'}.get(exc.code)
        receipt.update(status=status or ('failed' if receipt['executed'] else 'blocked'), reason_code=exc.code)
    except Exception: receipt.update(status='failed' if receipt['executed'] else 'blocked', reason_code='configuration_or_runtime_invalid')
    finally:
        if lease is not None: lease.__exit__(None, None, None)
        if acquired: _SLOT.release()
        receipt['seconds'] = round(time.monotonic() - started, 3)
    if _QUARANTINED: receipt.update(status='cleanup_failed', reason_code='container_cleanup_unverified', stdout='', stderr='')
    if receipt['status'] in {'revoked', 'cancelled', 'timeout', 'cleanup_failed'}: receipt.update(stdout='', stderr='', runner_exit_zero=None)
    return receipt


def cleanup_owned(config: IsolatedConfig, container_name: str, *, expected_id=None, expected_image=None):
    try:
        with worker_lease():
            return _cleanup_owned(config, container_name, expected_id=expected_id, expected_image=expected_image)
    except LeaseError as exc:
        raise IsolatedError(exc.code) from None


def _cleanup_owned(config: IsolatedConfig, container_name: str, *, expected_id=None, expected_image=None):
    """Recover one persisted owned container after a supervisor restart.

    The application must persist the container_created event before start and
    invoke this for interrupted jobs. This function never starts a container.
    """
    global _QUARANTINED
    if not re.fullmatch(r'riwaq-isolated-[0-9a-f]{32}', container_name):
        raise IsolatedError('container_name_invalid')
    with tempfile.TemporaryDirectory(prefix='riwaq-isolated-cleanup-') as folder:
        client = _Client(config, folder)
        info = _json(client.command(['info', '--format', '{{json .}}']))
        if info.get('ID') != config.daemon_id or info.get('OSType') != 'linux':
            raise IsolatedError('daemon_identity_mismatch')
        listed = client.command(['container', 'ls', '--all', '--quiet', '--filter', 'name=^/' + container_name + '$'])
        if listed.returncode: raise IsolatedError('cleanup_unverified')
        if not listed.stdout.strip():
            _QUARANTINE_NAMES.discard(container_name); _QUARANTINED = bool(_QUARANTINE_NAMES)
            return {'cleanup_verified': True, 'already_absent': True}
        rows = _json(client.command(['inspect', container_name]))
        if len(rows) != 1 or rows[0].get('Config', {}).get('Labels', {}).get('riwaq.isolated') != VERSION:
            raise IsolatedError('container_ownership_mismatch')
        if expected_id is not None and rows[0].get('Id') != expected_id:
            raise IsolatedError('container_identity_mismatch')
        if expected_image is not None and rows[0].get('Image') != expected_image:
            raise IsolatedError('image_identity_mismatch')
        ok = _cleanup(client, container_name)
        if not ok: raise IsolatedError('cleanup_unverified')
        _QUARANTINE_NAMES.discard(container_name); _QUARANTINED = bool(_QUARANTINE_NAMES)
        return {'cleanup_verified': True, 'already_absent': False}
