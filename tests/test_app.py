import json
import pytest
from fastapi.testclient import TestClient
from backend import app as service

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    service.ACTIVE.clear()
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as c:
        from backend.local_auth import issue_pairing_code
        assert c.post('/api/auth/login', json={'pairing_code': issue_pairing_code()}).status_code == 200
        yield c

def new_chat(client):
    p=client.get('/api/projects').json()[0]
    return client.post('/api/conversations',json={'project_id':p['id']}).json()['id']

def test_csrf_and_dns_rebinding(client):
    assert client.get('/api/projects',headers={'X-Riwaq-Client':''}).status_code==403
    assert client.get('/api/projects',headers={'Origin':'https://evil.example'}).status_code==403
    assert client.get('/api/projects',headers={'Host':'evil.example'}).status_code==400
    assert client.get('/index.html').headers['x-frame-options']=='DENY'
    assert "frame-ancestors 'none'" in client.get('/index.html').headers['content-security-policy']

def test_save_restore_export_search(client):
    id=new_chat(client)
    assert client.get('/api/conversations/'+id).json()['messages']==[]
    assert client.get('/api/conversations?q=محادثة').json()[0]['id']==id
    assert client.get(f'/api/conversations/{id}/export?format=json').json()['id']==id

def test_cloud_blocked_before_key_or_network(client,monkeypatch):
    def fail(*args): raise AssertionError('Must not inspect cloud secrets in local mode')
    monkeypatch.setattr(service.providers,'secret',fail)
    r=client.post('/api/conversations/'+new_chat(client)+'/generate',json={'content':'hello','provider':'openai','model':'custom'})
    assert r.status_code==400 and 'blocked' in r.json()['detail']

def test_remote_ollama_model_blocked(client):
    r=client.post('/api/conversations/'+new_chat(client)+'/generate',json={'content':'hello','model':'model:cloud'})
    assert r.status_code==400

def test_memories_scope_and_edit(client):
    p1=client.get('/api/projects').json()[0]['id']
    p2=client.post('/api/projects',json={'name':'other'}).json()['id']
    m=client.post('/api/memories',json={'content':'private','scope':'project','project_id':p1}).json()
    assert client.get('/api/memories?project_id='+p2).json()==[]
    updated=client.patch('/api/memories/'+m['id'],json={'content':'changed','scope':'project','project_id':p1,'expected_version':m['version']})
    assert updated.status_code==200
    assert client.delete('/api/memories/'+m['id']+'?expected_version='+str(updated.json()['version'])).status_code==200
    assert client.get('/api/memories?project_id='+p1).json()==[]

def test_project_state_only_real_events(client):
    p=client.get('/api/projects').json()[0]['id']
    assert client.get(f'/api/projects/{p}/state').json()['goal']==''
    client.post(f'/api/projects/{p}/events',json={'kind':'goal','summary':'Ship v1'})
    assert client.get(f'/api/projects/{p}/state').json()['goal']=='Ship v1'

def test_web_search_fail_closed(client):
    assert client.get('/api/search/web?q=test').status_code==403

def test_auth_validation_does_not_echo_password(client):
    password='x'*129
    r=client.post('/api/sync/login',json={'email':'test@example.test','password':password})
    assert r.status_code==422
    assert password not in r.text

def test_local_logout_forgets_token_without_network(client,monkeypatch):
    from backend import sync_client
    cleared=[]
    monkeypatch.setattr(sync_client,'token',lambda:'private-token')
    monkeypatch.setattr(sync_client.keyring,'delete_password',lambda *args:cleared.append(args))
    async def fail(*args): raise AssertionError('No remote revocation in local mode')
    monkeypatch.setattr(sync_client,'proxy',fail)
    r=client.post('/api/sync/logout')
    assert r.json()=={'signed_out':True,'remote_revoked':False}
    assert cleared

def test_stale_privacy_configuration_rejected(client,monkeypatch):
    client.patch('/api/settings',json={'mode':'hybrid'})
    async def change_during_validation(*args):
        from backend.db import get_db
        with get_db() as c:c.execute("INSERT OR REPLACE INTO settings VALUES ('mode','\"local\"')")
    monkeypatch.setattr(service.providers,'validate_provider',change_during_validation)
    r=client.post('/api/conversations/'+new_chat(client)+'/generate',json={'content':'secret','provider':'openai'})
    assert r.status_code==409

def test_output_failure_saves_partial_and_branch_keeps_original(client,monkeypatch):
    # Deterministic failure test, explicitly not evidence for real model quality.
    async def validate(*args): pass
    async def stream(*args):
        yield {'content':'partial'}
        raise ValueError('test provider interruption')
    monkeypatch.setattr(service.providers,'validate_provider',validate)
    monkeypatch.setattr(service.providers,'stream',stream)
    id=new_chat(client)
    res=client.post(f'/api/conversations/{id}/generate',json={'content':'original'})
    packets=[json.loads(x) for x in res.text.splitlines()]
    assert packets[-1]['message']['status']=='error'
    saved=client.get('/api/conversations/'+id).json()['messages']
    assert saved[-1]['content']=='partial'
    res=client.post(f'/api/conversations/{id}/generate',json={'content':'edited','edit_message_id':saved[0]['id']})
    fork=json.loads(res.text.splitlines()[0])['conversation_id']
    assert fork!=id
    assert client.get('/api/conversations/'+id).json()['messages'][0]['content']=='original'
    assert client.get('/api/conversations/'+fork).json()['messages'][0]['content']=='edited'

def test_body_limit(client):
    assert client.post('/api/projects',content=b'x'*(9*1024*1024+1)).status_code==413

def test_source_absence_does_not_generate_fabrication(client,monkeypatch):
    async def validate(*args): pass
    async def fail(*args):
        raise AssertionError('No model call without relevant source')
        yield
    monkeypatch.setattr(service.providers,'validate_provider',validate)
    monkeypatch.setattr(service.providers,'stream',fail)
    id=new_chat(client)
    res=client.post(f'/api/conversations/{id}/generate',json={'content':'unknown source fact','use_files':True})
    assert 'No relevant source' in res.text

def test_lessons_notebook_round_trip(client):
    made=client.post('/api/lessons',json={'title':'موعد صيانة الضاغط','content':'كل 500 ساعة تشغيل.'})
    assert made.status_code==200,made.text
    identifier=made.json()['id']
    assert [x['id'] for x in client.get('/api/lessons').json()]==[identifier]
    assert client.patch(f'/api/lessons/{identifier}',json={'enabled':False}).json()['enabled'] is False
    assert client.post('/api/lessons',json={'title':'x','content':'y','owner_id':'someone'}).status_code==422
    assert client.delete(f'/api/lessons/{identifier}').json()=={'ok':True}
    assert client.delete(f'/api/lessons/{identifier}').status_code==404

def test_taught_lesson_reaches_the_model_and_marks_the_answer(client,monkeypatch):
    # Deterministic wiring test with a fake model, explicitly not evidence for real model quality.
    seen={}
    async def validate(*args): pass
    async def stream(provider,model,messages,*args):
        seen['prompt']=json.dumps(messages,ensure_ascii=False)
        yield {'content':'كل 500 ساعة تشغيل.'}
    monkeypatch.setattr(service.providers,'validate_provider',validate)
    monkeypatch.setattr(service.providers,'stream',stream)
    client.post('/api/lessons',json={'title':'موعد صيانة الضاغط','content':'كل 500 ساعة تشغيل.'})
    id=new_chat(client)
    client.post(f'/api/conversations/{id}/generate',json={'content':'متى موعد صيانة الضاغط؟'})
    assert 'saved_lessons' in seen['prompt'] and 'كل 500 ساعة تشغيل.' in seen['prompt']
    saved=client.get('/api/conversations/'+id).json()['messages'][-1]['content']
    assert saved.startswith('⟦من دروسك المحفوظة: «موعد صيانة الضاغط»⟧') and saved.endswith('كل 500 ساعة تشغيل.')
    assert client.get('/api/lessons').json()[0]['uses']==1
    other=new_chat(client)
    client.post(f'/api/conversations/{other}/generate',json={'content':'ما عاصمة فرنسا؟'})
    assert 'saved_lessons' not in seen['prompt']
    assert '⟦من دروسك' not in client.get('/api/conversations/'+other).json()['messages'][-1]['content']

def test_wikipedia_sources_need_consent_and_are_listed_by_the_application(client,monkeypatch):
    # Deterministic wiring test with a fake model and a fake fetch, explicitly not evidence for real model quality.
    seen={}
    async def validate(*args): pass
    async def stream(provider,model,messages,*args):
        seen['prompt']=json.dumps(messages,ensure_ascii=False)
        yield {'content':'اكتمل بناؤه عام 1889 [1].'}
    async def fetch(question,preferred=None,client=None):
        seen['asked']=question
        return [{'n':1,'title':'برج إيفل','url':'https://ar.wikipedia.org/wiki/x','lang':'ar','text':'اكتمل بناؤه عام 1889.','retrieved':'2026-11-20'}]
    monkeypatch.setattr(service.providers,'validate_provider',validate)
    monkeypatch.setattr(service.providers,'stream',stream)
    monkeypatch.setattr(service.wiki_sources,'fetch',fetch)
    id=new_chat(client)
    blocked=client.post(f'/api/conversations/{id}/generate',json={'content':'متى اكتمل برج إيفل؟','web_sources':True})
    assert 'asked' not in seen and blocked.status_code==403 and 'محجوبة' in blocked.text
    assert client.patch('/api/settings',json={'mode':'hybrid'}).status_code==200
    plain=new_chat(client)
    client.post(f'/api/conversations/{plain}/generate',json={'content':'متى اكتمل برج إيفل؟'})
    assert 'asked' not in seen and 'web_sources' not in seen['prompt']
    other=new_chat(client)
    client.post(f'/api/conversations/{other}/generate',json={'content':'متى اكتمل برج إيفل؟','web_sources':True})
    assert seen['asked']=='متى اكتمل برج إيفل؟' and 'web_sources' in seen['prompt'] and 'ar.wikipedia.org' not in seen['prompt']
    saved=client.get('/api/conversations/'+other).json()['messages'][-1]['content']
    assert saved.startswith('اكتمل بناؤه عام 1889 [1].') and '1. [برج إيفل](https://ar.wikipedia.org/wiki/x)' in saved
    # Passages that do not fit beside the request are dropped, never an error: the answer is generated and marked unsourced.
    async def huge(question,preferred=None,client=None):
        return [{'n':n,'title':'صفحة','url':'https://ar.wikipedia.org/wiki/y','lang':'ar','text':'نص '*20000,'retrieved':'2026-11-20'} for n in (1,2)]
    monkeypatch.setattr(service.wiki_sources,'fetch',huge)
    big=new_chat(client)
    assert client.post(f'/api/conversations/{big}/generate',json={'content':'متى اكتمل برج إيفل؟','web_sources':True}).status_code==200
    assert 'لم يجد صفحة مطابقة' in client.get('/api/conversations/'+big).json()['messages'][-1]['content']
