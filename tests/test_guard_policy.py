"""Synthetic data only. Model fakes below test orchestration, not model quality.
The separate guard-live report covers an actual Ollama/Chrome path.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import sqlite3
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from backend import app as service, guard, guard_api, local_auth, providers
from backend.db import get_db


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY',str(tmp_path/'absent.json'))
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as c:
        login=c.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()})
        assert login.status_code==200
        yield c


def setup(client):
    pid=client.get('/api/projects').json()[0]['id']
    row=client.post(f'/api/projects/{pid}/files',files={'file':('fixture.txt','حجم العينة 42 وحدة.\nSYNTHETIC_PRIVATE_CANARY_42','text/plain')})
    assert row.status_code==201
    with get_db() as c:
        session=c.execute('SELECT id,owner_id FROM local_auth_sessions ORDER BY created_at DESC LIMIT 1').fetchone()
        who=guard.Actor(session['owner_id'],session['id'])
        snapshot,label,chunks=guard.resource(c,who,pid,row.json()['id'])
    return pid,row.json()['id'],who,snapshot,label


def permit(client,**overrides):
    pid,fid,who,snapshot,label=setup(client)
    args=dict(actor=who,project=pid,task='task-fixture',operation='summarize_file',destination=guard.LOCAL_DESTINATION,
              parameters={'model':'fixture-local','file_id':fid},resources=[snapshot],budget=100)
    args.update(overrides)
    token,decision=guard.issue(**args)
    args.pop('budget');args['byte_count']=50
    return token,args,decision


def wait_result(client,pid,rid):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        response=client.get(f'/api/projects/{pid}/guard/results/{rid}')
        if response.status_code!=200 or response.json()['status'] not in {'pending','running'}:return response
        time.sleep(.02)
    raise AssertionError('Guard task did not finish')


def fake_model(monkeypatch,delay=0):
    calls=[]
    async def validate(*args):pass
    async def stream(*args):
        calls.append(args)
        if delay:await asyncio.sleep(delay)
        yield {'content':'حجم العينة 42 وحدة.'}
        yield {'finish_reason':'stop'}
    monkeypatch.setattr(providers,'validate_provider',validate)
    monkeypatch.setattr(providers,'stream',stream)
    return calls


def test_bound_permit_once_and_safe_receipts(client):
    token,args,decision=permit(client)
    execution=guard.consume(token,**args)
    with pytest.raises(guard.Denied,match='already_used'):guard.consume(token,**args)
    with get_db() as c:
        records=[dict(x) for x in c.execute('SELECT * FROM guard_audit')]
        persisted=c.execute('SELECT token_hash,state FROM guard_permits').fetchone()
    assert persisted['state']=='claimed' and persisted['token_hash']!=token
    assert token not in json.dumps(records) and 'SYNTHETIC_PRIVATE_CANARY' not in json.dumps(records)
    proof=client.get('/api/guard/decisions/'+execution)
    assert proof.status_code==200 and proof.json()['decision']=='execute'


@pytest.mark.parametrize('field,value',[
    ('destination','https://example.invalid/exfil'),('operation','send_email'),('task','another-task'),
    ('parameters',{'model':'changed'}),('byte_count',101),('actor',guard.Actor('other-owner','other-session'))])
def test_execution_substitution_denied(client,field,value):
    token,args,_=permit(client);args[field]=value
    with pytest.raises(guard.Denied):guard.consume(token,**args)


@pytest.mark.parametrize('mutation,reason',[
    ("UPDATE guard_permits SET expires=0",'expired'),
    ("UPDATE guard_permits SET policy_version='old'",'policy_changed'),
    ("UPDATE guard_permits SET state='revoked'",'already_used'),
    ("UPDATE local_auth_sessions SET revoked_at=1",'session_inactive'),
    ("UPDATE guard_file_policies SET revision=revision+1",'changed'),
    ("UPDATE knowledge_chunks SET text='new source'",'changed'),
    ("UPDATE guard_file_policies SET revoked=1",'not_authorized'),
    ("UPDATE guard_control SET value='disabled' WHERE key='local_summary'",'tool_disabled')])
def test_revocation_expiry_versions_and_quarantine(client,mutation,reason):
    token,args,_=permit(client)
    with get_db() as c:c.execute(mutation)
    with pytest.raises(guard.Denied,match=reason):guard.consume(token,**args)


def test_policy_store_failure_is_not_allow(client,monkeypatch):
    token,args,_=permit(client)
    @contextmanager
    def unavailable():raise sqlite3.OperationalError('synthetic unavailable');yield
    monkeypatch.setattr(guard,'get_db',unavailable)
    with pytest.raises(sqlite3.OperationalError):guard.consume(token,**args)


def test_two_real_processes_cannot_claim_same_permit(client):
    token,args,_=permit(client)
    args['actor']={'owner_id':args['actor'].owner_id,'session_id':args['actor'].session_id}
    payload=json.dumps({'token':token,'arguments':args})
    code="""import json,sys
from backend import guard
x=json.load(sys.stdin);a=x['arguments'];a['actor']=guard.Actor(**a['actor'])
try:guard.consume(x['token'],**a);print('claimed')
except guard.Denied:print('denied')
"""
    children=[subprocess.Popen([sys.executable,'-B','-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8') for _ in range(2)]
    try:
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda p:p.communicate(payload,timeout=12),children))
        assert sorted(x[0].strip() for x in results)==['claimed','denied']
        assert all(p.returncode==0 for p in children)
    finally:
        for p in children:
            if p.poll() is None:p.kill();p.wait()


def test_data_policy_intersection_never_broadens_scope():
    common={'owner_id':'a','project_id':'p','permission_revision':1}
    result=guard.combine_policy([common|{'allowed_destinations':['local:ollama','cloud:a']},common|{'allowed_destinations':['local:ollama']}])
    assert result['allowed_destinations']==['local:ollama'] and result['trust_origin']=='derived_from_untrusted_sources'
    with pytest.raises(guard.Denied):guard.combine_policy([common|{'allowed_destinations':[]},common|{'owner_id':'b','allowed_destinations':[]}])


def test_real_api_orchestration_and_denied_model_proposal(client,monkeypatch):
    calls=fake_model(monkeypatch);pid,fid,_,_,_=setup(client)
    body={'file_id':fid,'idempotency_key':'safe-request-001'}
    first=client.post(f'/api/projects/{pid}/guard/summaries',json=body)
    assert first.status_code==202
    rid=first.json()['id'];response=wait_result(client,pid,rid)
    assert response.status_code==200 and response.json()['status']=='completed'
    assert response.json()['policy']['allowed_destinations']==['local:ollama']
    repeat=client.post(f'/api/projects/{pid}/guard/summaries',json=body)
    assert repeat.json()['id']==rid and len(calls)==1
    # A model-supplied URL is still just data at the execution gate.
    rejection=client.post(f'/api/projects/{pid}/guard/results/{rid}/egress-check',json={'destination':'https://example.invalid/SYNTHETIC_PRIVATE_CANARY_42'})
    assert rejection.status_code==403
    proof=client.get('/api/guard/decisions/'+rejection.json()['detail']['decision_id'])
    assert proof.status_code==200 and proof.json()['decision']=='deny'
    assert 'CANARY' not in proof.text and 'example.invalid' not in proof.text
    assert client.post(f'/api/projects/{pid}/guard/summaries',json=body|{'owner_id':'other'}).status_code==422


def test_project_scope_cannot_be_substituted(client,monkeypatch):
    fake_model(monkeypatch);pid,fid,who,snapshot,_=setup(client)
    other=client.post('/api/projects',json={'name':'other'}).json()['id']
    response=client.post(f'/api/projects/{other}/guard/summaries',json={'file_id':fid,'idempotency_key':'another-001'})
    assert response.status_code==403
    with get_db() as c:
        with pytest.raises(guard.Denied):guard.resource(c,guard.Actor('other-owner',who.session_id),pid,fid)


@pytest.mark.parametrize('action',['delete','cancel','logout','disable'])
def test_late_generation_cannot_publish_after_revocation(client,monkeypatch,action):
    fake_model(monkeypatch,1);pid,fid,_,_,_=setup(client)
    rid=client.post(f'/api/projects/{pid}/guard/summaries',json={'file_id':fid,'idempotency_key':'late-fixture-001'}).json()['id']
    if action=='delete':assert client.delete(f'/api/projects/{pid}/files/{fid}').status_code==200
    elif action=='cancel':assert client.post(f'/api/projects/{pid}/guard/results/{rid}/cancel').status_code==200
    elif action=='logout':assert client.post('/api/auth/logout').status_code==200
    else:
        with get_db() as c:c.execute("UPDATE guard_control SET value='disabled' WHERE key='local_summary'")
    time.sleep(.5)
    with get_db() as c:
        row=c.execute('SELECT status,content FROM guard_results WHERE id=?',(rid,)).fetchone()
    assert row['status'] in {'cancelled','revoked','failed'} and row['content']==''
    if action=='delete':assert client.get(f'/api/projects/{pid}/guard/results/{rid}').status_code==403


def test_cloud_final_dispatch_is_denied_before_secrets_or_http(monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('Must not construct HTTP or read a key')
    monkeypatch.setattr(providers.httpx,'AsyncClient',forbidden);monkeypatch.setattr(providers,'secret',forbidden)
    async def check():
        for provider in ('openai','anthropic','unknown'):
            with pytest.raises(ValueError,match='Guard'):await anext(providers.stream(provider,'model',[],{'mode':'hybrid'}))
    asyncio.run(check())


def test_personal_memory_gate_and_project_memory_preserved(client):
    pid=client.get('/api/projects').json()[0]['id']
    assert client.post('/api/memories',json={'content':'private long term preference'}).status_code==423
    response=client.post('/api/memories',json={'content':'project decision','scope':'project','project_id':pid})
    assert response.status_code==200
    assert client.delete('/api/memories/'+response.json()['id']+'?expected_version='+str(response.json()['version'])).status_code==200


def test_session_and_payload_not_in_safe_decision(client):
    token,args,decision=permit(client)
    response=client.get('/api/guard/decisions/'+decision)
    assert response.headers['cache-control']=='no-store'
    assert not any(k in response.json() for k in ('token','session_id','parameters','resources','content','destination'))


def test_optional_telemetry_is_explicitly_off_not_inherited_from_environment():
    from sync_server.app import app as sync_app
    for application in (service.app,sync_app):
        for flag in ('auto_configure','tracing','metrics','logs','operation_spans'):
            assert application._telemetry[flag] is False


def test_truncated_summary_is_not_a_completed_result(client,monkeypatch):
    fake_model(monkeypatch)
    async def truncated(*args):
        yield {'content':'partial synthetic answer'}
        yield {'finish_reason':'length'}
    monkeypatch.setattr(providers,'stream',truncated)
    pid,fid,_,_,_=setup(client)
    rid=client.post(f'/api/projects/{pid}/guard/summaries',json={'file_id':fid,'idempotency_key':'truncated-001'}).json()['id']
    response=wait_result(client,pid,rid)
    assert response.json()['status']=='failed' and response.json()['content']==''
    proof=client.get('/api/guard/decisions/'+response.json()['decision_id']).json()
    assert proof['reason']=='incomplete_model_response'


def test_lease_recovery_does_not_replay_or_steal_live_claims(client):
    token,args,_=permit(client)
    guard.consume(token,**args)
    with get_db() as c:
        guard.reap_expired(c)
        assert c.execute('SELECT state FROM guard_permits').fetchone()[0]=='claimed'
        c.execute('UPDATE guard_permits SET expires=0')
        guard.reap_expired(c)
        assert c.execute('SELECT state FROM guard_permits').fetchone()[0]=='unknown'
    with pytest.raises(guard.Denied):guard.consume(token,**args)


def test_operator_quarantine_and_session_revoke_are_durable(client):
    from backend.guard_admin import operate
    token,args,_=permit(client)
    assert operate('quarantine-summary')['summary_tool']=='disabled'
    with pytest.raises(guard.Denied):guard.consume(token,**args)
    assert operate('resume-summary')['summary_tool']=='enabled'
    assert operate('revoke-sessions')['active_sessions']==0
    assert client.get('/api/projects').status_code==401


def test_personal_memory_not_injected_into_new_chat(client,monkeypatch):
    captured=[]
    async def validate(*args):pass
    async def stream(provider,model,messages,cfg):
        captured.extend(messages);yield {'content':'مرحبًا'};yield {'finish_reason':'stop'}
    monkeypatch.setattr(providers,'validate_provider',validate);monkeypatch.setattr(providers,'stream',stream)
    pid=client.get('/api/projects').json()[0]['id']
    with get_db() as c:c.execute("INSERT INTO memories VALUES('legacy','preference',NULL,'SYNTHETIC_PERSONAL_MEMORY_MARKER','2026-11-15')")
    cid=client.post('/api/conversations',json={'project_id':pid}).json()['id']
    assert client.post(f'/api/conversations/{cid}/generate',json={'content':'مرحبًا'}).status_code==200
    assert 'SYNTHETIC_PERSONAL_MEMORY_MARKER' not in json.dumps(captured)
    assert client.get('/api/memories').json()[0]['id']=='legacy'
    assert client.delete('/api/memories/legacy').status_code==200


def test_early_denials_and_spa_keep_security_headers(client):
    response=client.get('/api/projects',headers={'X-Riwaq-Client':''})
    assert response.status_code==403 and response.headers['cache-control']=='no-store'
    assert response.headers['x-content-type-options']=='nosniff'
    assert "form-action 'none'" in client.get('/').headers['content-security-policy']
    assert "base-uri 'none'" in client.get('/').headers['content-security-policy']
    public_spec=client.get('/openapi.json')
    assert public_spec.status_code!=200 or public_spec.headers['content-type'].startswith('text/html')


def test_shared_owner_quota_does_not_block_logout(client,monkeypatch):
    from backend import guard_rate
    monkeypatch.setattr(guard_rate,'REQUESTS_PER_MINUTE',3)
    with get_db() as c:c.execute('DELETE FROM guard_rate_state')
    assert [client.get('/api/projects').status_code for _ in range(4)]==[200,200,200,429]
    assert client.post('/api/auth/logout').status_code==200


def test_quota_storage_failure_fails_closed(client,monkeypatch):
    from backend import guard_rate
    @contextmanager
    def unavailable():raise sqlite3.OperationalError('synthetic');yield
    monkeypatch.setattr(guard_rate,'get_db',unavailable)
    assert client.get('/api/projects').status_code==503
