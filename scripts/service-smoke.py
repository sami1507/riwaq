"""Real packaged-service validation, without mocks or native-shell claims.

Requires Ollama/model already running and port 8765 free. Never bypasses an OS
execution block. Use root-authorized execution to launch the packaged service.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time

import httpx

BASE = 'http://127.0.0.1:8765'
HEADERS = {'X-Riwaq-Client': 'local'}


def port_open():
    try:
        with socket.create_connection(('127.0.0.1', 8765), timeout=.3):
            return True
    except OSError:
        return False


def stop_owned(process):
    if process is None or process.poll() is not None:
        return
    if os.name == 'nt':
        killer = Path(os.environ['SystemRoot']) / 'System32' / 'taskkill.exe'
        subprocess.run([str(killer), '/PID', str(process.pid), '/T', '/F'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def wait_ready(client, process):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Packaged service exited with code {process.returncode}')
        try:
            response = client.get(BASE + '/api/settings', timeout=2)
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError:
            pass
        time.sleep(.25)
    raise TimeoutError('Packaged service startup exceeded 40 seconds')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', required=True)
    parser.add_argument('--output', default='reports/packaged-service.json')
    parser.add_argument('--model', default='qwen3:4b-instruct')
    args = parser.parse_args()
    executable = Path(args.exe).resolve(strict=True)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if port_open():
        raise SystemExit('Port 8765 is occupied. Stop the owned development service first; this test will not attach to it.')
    data_root = Path('data').resolve()
    data_root.mkdir(exist_ok=True)
    data_dir = Path(tempfile.mkdtemp(prefix='packaged-smoke-', dir=data_root))
    environment = os.environ.copy()
    environment['RIWAQ_DATA_DIR'] = str(data_dir)
    environment.pop('RIWAQ_STATIC_DIR', None)
    environment.pop('RIWAQ_PACKAGED_MEDIA_WORKER', None)
    report = {'artifact': str(executable), 'sha256': hashlib.sha256(executable.read_bytes()).hexdigest(), 'data_dir': str(data_dir), 'passed': False, 'checks': [], 'scope': 'Packaged Python service + bundled web assets; not a native Tauri or Edge window test'}
    process = None
    started = time.monotonic()
    try:
        with (data_dir / 'service.log').open('ab') as log, httpx.Client(headers=HEADERS, timeout=180, trust_env=False) as client:
            def launch():
                print('Starting packaged service', flush=True)
                return subprocess.Popen([str(executable)], env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            process = launch()
            configuration = wait_ready(client, process)
            assert configuration['mode'] == 'local' and configuration['provider'] == 'ollama'
            report['checks'].append('Packaged service started with local-only defaults')
            page = client.get(BASE + '/')
            assert page.status_code == 200 and ('Riwaq' in page.text or 'رِواق' in page.text)
            assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page.text)
            assert assets and all(client.get(BASE + item).status_code == 200 for item in assets)
            report['checks'].append('Bundled HTML and compiled assets served successfully')
            project = client.get(BASE + '/api/projects').json()[0]
            chat = client.post(BASE + '/api/conversations', json={'project_id': project['id'], 'title': 'Packaged service validation'}).json()
            packets = []
            print('Requesting a real local Arabic model response', flush=True)
            with client.stream('POST', BASE + f"/api/conversations/{chat['id']}/generate", json={'content': 'اكتب جملة عربية قصيرة تصف فائدة حفظ المحادثة محليًا.', 'model': args.model, 'provider': 'ollama'}) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line:
                        packets.append(json.loads(line))
            deltas = [item['content'] for item in packets if item['type'] == 'delta']
            completion = next((item['message'] for item in reversed(packets) if item['type'] == 'done'), None)
            assert len(deltas) > 1 and completion and completion['status'] == 'complete'
            assert re.search(r'[\u0600-\u06ff]', completion['content'])
            report['checks'].append('Real Ollama Arabic response streamed through packaged service')
            report['model'] = args.model
            report['response'] = completion['content']
            report['stream_chunks'] = len(deltas)
            stop_owned(process)
            process = None
            assert not port_open(), 'Packaged service worker remained after its owned process tree stopped'
            process = launch()
            wait_ready(client, process)
            restored = client.get(BASE + f"/api/conversations/{chat['id']}").json()
            assert restored['messages'][-1]['content'] == completion['content']
            report['checks'].append('Conversation restored after packaged process restart')
            snapshot = client.get(BASE + f"/api/projects/{project['id']}/snapshot")
            assert snapshot.status_code == 200 and snapshot.json()['version'] == 1
            report['checks'].append('Snapshot module included in packaged build')
            media = client.get(BASE + '/api/media/status')
            assert media.status_code == 200 and media.json()['stt']['available'] is False
            report['checks'].append('Packaged transcription accurately unavailable')
            report['passed'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        if isinstance(exc, OSError) and getattr(exc, 'winerror', None) == 4551:
            report['blocked_by_application_control'] = True
            report['note'] = 'Stopped without renaming the executable or modifying/bypassing Windows policy.'
        raise
    finally:
        stop_owned(process)
        report['seconds'] = round(time.monotonic() - started, 2)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'passed': report['passed'], 'report': str(output), 'checks': len(report['checks'])}), flush=True)


if __name__ == '__main__':
    main()
