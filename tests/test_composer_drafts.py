"""Authenticated HTTP/SQLite composer recovery; no model or network fixtures."""
import pytest
from fastapi.testclient import TestClient
from backend import app as service, db, local_auth

EMPTY={'assistant':'','calculator':'','problem':''}

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as value:
        assert value.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
        yield value

def setup(client):
    pid=client.get('/api/projects').json()[0]['id']
    return pid,f'/api/projects/{pid}/composer-draft'

def save(client,path,content,version=0,conversation_id=None):
    return client.patch(path,json={'conversation_id':conversation_id,'expected_version':version,'contents':{**EMPTY,**content}})

def test_draft_persists_real_database_and_restart(client):
    pid,path=setup(client)
    response=save(client,path,{'assistant':'مسودة 001 لا ترسل','calculator':'9007199254740993 + 1'})
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='no-store'
    assert response.json()['version']==1
    db.init_db()
    read=client.get(path).json()
    assert read['contents']['assistant']=='مسودة 001 لا ترسل'
    assert read['contents']['calculator']=='9007199254740993 + 1'
    with db.get_db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==0

def test_context_isolation_and_conversation_delete(client):
    pid,path=setup(client);other=client.post('/api/projects',json={'name':'Other'}).json()['id']
    cid=client.post('/api/conversations',json={'project_id':pid}).json()['id']
    save(client,path,{'assistant':'NEW'})
    save(client,path,{'assistant':'CHAT'},conversation_id=cid)
    assert client.get(path,params={'conversation_id':cid}).json()['contents']['assistant']=='CHAT'
    assert client.get(f'/api/projects/{other}/composer-draft').json()['contents']==EMPTY
    assert client.get(f'/api/projects/{other}/composer-draft',params={'conversation_id':cid}).status_code==404
    assert client.delete('/api/conversations/'+cid).status_code==200
    assert client.get(path,params={'conversation_id':cid}).status_code==404
    with db.get_db() as conn:assert not conn.execute('SELECT 1 FROM composer_drafts WHERE conversation_key=?',(cid,)).fetchone()
    assert client.get(path).json()['contents']['assistant']=='NEW'

def test_compare_and_swap_and_clear_tombstone(client):
    _,path=setup(client)
    assert save(client,path,{'assistant':'First'}).status_code==200
    assert save(client,path,{'assistant':'Stale'}).status_code==409
    assert save(client,path,{},1).status_code==200
    assert save(client,path,{'assistant':'Late worker'},1).status_code==409
    assert client.get(path).json()=={'version':2,'contents':EMPTY,'scope':'local_composer_only'}

def test_concurrent_browser_writes_keep_one_version(client):
    from concurrent.futures import ThreadPoolExecutor
    _,path=setup(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda value:save(client,path,{'assistant':value}),['WINDOW A','WINDOW B']))
    assert sorted(r.status_code for r in responses)==[200,409]
    winner=next(r.json()['contents']['assistant'] for r in responses if r.status_code==200)
    assert client.get(path).json()['contents']['assistant']==winner
    assert client.get(path).json()['version']==1

def test_atomic_transfer_preserves_all_modes_and_leaves_tombstone(client):
    pid,path=setup(client);cid=client.post('/api/conversations',json={'project_id':pid}).json()['id']
    save(client,path,{'assistant':'A','problem':'x=2'})
    response=client.post(path+'/transfer',json={'expected_version':1,'target_conversation_id':cid})
    assert response.status_code==200,response.text
    assert response.json()['contents']['problem']=='x=2'
    assert client.get(path).json()['contents']==EMPTY
    assert client.get(path,params={'conversation_id':cid}).json()['contents']['assistant']=='A'
    assert save(client,path,{'assistant':'Stale'},1).status_code==409

def test_transfer_never_overwrites_target_or_other_project(client):
    pid,path=setup(client);other=client.post('/api/projects',json={'name':'Other'}).json()['id']
    cid=client.post('/api/conversations',json={'project_id':other}).json()['id']
    save(client,path,{'assistant':'Source'})
    assert client.post(path+'/transfer',json={'expected_version':1,'target_conversation_id':cid}).status_code==404
    cid=client.post('/api/conversations',json={'project_id':pid}).json()['id']
    save(client,path,{'assistant':'Target'},conversation_id=cid)
    assert client.post(path+'/transfer',json={'expected_version':1,'target_conversation_id':cid}).status_code==409
    assert client.get(path).json()['contents']['assistant']=='Source'

def test_authority_revoked_and_signed_out(client):
    pid,path=setup(client);save(client,path,{'assistant':'PRIVATE'})
    with db.get_db() as conn:conn.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?',('other-owner',pid))
    for response in [client.get(path),save(client,path,{'assistant':'write'},1)]:
        assert response.status_code==404 and 'PRIVATE' not in response.text
    assert client.post('/api/auth/logout').status_code==200
    assert client.get(path).status_code==401

@pytest.mark.parametrize('extra',[{'actor':'admin'},{'tenant':'other'},{'contents':{**EMPTY,'unknown':'x'}},{'expected_version':-1},{'contents':{**EMPTY,'assistant':'x'*20001}}])
def test_untrusted_schema_is_rejected(client,extra):
    _,path=setup(client);payload={'expected_version':0,'contents':EMPTY,**extra}
    assert client.patch(path,json=payload).status_code==422
