"""Failure-state and rights regression tests. These do not measure model accuracy."""
import asyncio
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from backend import app as service, providers, public_knowledge as public
from backend.knowledge_fetch import FetchResult

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY', str(tmp_path/'missing.json'))
    service.ACTIVE.clear()
    with TestClient(service.app, headers={'X-Riwaq-Client':'local'}) as client:
        from backend.local_auth import issue_pairing_code
        assert client.post('/api/auth/login', json={'pairing_code': issue_pairing_code()}).status_code == 200
        yield client

def chat(client):
    project=client.get('/api/projects').json()[0]
    return client.post('/api/conversations', json={'project_id':project['id']}).json()['id']

@pytest.mark.parametrize('reason,expected',[('stop','complete'),('end_turn','complete'),('length','interrupted'),('max_tokens','interrupted'),('tool_use','interrupted'),('unknown','interrupted')])
def test_terminal_reason_survives_reopen_and_export(client,monkeypatch,reason,expected):
    async def validate(*args):pass
    async def stream(*args):
        yield {'content':'Saved partial or full answer.'}
        yield {'finish_reason':reason}
    monkeypatch.setattr(providers,'validate_provider',validate)
    monkeypatch.setattr(providers,'stream',stream)
    cid=chat(client)
    events=[json.loads(line) for line in client.post(f'/api/conversations/{cid}/generate',json={'content':'hello'}).text.splitlines()]
    saved=client.get('/api/conversations/'+cid).json()['messages'][-1]
    assert saved==events[-1]['message']
    assert saved['status']==expected
    assert saved['content']=='Saved partial or full answer.'
    assert client.get(f'/api/conversations/{cid}/export?format=json').json()['messages'][-1]['status']==expected

def test_empty_terminal_answer_is_not_complete(client,monkeypatch):
    async def validate(*args):pass
    async def stream(*args):yield {'finish_reason':'stop'}
    monkeypatch.setattr(providers,'validate_provider',validate)
    monkeypatch.setattr(providers,'stream',stream)
    response=client.post(f'/api/conversations/{chat(client)}/generate',json={'content':'hello'})
    events=[json.loads(line) for line in response.text.splitlines()]
    assert events[-1]['message']['status']=='error'
    assert any('no answer text' in event.get('message','') for event in events if event['type']=='error')

def seed_public(client):
    spec={'id':'race-source','name':'Race fixture','base_url':'https://example.org/docs/',
      'allowed_hosts':['example.org'],'allowed_prefixes':['/docs/'],
      'urls':[{'url':'https://example.org/docs/a.txt','title':'Race fixture','version':'1'}],
      'status':'allowed','rights_confirmed':True,'license':{'name':'MIT','url':'https://example.org/license',
      'storage_allowed':True,'redistribution_allowed':True,'attribution':'Test fixture',
      'reviewed_at':'2026-11-12','evidence':'Synthetic test source'}}
    response=client.post('/api/knowledge/sources',json=spec)
    assert response.status_code==201,response.text
    with service.get_db() as conn:
        source=public.source_dict(public.source_row(conn,'race-source'))
        item=source['urls'][0]
        public.store_document(conn,source,item,FetchResult(item['url'],200,{'content-type':'text/plain'},b'REVOKED_EVIDENCE'),[(None,'REVOKED_EVIDENCE')],'Test MIT license')
        return public.corpus_rows(conn)[0]

@pytest.mark.parametrize('change',['suspend','delete','replace'])
def test_rights_or_revision_change_during_validation_never_sent(client,monkeypatch,change):
    chunk=seed_public(client)
    async def retrieve(*args,**kwargs):return [chunk]
    async def validate(*args):
        with service.get_db() as conn:
            if change=='suspend':conn.execute("UPDATE public_sources SET status='suspended' WHERE id='race-source'")
            elif change=='delete':conn.execute("DELETE FROM public_documents WHERE id=?",(chunk['document_id'],))
            else:
                source=public.source_dict(public.source_row(conn,'race-source'))
                item=source['urls'][0]
                public.store_document(conn,source,item,FetchResult(item['url'],200,{'content-type':'text/plain'},b'NEW_EVIDENCE'),[(None,'NEW_EVIDENCE')],'Test MIT license')
    async def forbidden(*args):
        raise AssertionError('Revoked/stale evidence must never be sent')
        yield {}
    monkeypatch.setattr(public,'retrieve_public',retrieve)
    monkeypatch.setattr(providers,'validate_provider',validate)
    monkeypatch.setattr(providers,'stream',forbidden)
    cid=chat(client)
    response=client.post(f'/api/conversations/{cid}/generate',json={'content':'question','use_knowledge':True})
    assert response.status_code==409,response.text
    assert client.get('/api/conversations/'+cid).json()['messages']==[]

@pytest.mark.parametrize('provider,frame,expected',[
 ('ollama',{'done':True,'done_reason':'length','message':{'content':'partial'}},'length'),
 ('anthropic',{'type':'message_delta','delta':{'stop_reason':'max_tokens'}},'max_tokens'),
 ('openai',{'type':'response.incomplete','response':{'incomplete_details':{'reason':'max_output_tokens'}}},'max_output_tokens')])
def test_adapters_preserve_stop_reason(monkeypatch,provider,frame,expected):
    # Protocol parsing remains covered independently of the deliberately closed
    # cloud dispatch gate. No policy check is removed or mocked for this test.
    packets, completed = providers.decode_frame(provider, frame)
    assert any(packet.get('finish_reason')==expected for packet in packets)

def test_status_only_offers_verified_local_chat_models(monkeypatch):
    real_client=httpx.AsyncClient
    def reply(request):
        if request.url.path=='/api/tags':return httpx.Response(200,json={'models':[{'name':'embed'},{'name':'chat'},{'name':'remote'},{'name':'broken'}]})
        name=json.loads(request.content)['model']
        if name=='broken':return httpx.Response(503)
        return httpx.Response(200,json={'capabilities':['embedding'] if name=='embed' else ['completion'],'remote_host':'https://example.org' if name=='remote' else ''})
    transport=httpx.MockTransport(reply)
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kwargs:real_client(transport=transport,**kwargs))
    status=asyncio.run(providers.ollama_status())
    assert status['available']
    assert [model['name'] for model in status['models'] if model['chat_capable']]==['chat']


def test_context_rejection_rolls_back_user_message(client,monkeypatch):
    async def validate(*args):pass
    monkeypatch.setattr(providers,'validate_provider',validate)
    cid=chat(client)
    response=client.post(f'/api/conversations/{cid}/generate',json={'content':'ع'*10000})
    assert response.status_code==413
    assert client.get('/api/conversations/'+cid).json()['messages']==[]
    assert client.patch('/api/settings',json={'context_size':2048,'max_tokens':2048}).status_code==422

@pytest.fixture
def simulated_replay_worker(monkeypatch):
    # Transaction/recipe contract only. OS isolation is tested separately with
    # the real pinned image; these fixed synthetic bytes never imply that test.
    from backend import isolated_extract, isolated_parsers
    calls = []
    def extract(body, metadata, stop=None):
        calls.append(dict(metadata))
        assert metadata['mode'] == 'public'
        assert set(metadata['source']) == {'content_type'}
        return isolated_parsers.extract_public(body, metadata['source'], metadata['url'],
            metadata['content_type'], extractor=metadata.get('extractor')), {'simulated': True}
    monkeypatch.setattr(isolated_extract, 'extract', extract)
    return calls


def test_html_reprocess_preserves_original_and_is_idempotent(client, simulated_replay_worker):
    seed_public(client)
    raw=b'<h2>Example</h2><pre>def f():\n    return 1\n</pre>'
    with service.get_db() as conn:
        source=public.source_dict(public.source_row(conn,'race-source'))
        item=source['urls'][0]
        fetched=FetchResult(item['url'],200,{'content-type':'text/html','etag':'old-tag'},raw)
        legacy=public.extract_public(raw,source,item['url'],'text/html',extractor='legacy-v1')
        did,_=public.store_document(conn,source,item,fetched,legacy,'Test MIT license',fetched_at='2026-11-01T00:00:00+00:00')
        prior=conn.execute('SELECT v.* FROM public_versions v JOIN public_documents d ON d.current_version_id=v.id WHERE d.id=?',(did,)).fetchone()
        old_policy=json.loads(prior['chunking']);old_policy.pop('extractor')
        conn.execute('UPDATE public_versions SET chunking=? WHERE id=?',(json.dumps(old_policy),prior['id']))
        before=dict(conn.execute('SELECT * FROM public_versions WHERE id=?',(prior['id'],)).fetchone())
    with service.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        first=public.reprocess_html_document(conn,did,prior['id'])
        assert first['changed']
        current=dict(conn.execute('SELECT * FROM public_versions WHERE id=?',(first['current_version'],)).fetchone())
        assert '    return 1' in current['extracted_text']
        for key in ('original','sha256','version','license','license_text','fetched_at','etag','last_modified','published_at','modified_at'):
            assert current[key]==before[key]
        assert dict(conn.execute('SELECT * FROM public_versions WHERE id=?',(prior['id'],)).fetchone())==before
        assert not public.reprocess_html_document(conn,did,first['current_version'])['changed']
        with pytest.raises(ValueError,match='Current version changed'):
            public.reprocess_html_document(conn,did,prior['id'])
    assert [call['extractor'] for call in simulated_replay_worker] == ['html-pre-v2', 'html-pre-v2']


def test_reprocess_uses_historical_chunk_policy_and_empty_version(client, simulated_replay_worker):
    seed_public(client)
    raw=b'<pre>def f():\n    return 1</pre>'
    with service.get_db() as conn:
        source=public.source_dict(public.source_row(conn,'race-source'))
        source['version']=''
        source['chunking']={'max_characters':400,'max_lines':8,'overlap_lines':0,'respect_headings':False}
        item=dict(source['urls'][0],version='')
        pages=public.extract_public(raw,source,item['url'],'text/html',extractor='legacy-v1')
        did,_=public.store_document(conn,source,item,FetchResult(item['url'],200,{'content-type':'text/html'},raw),pages,'MIT')
        prior=conn.execute('SELECT v.* FROM public_versions v JOIN public_documents d ON d.current_version_id=v.id WHERE d.id=?',(did,)).fetchone()
        policy=json.loads(prior['chunking']);policy.pop('extractor')
        conn.execute('UPDATE public_versions SET chunking=? WHERE id=?',(json.dumps(policy),prior['id']))
        source['version']='new-source-label';source['chunking']['max_characters']=1400
        conn.execute("UPDATE public_sources SET data=? WHERE id='race-source'",(json.dumps(source),))
    with service.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        result=public.reprocess_html_document(conn,did,prior['id'])
        current=conn.execute('SELECT * FROM public_versions WHERE id=?',(result['current_version'],)).fetchone()
        assert current['version']==''
        assert json.loads(current['chunking'])==policy|{'extractor':'html-pre-v2'}
    assert simulated_replay_worker[0]['extractor'] == 'html-pre-v2'
