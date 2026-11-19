"""Authorization and context-boundary checks; live generation is tested separately."""
import json
import pytest
from fastapi.testclient import TestClient
from backend import app as service


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    service.ACTIVE.clear()
    with TestClient(service.app, headers={'X-Riwaq-Client': 'local'}) as client:
        from backend.local_auth import issue_pairing_code
        assert client.post('/api/auth/login', json={'pairing_code': issue_pairing_code()}).status_code == 200
        yield client


def chat(client):
    project=client.get('/api/projects').json()[0]
    return client.post('/api/conversations', json={'project_id':project['id']}).json()['id']


def source(text='Trusted source describes a request body.'):
    return {'id':'public-chunk','filename':'FastAPI body','text':text,'line_start':1,'line_end':2,
            'scope':'public','url':'https://example.org/docs','version':'0.162.5','license':'MIT'}


def test_public_retrieval_requires_explicit_toggle(client, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('Public retrieval must be opt in')
    async def validate(*args): pass
    async def stream(*args): yield {'content':'ordinary answer'}
    monkeypatch.setattr(service.public_knowledge,'retrieve_public',forbidden)
    monkeypatch.setattr(service.providers,'validate_provider',validate)
    monkeypatch.setattr(service.providers,'stream',stream)
    response=client.post(f'/api/conversations/{chat(client)}/generate', json={'content':'hello'})
    assert response.status_code==200
    assert 'ordinary answer' in response.text


def test_public_only_excludes_private_project_and_memory_from_other_projects(client, monkeypatch):
    other=client.post('/api/projects',json={'name':'Other private project'}).json()['id']
    client.post(f'/api/projects/{other}/files',files={'file':('secret.txt',b'FOREIGN_PRIVATE_TOKEN','text/plain')})
    client.post('/api/memories',json={'scope':'project','project_id':other,'content':'FOREIGN_PRIVATE_MEMORY'})
    captured=[]
    # Materialize a real authorized source, so lineage authorization remains active.
    from backend.db import uid,now,get_db
    from backend.facts import sha
    sid,did,vid,cid=uid(),uid(),uid(),uid();stamp=now();text=source()['text'];digest=sha(text)
    with get_db() as c:
        c.execute('INSERT INTO public_sources(id,data,status,created_at,updated_at) VALUES(?,?,?,?,?)',(sid,json.dumps({'rights_confirmed':True,'license':{'storage_allowed':True},'allowed_hosts':['example.org'],'allowed_prefixes':['/docs']}),'allowed',stamp,stamp))
        c.execute('INSERT INTO public_documents(id,source_id,requested_url,url,title,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(did,sid,'https://example.org/docs','https://example.org/docs','FastAPI body',stamp,stamp))
        c.execute('INSERT INTO public_versions(id,document_id,revision,version,sha256,original,extracted_text,byte_size,content_type,license,license_text,language,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(vid,did,1,'0.162.5',digest,text.encode(),text,len(text),'text/plain','{}','synthetic fixture','en',stamp))
        c.execute('INSERT INTO public_chunks VALUES(?,?,?,?,?,?,?,?)',(cid,did,vid,sid,None,1,1,text));c.execute('UPDATE public_documents SET current_version_id=? WHERE id=?',(vid,did))
    async def retrieve(*args,**kwargs):
        with get_db() as c:return service.public_knowledge.lexical_public(c,'request body')
    async def validate(*args): pass
    async def stream(provider,model,messages,cfg):
        captured.extend(messages)
        yield {'content':'A request body is client data. [S1]'}
    monkeypatch.setattr(service.public_knowledge,'retrieve_public',retrieve)
    monkeypatch.setattr(service.providers,'validate_provider',validate)
    monkeypatch.setattr(service.providers,'stream',stream)
    id=chat(client)
    response=client.post(f'/api/conversations/{id}/generate',json={'content':'What is a request body?','use_knowledge':True})
    assert response.status_code==200
    packed=json.dumps(captured)
    assert 'FOREIGN_PRIVATE' not in packed
    assert '0.162.5' in packed and 'untrusted_source_excerpts' in packed
    saved=client.get(f'/api/conversations/{id}').json()['messages'][-1]
    assert saved['citations'][0]['scope']=='public'
    assert saved['citations'][0]['url']=='https://example.org/docs'


def test_no_evidence_does_not_invoke_generator(client,monkeypatch):
    async def empty(*args,**kwargs):return []
    async def validate(*args):pass
    async def forbidden(*args):
        raise AssertionError('No evidence should not manufacture an answer')
        yield {}
    monkeypatch.setattr(service.public_knowledge,'retrieve_public',empty)
    monkeypatch.setattr(service.providers,'validate_provider',validate)
    monkeypatch.setattr(service.providers,'stream',forbidden)
    response=client.post(f'/api/conversations/{chat(client)}/generate',json={'content':'Unknown fact','use_knowledge':True})
    events=[json.loads(line) for line in response.text.splitlines()]
    assert events[-1]['message']['status']=='complete'
    assert events[-1]['message']['citations']==[]
    assert 'No relevant source excerpts' in events[-1]['message']['content']


def test_privacy_change_during_retrieval_rejected_before_save(client,monkeypatch):
    async def change(*args,**kwargs):
        with service.get_db() as conn:
            conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('max_tokens','512'))
        return [source()]
    monkeypatch.setattr(service.public_knowledge,'retrieve_public',change)
    id=chat(client)
    response=client.post(f'/api/conversations/{id}/generate',json={'content':'question','use_knowledge':True})
    assert response.status_code==409
    assert client.get(f'/api/conversations/{id}').json()['messages']==[]
