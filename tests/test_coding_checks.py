"""Simulated adapter tests: no fixture source executes on the host."""
import json
import threading
import time
import pytest

from backend import coding, coding_checks, coding_sandbox, local_auth
from backend.db import get_db
from test_coding_api import api, create, wait, propose, PREFIX, SOURCE


@pytest.fixture
def checks(api, monkeypatch):
    state = {'calls': [], 'started': threading.Event(), 'release': threading.Event(), 'valid': True, 'stall': False}
    monkeypatch.setattr(coding_sandbox, 'status', lambda: {'execution': {'available': False, 'can_request': True}})
    # The paired owner comes from the api fixture (fixture_owner); a made-up
    # owner id is rejected by project ownership checks since local pairing.
    monkeypatch.setattr(local_auth, 'session_active', lambda *a: state['valid'])
    def execute(files, intent, *, stop, authorize, progress):
        assert authorize()
        state['calls'].append(dict(files)); state['started'].set()
        progress({'stage': 'container_created', 'container_name': 'riwaq-isolated-'+'a'*32, 'container_id': 'b'*64})
        while state['stall'] and not state['release'].is_set():
            if stop.is_set() or not authorize():
                return {'status': 'cancelled' if stop.is_set() else 'revoked', 'executed': True, 'cleanup_verified': True, 'tests_passed': None}
            time.sleep(.01)
        return {'status': 'completed', 'executed': True, 'runner_exit_zero': True, 'exit_code': 0,
                'stdout': 'simulated diagnostic', 'stderr': '', 'cleanup_verified': True, 'policy_inspected': True, 'tests_passed': True}
    monkeypatch.setattr(coding_sandbox, 'run_snapshot', execute)
    (api.root/'test_example.py').write_text('raise RuntimeError("fixture must stay inert on host")\n')
    api.check_state = state
    yield api
    state['release'].set()


def check(api, tid, key='check-request-1', **changes):
    return api.client.post(PREFIX+'/tasks/'+tid+'/checks', json={'runner': 'python_unittest', 'targets': ['test_example.py'], 'request_key': key, **changes})


def finished(api, tid):
    deadline = time.monotonic()+5
    while time.monotonic()<deadline:
        v=api.client.get(PREFIX+'/tasks/'+tid).json()
        if tid not in coding.ACTIVE and v['status'] not in ('checking','cancelling'): return v
        time.sleep(.01)
    raise AssertionError('Check did not finish')


def test_explicit_copy_idempotency_and_honest_exit_receipt(checks):
    item=create(checks); wait(checks,item['id'],'completed')
    checks.check_state['stall']=True
    response=check(checks,item['id']); assert response.status_code==202,response.text
    assert checks.check_state['started'].wait(2)
    duplicate=check(checks,item['id']); assert duplicate.status_code==202
    assert duplicate.json()['id']==response.json()['id']
    assert len(checks.check_state['calls'])==1
    assert set(checks.check_state['calls'][0])=={'main.ts','test_example.py'}
    assert check(checks,item['id'],memory_mb=256).status_code==409
    checks.check_state['release'].set(); done=finished(checks,item['id'])
    assert done['validation']['runner_exit_zero'] is True
    assert done['validation']['tests_passed'] is None and done['validation']['test_count'] is None
    assert check(checks,item['id']).status_code==200
    assert len(checks.check_state['calls'])==1
    assert (checks.root/'main.ts').read_bytes()==SOURCE


def test_proposed_bytes_are_checked_without_changing_original(checks):
    item=propose(checks)
    assert check(checks,item['id']).status_code==202
    done=finished(checks,item['id'])
    assert done['validation']['snapshot_kind']=='proposed_copy'
    assert b'a + b' in checks.check_state['calls'][0]['main.ts']
    assert (checks.root/'main.ts').read_bytes()==SOURCE


def test_source_change_after_proposal_refuses_dispatch(checks):
    item=propose(checks)
    (checks.root/'main.ts').write_text('new user edit\n')
    assert check(checks,item['id']).status_code==409
    assert not checks.check_state['calls']


def test_source_change_while_running_marks_result_stale(checks):
    item=create(checks);wait(checks,item['id'],'completed')
    checks.check_state['stall']=True; assert check(checks,item['id']).status_code==202
    assert checks.check_state['started'].wait(2)
    (checks.root/'main.ts').write_text('new user edit\n')
    checks.check_state['release'].set()
    assert finished(checks,item['id'])['validation']['status']=='stale'


def test_revocation_clears_result_and_late_worker_cannot_restore_it(checks):
    item=create(checks);wait(checks,item['id'],'completed')
    checks.check_state['stall']=True; assert check(checks,item['id']).status_code==202
    assert checks.check_state['started'].wait(2)
    assert checks.client.delete(PREFIX+'/workspaces/'+checks.workspace['id']).status_code==200
    checks.check_state['release'].set()
    deadline=time.monotonic()+3
    while item['id'] in coding.ACTIVE and time.monotonic()<deadline: time.sleep(.01)
    with get_db() as c:
        row=c.execute('SELECT * FROM coding_check_runs WHERE task_id=?',(item['id'],)).fetchone()
        assert row['status']=='revoked'
        assert 'main.ts' not in row['data'] and 'simulated diagnostic' not in row['data']
    assert checks.client.get(PREFIX+'/tasks/'+item['id']).status_code==404


def test_cancel_and_session_revocation_do_not_publish_late_diagnostics(checks):
    item=create(checks);wait(checks,item['id'],'completed')
    checks.check_state['stall']=True; assert check(checks,item['id']).status_code==202
    assert checks.check_state['started'].wait(2)
    assert checks.client.post(PREFIX+'/tasks/'+item['id']+'/stop').status_code==200
    done=finished(checks,item['id'])
    assert done['validation']['status']=='cancelled'
    checks.check_state['started'].clear(); checks.check_state['stall']=True
    assert check(checks,item['id'],key='check-request-2').status_code==202
    assert checks.check_state['started'].wait(2)
    checks.check_state['valid']=False
    deadline=time.monotonic()+3
    while item['id'] in coding.ACTIVE and time.monotonic()<deadline: time.sleep(.01)
    with get_db() as c:
        row=c.execute("SELECT data FROM coding_check_runs WHERE task_id=? AND request_key='check-request-2'",(item['id'],)).fetchone()
        assert json.loads(row[0])['receipt']['status']=='revoked'
        assert 'simulated diagnostic' not in row[0]
    # A revoked session reads nothing; once a valid session returns, the task
    # is back at its pre-check state with no late diagnostics.
    assert checks.client.get(PREFIX+'/tasks/'+item['id']).status_code in (401,404)
    checks.check_state['valid']=True
    assert checks.client.get(PREFIX+'/tasks/'+item['id']).json()['status']=='completed'


def test_project_isolation_and_secret_exclusion(checks):
    item=create(checks);wait(checks,item['id'],'completed')
    other='/api/projects/p2/coding/tasks/'+item['id']+'/checks'
    assert checks.client.post(other,json={'runner':'python_unittest','targets':['test_example.py']}).status_code==404
    (checks.root/'test_example.py').write_text('API_KEY="synthetic-do-not-send-value"\n')
    assert check(checks,item['id']).status_code==422
    assert not checks.check_state['calls']


def test_restarted_checks_are_not_replayed(checks):
    item=create(checks);wait(checks,item['id'],'completed')
    assert check(checks,item['id']).status_code==202
    finished(checks,item['id'])
    with get_db() as c:
        c.execute("UPDATE coding_check_runs SET status='running' WHERE task_id=?",(item['id'],))
        coding_checks.init(c)
    assert check(checks,item['id']).json()['status']=='interrupted'
    assert check(checks,item['id'],key='new-after-restart').status_code==503
    assert len(checks.check_state['calls'])==1


@pytest.mark.parametrize('authority,expected', [('propose','proposed'),('apply_local','applied')])
def test_pipeline_checks_proposed_copy_before_authorized_apply(checks, authority, expected):
    checks.control['response']['edits']=[{'path':'main.ts','before':'a - b','after':'a + b'}]
    item=create(checks, mode='edit', authority=authority,
                test_request={'runner':'python_unittest','targets':['test_example.py']})
    done=wait(checks,item['id'],expected)
    assert len(checks.check_state['calls'])==1
    assert b'a + b' in checks.check_state['calls'][0]['main.ts']
    assert done['validation']['snapshot_kind']=='proposed_copy'
    assert done['validation']['tests_passed'] is None
    assert (checks.root/'main.ts').read_bytes()==(SOURCE.replace(b'a - b',b'a + b') if authority=='apply_local' else SOURCE)


def test_pipeline_nonzero_does_not_apply(checks, monkeypatch):
    def fail(*args, **kwargs):
        return {'status':'failed','executed':True,'exit_code':1,'policy_inspected':True,'cleanup_verified':True}
    monkeypatch.setattr(coding_sandbox,'run_snapshot',fail)
    item=propose(checks,authority='apply_local',test_request={'runner':'python_unittest','targets':['test_example.py']})
    assert item['validation']['exit_code']==1
    assert 'withheld' in item['error']
    assert (checks.root/'main.ts').read_bytes()==SOURCE


def test_durable_admission_blocks_another_process_record(checks):
    item=create(checks);wait(checks,item['id'],'completed')
    assert check(checks,item['id']).status_code==202
    finished(checks,item['id'])
    with get_db() as c:c.execute("UPDATE coding_check_runs SET status='running' WHERE task_id=?",(item['id'],))
    assert not coding.ACTIVE
    assert check(checks,item['id'],key='different-request').status_code==409
    assert len(checks.check_state['calls'])==1


@pytest.mark.parametrize('cleanup_ok', [True,False])
def test_restart_cleanup_requires_owned_container_and_never_reexecutes(checks, monkeypatch, cleanup_ok):
    from types import SimpleNamespace
    from backend import isolated_worker
    item=create(checks);wait(checks,item['id'],'completed')
    assert check(checks,item['id']).status_code==202
    finished(checks,item['id'])
    with get_db() as c:
        c.execute("UPDATE coding_check_runs SET status='running' WHERE task_id=?",(item['id'],))
        c.execute("UPDATE coding_tasks SET status='checking' WHERE id=?",(item['id'],))
        coding.init_coding(c)
    called=[]
    monkeypatch.setattr(isolated_worker,'load_config',lambda _:SimpleNamespace(images={'python_unittest':'sha256:'+'c'*64}))
    def cleanup(config,name,**kwargs):
        called.append((name,kwargs));return {'cleanup_verified':cleanup_ok}
    monkeypatch.setattr(isolated_worker,'cleanup_owned',cleanup)
    result=coding_checks.recover_interrupted()
    assert result['execution_replayed'] is False
    assert len(called)==1 and called[0][1]['expected_id']=='b'*64
    assert result['cleaned']==int(cleanup_ok)
    assert bool(result['blocked']) is not cleanup_ok
    assert len(checks.check_state['calls'])==1
    if cleanup_ok:
        task=checks.client.get(PREFIX+'/tasks/'+item['id']).json()
        assert task['status']=='interrupted' and task['validation']['cleanup_verified'] is True
        assert coding_checks.recover_interrupted()['checked']==0
