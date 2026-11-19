"""Real OS file-lock tests with trusted child Python only; NO Docker/user code."""
import asyncio
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

import pytest

from backend import isolated_locks as locks
from backend import isolated_journal as journal

ROOT = Path(__file__).resolve().parents[1]
CHILD = '''
import json, sys, time
from backend.isolated_locks import service_lease, worker_lease, LeaseError
from backend.isolated_journal import recover_pending
kind, mode = sys.argv[1:]
if mode == 'recover':
 print(json.dumps(recover_pending()), flush=True)
else:
 try:
  with (service_lease() if kind == 'service' else worker_lease()):
   print('acquired', flush=True)
   if mode == 'hold': time.sleep(60)
 except LeaseError as exc: print(exc.code, flush=True)
'''


@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    return tmp_path


def child(kind, mode='try'):
    return subprocess.run([sys.executable, '-B', '-c', CHILD, kind, mode],
        cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True,
        timeout=10, check=True).stdout.strip()


@pytest.mark.parametrize('kind', ['service', 'worker'])
def test_real_second_process_refused_and_lock_file_never_replaced(isolated_data, kind):
    lease = locks.service_lease if kind == 'service' else locks.worker_lease
    code = 'service_already_running' if kind == 'service' else 'isolated_worker_busy'
    with lease():
        path = locks._path(kind)
        identity = (path.stat().st_dev, path.stat().st_ino)
        assert child(kind) == code
    assert path.is_file()
    assert child(kind) == 'acquired'
    assert (path.stat().st_dev, path.stat().st_ino) == identity


@pytest.mark.parametrize('kind', ['service', 'worker'])
def test_real_owner_death_releases_os_lock_without_removing_file(isolated_data, kind):
    proc = subprocess.Popen([sys.executable, '-B', '-c', CHILD, kind, 'hold'],
        cwd=ROOT, env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    received = queue.Queue()
    threading.Thread(target=lambda: received.put(proc.stdout.readline()), daemon=True).start()
    try:
        assert received.get(timeout=10).strip() == 'acquired'
        path = locks._path(kind)
        identity = (path.stat().st_dev, path.stat().st_ino)
        lease = locks.service_lease if kind == 'service' else locks.worker_lease
        with pytest.raises(locks.LeaseError):
            with lease(): pass
        proc.kill()
        proc.wait(timeout=10)
        # In a Windows virtual environment sys.executable is a launcher: the interpreter that
        # holds the lock is its child and exits a moment after the launcher is killed. Wait
        # for the operating system to release the lock; the property under test is unchanged.
        deadline = time.monotonic() + 10
        while True:
            try:
                with lease():
                    assert (path.stat().st_dev, path.stat().st_ino) == identity
                break
            except locks.LeaseError:
                if time.monotonic() > deadline: raise
                time.sleep(0.1)
    finally:
        if proc.poll() is None:
            proc.kill(); proc.wait(timeout=10)
        proc.stdout.close(); proc.stderr.close()


def test_live_worker_cannot_be_recovered_by_another_process(isolated_data):
    folder = journal._directory()
    evidence = folder / ('riwaq-isolated-' + 'c'*32 + '.json')
    # Intentionally invalid evidence proves recovery never reads it while busy.
    evidence.write_text('{not read while owner is alive}', encoding='ascii')
    with locks.worker_lease():
        result = json.loads(child('worker', 'recover'))
        assert result['checked'] == result['cleaned'] == 0
        assert result['blocked'] == [{'reason_code': 'isolated_worker_busy'}]
        assert not result['execution_replayed']
        assert evidence.exists()


def test_wrapper_adapter_same_thread_nesting_retains_exclusivity(isolated_data):
    with locks.worker_lease():
        with locks.worker_lease():
            assert child('worker') == 'isolated_worker_busy'
        assert child('worker') == 'isolated_worker_busy'
    assert child('worker') == 'acquired'


def test_same_process_second_service_is_refused(isolated_data):
    with locks.service_lease():
        with pytest.raises(locks.LeaseError, match='service_already_running'):
            with locks.service_lease(): pass


def test_lifespan_cannot_initialize_or_recover_before_service_lock(isolated_data, monkeypatch):
    from backend import app
    calls = []
    def forbidden():
        calls.append('init_db')
        raise AssertionError('Database must not be touched by a second service')
    monkeypatch.setattr(app, 'init_db', forbidden)
    async def enter():
        async with app.lifespan(app.app):
            raise AssertionError('Second service started')
    with locks.service_lease():
        with pytest.raises(locks.LeaseError, match='service_already_running'):
            asyncio.run(enter())
    assert not calls and not (isolated_data/'riwaq.sqlite3').exists()


def test_exception_releases_worker_lease(isolated_data):
    with pytest.raises(ValueError):
        with locks.worker_lease(): raise ValueError('synthetic')
    assert child('worker') == 'acquired'
