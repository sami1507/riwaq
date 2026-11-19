"""Coding API contract/security tests. Provider replies are SIMULATED, not model-quality evidence.
Only isolated tmp_path source fixtures are read/edited; project code is never run.
"""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace
from authority_fixture import fixture_owner
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import coding, workspace
from backend.db import get_db, now

SOURCE = b'export function add(a, b) { return a - b; }\n'
PREFIX = '/api/projects/p1/coding'


@pytest.fixture
def api(tmp_path_factory, monkeypatch):
    # Keep fixture ancestors independent of test names containing 'secret'.
    tmp_path=tmp_path_factory.mktemp('coding-api')
    data=tmp_path/'data';data.mkdir()
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(data))
    with get_db() as conn:
        conn.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,instructions TEXT,created_at TEXT)')
        for pid in ('p1','p2'):conn.execute('INSERT INTO projects VALUES(?,?,?,?)',(pid,pid,'',now()))
        coding.init_coding(conn)
    root=tmp_path/'user-project';root.mkdir();(root/'main.ts').write_bytes(SOURCE)
    cfg={'provider':'ollama','mode':'local','model':'simulated-local-model','max_tokens':1024,'context_size':8192}
    control={'calls':[],'validations':[],'stall':False,'started':threading.Event(),'release':threading.Event(),'closed':threading.Event(),
             'response':{'answer':'Simulated static review; no tests executed.','refs':[{'path':'main.ts','line_start':1,'line_end':1}],'edits':[]}}
    async def validate(provider,model,mode):
        assert provider=='ollama' and mode=='local';control['validations'].append((provider,model,mode))
    async def stream(provider,model,messages,config):
        assert provider=='ollama' and config['temperature']==0
        control['calls'].append({'messages':messages,'config':config});control['started'].set()
        try:
            while control['stall'] and not control['release'].is_set():await asyncio.sleep(.005)
            value=control['response']
            if callable(value):value=value()
            yield {'content':json.dumps(value,ensure_ascii=False)}
            yield {'finish_reason':'stop'}
        finally:control['closed'].set()
    monkeypatch.setattr(coding,'settings',lambda:dict(cfg))
    monkeypatch.setattr(coding,'busy',lambda:bool(coding.ACTIVE))
    monkeypatch.setattr(coding.providers,'validate_provider',validate)
    monkeypatch.setattr(coding.providers,'stream',stream)
    assert not coding.ACTIVE
    @asynccontextmanager
    async def lifespan(app):
        yield
        control['release'].set()
        await coding.shutdown()
    application=FastAPI(lifespan=lifespan);application.include_router(coding.router);application.include_router(workspace.router)
    with fixture_owner(), TestClient(application) as client:
        r=client.post(PREFIX+'/workspaces',json={'path':str(root),'label':'Fixture'})
        assert r.status_code==201,r.text
        yield SimpleNamespace(client=client,root=root,workspace=r.json(),control=control,cfg=cfg,tmp=tmp_path)
    assert not coding.ACTIVE


def request(**changes):
    return {'request_key':'request-0001','mode':'explain','authority':'read_only','language':'en',
            'prompt':'Explain the selected source.','acceptance':'Use actual source references.','selected_paths':['main.ts'],**changes}


def create(api,**changes):
    r=api.client.post(PREFIX+'/workspaces/'+api.workspace['id']+'/tasks',json=request(**changes))
    assert r.status_code==202,r.text
    return r.json()


def wait(api,tid,status=None):
    deadline=time.monotonic()+6
    while time.monotonic()<deadline:
        r=api.client.get(PREFIX+'/tasks/'+tid);assert r.status_code==200,r.text
        value=r.json()
        if tid not in coding.ACTIVE and value['status'] not in ('queued','inspecting','generating','cancelling','applying','checking'):
            if status is not None:assert value['status']==status,value
            return value
        time.sleep(.01)
    pytest.fail('Simulated API task did not settle within six seconds')


def propose(api,authority='propose',**changes):
    api.control['response']['edits']=[{'path':'main.ts','before':'a - b','after':'a + b'}]
    item=create(api,mode='edit',authority=authority,prompt='Change subtraction to addition.',**changes)
    return wait(api,item['id'],'proposed' if authority=='propose' or changes.get('test_request') else 'applied')


def confirm(api,item,verb,**changes):
    return api.client.post(PREFIX+'/tasks/'+item['id']+'/'+verb,json={'confirm':True,'expected_revision':item['revision'],**changes})


def test_owner_isolation_and_legacy_grant_bypass_denied(api):
    wid=api.workspace['id'];gid=api.workspace['grant_id'];client=api.client
    own=PREFIX+'/workspaces/'+wid
    assert client.get(own+'/read',params={'path':'main.ts'}).status_code==200
    other='/api/projects/p2/coding/workspaces/'+wid
    for suffix in ('/files','/read?path=main.ts','/tasks'):
        assert client.get(other+suffix).status_code==404
    assert client.post(other+'/tasks',json=request()).status_code==404
    assert client.get('/api/workspace/'+gid+'/read',params={'path':'main.ts'}).status_code==404
    assert client.get('/api/workspace/'+gid+'/events').status_code==404
    assert client.delete('/api/workspace/grants/'+gid).status_code==404
    assert all(x['id']!=gid for x in client.get('/api/workspace/grants').json())
    assert client.post('/api/workspace/grant',json={'path':str(api.root)}).status_code in (403,409)
    assert client.post('/api/projects/p2/coding/workspaces',json={'path':str(api.root)}).status_code in (403,409)


def test_protected_service_root_and_ancestors_refused(api):
    for path in (workspace.ROOT,workspace.ROOT.parent,Path(workspace.data_dir())):
        r=api.client.post(PREFIX+'/workspaces',json={'path':str(path)})
        assert r.status_code==403,r.text


def test_secret_path_content_and_request_never_reach_provider(api):
    root=api.root;client=api.client;base=PREFIX+'/workspaces/'+api.workspace['id']
    (root/'.env').write_text('SECRET=not-for-display')
    (root/'app.py').write_text('API_KEY="sensitive-example-001"\n')
    for path in ('.env','../outside','main.ts:alternate'):
        assert client.get(base+'/read',params={'path':path}).status_code in (400,403)
    r=client.get(base+'/read',params={'path':'app.py'})
    assert r.status_code==200 and r.json()['content'] is None and 'sensitive-example-001' not in r.text
    inspected=client.post(base+'/inspect',json={'selected_paths':['app.py'],'query':''})
    assert inspected.status_code==200 and inspected.json()['files']==[]
    assert 'sensitive-example-001' not in inspected.text
    r=client.post(base+'/tasks',json=request(prompt='API_KEY="sensitive-example-001"'))
    assert r.status_code==422 and not api.control['calls']
    item=create(api,selected_paths=['app.py']);wait(api,item['id'],'failed')
    assert not api.control['calls']


def test_legacy_unowned_read_search_and_proposal_content_guard(api):
    root=api.tmp/'legacy-project';root.mkdir();(root/'config.txt').write_text('PASSWORD="private-legacy-value"')
    r=api.client.post('/api/workspace/grant',json={'path':str(root)});assert r.status_code==201,r.text
    gid=r.json()['id']
    assert api.client.get('/api/workspace/'+gid+'/read',params={'path':'config.txt'}).status_code==422
    r=api.client.get('/api/workspace/'+gid+'/search',params={'q':'private-legacy-value'})
    assert r.status_code==200 and 'private-legacy-value' not in r.text
    r=api.client.post('/api/workspace/'+gid+'/proposals',json={'path':'config.txt','content':'safe replacement'})
    assert r.status_code==422
    assert (root/'config.txt').read_text()=='PASSWORD="private-legacy-value"'


def test_simulated_readonly_analysis_idempotency_and_current_refs(api):
    api.control['stall']=True
    item=create(api);assert api.control['started'].wait(2)
    duplicate=create(api);assert duplicate['id']==item['id'] and len(api.control['calls'])==1
    r=api.client.post(PREFIX+'/workspaces/'+api.workspace['id']+'/tasks',json=request(prompt='Different request'))
    assert r.status_code==409
    api.control['release'].set();done=wait(api,item['id'],'completed')
    assert done['refs'][0]['sha256']==workspace.digest(SOURCE)
    assert done['validation']['executed'] is False and done['validation']['tests_passed'] is None
    assert done['metrics']['model_calls']==1 and done['can_apply'] is False and done['can_resume'] is False
    assert (api.root/'main.ts').read_bytes()==SOURCE
    assert api.client.get('/api/projects/p2/coding/tasks/'+item['id']).status_code==404
    assert '_baseline' not in done and '_changes' not in done and '_input' not in done


def test_readonly_model_edits_rejected_without_file_changes(api):
    api.control['response']['edits']=[{'path':'main.ts','before':'a - b','after':'a + b'}]
    item=create(api);done=wait(api,item['id'],'failed')
    assert not done['can_apply'] and (api.root/'main.ts').read_bytes()==SOURCE


@pytest.mark.parametrize('reference',[{'path':'outside.ts','line_start':1,'line_end':1},{'path':'main.ts','line_start':50,'line_end':51},{'path':'main.ts','line_start':2,'line_end':1}])
def test_fabricated_or_outside_excerpt_reference_rejected(api,reference):
    api.control['response']['refs']=[reference]
    item=create(api);done=wait(api,item['id'],'failed')
    assert done['refs']==[] and (api.root/'main.ts').read_bytes()==SOURCE


def test_secret_model_output_never_persisted_as_answer(api):
    api.control['response']['answer']='API_KEY="provider-secret-value"'
    item=create(api);done=wait(api,item['id'],'failed')
    assert 'provider-secret-value' not in json.dumps(done)
    with get_db() as conn:assert 'provider-secret-value' not in conn.execute('SELECT data FROM coding_tasks WHERE id=?',(item['id'],)).fetchone()[0]


def test_cancellation_closes_stalled_provider_and_resume_is_idempotent(api):
    api.control['stall']=True
    item=create(api);assert api.control['started'].wait(2)
    stopped=api.client.post(PREFIX+'/tasks/'+item['id']+'/stop');assert stopped.status_code==200
    done=wait(api,item['id'],'cancelled');assert api.control['closed'].is_set() and done['can_resume']
    assert (api.root/'main.ts').read_bytes()==SOURCE
    api.control['stall']=False
    body={'request_key':'resume-0001'}
    r=api.client.post(PREFIX+'/tasks/'+item['id']+'/resume',json=body);assert r.status_code==202,r.text
    same=api.client.post(PREFIX+'/tasks/'+item['id']+'/resume',json=body);assert same.status_code==202
    wait(api,item['id'],'completed');assert len(api.control['calls'])==2


def test_revocation_clears_snapshots_and_blocks_late_model_result(api):
    api.control['stall']=True;item=create(api);assert api.control['started'].wait(2)
    r=api.client.delete(PREFIX+'/workspaces/'+api.workspace['id']);assert r.status_code==200
    assert r.json()['snapshots_cleared'] and not r.json()['files_changed']
    deadline=time.monotonic()+3
    while item['id'] in coding.ACTIVE and time.monotonic()<deadline:time.sleep(.01)
    assert item['id'] not in coding.ACTIVE
    assert api.client.get(PREFIX+'/tasks/'+item['id']).status_code==404
    with get_db() as conn:
        row=conn.execute('SELECT status,data FROM coding_tasks WHERE id=?',(item['id'],)).fetchone()
    saved=json.loads(row['data']);assert row['status']=='revoked'
    assert saved['context']=={} and saved['_baseline']=={} and saved['answer']=='' and saved['prompt']==''
    assert (api.root/'main.ts').read_bytes()==SOURCE


def test_stale_revision_rejected_before_journal_or_filesystem_write(api):
    item=propose(api);assert item['can_apply']
    with get_db() as conn:before=tuple(conn.execute('SELECT revision,data FROM coding_tasks WHERE id=?',(item['id'],)).fetchone())
    r=confirm(api,item,'apply',expected_revision=item['revision']-1)
    assert r.status_code==409 and (api.root/'main.ts').read_bytes()==SOURCE
    with get_db() as conn:after=tuple(conn.execute('SELECT revision,data FROM coding_tasks WHERE id=?',(item['id'],)).fetchone())
    assert before==after
    assert api.client.post(PREFIX+'/tasks/'+item['id']+'/apply',json={'confirm':True}).status_code==422
    assert confirm(api,item,'apply',confirm=False).status_code==422


def test_reviewed_apply_and_guarded_undo_persist(api):
    item=propose(api);r=confirm(api,item,'apply');assert r.status_code==200,r.text
    applied=r.json();assert applied['status']=='applied' and applied['can_undo'] and not applied['can_apply']
    assert (api.root/'main.ts').read_bytes()==SOURCE.replace(b'a - b',b'a + b')
    r=confirm(api,applied,'undo');assert r.status_code==200,r.text
    assert r.json()['status']=='undone' and (api.root/'main.ts').read_bytes()==SOURCE
    assert api.client.get(PREFIX+'/tasks/'+item['id']).json()['status']=='undone'


def test_apply_conflict_preserves_later_user_edit(api):
    item=propose(api);later=b'export const userChange = 42;\n';(api.root/'main.ts').write_bytes(later)
    r=confirm(api,item,'apply');assert r.status_code==200,r.text
    assert r.json()['status']=='conflict' and r.json()['proposal']['conflicts']
    assert (api.root/'main.ts').read_bytes()==later


def test_undo_conflict_preserves_post_apply_edit_and_never_resumes(api):
    applied=confirm(api,propose(api),'apply').json();later=b'export const userChange = 42;\n'
    (api.root/'main.ts').write_bytes(later)
    r=confirm(api,applied,'undo');assert r.status_code==200
    result=r.json();assert result['status']=='conflict' and result['can_undo'] and not result['can_resume']
    assert (api.root/'main.ts').read_bytes()==later
    assert api.client.post(PREFIX+'/tasks/'+applied['id']+'/resume',json={'request_key':'resume-0001'}).status_code==409


def test_declared_apply_authority_applies_only_reviewable_changes(api):
    result=propose(api,authority='apply_local')
    assert result['can_undo'] and result['apply_receipt']['written']
    assert (api.root/'main.ts').read_bytes()==SOURCE.replace(b'a - b',b'a + b')


def test_checks_blocked_and_requested_checks_prevent_automatic_apply(api,monkeypatch):
    marker=api.root/'must-not-execute';(api.root/'test_project.py').write_text('raise RuntimeError("must not execute")')
    def forbidden(*args,**kwargs):pytest.fail('Project execution attempted on host')
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    intent={'runner':'python_unittest','targets':['test_project.py']}
    item=propose(api,authority='apply_local',test_request=intent)
    assert item['validation']['status']=='blocked' and item['validation']['executed'] is False
    assert item['validation']['tests_passed'] is None and not marker.exists()
    assert (api.root/'main.ts').read_bytes()==SOURCE
    r=api.client.post(PREFIX+'/tasks/'+item['id']+'/checks',json=intent)
    assert r.status_code==503 and r.json()['executed'] is False and r.json()['tests_passed'] is None
    assert not marker.exists()


def test_source_changes_while_model_streams_cannot_publish_stale_proposal(api):
    api.control['stall']=True;api.control['response']['edits']=[{'path':'main.ts','before':'a - b','after':'a + b'}]
    item=create(api,mode='edit',authority='propose');assert api.control['started'].wait(2)
    later=b'export const changed = true;\n';(api.root/'main.ts').write_bytes(later)
    api.control['release'].set();done=wait(api,item['id'],'conflict')
    assert (api.root/'main.ts').read_bytes()==later
    assert done.get('apply_receipt') is None


def test_cloud_selection_fails_without_provider_call(api):
    api.cfg['provider']='openai'
    item=create(api);done=wait(api,item['id'],'failed')
    assert not api.control['calls'] and not api.control['validations']
    assert done['validation']['executed'] is False


def test_model_token_budget_never_exceeds_selected_setting(api):
    api.cfg['max_tokens']=96
    done=wait(api,create(api)['id'],'completed')
    assert api.control['calls'][0]['config']['max_tokens']==96
    assert done['metrics']['max_tokens']==96


def test_storage_reserves_utf8_bytes_before_model_request(api,monkeypatch):
    item=wait(api,create(api)['id'],'completed')
    with get_db() as conn:
        data=json.loads(conn.execute('SELECT data FROM coding_tasks WHERE id=?',(item['id'],)).fetchone()[0])
        data['answer']='ع'*1000
        encoded=json.dumps(data,ensure_ascii=False)
        conn.execute('UPDATE coding_tasks SET data=? WHERE id=?',(encoded,item['id']))
    monkeypatch.setattr(coding,'STORAGE',coding.MAX_TASK_BYTES+len(encoded)+20)
    assert len(encoded.encode())>len(encoded)+20
    prior=len(api.control['calls'])
    r=api.client.post(PREFIX+'/workspaces/'+api.workspace['id']+'/tasks',json=request(request_key='storage-next-001'))
    assert r.status_code==413 and len(api.control['calls'])==prior


def test_revoked_workspace_does_not_consume_active_workspace_capacity(api):
    wid=api.workspace['id']
    for _ in range(11):
        assert api.client.delete(PREFIX+'/workspaces/'+wid).status_code==200
        result=api.client.post(PREFIX+'/workspaces',json={'path':str(api.root),'label':'Fixture'})
        assert result.status_code==201,result.text
        wid=result.json()['id']
    assert len(api.client.get(PREFIX+'/workspaces').json())==1


@pytest.mark.parametrize('previous,expected',[('generating','interrupted'),('applying','recovery_required')])
def test_startup_journal_marks_interrupted_without_replay(api,previous,expected):
    item=propose(api)
    count=len(api.control['calls'])
    with get_db() as conn:
        conn.execute('UPDATE coding_tasks SET status=? WHERE id=?',(previous,item['id']))
    with get_db() as conn:
        coding.init_coding(conn)
    current=api.client.get(PREFIX+'/tasks/'+item['id']).json()
    assert current['status']==expected and not coding.ACTIVE
    assert len(api.control['calls'])==count and (api.root/'main.ts').read_bytes()==SOURCE
    if previous=='applying':
        assert current['can_undo'] and not current['can_resume'] and not current['can_apply']
        restored=confirm(api,current,'undo')
        assert restored.status_code==200 and restored.json()['status']=='undone'
        assert (api.root/'main.ts').read_bytes()==SOURCE
    else:
        assert current['can_resume']
