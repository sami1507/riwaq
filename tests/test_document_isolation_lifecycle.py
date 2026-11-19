"""Real temporary SQLite lifecycle checks with explicitly SIMULATED workers.

No Docker, provider, downloaded fixture, host document parsing or user data.
These tests establish persistence/authority guards, not container isolation.
"""
import asyncio
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import sqlite3
import threading
import time

import pytest
from fastapi import HTTPException

from backend import documents, knowledge, local_auth, guard
from backend.db import get_db


DERIVATIVES = ('document_pages', 'document_elements', 'document_element_versions',
               'document_chunk_refs', 'knowledge_chunks')


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(documents, 'JOBS', {})
    monkeypatch.setattr(documents, 'SLOTS', threading.BoundedSemaphore(1))
    monkeypatch.setattr(documents, 'extraction_fingerprint', lambda: 'synthetic-reviewed-recipe')
    # Lifecycle tests inject a supervised result; they do not assert readiness
    # of any installed engine. Unavailability is tested separately below.
    monkeypatch.setattr(documents, 'worker_status', lambda: {'available': True, 'reason_code': None})
    with get_db() as conn:
        conn.executescript('''
          CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT NOT NULL,
            instructions TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
          CREATE TABLE events(id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            kind TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL);
          CREATE TABLE local_auth_owner(slot INTEGER PRIMARY KEY CHECK(slot=1),
            owner_id TEXT UNIQUE NOT NULL,created_at REAL NOT NULL,paired_at REAL);
          CREATE TABLE local_auth_sessions(id TEXT PRIMARY KEY,token_hash TEXT UNIQUE NOT NULL,
            owner_id TEXT NOT NULL,created_at REAL NOT NULL,expires_at REAL NOT NULL,
            revoked_at REAL,last_seen REAL NOT NULL);
        ''')
        conn.executemany('INSERT INTO projects VALUES(?,?,?,?)',
                         [('p', 'Synthetic owner', '', '2026-11-15T00:00:00Z'),
                          ('q', 'Synthetic other project', '', '2026-11-15T00:00:00Z')])
        knowledge.init_knowledge(conn)
        documents.init_documents(conn)
        stamp = time.time()
        principal = {'owner_id': 'a'*32, 'session_id': 'b'*32}
        conn.execute('INSERT INTO local_auth_owner VALUES(1,?,?,?)',
                     (principal['owner_id'], stamp, stamp))
        conn.execute('INSERT INTO local_auth_sessions VALUES(?,?,?,?,?,NULL,?)',
                     (principal['session_id'], 'c'*64, principal['owner_id'], stamp, stamp+3600, stamp))
        guard.init(conn)
        assert local_auth.session_active(conn, **{'owner': principal['owner_id'], 'session_id': principal['session_id']})
    token = local_auth._principal.set(principal)
    try:
        yield principal
        assert not documents.JOBS
    finally:
        local_auth._principal.reset(token)


def seed_file(fid='fixture', pages=1):
    raw = b'SYNTHETIC ORIGINAL; never parsed by these tests'
    config = {'languages': 'ara+eng+heb', 'extractor': documents.VERSION,
              'limits': asdict(documents.load_limits()),
              'extraction_fingerprint': documents.extraction_fingerprint()}
    with get_db() as conn:
        conn.execute('INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)',
            (fid, 'p', 'fixture.pdf', len(raw), hashlib.sha256(raw).hexdigest(),
             0, pages, 0, '2026-11-15T00:00:00Z'))
        conn.execute('INSERT INTO document_originals VALUES(?,?,?,?,?,?,?)',
            (fid, raw, 'application/pdf', 1, documents.pack(config), '{}', 'queued'))
        conn.execute('INSERT INTO document_jobs(id,file_id,status,pages_done,page_count,error,updated_at) VALUES(?,?,?,?,?,?,?)',
            ('job-'+fid, fid, 'queued', 0, pages, None, '2026-11-15T00:00:00Z'))
    return fid, raw


def simulated_page(number, text=None):
    text = text or 'Synthetic source page %d with value 731.' % number
    return {'number': number, 'kind': 'native', 'status': 'complete',
            'method': 'simulated-fixed-worker', 'language': 'eng', 'warnings': [],
            'width': 100, 'height': 100, 'preview_file': 'page.png',
            'elements': [{'id': 'p%d-e00001' % number, 'type': 'text',
              'text': text, 'text_raw': text, 'bbox': [0.1, 0.1, 0.9, 0.2],
              'locator': {'page': number}, 'quality': {'method': 'simulated'}}]}


def fake_reply(number):
    # A marker stands in for an already-validated preview. These tests do not
    # claim that it is decodable PNG data; protocol tests cover that boundary.
    return ({'page': simulated_page(number)}, b'SYNTHETIC_PREVIEW_NOT_A_REAL_PNG',
            {'status': 'completed', 'seconds': 0, 'cleanup_verified': True,
             'policy_inspected': True, 'stdout': 'NOT_FOR_PERSISTENCE'})


def run_thread(fid, stop, principal):
    failures = []
    def target():
        try: documents.process_job('p', fid, stop, principal=principal)
        except BaseException as error: failures.append(error)
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, failures


def finish(thread, failures, release):
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive(), 'Simulated page job failed to terminate'
    assert not failures


def test_simulated_worker_result_after_cancel_is_not_persisted(database, monkeypatch):
    fid, raw = seed_file()
    entered, release, stop = threading.Event(), threading.Event(), threading.Event()
    def late(data, filename, page, language, cancel, authorize=None):
        assert data == raw
        entered.set()
        assert release.wait(5)
        return fake_reply(page)  # Deliberately ignores the now-set stop event.
    monkeypatch.setattr(documents, 'parse', late)
    thread, failures = run_thread(fid, stop, database)
    try:
        assert entered.wait(5)
        stop.set()
    finally:
        finish(thread, failures, release)
    with get_db() as conn:
        for table in DERIVATIVES:
            assert conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0
        assert conn.execute('SELECT original FROM document_originals WHERE file_id=?', (fid,)).fetchone()[0] == raw
        job = conn.execute('SELECT * FROM document_jobs WHERE file_id=?', (fid,)).fetchone()
        assert job['status'] == 'cancelled' and job['pages_done'] == 0
        assert json.loads(job['resources']) == []


def test_simulated_stale_result_after_source_deletion_restores_nothing(database, monkeypatch):
    fid, _ = seed_file()
    entered, release, stop = threading.Event(), threading.Event(), threading.Event()
    def late(*args, **kwargs):
        entered.set(); assert release.wait(5)
        return fake_reply(1)
    monkeypatch.setattr(documents, 'parse', late)
    thread, failures = run_thread(fid, stop, database)
    try:
        assert entered.wait(5)
        knowledge.delete_file('p', fid)
        assert not stop.is_set()  # Existence guard, independent of cooperative cancellation.
    finally:
        finish(thread, failures, release)
    with get_db() as conn:
        for table in (*DERIVATIVES, 'knowledge_files', 'document_originals', 'document_jobs'):
            assert conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0


def test_cancel_preserves_committed_page_and_resume_only_processes_remainder(database, monkeypatch):
    fid, raw = seed_file(pages=2)
    entered, release, stop = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def simulated(data, filename, page, language, cancel, authorize=None):
        calls.append(page)
        if page == 2 and calls == [1, 2]:
            entered.set(); assert release.wait(5)
        return fake_reply(page)
    monkeypatch.setattr(documents, 'parse', simulated)
    thread, failures = run_thread(fid, stop, database)
    try:
        assert entered.wait(5)
        with get_db() as conn:
            original_page = conn.execute('SELECT payload FROM document_pages WHERE file_id=? AND number=1', (fid,)).fetchone()[0]
        stop.set()
    finally:
        finish(thread, failures, release)
    dispatched = []
    monkeypatch.setattr(documents, 'enqueue', lambda pid, doc, principal: dispatched.append((pid, doc, principal)))
    assert asyncio.run(documents.resume('p', fid))['status'] == 'queued'
    assert dispatched == [('p', fid, database)]
    documents.process_job('p', fid, threading.Event(), principal=database)
    with get_db() as conn:
        assert conn.execute('SELECT payload FROM document_pages WHERE file_id=? AND number=1', (fid,)).fetchone()[0] == original_page
        assert conn.execute('SELECT original FROM document_originals WHERE file_id=?', (fid,)).fetchone()[0] == raw
        job = conn.execute('SELECT * FROM document_jobs WHERE file_id=?', (fid,)).fetchone()
        assert job['status'] == 'complete' and job['pages_done'] == 2
        assert len(json.loads(job['resources'])) == 2
    assert calls == [1, 2, 2]


def test_recipe_change_refuses_resume_without_dispatch_or_original_mutation(database, monkeypatch):
    fid, raw = seed_file()
    calls = []
    monkeypatch.setattr(documents, 'enqueue', lambda *args: calls.append(args))
    monkeypatch.setattr(documents, 'extraction_fingerprint', lambda: 'different-pinned-image')
    with pytest.raises(HTTPException) as error:
        asyncio.run(documents.resume('p', fid))
    assert error.value.status_code == 409 and not calls
    with get_db() as conn:
        assert conn.execute('SELECT original FROM document_originals WHERE file_id=?', (fid,)).fetchone()[0] == raw
        assert conn.execute('SELECT COUNT(*) FROM document_pages').fetchone()[0] == 0


@pytest.mark.parametrize('action', ['resume', 'cancel'])
def test_other_project_cannot_cancel_or_resume_owned_document(database, monkeypatch, action):
    fid, _ = seed_file()
    calls = []
    monkeypatch.setattr(documents, 'enqueue', lambda *args: calls.append(args))
    monkeypatch.setattr(documents, 'cancel_file', lambda *args: calls.append(args))
    with pytest.raises(HTTPException) as error:
        asyncio.run(getattr(documents, action)('q', fid))
    assert error.value.status_code == 404 and not calls
    with get_db() as conn:
        assert conn.execute('SELECT status FROM document_jobs WHERE file_id=?', (fid,)).fetchone()[0] == 'queued'


def test_restart_marks_interrupted_without_replaying_committed_page(database, monkeypatch):
    fid, raw = seed_file(pages=2)
    with get_db() as conn:
        documents.store_page(conn, 'p', fid, 'application/pdf', simulated_page(1), b'SYNTHETIC_PREVIEW')
        documents.set_status(conn, fid, 'running')
    calls = []
    monkeypatch.setattr(documents, 'parse', lambda *args, **kwargs: calls.append(args))
    with get_db() as conn:
        documents.init_documents(conn)
        assert conn.execute('SELECT status FROM document_jobs WHERE file_id=?', (fid,)).fetchone()[0] == 'interrupted'
        assert conn.execute('SELECT COUNT(*) FROM document_pages').fetchone()[0] == 1
        assert conn.execute('SELECT original FROM document_originals WHERE file_id=?', (fid,)).fetchone()[0] == raw
    assert not calls


def test_cancel_while_waiting_for_worker_never_dispatches(database, monkeypatch):
    fid, _ = seed_file()
    calls = []
    monkeypatch.setattr(documents, 'parse', lambda *args, **kwargs: calls.append(args))
    stop = threading.Event()
    documents.SLOTS.acquire()
    try:
        thread, failures = run_thread(fid, stop, database)
        stop.set()
        thread.join(timeout=3)
        assert not thread.is_alive() and not failures and not calls
    finally:
        documents.SLOTS.release()
    with get_db() as conn:
        assert conn.execute('SELECT status FROM document_jobs WHERE file_id=?', (fid,)).fetchone()[0] == 'cancelled'
        assert conn.execute('SELECT COUNT(*) FROM document_pages').fetchone()[0] == 0


def revoke(principal):
    with get_db() as conn:
        conn.execute('UPDATE local_auth_sessions SET revoked_at=? WHERE id=?',
                     (time.time(), principal['session_id']))
        assert not local_auth.session_active(conn, principal['owner_id'], principal['session_id'])


@pytest.mark.parametrize('withdrawal',['session','project','file'])
def test_session_revocation_during_worker_discards_all_late_derivatives(database, monkeypatch, withdrawal):
    fid, raw = seed_file()
    entered, release, stop = threading.Event(), threading.Event(), threading.Event()
    callback_checks = []
    def delayed(data, filename, page, language, cancel, authorize=None):
        assert callable(authorize)
        callback_checks.append(authorize())
        entered.set(); assert release.wait(5)
        callback_checks.append(authorize())
        return fake_reply(page)  # A compromised/stale worker ignores revocation.
    monkeypatch.setattr(documents, 'parse', delayed)
    thread, failures = run_thread(fid, stop, database)
    try:
        assert entered.wait(5)
        if withdrawal=='session':revoke(database)
        else:
            with get_db() as conn:
                if withdrawal=='project':conn.execute("UPDATE guard_projects SET owner_id='other' WHERE project_id='p'")
                else:conn.execute('UPDATE guard_file_policies SET revoked=1,revision=revision+1 WHERE file_id=?',(fid,))
    finally:
        finish(thread, failures, release)
    assert callback_checks == [True, False]
    with get_db() as conn:
        for table in DERIVATIVES:
            assert conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0
        assert conn.execute('SELECT original FROM document_originals WHERE file_id=?', (fid,)).fetchone()[0] == raw
        assert json.loads(conn.execute('SELECT resources FROM document_jobs WHERE file_id=?', (fid,)).fetchone()[0]) == []
        assert conn.execute('SELECT status FROM document_jobs WHERE file_id=?', (fid,)).fetchone()[0] not in {'queued', 'running', 'cancelling'}


def test_revocation_after_last_worker_authorization_is_rechecked_before_commit(database, monkeypatch):
    fid, _ = seed_file()
    checks = []
    def already_finished(data, filename, page, language, stop, authorize=None):
        assert callable(authorize)
        checks.append(authorize())
        result = fake_reply(page)
        revoke(database)  # After the worker's final check, before DB publication.
        return result
    monkeypatch.setattr(documents, 'parse', already_finished)
    documents.process_job('p', fid, threading.Event(), principal=database)
    assert checks == [True]
    with get_db() as conn:
        for table in DERIVATIVES:
            assert conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0
        assert json.loads(conn.execute('SELECT resources FROM document_jobs WHERE file_id=?', (fid,)).fetchone()[0]) == []
        assert conn.execute('SELECT status FROM document_jobs WHERE file_id=?', (fid,)).fetchone()[0] not in {'queued', 'running', 'cancelling'}


@pytest.mark.parametrize('invalid', ['missing', 'wrong-owner', 'revoked', 'expired'])
def test_job_requires_a_current_explicit_principal_before_dispatch(database, monkeypatch, invalid):
    fid, _ = seed_file()
    principal = dict(database)
    if invalid == 'missing': principal = None
    elif invalid == 'wrong-owner': principal['owner_id'] = 'd'*32
    elif invalid == 'revoked': revoke(database)
    elif invalid == 'expired':
        with get_db() as conn:
            conn.execute('UPDATE local_auth_sessions SET expires_at=? WHERE id=?',
                         (time.time()-1, database['session_id']))
    calls = []
    monkeypatch.setattr(documents, 'parse', lambda *a, **kw: calls.append(a))
    documents.process_job('p', fid, threading.Event(), principal=principal)
    assert not calls
    with get_db() as conn:
        for table in DERIVATIVES:
            assert conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0


def test_wrong_project_direct_job_cannot_change_owned_job_status(database, monkeypatch):
    fid, _ = seed_file()
    calls = []
    monkeypatch.setattr(documents, 'parse', lambda *a, **kw: calls.append(a))
    documents.process_job('q', fid, threading.Event(), principal=database)
    assert not calls
    with get_db() as conn:
        assert conn.execute('SELECT status FROM document_jobs WHERE file_id=?', (fid,)).fetchone()[0] == 'queued'
        assert conn.execute('SELECT status FROM document_originals WHERE file_id=?', (fid,)).fetchone()[0] == 'queued'


def test_source_version_change_before_commit_rejects_old_page_result(database, monkeypatch):
    fid, _ = seed_file()
    callback_checks = []
    def stale(data, filename, page, language, stop, authorize=None):
        assert callable(authorize)
        callback_checks.append(authorize())
        with get_db() as conn:
            conn.execute('UPDATE document_originals SET version=version+1 WHERE file_id=?', (fid,))
        callback_checks.append(authorize())
        return fake_reply(page)
    monkeypatch.setattr(documents, 'parse', stale)
    documents.process_job('p', fid, threading.Event(), principal=database)
    assert callback_checks == [True, False]
    with get_db() as conn:
        for table in DERIVATIVES:
            assert conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0
        assert conn.execute('SELECT version FROM document_originals WHERE file_id=?', (fid,)).fetchone()[0] == 2


def test_missing_container_configuration_never_uses_host_parser(tmp_path, monkeypatch):
    from backend import document_extract, document_isolation, visual_runner
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('RIWAQ_DOCUMENT_LIMITS', raising=False)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append('host-parser-or-supervisor')
        raise AssertionError('Unconfigured isolation must not parse on the host')
    monkeypatch.setattr(document_extract, 'extract_document', forbidden)
    monkeypatch.setattr(visual_runner, 'execute', forbidden)
    assert not documents.worker_status()['available']
    with pytest.raises(document_isolation.IsolationError) as error:
        documents.parse(b'synthetic', 'fixture.txt', 0, 'eng', threading.Event(),
                        authorize=lambda: True)
    assert error.value.status_code == 503 and not calls


def test_database_failure_after_worker_and_during_finalization_releases_slot(database, monkeypatch):
    fid, raw = seed_file()
    fault, stop = threading.Event(), threading.Event()
    real_get_db = documents.get_db

    @contextmanager
    def unavailable_after_parse():
        if fault.is_set():
            raise sqlite3.OperationalError('Synthetic database unavailable after worker')
        with real_get_db() as conn:
            yield conn

    def completed_before_database_failure(data, filename, page, language, cancel, authorize=None):
        assert callable(authorize) and authorize()
        # Exercise both the post-worker commit and the cancellation finalizer
        # while database access is unavailable. The worker result is simulated.
        fault.set()
        assert authorize() is False  # Storage failure cannot grant authority.
        stop.set()
        return fake_reply(page)

    monkeypatch.setattr(documents, 'get_db', unavailable_after_parse)
    monkeypatch.setattr(documents, 'parse', completed_before_database_failure)
    thread, failures = run_thread(fid, stop, database)
    thread.join(timeout=5)
    assert not thread.is_alive() and fault.is_set()
    # A storage error may propagate from finalization; that is not a successful
    # job. The invariant here is cleanup capacity plus no accepted late output.
    assert all(isinstance(error, sqlite3.OperationalError) for error in failures)
    acquired = documents.SLOTS.acquire(blocking=False)
    try:
        assert acquired, 'Database failure leaked the global document-worker slot'
    finally:
        if acquired:
            documents.SLOTS.release()
    with real_get_db() as conn:
        for table in DERIVATIVES:
            assert conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0
        assert conn.execute('SELECT original FROM document_originals WHERE file_id=?', (fid,)).fetchone()[0] == raw
        job = conn.execute('SELECT status,resources FROM document_jobs WHERE file_id=?', (fid,)).fetchone()
        assert job['status'] != 'complete' and json.loads(job['resources']) == []


def test_cancel_route_settles_when_the_worker_finished_first(database, monkeypatch):
    """A cancelled job that ends before the route records 'cancelling' must not stay in
    'cancelling' forever (seen live on Windows: twelve-page upload cancelled at once)."""
    fid, _ = seed_file()
    monkeypatch.setattr(documents, 'parse', lambda *args, **kwargs: pytest.fail('no page may be dispatched'))
    stop = threading.Event()
    documents.SLOTS.acquire()
    thread, failures = run_thread(fid, stop, database)

    class Task:
        def done(self): return not thread.is_alive()
    documents.JOBS[fid] = {'task': Task(), 'stop': stop}

    def signal_then_let_the_worker_finish(file_id):
        stop.set(); thread.join(timeout=3)  # the worker records 'cancelled' before the route writes
        return True
    monkeypatch.setattr(documents, 'cancel_file', signal_then_let_the_worker_finish)
    try:
        described = asyncio.run(documents.cancel('p', fid))
    finally:
        documents.SLOTS.release()
        documents.JOBS.pop(fid, None)
    assert not failures and not thread.is_alive()
    assert described['status'] == 'cancelled'
    with get_db() as conn:
        assert conn.execute('SELECT status FROM document_jobs WHERE file_id=?', (fid,)).fetchone()[0] == 'cancelled'

