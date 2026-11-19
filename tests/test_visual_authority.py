"""Actual authenticated API permission regression; fixture pixels/jobs are synthetic."""
import asyncio,io,json,time,threading,sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from backend import app,local_auth,visuals as v,visual_jobs as jobs
from backend.db import get_db,now
from backend import guard
from visual_auth_fixture import synthetic_visual_decoder


def pixels():
    out=io.BytesIO();Image.new('RGB',(20,20),'red').save(out,format='PNG');return out.getvalue()


@pytest.fixture
def scoped_client(tmp_path,monkeypatch):
    synthetic_visual_decoder(monkeypatch)
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY',str(tmp_path/'absent'))
    with TestClient(app.app,headers={'X-Riwaq-Client':'local'}) as client:
        assert client.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
        pid=client.post('/api/projects',json={'name':'Synthetic visual permission boundary'}).json()['id'];base=f'/api/projects/{pid}/visual'
        uploaded=client.post(base+'/assets',files={'file':('fixture.png',pixels(),'image/png')});assert uploaded.status_code==201
        aid=uploaded.json()['id'];spec={'width':512,'height':512,'background':'#ffffff','background_asset_id':aid,'layers':[],'shapes':[]}
        made=client.post(base+'/designs',json={'title':'SYNTHETIC_PRIVATE_TITLE','spec':spec});assert made.status_code==201
        did=made.json()['id']
        with get_db() as c:
            c.execute('INSERT INTO visual_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',('scope-job',pid,'render','completed',did,1,json.dumps({'prompt':'SYNTHETIC_PRIVATE_PROMPT'}),'{}',aid,json.dumps({'text':'SYNTHETIC_PRIVATE_REPORT'}),None,now(),now()))
            row=c.execute('SELECT id,owner_id FROM local_auth_sessions WHERE revoked_at IS NULL').fetchone();principal={'owner_id':row['owner_id'],'session_id':row['id']}
        yield client,pid,base,aid,did,spec,principal


def test_revoked_project_blocks_every_visual_read_and_write(scoped_client):
    client,pid,base,aid,did,spec,_=scoped_client
    with get_db() as c:c.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?',('different-test-owner',pid))
    paths=['/assets',f'/assets/{aid}',f'/assets/{aid}/content','/designs',f'/designs/{did}',f'/designs/{did}/versions',f'/designs/{did}/bundle','/jobs','/jobs/scope-job','/status']
    observed={path:client.get(base+path).status_code for path in paths}
    observed['POST designs']=client.post(base+'/designs',json={'title':'no write','spec':spec}).status_code
    observed['PATCH design']=client.patch(base+f'/designs/{did}',json={'title':'no write','spec':spec,'expected_version':1,'confirm_text_changes':True}).status_code
    observed['POST upload']=client.post(base+'/assets',files={'file':('new.png',pixels(),'image/png')}).status_code
    observed['POST job']=client.post(base+'/jobs',json={'kind':'render','design_id':did,'version':1}).status_code
    observed['POST cancel']=client.post(base+'/jobs/scope-job/cancel').status_code
    observed['DELETE design']=client.delete(base+f'/designs/{did}?expected_version=1&confirm=true').status_code
    target=Path('reports/closure-20261116/visual-authority');target.mkdir(exist_ok=True,parents=True)
    (target/('api-before.json' if any(code not in (403,404) for code in observed.values()) else 'api-after.json')).write_text(json.dumps(observed,indent=2))
    assert all(code in (403,404) for code in observed.values()),observed


def test_stale_server_principal_is_rechecked(scoped_client):
    _,pid,_,aid,did,_,principal=scoped_client
    token=local_auth._principal.set(principal)
    try:
        with get_db() as c:c.execute('UPDATE local_auth_sessions SET revoked_at=? WHERE id=?',(time.time(),principal['session_id']))
        for call in (lambda c:v.asset(c,pid,aid),lambda c:v.design(c,pid,did),lambda c:v.version(c,did,1),lambda c:jobs.row(c,pid,'scope-job')):
            with get_db() as c:
                with pytest.raises(v.HTTPException) as error:call(c)
                assert error.value.status_code in (401,403,404)
    finally:local_auth._principal.reset(token)


def test_upload_rechecks_owner_before_save(scoped_client,monkeypatch):
    client,pid,base,_,_,_,_=scoped_client;normalize=v.normalize_image
    def revoked(raw,**kwargs):
        result=normalize(raw,**kwargs)
        with get_db() as c:c.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?',('other',pid))
        return result
    monkeypatch.setattr(v,'normalize_image',revoked)
    result=client.post(base+'/assets',files={'file':('late.png',pixels(),'image/png')})
    assert result.status_code==404
    with get_db() as c:assert c.execute('SELECT COUNT(*) FROM visual_assets WHERE project_id=?',(pid,)).fetchone()[0]==1


@pytest.mark.asyncio
async def test_bundle_rechecks_between_stream_chunks(scoped_client):
    _,pid,_,_,did,_,principal=scoped_client;token=local_auth._principal.set(principal)
    try:
        response=v.design_bundle(pid,did);stream=response.body_iterator
        header=await anext(stream);assert b'SYNTHETIC_PRIVATE_TITLE' in header
        with get_db() as c:c.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?',('other',pid))
        with pytest.raises(v.HTTPException):await anext(stream)
        await stream.aclose()
    finally:local_auth._principal.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize('when',['before','after'])
async def test_worker_bound_actor_prevents_dispatch_or_publication(scoped_client,monkeypatch,when):
    _,pid,_,_,did,_,principal=scoped_client
    config=jobs.JobInput(kind='render',design_id=did,version=1).model_dump()
    with get_db() as c:c.execute("UPDATE visual_jobs SET status='queued',input=? WHERE id='scope-job'",(json.dumps(config),))
    calls=[]
    def execute(*a,**kw):
        calls.append(True);assert kw['authorize']() is True
        with get_db() as c:c.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?',('other',pid))
        return {'abort_reason':None,'log_tail':'UNTRUSTED_PRIVATE_WORKER_ERROR'}
    monkeypatch.setattr('backend.visual_runner.execute',execute)
    if when=='before':
        with get_db() as c:c.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?',('other',pid))
    state={'stop':threading.Event(),'actor':guard.Actor(principal['owner_id'],principal['session_id'])}
    await jobs.run('scope-job',pid,state)
    assert bool(calls)==(when=='after')
    with get_db() as c:
        result=dict(c.execute("SELECT * FROM visual_jobs WHERE id='scope-job'").fetchone())
        assert result['status']=='failed' and result['report']=='{}' and 'UNTRUSTED' not in result['error']
        assert c.execute('SELECT COUNT(*) FROM visual_assets WHERE project_id=?',(pid,)).fetchone()[0]==1


def test_real_worker_revocation_stops_process_tree(tmp_path):
    from backend.visual_runner import execute
    script=tmp_path/'fixed.py';script.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\ntime.sleep(30)\n")
    started=time.monotonic()
    result=execute([sys.executable,str(script)],tmp_path,tmp_path,threading.Event(),lambda _:None,seconds=8,
        authorize=lambda:time.monotonic()-started<.7)
    assert result['abort_reason']=='authorization_revoked' and result['remaining_processes']==0 and result['seconds']<8
