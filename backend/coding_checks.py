"""Durable, authorized checks on bounded copies, never on host project paths.

Only the server builds snapshots. Container output is untrusted diagnostics;
the independently observed exit status is not proof of test coverage/correctness.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
from fastapi import HTTPException
from pydantic import Field

from backend import coding_sandbox as sandbox, workspace, local_auth
from backend.db import get_db, now, uid

MAX_BYTES = 256 * 1024
MAX_FILES = 32


class CheckInput(sandbox.TestRequest):
    request_key: str | None = Field(default=None, min_length=8, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')


def init(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS coding_check_runs(
      id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES coding_tasks(id),
      project_id TEXT NOT NULL, request_key TEXT NOT NULL, signature TEXT NOT NULL,
      owner_id TEXT NOT NULL, session_id TEXT NOT NULL, status TEXT NOT NULL,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, data TEXT NOT NULL,
      UNIQUE(task_id,request_key))''')
    # Never replay arbitrary code after service restart, even when the outcome
    # of a previous container is unknown. The operator cleans tagged orphans.
    conn.execute("UPDATE coding_check_runs SET status='interrupted',updated_at=? WHERE status IN ('queued','running')", (now(),))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def snapshot(grant, data, intent):
    from backend.coding_context import scan_secrets
    selected = list(dict.fromkeys([*data['selected_paths'], *intent.targets]))
    if len(selected) > MAX_FILES:
        raise HTTPException(413, 'Too many source and test files')
    files, bindings, total = {}, [], 0
    for path in selected:
        workspace.relative_parts(path)
        raw = workspace.read_bytes(grant, path)
        total += len(raw)
        if len(raw) > 65536 or total > MAX_BYTES:
            raise HTTPException(413, 'Isolated check snapshot exceeds its size limit')
        try:
            text = raw.decode('utf-8-sig', errors='strict')
        except UnicodeError:
            raise HTTPException(422, 'Only selected UTF-8 source files may enter the check') from None
        if '\x00' in text or scan_secrets(text) or scan_secrets(path):
            raise HTTPException(422, 'A selected source is binary or contains possible credentials')
        files[path] = raw
        bindings.append({'path': path, 'sha256': workspace.digest(raw)})
    kind = 'current_files'
    if data.get('_changes') and not data.get('_ever_applied'):
        if data['proposal']['state'] != 'proposed':
            raise HTTPException(409, 'Refresh the proposal before checking it')
        kind = 'proposed_copy'
        for change in data['_changes']:
            path = change['path']
            if path not in files or workspace.digest(files[path]) != change['original_sha']:
                raise HTTPException(409, 'Source changed after the proposal; no stale copy will be tested')
            raw = base64.b64decode(change['new_bytes_b64'], validate=True)
            if workspace.digest(raw) != change['new_sha']:
                raise HTTPException(409, 'Stored proposal integrity check failed')
            if scan_secrets(raw.decode('utf-8-sig')):
                raise HTTPException(422, 'Possible credential in proposed content')
            files[path] = raw
    if sum(map(len, files.values())) > MAX_BYTES:
        raise HTTPException(413, 'Proposed check snapshot exceeds its size limit')
    digest = hashlib.sha256(canonical({p: workspace.digest(raw) for p, raw in files.items()}).encode()).hexdigest()
    return files, bindings, kind, digest


def authorized(pid, tid, principal):
    from backend import coding
    try:
        with get_db() as conn:
            coding.task(conn, pid, tid)  # Rechecks the workspace grant.
            return local_auth.session_active(conn, principal['owner_id'], principal['session_id'])
    except Exception:
        return False


def clean_receipt(receipt):
    from backend.coding_context import scan_secrets
    result = dict(receipt)
    # The sandbox cannot see host secrets. Selected code can still print its
    # own sensitive data or malicious terminal escape sequences.
    for key in ('stdout', 'stderr'):
        value = str(result.get(key, ''))[:65536]
        if scan_secrets(value):
            result[key] = '[Output withheld: possible credential content]'
        else:
            result[key] = ''.join(c for c in value if c in '\n\t' or (ord(c) >= 32 and ord(c) != 127))
    result['tests_passed'] = None
    result['test_count'] = None
    result['test_count_verified'] = False
    return result


def recover_interrupted():
    """Clean only persisted check containers; never rerun a source or patch."""
    from backend.db import data_dir
    from backend.isolated_worker import cleanup_owned, load_config
    result = {'checked': 0, 'cleaned': 0, 'blocked': [], 'execution_replayed': False}
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM coding_check_runs WHERE status='interrupted' LIMIT 100").fetchall()
    for row in rows:
        result['checked'] += 1
        try:
            data = json.loads(row['data'])
            container = data.get('runtime_container')
            if container:
                config = load_config(data_dir()/'isolated-workers.json')
                runner = data['intent']['runner']
                receipt = cleanup_owned(config, container['container_name'],
                    expected_id=container['container_id'], expected_image=config.images[runner])
                if receipt.get('cleanup_verified') is not True:
                    raise RuntimeError('Cleanup could not be verified')
            # A queued job without a container identity never passed the
            # mandatory durable-before-start callback; no code is replayed.
            data['receipt'].update(status='interrupted', cleanup_verified=True,
                                   reason_code='service_restarted', tests_passed=None)
            with get_db() as conn:
                conn.execute("UPDATE coding_check_runs SET status='interrupted_cleaned',data=?,updated_at=? WHERE id=? AND status='interrupted'",
                             (canonical(data), now(), row['id']))
                task_row = conn.execute('SELECT data FROM coding_tasks WHERE id=? AND status=?', (row['task_id'], 'interrupted')).fetchone()
                if task_row:
                    task_data = json.loads(task_row['data'])
                    if task_data.get('validation', {}).get('id') == row['id']:
                        task_data['validation'] = data['receipt']
                        conn.execute('UPDATE coding_tasks SET data=?,revision=revision+1,updated_at=? WHERE id=? AND status=?',
                                     (canonical(task_data), now(), row['task_id'], 'interrupted'))
            result['cleaned'] += 1
        except Exception:
            result['blocked'].append({'check_id': row['id'], 'reason_code': 'cleanup_unverified'})
    return result


async def start(pid, tid, body: CheckInput, *, pipeline_state=None):
    from backend import coding
    intent = sandbox.TestRequest.model_validate(body.model_dump(exclude={'request_key'}))
    # Keep the old no-execution contract when no reviewed adapter is configured.
    capabilities = sandbox.status()['execution']
    if not (capabilities.get('can_request') or capabilities['available']):
        if tid in coding.ACTIVE and coding.ACTIVE[tid] is not pipeline_state:
            raise HTTPException(409, 'Wait for the task or stop it before requesting checks')
        with get_db() as conn:
            row, data = coding.task(conn, pid, tid)
            receipt = sandbox.run_tests('', intent)
            data['validation'] = receipt
            coding.log(data, 'checks_blocked', 'No project code executed: isolation unavailable.')
            coding.save(conn, row, data)
        return receipt, 503
    principal = local_auth.current_principal()
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row, data = coding.task(conn, pid, tid)
        _, grant = coding.binding(conn, pid, row['workspace_id'])
        # Explicit retries use a fresh request key; a transport retry returns
        # the same persisted outcome without running code a second time.
        if body.request_key:
            old = conn.execute('SELECT * FROM coding_check_runs WHERE task_id=? AND request_key=?', (tid, body.request_key)).fetchone()
            if old:
                saved = json.loads(old['data'])
                if saved['intent'] != intent.model_dump():
                    raise HTTPException(409, 'Check request key already names different inputs')
                receipt = saved['receipt']
                if old['status'] == 'interrupted':
                    receipt = {**receipt, 'status': 'interrupted', 'reason_code': 'service_restarted', 'tests_passed': None}
                return receipt, 202 if old['status'] in ('queued', 'running') else 200
        inline = pipeline_state is not None and coding.ACTIVE.get(tid) is pipeline_state
        if pipeline_state is not None and not inline:
            raise HTTPException(409, 'The owning task is no longer active')
        if (not inline and coding.busy()) or (inline and set(coding.ACTIVE) != {tid}):
            raise HTTPException(409, 'Wait for active work to finish before checking')
        # Shared SQLite admission, not just this process's ACTIVE dictionary.
        # BEGIN IMMEDIATE serializes competing check admissions.
        if conn.execute("SELECT 1 FROM coding_check_runs WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise HTTPException(409, 'Another isolated check is already recorded as active')
        if conn.execute("SELECT 1 FROM coding_check_runs WHERE status='interrupted' LIMIT 1").fetchone():
            raise HTTPException(503, 'An interrupted check requires container cleanup before another execution')
        if not local_auth.session_active(conn, principal['owner_id'], principal['session_id']):
            raise HTTPException(401, 'Session no longer authorized')
        if row['status'] in ('recovery_required', 'revoked'):
            raise HTTPException(409, 'Resolve workspace recovery before checking')
        if conn.execute('SELECT COUNT(*) FROM coding_check_runs WHERE task_id=?', (tid,)).fetchone()[0] >= 30:
            raise HTTPException(413, 'Check attempt limit reached for this task')
        files, bindings, kind, digest = snapshot(grant, data, intent)
        signature = hashlib.sha256(canonical({'intent': intent.model_dump(), 'snapshot': digest, 'kind': kind}).encode()).hexdigest()
        key = body.request_key or signature
        old = conn.execute('SELECT * FROM coding_check_runs WHERE task_id=? AND request_key=?', (tid, key)).fetchone()
        if old:
            return json.loads(old['data'])['receipt'], 200
        check_id = uid()
        receipt = {'id': check_id, 'status': 'queued', 'executed': False, 'tests_passed': None,
                   'test_count': None, 'runner': intent.runner, 'snapshot_kind': kind,
                   'snapshot_sha256': digest, 'snapshot_paths': sorted(files),
                   'reason_ar': 'فحص نسخة محدودة؛ ملفات المشروع الأصلية لا تُنفّذ على المضيف.',
                   'reason_en': 'Checking a bounded copy; original project files never execute on the host.'}
        stored = {'receipt': receipt, 'intent': intent.model_dump(), 'before_status': row['status'], 'bindings': bindings}
        stamp = now()
        conn.execute('INSERT INTO coding_check_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                     (check_id, tid, pid, key, signature, principal['owner_id'], principal['session_id'], 'queued', stamp, stamp, canonical(stored)))
        data['validation'] = receipt
        coding.log(data, 'checking', 'Authorized isolated check queued for the recorded snapshot; no source files changed.')
        coding.save(conn, row, data, 'checking')
    if pipeline_state is not None:
        await run(pid, tid, check_id, principal, intent, files, stored, pipeline_state, keep_active=True)
        with get_db() as conn:
            _, current = coding.task(conn, pid, tid)
        return current['validation'], 200
    state = {'workspace_id': row['workspace_id'], 'stop': threading.Event()}
    coding.ACTIVE[tid] = state
    state['task'] = asyncio.create_task(run(pid, tid, check_id, principal, intent, files, stored, state))
    return receipt, 202


async def run(pid, tid, check_id, principal, intent, files, stored, state, *, keep_active=False):
    from backend import coding
    receipt = stored['receipt']
    try:
        with get_db() as conn:
            conn.execute("UPDATE coding_check_runs SET status='running',updated_at=? WHERE id=?", (now(), check_id))
        def progress(event):
            # Persist the server-generated container identity before any source
            # bytes are handed over, so a crash cannot orphan unknown work.
            if event.get('stage') == 'container_created':
                stored['runtime_container'] = {k: event[k] for k in ('container_name', 'container_id')}
                with get_db() as conn:
                    changed = conn.execute("UPDATE coding_check_runs SET data=?,updated_at=? WHERE id=? AND status='running'", (canonical(stored), now(), check_id)).rowcount
                    if changed != 1:
                        raise RuntimeError('Check authority was revoked before dispatch')
        result = await asyncio.to_thread(sandbox.run_snapshot, files, intent,
                         stop=state['stop'], authorize=lambda: authorized(pid, tid, principal), progress=progress)
        receipt = {**receipt, **clean_receipt(result)}
        if not authorized(pid, tid, principal):
            receipt = {k: v for k, v in receipt.items() if k not in ('stdout', 'stderr', 'snapshot_paths')}
            receipt.update(status='revoked', reason_code='authorization_revoked', tests_passed=None)
        with get_db() as conn:
            # Do not republish into a revoked workspace or stale task.
            try:
                row, data = coding.task(conn, pid, tid)
                _, grant = coding.binding(conn, pid, row['workspace_id'])
            except HTTPException:
                row = None
            if row is not None and receipt['status'] != 'revoked':
                try:
                    coding.check_preimages(grant, stored['bindings'])
                except HTTPException:
                    receipt.update(status='stale', reason_code='source_changed_during_check')
                data['validation'] = receipt
                coding.log(data, 'checks_finished', 'Container supervisor result recorded. Exit zero alone does not establish coverage or correctness.')
                coding.save(conn, row, data, stored['before_status'])
            elif row is not None:
                data['validation'] = {'id': check_id, 'status': 'revoked', 'executed': bool(receipt.get('executed')), 'tests_passed': None, 'reason_code': 'authorization_revoked'}
                coding.save(conn, row, data, stored['before_status'])
            else:
                # The session or grant is gone, so the guarded loader refuses. Do not
                # leave the task stuck in 'checking': restore its earlier state with a
                # bare revoked marker and no diagnostics, for the next valid session.
                raw = conn.execute("SELECT * FROM coding_tasks WHERE id=? AND project_id=? AND status='checking'", (tid, pid)).fetchone()
                if raw is not None:
                    data = json.loads(raw['data'])
                    data['validation'] = {'id': check_id, 'status': 'revoked', 'executed': bool(receipt.get('executed')), 'tests_passed': None, 'reason_code': 'authorization_revoked'}
                    coding.save(conn, dict(raw), data, stored['before_status'])
                    receipt = {k: v for k, v in receipt.items() if k not in ('stdout', 'stderr', 'snapshot_paths')}
                    receipt.update(status='revoked', reason_code='authorization_revoked', tests_passed=None)
            if receipt['status'] == 'revoked':
                stored = {'receipt': {'id': check_id, 'status': 'revoked', 'executed': bool(receipt.get('executed')), 'tests_passed': None}}
            else:
                stored['receipt'] = receipt
            conn.execute("UPDATE coding_check_runs SET status=?,updated_at=?,data=? WHERE id=? AND status<>'revoked'",
                         (receipt['status'], now(), canonical(stored), check_id))
    except Exception:
        # Never persist process exception messages that may contain source data.
        unknown = bool(stored.get('runtime_container'))
        receipt.update(status='interrupted' if unknown else 'failed',
                       executed=None if unknown else False,
                       cleanup_verified=None if unknown else True,
                       reason_code='check_supervision_failed', tests_passed=None)
        with get_db() as conn:
            stored['receipt'] = receipt
            conn.execute("UPDATE coding_check_runs SET status=?,updated_at=?,data=? WHERE id=? AND status<>'revoked'", (receipt['status'], now(), canonical(stored), check_id))
            try:
                row, data = coding.task(conn, pid, tid)
                data['validation'] = receipt
                coding.save(conn, row, data, stored['before_status'])
            except HTTPException:
                pass
    finally:
        files.clear()
        if not keep_active:
            coding.ACTIVE.pop(tid, None)
