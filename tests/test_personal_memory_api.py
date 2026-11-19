"""Actual authenticated routes and storage; only admission/provider are simulated.

Real admission still returns 423. Success/race cases intentionally assume a
future independent admission and use a controlled provider, not a live model.
"""
import json
import pytest
from fastapi.testclient import TestClient
from backend import app as service, db, local_auth, personal_memory, providers
from test_project_memory_versions import client, project


@pytest.fixture
def admitted(monkeypatch):
    monkeypatch.setattr(personal_memory,'require_admission',lambda c,a:personal_memory._actor(c,a))


def save(client):
    policy=client.get('/api/personal-memory/settings').json()
    enabled=client.patch('/api/personal-memory/settings',json={'enabled':True,'expected_version':policy['version']})
    assert enabled.status_code==200,enabled.text
    created=client.post('/api/personal-memory',json={'rule':{'kind':'language','value':'en'},'idempotency_key':'api-personal-key','consent_version':enabled.json()['version']})
    assert created.status_code==200,created.text
    return created.json(),enabled.json()


def test_production_routes_enforce_stage8_and_allow_optout(client):
    settings=client.get('/api/personal-memory/settings').json()
    assert not settings['gate']['available'] and not settings['enabled']
    assert client.patch('/api/personal-memory/settings',json={'enabled':True,'expected_version':1}).status_code==423
    assert client.post('/api/personal-memory',json={'rule':{'kind':'language','value':'en'},'idempotency_key':'api-create-key','consent_version':1}).status_code==423
    assert client.patch('/api/personal-memory/settings',json={'enabled':False,'expected_version':1}).status_code==200
    assert client.post('/api/personal-memory',json={'rule':{'kind':'language','value':'en'},'idempotency_key':'api-create-key','consent_version':1,'verified':True}).status_code==422
    assert client.get('/api/personal-memory').json()==[]
    client.cookies.clear()
    assert client.get('/api/personal-memory').status_code==401


def test_legacy_read_delete_routes_remain_available_under_real_gate(client):
    with db.get_db() as c:c.execute('INSERT INTO memories VALUES(?,?,?,?,?)',('legacy-test','preference',None,'old preference',db.now()))
    rows=client.get('/api/personal-memory').json()
    assert rows[0]['status']=='legacy_read_only'
    assert client.get('/api/memories').json()[0]['id']=='legacy-test'
    assert client.patch('/api/memories/legacy-test',json={'content':'new'}).status_code==423
    assert client.delete('/api/memories/legacy-test').status_code==200
    assert client.get('/api/personal-memory').json()==[]
    with db.get_db() as c:assert c.execute('SELECT 1 FROM personal_memory_tombstones WHERE memory_id=?',('legacy-test',)).fetchone()


def test_route_edit_cas_and_delete_use_current_versions(client,admitted):
    row,policy=save(client)
    patch={'rule':{'kind':'language','value':'he'},'expected_version':1,'consent_version':policy['version']}
    changed=client.patch('/api/personal-memory/'+row['id'],json=patch)
    assert changed.status_code==200 and changed.json()['version']==2
    assert client.patch('/api/personal-memory/'+row['id'],json=patch).status_code==409
    assert client.delete('/api/personal-memory/'+row['id']+'?expected_version=1').status_code==409
    assert client.delete('/api/personal-memory/'+row['id']+'?expected_version=2').status_code==200
    assert client.get('/api/personal-memory/'+row['id']).status_code==404


def test_optout_during_generation_prevents_late_publication(client,admitted,monkeypatch):
    _,policy=save(client);pid=project(client)
    cid=client.post('/api/conversations',json={'project_id':pid}).json()['id']
    seen=[]
    async def validate(*args):pass
    async def stream(provider,model,messages,cfg):
        seen.extend(messages)
        yield {'content':'OLD_PERSONAL_DERIVED_ANSWER'}
        yield {'finish_reason':'stop'}
        with db.get_db() as c:
            principal=local_auth.current_principal()
            from backend.guard import Actor
            personal_memory.set_enabled(c,Actor(principal['owner_id'],principal['session_id']),False,policy['version'])
    monkeypatch.setattr(providers,'validate_provider',validate);monkeypatch.setattr(providers,'stream',stream)
    response=client.post('/api/conversations/'+cid+'/generate',json={'content':'Summarize a simple idea'})
    assert response.status_code==200
    assert 'Reply in English' in json.dumps(seen)
    latest=client.get('/api/conversations/'+cid).json()['messages'][-1]
    assert latest['status']=='error' and latest['content']==''


def test_deleted_preference_stops_historical_answer_from_reentering_context(client,admitted,monkeypatch):
    entry,_=save(client);cid=client.post('/api/conversations',json={'project_id':project(client)}).json()['id']
    captured=[]
    async def validate(*args):pass
    async def stream(provider,model,messages,cfg):
        captured.append(messages)
        yield {'content':'OLD_PERSONAL_DERIVED_ANSWER' if len(captured)==1 else 'new answer'}
        yield {'finish_reason':'stop'}
    monkeypatch.setattr(providers,'validate_provider',validate);monkeypatch.setattr(providers,'stream',stream)
    assert client.post('/api/conversations/'+cid+'/generate',json={'content':'First request'}).status_code==200
    assert client.delete('/api/personal-memory/'+entry['id']+'?expected_version=1').status_code==200
    assert client.post('/api/conversations/'+cid+'/generate',json={'content':'Continue'}).status_code==200
    assert 'OLD_PERSONAL_DERIVED_ANSWER' not in json.dumps(captured[-1])
    rows=client.get('/api/conversations/'+cid).json()['messages']
    assert not any(m['content']=='OLD_PERSONAL_DERIVED_ANSWER' for m in rows)
    assert any(m.get('content_redacted') and m['content']=='' for m in rows)


def test_application_restart_reads_saved_preference_but_real_gate_stays_closed(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY',str(tmp_path/'absent.json'))
    original=personal_memory.require_admission
    monkeypatch.setattr(personal_memory,'require_admission',lambda c,a:personal_memory._actor(c,a))
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as one:
        assert one.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
        item,_=save(one)
    monkeypatch.setattr(personal_memory,'require_admission',original)
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as two:
        assert two.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
        stored=two.get('/api/personal-memory/'+item['id'])
        assert stored.status_code==200 and stored.json()['rule']['value']=='en'
        assert two.patch('/api/personal-memory/'+item['id'],json={'rule':{'kind':'language','value':'ar'},'expected_version':1,'consent_version':2}).status_code==423
        assert two.patch('/api/personal-memory/settings',json={'enabled':False,'expected_version':2}).status_code==200


def test_optout_remains_available_after_gate_closes(client,admitted,monkeypatch):
    _,policy=save(client)
    def closed(c,actor):raise personal_memory.PersonalMemoryError(423,'stage8_security_acceptance_required')
    monkeypatch.setattr(personal_memory,'require_admission',closed)
    assert client.patch('/api/personal-memory/settings',json={'enabled':False,'expected_version':policy['version']}).status_code==200


def test_personal_context_is_never_sent_to_external_provider(client,admitted,monkeypatch):
    save(client)
    cid=client.post('/api/conversations',json={'project_id':project(client)}).json()['id']
    called=[]
    async def validate(*args):pass
    async def stream(*args):
        called.append(True)
        yield {'content':'must not be invoked'}
    monkeypatch.setattr(providers,'validate_provider',validate)
    monkeypatch.setattr(providers,'stream',stream)
    response=client.post('/api/conversations/'+cid+'/generate',json={'content':'Explain a simple idea','provider':'openai','model':'test'})
    assert response.status_code==400,response.text
    assert 'Personal preference context' in response.text
    assert called==[]
