"""Actual task-path regressions; synthetic SQLite and local artifact bytes only."""
import asyncio
from datetime import datetime,timedelta,timezone
import pytest
from fastapi import HTTPException
from backend import skills
from backend.db import get_db
from tests.test_skill_runtime_security import sandbox,request,finish

def expire(rid):
    past=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
    with get_db() as c:
        c.execute('UPDATE skill_runs SET created_at=? WHERE id=?',(past,rid))
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='skill_run_controls'").fetchone():
            c.execute('UPDATE skill_run_controls SET deadline_epoch=0 WHERE run_id=?',(rid,))

@pytest.mark.asyncio
async def test_queue_time_counts_toward_deadline_and_no_tool_is_dispatched(sandbox,monkeypatch):
    calls=[]
    from backend import skill_artifacts
    original=skill_artifacts.build_artifact
    def spy(*a,**kw):calls.append(1);return original(*a,**kw)
    monkeypatch.setattr(skill_artifacts,'build_artifact',spy)
    created=await skills.create_run('a',request('queue-expired-001'))
    expire(created['id'])
    await skills.ACTIVE[created['id']]
    with get_db() as c:
        assert c.execute('SELECT state FROM skill_runs WHERE id=?',(created['id'],)).fetchone()[0]=='failed'
        assert c.execute('SELECT count(*) FROM skill_artifacts').fetchone()[0]==0
    assert not calls

@pytest.mark.asyncio
async def test_result_after_deadline_is_not_published_as_success(sandbox,monkeypatch):
    from backend import skill_artifacts
    original=skill_artifacts.build_artifact
    def finish_after_deadline(*a,**kw):
        result=original(*a,**kw)
        with get_db() as c:rid=c.execute('SELECT id FROM skill_runs').fetchone()[0]
        expire(rid)
        return result
    monkeypatch.setattr(skill_artifacts,'build_artifact',finish_after_deadline)
    rid=await finish(request('late-artifact-001'))
    with get_db() as c:
        assert c.execute('SELECT state FROM skill_runs WHERE id=?',(rid,)).fetchone()[0]=='failed'
        assert c.execute('SELECT count(*) FROM skill_artifacts').fetchone()[0]==0

@pytest.mark.asyncio
async def test_same_operation_key_coalesces_concurrent_requests_but_new_key_is_new_intent(sandbox):
    a,b=await asyncio.gather(skills.create_run('a',request('parallel-same-001')),skills.create_run('a',request('parallel-same-001')))
    assert a['id']==b['id']
    task=skills.ACTIVE.get(a['id'])
    if task:await task
    with pytest.raises(HTTPException) as failure:await skills.create_run('a',request('parallel-same-001',request='Different intent'))
    assert failure.value.status_code==409
    second=await finish(request('parallel-new-002'))
    assert second!=a['id']
    with get_db() as c:assert c.execute('SELECT count(*) FROM skill_artifacts').fetchone()[0]==2

@pytest.mark.asyncio
async def test_terminal_task_cannot_be_reopened_by_late_event(sandbox):
    from backend import skill_execution,guard
    import json
    rid=await finish(request('terminal-event-001'))
    with get_db() as c:
        row=c.execute('SELECT * FROM skill_runs WHERE id=?',(rid,)).fetchone()
        with pytest.raises(ValueError,match='transition'):
            skill_execution.transition(c,guard.Actor('owner-a','session-a'),row,'running',json.loads(row['document']))
        assert c.execute('SELECT state FROM skill_runs WHERE id=?',(rid,)).fetchone()[0]=='completed'

@pytest.mark.asyncio
async def test_cancel_requested_keeps_capacity_until_worker_acknowledges(sandbox,monkeypatch):
    started=asyncio.Event();release=asyncio.Event()
    async def pending(rid,lease=None):
        started.set()
        try:await release.wait()
        except asyncio.CancelledError:await release.wait()
        skills.fail(rid,'cancelled','Synthetic worker acknowledged cancellation')
    monkeypatch.setattr(skills,'execute',pending)
    first=await skills.create_run('a',request('capacity-first-001'))
    await started.wait()
    second=await skills.create_run('a',request('capacity-second-002'))
    await skills.cancel('a',first['id'])
    try:
        with pytest.raises(HTTPException) as error:await skills.create_run('a',request('capacity-third-003'))
        assert error.value.status_code==429
    finally:
        tasks=list(skills.ACTIVE.values());release.set();await asyncio.gather(*tasks)

@pytest.mark.asyncio
async def test_independent_builds_overlap_without_global_serialization(sandbox,monkeypatch):
    from backend import skill_artifacts
    import threading,time
    original=skill_artifacts.build_artifact;barrier=threading.Barrier(2);intervals=[]
    def synchronized(*a,**kw):
        start=time.monotonic();barrier.wait(timeout=5)
        output=original(*a,**kw);intervals.append((start,time.monotonic()));return output
    monkeypatch.setattr(skill_artifacts,'build_artifact',synchronized)
    one=await skills.create_run('a',request('concurrent-build-001'))
    two=await skills.create_run('a',request('concurrent-build-002'))
    await asyncio.gather(*list(skills.ACTIVE.values()))
    assert len(intervals)==2 and max(x[0] for x in intervals)<min(x[1] for x in intervals)
    assert skills.get_run('a',one['id'])['status']==skills.get_run('a',two['id'])['status']=='completed'

@pytest.mark.asyncio
async def test_resume_cannot_bypass_busy_worker_capacity(sandbox,monkeypatch):
    from backend import skill_lifecycle,guard
    async def idle(*args):pass
    monkeypatch.setattr(skills,'execute',idle)
    original=await skills.create_run('a',request('resume-capacity-old'))
    await skills.ACTIVE[original['id']]
    with get_db() as c:
        checkpoint=skill_lifecycle.save_checkpoint(c,guard.Actor('owner-a','session-a'),original['id'])
        c.execute("UPDATE skill_runs SET state='unknown' WHERE id=?",(original['id'],))
    await skills.create_run('a',request('resume-capacity-new1'))
    await skills.create_run('a',request('resume-capacity-new2'))
    try:
        with pytest.raises(HTTPException) as error:
            await skills.resume_run('a',original['id'],skills.ResumeRequest(expected_revision=checkpoint['revision']))
        assert error.value.status_code==429
        with get_db() as c:assert c.execute('SELECT state FROM skill_runs WHERE id=?',(original['id'],)).fetchone()[0]=='unknown'
    finally:await asyncio.gather(*list(skills.ACTIVE.values()))

@pytest.mark.asyncio
async def test_cancelled_builder_holds_capacity_until_thread_exits(sandbox,monkeypatch):
    from backend import skill_artifacts
    import threading
    original=skill_artifacts.build_artifact;entered=threading.Event();release=threading.Event()
    def blocked(*args,**kwargs):
        entered.set()
        if not release.wait(5):raise TimeoutError('Synthetic builder release missing')
        return original(*args,**kwargs)
    monkeypatch.setattr(skill_artifacts,'build_artifact',blocked)
    created=await skills.create_run('a',request('cancel-thread-001'));task=skills.ACTIVE[created['id']]
    try:
        for _ in range(200):
            if entered.is_set():break
            await asyncio.sleep(.005)
        assert entered.is_set()
        await skills.cancel('a',created['id'])
        # Allow the cancellation to propagate; completion is controlled by the
        # thread event, not a timing benchmark or a provider sleep.
        for _ in range(5):await asyncio.sleep(0)
        assert not task.done()
        with get_db() as c:
            assert c.execute('SELECT state FROM skill_runs WHERE id=?',(created['id'],)).fetchone()[0]=='cancelling'
            assert c.execute('SELECT count(*) FROM skill_artifacts').fetchone()[0]==0
    finally:release.set();await task
    assert skills.get_run('a',created['id'])['status']=='cancelled'

@pytest.mark.asyncio
async def test_expired_lease_can_only_clean_up_its_own_worker(sandbox,monkeypatch):
    import hashlib
    from backend import skill_lifecycle,guard
    async def idle(*args):pass
    monkeypatch.setattr(skills,'execute',idle)
    created=await skills.create_run('a',request('expired-cleanup-001'));await skills.ACTIVE[created['id']]
    with get_db() as c:
        skill_lifecycle.save_checkpoint(c,guard.Actor('owner-a','session-a'),created['id'])
        c.execute("UPDATE skill_runs SET state='running' WHERE id=?",(created['id'],))
        c.execute('UPDATE skill_checkpoints SET lease_hash=?,lease_expires=0 WHERE run_id=?',(hashlib.sha256(b'current-lease').hexdigest(),created['id']))
    skills.fail(created['id'],'cancelled','Synthetic obsolete worker','old-lease')
    assert skills.get_run('a',created['id'])['status']=='running'
    skills.fail(created['id'],'cancelled','Synthetic expired worker stopped','current-lease')
    assert skills.get_run('a',created['id'])['status']=='cancelled'
