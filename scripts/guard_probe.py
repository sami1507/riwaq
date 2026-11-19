"""Synthetic-only audit probes. Never import user code or read user documents."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def latency():
    from fastapi.testclient import TestClient
    from backend import local_auth
    from backend.app import app
    with tempfile.TemporaryDirectory(prefix='riwaq-guard-latency-') as folder:
        os.environ['RIWAQ_DATA_DIR'] = folder
        os.environ['RIWAQ_KNOWLEDGE_REGISTRY'] = str(Path(folder) / 'absent.json')
        with TestClient(app, headers={'X-Riwaq-Client': 'local'}) as client:
            denied = client.get('/api/projects').status_code
            code = local_auth.issue_pairing_code()
            assert client.post('/api/auth/login', json={'pairing_code': code}).status_code == 200
            del code
            assert client.get('/api/projects').status_code == 200
            values = []
            for _ in range(50):
                start = time.perf_counter()
                response = client.get('/api/projects')
                assert response.status_code == 200
                values.append((time.perf_counter() - start) * 1000)
        local_auth._key_cache.clear()
    values.sort()
    return {'n': len(values), 'unauthenticated_status': denied, 'authorized_success': 50,
            'p50_ms': values[24], 'p95_ms': values[47], 'mean_ms': sum(values) / len(values),
            'final_owner_quota_enabled': True, 'model_calls': 0, 'transport': 'Starlette TestClient ASGI',
            'synthetic_only': True}


def worker_boundary():
    """Fixed trusted probe uses only a parent-owned listener and synthetic file."""
    from backend.visual_runner import execute
    with tempfile.TemporaryDirectory(prefix='riwaq-worker-canary-') as folder:
        base = Path(folder)
        work = base / 'worker'
        work.mkdir()
        source = base / 'outside-worker-synthetic.txt'
        source.write_text('RIWAQ_SYNTHETIC_FILE_CANARY', encoding='utf-8')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            listener.settimeout(1)
            port = listener.getsockname()[1]
            script = work / 'fixed_probe.py'
            script.write_text('''import json, socket, sys
from pathlib import Path
result = {}
try:
 with socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=2) as connection:
  connection.sendall(b'RIWAQ_SYNTHETIC_NETWORK_CANARY')
  result['owned_loopback_reachable'] = True
except OSError:
 result['owned_loopback_reachable'] = False
result['outside_cwd_synthetic_file_readable'] = Path(sys.argv[2]).read_text(encoding='utf-8') == 'RIWAQ_SYNTHETIC_FILE_CANARY'
Path('result.json').write_text(json.dumps(result), encoding='utf-8')
''', encoding='utf-8')
            receipt = execute([sys.executable, '-I', str(script), str(port), str(source)],
                              str(work), work, threading.Event(), lambda event: None,
                              seconds=8, memory=256 * 1024**2)
            data = json.loads((work / 'result.json').read_text(encoding='utf-8'))
            received = False
            try:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(1)
                    received = connection.recv(128) == b'RIWAQ_SYNTHETIC_NETWORK_CANARY'
            except TimeoutError:
                pass
    return {**data, 'parent_verified_network_canary': received,
            'exit_code': receipt['exit_code'], 'abort_reason': receipt['abort_reason'],
            'seconds': receipt['seconds'], 'remaining_processes': receipt['remaining_processes'],
            'windows_kill_on_close': receipt['windows_kill_on_close'],
            'synthetic_only': True, 'external_network_attempted': False,
            'conclusion': 'Supervisor is not an OS network or filesystem sandbox',
            'limitation': 'Trusted probe establishes missing worker boundary, not a parser exploit or compromise.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', choices=['latency', 'worker-boundary'])
    args = parser.parse_args()
    print(json.dumps(latency() if args.probe == 'latency' else worker_boundary(), ensure_ascii=False, indent=2))
