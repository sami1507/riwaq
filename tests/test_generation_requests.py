"""Durable attempt admission through authenticated HTTP; no paid provider."""
import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from backend import app as service,local_auth,context_lineage,facts
from backend.db import get_db,init_db,uid,now

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));service.ACTIVE.clear()
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as c:
        assert c.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
        yield c

def setup(client):
    project=client.get('/api/projects').json()[0]['id']
    return project,client.post('/api/conversations',json={'project_id':project}).json()['id']

def body(key='attempt-key-001',content='0.1+0.2'):
    return {'request_id':key,'content':content,'answer_mode':'calculator'}

def completed(response):
    assert response.status_code==200,response.text
    events=[json.loads(line) for line in response.text.splitlines()]
    assert events[-1]['type']=='done'
    return events[-1]['message']

def test_duplicate_completed_attempt_reuses_actual_calculator_and_messages(client,monkeypatch):
    _,chat=setup(client);calls=[];original=service.reasoning.checker
    async def checked(*args,**kwargs):calls.append(args);return await original(*args,**kwargs)
    monkeypatch.setattr(service.reasoning,'checker',checked)
    route=f'/api/conversations/{chat}/generate'
    first=completed(client.post(route,json=body()));second=completed(client.post(route,json=body()))
    assert first==second and len(calls)==1 and first['status']=='complete'
    assert len(client.get('/api/conversations/'+chat).json()['messages'])==2
    result=client.get(f'/api/conversations/{chat}/requests/attempt-key-001')
    assert result.status_code==200 and result.json()['message']==first
    assert result.headers['cache-control']=='no-store'
    with get_db() as c:
        row=dict(c.execute('SELECT * FROM generation_requests').fetchone())
        assert 'content' not in row and len(row['payload_sha256'])==64

def test_key_payload_conflict_and_cross_conversation_never_create_work(client):
    project,chat=setup(client);route=f'/api/conversations/{chat}/generate'
    completed(client.post(route,json=body()))
    conflict=client.post(route,json=body(content='2+2'))
    assert conflict.status_code==409 and conflict.json()['detail']['code']=='generation_key_conflict'
    other=client.post('/api/conversations',json={'project_id':project}).json()['id']
    assert client.post(f'/api/conversations/{other}/generate',json=body()).status_code==409
    assert client.get('/api/conversations/'+other).json()['messages']==[]

def test_same_key_during_execution_reports_pending_without_second_provider(client,monkeypatch):
    _,chat=setup(client);entered=threading.Event();release=threading.Event();calls=[]
    async def preflight(*a,**k):pass
    async def stream(*a,**k):
        calls.append(1);entered.set();await asyncio.to_thread(release.wait,5)
        yield {'content':'synthetic controlled stream'};yield {'finish_reason':'stop'}
    monkeypatch.setattr(service.providers,'preflight',preflight);monkeypatch.setattr(service.providers,'stream',stream)
    payload={'request_id':'concurrent-key-001','content':'Fixture message'};route=f'/api/conversations/{chat}/generate'
    with ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(client.post,route,json=payload)
        try:
            assert entered.wait(8)
            second=client.post(route,json=payload)
            assert second.status_code==409 and second.json()['detail']['code']=='generation_in_progress_or_interrupted'
        finally:release.set()
        result=completed(first.result(timeout=15))
    assert len(calls)==1 and result['status']=='complete'
    assert completed(client.post(route,json=payload))==result and len(calls)==1

def test_explicit_new_attempt_and_retry_branch_use_distinct_keys(client):
    _,chat=setup(client);route=f'/api/conversations/{chat}/generate'
    first=completed(client.post(route,json=body()))
    payload={'request_id':'branch-key-001','retry_message_id':first['id']}
    branch=completed(client.post(route,json=payload))
    assert branch['conversation_id']!=chat
    assert completed(client.post(route,json=payload))==branch
    assert len(client.get('/api/conversations/'+chat).json()['messages'])==2
    assert len(client.get('/api/conversations/'+branch['conversation_id']).json()['messages'])==2

def test_failed_preparation_is_not_silently_retried(client):
    _,chat=setup(client);route=f'/api/conversations/{chat}/generate'
    assert client.post(route,json=body(content='1/0')).status_code==422
    again=client.post(route,json=body(content='1/0'))
    assert again.status_code==409 and again.json()['detail']['code']=='generation_not_started'
    assert client.get('/api/conversations/'+chat).json()['messages']==[]
    assert completed(client.post(route,json=body('corrected-key-001','2+2')))['status']=='complete'

def test_restart_and_new_session_keep_same_attempt(client):
    _,chat=setup(client);route=f'/api/conversations/{chat}/generate'
    first=completed(client.post(route,json=body()));init_db()
    client.post('/api/auth/logout')
    assert client.get(f'/api/conversations/{chat}/requests/attempt-key-001').status_code==401
    assert client.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
    assert completed(client.post(route,json=body()))==first

def test_revoked_source_cannot_be_disclosed_by_replay(client):
    project,chat=setup(client);route=f'/api/conversations/{chat}/generate'
    first=completed(client.post(route,json=body()));memory=uid()
    with get_db() as c:
        c.execute('INSERT INTO memories VALUES(?,?,?,?,?)',(memory,'project',project,'protected synthetic memory',now()))
        context_lineage.save(c,first['id'],[{'kind':'project_memory','id':memory,'version':1,'content_sha256':facts.sha('protected synthetic memory')}])
        c.execute('DELETE FROM memories WHERE id=?',(memory,))
    assert client.post(route,json=body()).status_code==404
    assert client.get(f'/api/conversations/{chat}/requests/attempt-key-001').status_code==404
    with get_db() as c:assert c.execute('SELECT count(*) FROM messages WHERE conversation_id=?',(chat,)).fetchone()[0]==2

def test_deleted_result_and_foreign_project_do_not_replay_or_restart(client):
    project,chat=setup(client);route=f'/api/conversations/{chat}/generate';first=completed(client.post(route,json=body()))
    with get_db() as c:c.execute('DELETE FROM messages WHERE id=?',(first['id'],))
    assert client.post(route,json=body()).status_code==409
    with get_db() as c:c.execute("UPDATE guard_projects SET owner_id='foreign-owner' WHERE project_id=?",(project,))
    assert client.post(route,json=body()).status_code==404
    assert client.get(f'/api/conversations/{chat}/requests/attempt-key-001').status_code==404
