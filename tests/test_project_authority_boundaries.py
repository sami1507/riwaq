"""Real authenticated API revocation; only provider/delay adapters are simulated."""
import asyncio
import json

import pytest

from backend import app as service, db, providers, knowledge, context_lineage, facts
from test_learning_api import client


def seeded(client):
    p = client.post('/api/projects',json={'name':'Private revoked project','instructions':'PRIVATE_INSTRUCTION'}).json()['id']
    cid = client.post('/api/conversations',json={'project_id':p,'title':'PRIVATE_CONVERSATION'}).json()['id']
    file = client.post(f'/api/projects/{p}/files',files={'file':('private.txt',b'PRIVATE_EVIDENCE secret marker','text/plain')})
    assert file.status_code == 201, file.text
    artifact = client.post(f'/api/projects/{p}/artifacts',json={'title':'PRIVATE_ARTIFACT','content':'PRIVATE_ARTIFACT_BODY'})
    assert artifact.status_code == 201, artifact.text
    with db.get_db() as c:
        c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',(db.uid(),cid,'assistant','PRIVATE_HISTORY','complete','fixture',db.now(),'[]'))
    return p,cid,file.json()['id'],artifact.json()['id']


def revoke(pid):
    with db.get_db() as c: c.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?',('other-synthetic-owner',pid))


def test_revoked_project_is_not_listed_or_searchable(client):
    pid,cid,_,_=seeded(client);revoke(pid)
    projects=client.get('/api/projects');chats=client.get('/api/conversations?q=PRIVATE')
    assert projects.status_code==chats.status_code==200
    assert pid not in str(projects.json()) and cid not in str(chats.json())
    assert 'PRIVATE' not in projects.text and 'PRIVATE' not in chats.text


def test_revoked_project_routes_all_reject_before_read_or_mutation(client):
    p,c,f,a=seeded(client);revoke(p)
    requests=[('GET',f'/projects/{p}',None),('PATCH',f'/projects/{p}',{'name':'changed'}),
      ('GET',f'/conversations?project_id={p}',None),('POST','/conversations',{'project_id':p}),
      ('GET',f'/conversations/{c}',None),('GET',f'/conversations/{c}/export',None),
      ('POST',f'/conversations/{c}/stop',None),('GET',f'/conversations/{c}/research-status',None),
      ('POST',f'/conversations/{c}/generate',{'content':'2+2','answer_mode':'calculator','use_files':False,'use_knowledge':False}),
      ('GET',f'/projects/{p}/events',None),('POST',f'/projects/{p}/events',{'kind':'goal','summary':'changed'}),
      ('GET',f'/projects/{p}/state',None),('GET',f'/projects/{p}/files',None),
      ('GET',f'/projects/{p}/retrieve?q=PRIVATE',None),('GET',f'/projects/{p}/artifacts',None),
      ('POST',f'/projects/{p}/artifacts',{'title':'new','content':'changed'}),('GET',f'/artifacts/{a}',None),
      ('PATCH',f'/artifacts/{a}',{'content':'changed','expected_version':1}),('GET',f'/artifacts/{a}/versions',None),
      ('GET',f'/artifacts/{a}/diff?from_version=1&to_version=1',None),('POST',f'/artifacts/{a}/restore',{'version':1}),
      ('DELETE',f'/projects/{p}/files/{f}',None),('DELETE',f'/conversations/{c}',None)]
    statuses={method+' '+route:client.request(method,'/api'+route,**({'json':body} if body is not None else {})).status_code
              for method,route,body in requests}
    assert all(v==404 for v in statuses.values()), statuses
    with db.get_db() as conn:
        assert conn.execute('SELECT name FROM projects WHERE id=?',(p,)).fetchone()[0]=='Private revoked project'
        assert conn.execute('SELECT content FROM artifacts WHERE id=?',(a,)).fetchone()[0]=='PRIVATE_ARTIFACT_BODY'
        assert conn.execute('SELECT 1 FROM knowledge_files WHERE id=?',(f,)).fetchone()
        assert conn.execute('SELECT 1 FROM conversations WHERE id=?',(c,)).fetchone()


def test_revoked_file_does_not_enter_listing_retrieval_or_duplicate_upload(client):
    p,_,f,_=seeded(client)
    with db.get_db() as c:c.execute('UPDATE guard_file_policies SET revoked=1,revision=revision+1 WHERE file_id=?',(f,))
    assert client.get(f'/api/projects/{p}/files').json()==[]
    assert client.get(f'/api/projects/{p}/retrieve?q=PRIVATE').json()['chunks']==[]
    duplicate=client.post(f'/api/projects/{p}/files',files={'file':('private.txt',b'PRIVATE_EVIDENCE secret marker','text/plain')})
    assert duplicate.status_code==404
    assert client.delete(f'/api/projects/{p}/files/{f}').status_code==404


def test_revocation_during_provider_stream_with_no_source_links_stops_delivery(client,monkeypatch):
    p,cid,_,_=seeded(client)
    async def preflight(*args): pass
    async def stream(*args,**kwargs):
        yield {'content':'AUTHORIZED_PREFIX'}
        revoke(p)
        yield {'content':'PRIVATE_AFTER_REVOCATION'}
        yield {'finish_reason':'stop'}
    monkeypatch.setattr(providers,'preflight',preflight);monkeypatch.setattr(providers,'stream',stream)
    response=client.post(f'/api/conversations/{cid}/generate',json={'content':'hello','use_files':False,'use_knowledge':False})
    assert response.status_code==200,response.text
    assert 'PRIVATE_AFTER_REVOCATION' not in response.text
    packets=[json.loads(line) for line in response.text.splitlines()]
    assert not any(row['type']=='done' for row in packets)
    assert any(row['type']=='error' for row in packets)
    assert cid not in service.ACTIVE
    with db.get_db() as c:
        last=c.execute("SELECT content,status FROM messages WHERE conversation_id=? AND role='assistant' ORDER BY rowid DESC LIMIT 1",(cid,)).fetchone()
        assert last['content']=='' and last['status']=='error'


def test_revocation_during_model_preflight_creates_no_new_messages(client,monkeypatch):
    p,cid,_,_=seeded(client)
    async def preflight(*args):revoke(p)
    monkeypatch.setattr(providers,'preflight',preflight)
    with db.get_db() as c:before=c.execute('SELECT count(*) FROM messages WHERE conversation_id=?',(cid,)).fetchone()[0]
    response=client.post(f'/api/conversations/{cid}/generate',json={'content':'hello','use_files':False,'use_knowledge':False})
    assert response.status_code==404
    with db.get_db() as c:assert c.execute('SELECT count(*) FROM messages WHERE conversation_id=?',(cid,)).fetchone()[0]==before


def test_revoked_file_redacts_cached_conversation_export_search_and_facts(client):
    p,cid,f,_=seeded(client)
    with db.get_db() as c:
        chunk=dict(c.execute('SELECT k.*,f.filename FROM knowledge_chunks k JOIN knowledge_files f ON f.id=k.file_id WHERE k.file_id=?',(f,)).fetchone())
        mid=db.uid()
        c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',(mid,cid,'assistant','UNIQUE_SOURCE_DERIVATIVE','complete','fixture',db.now(),json.dumps([chunk])))
        context_lineage.save(c,mid,context_lineage.source_links(c,p,[chunk]))
    assert 'UNIQUE_SOURCE_DERIVATIVE' in client.get(f'/api/conversations/{cid}').text
    with db.get_db() as c:
        c.execute('UPDATE guard_file_policies SET revoked=1,revision=revision+1 WHERE file_id=?',(f,))
        assert facts.source_raw(c,p,'private_chunk',chunk['id'])==(None,'revoked')
    for route in (f'/conversations/{cid}',f'/conversations/{cid}/export',f'/conversations/{cid}/export?format=json'):
        value=client.get('/api'+route)
        assert value.status_code==200
        assert 'UNIQUE_SOURCE_DERIVATIVE' not in value.text and 'PRIVATE_EVIDENCE' not in value.text
    assert client.get('/api/conversations?q=UNIQUE_SOURCE_DERIVATIVE').json()==[]
    options=client.get(f'/api/projects/{p}/facts/source-options').json()
    assert options['items']==[]


def test_unbound_import_is_readable_but_known_legacy_revocation_is_hidden(client):
    p,cid,f,_=seeded(client)
    with db.get_db() as c:
        items=[('imported','USER_IMPORTED_TEXT',[],[context_lineage.BLOCKED]),
               ('legacy','KNOWN_REVOKED_COPY',[{'file_id':f,'id':'legacy-chunk'}],None),
               ('unknown','UNKNOWN_PROVENANCE_TEXT',[{'file_id':'never-recorded','id':'unknown'}],None)]
        for mid,text,cites,links in items:
            c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',(mid,cid,'assistant',text,'complete','fixture',db.now(),json.dumps(cites)))
            if links:context_lineage.save(c,mid,links)
        c.execute('UPDATE guard_file_policies SET revoked=1,revision=revision+1 WHERE file_id=?',(f,))
    response=client.get(f'/api/conversations/{cid}').text
    assert 'USER_IMPORTED_TEXT' in response and 'UNKNOWN_PROVENANCE_TEXT' in response
    assert 'KNOWN_REVOKED_COPY' not in response


def test_private_revocation_between_prepare_and_first_packet_leaks_no_start_citations(client,monkeypatch):
    p,cid,f,_=seeded(client)
    async def preflight(*args):pass
    monkeypatch.setattr(providers,'preflight',preflight)
    original=service.ManagedStreamingResponse.__call__
    async def revoked_before_start(response,scope,receive,send):
        with db.get_db() as c:c.execute('UPDATE guard_file_policies SET revoked=1,revision=revision+1 WHERE file_id=?',(f,))
        return await original(response,scope,receive,send)
    monkeypatch.setattr(service.ManagedStreamingResponse,'__call__',revoked_before_start)
    response=client.post(f'/api/conversations/{cid}/generate',json={'content':'PRIVATE_EVIDENCE','use_files':True,'use_knowledge':False})
    assert response.status_code==200,response.text
    packets=[json.loads(line) for line in response.text.splitlines()]
    assert not any(row['type'] in ('start','delta') for row in packets)
    assert any(row['type']=='error' for row in packets)
    for row in packets:
        if row['type']=='done':
            assert row['message']['content']=='' and row['message']['citations']==[]
            assert row['message']['status']=='error' and 'run' not in row['message'] and 'research' not in row['message']
    assert 'PRIVATE_EVIDENCE' not in response.text


def test_upload_revocation_during_parser_stops_and_never_persists(client,monkeypatch):
    from backend import knowledge_worker
    p=client.post('/api/projects',json={'name':'Parser revocation'}).json()['id']
    observed=[]
    def parse(filename,raw,stop):
        revoke(p);observed.append(True)
        return [(1,'FORBIDDEN_EXTRACTED_BODY')]
    monkeypatch.setattr(knowledge_worker,'extract_bounded',parse)
    response=client.post(f'/api/projects/{p}/files',files={'file':('synthetic.pdf',b'%PDF-synthetic-only','application/pdf')})
    assert response.status_code==404,response.text
    assert observed==[True]
    with db.get_db() as c:
        assert c.execute('SELECT count(*) FROM knowledge_files WHERE project_id=?',(p,)).fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM knowledge_chunks WHERE project_id=?',(p,)).fetchone()[0]==0


@pytest.mark.parametrize('withdrawal',['project','file'])
def test_original_document_surfaces_respect_withdrawal_without_parser(client,withdrawal):
    p,cid,f,_=seeded(client)
    with db.get_db() as c:
        c.execute('INSERT INTO document_originals VALUES(?,?,?,?,?,?,?)',(f,b'PRIVATE_ORIGINAL','text/plain',1,'{}','{}','complete'))
        c.execute('INSERT INTO document_pages VALUES(?,?,?,?)',(f,1,json.dumps({'number':1,'status':'complete','elements':[]}),b'PRIVATE_PREVIEW'))
    route=f'/api/projects/{p}/documents/{f}'
    assert client.get(route+'/original').content==b'PRIVATE_ORIGINAL'
    if withdrawal=='project':revoke(p)
    else:
        with db.get_db() as c:c.execute('UPDATE guard_file_policies SET revoked=1,revision=revision+1 WHERE file_id=?',(f,))
    for suffix in ('','/original','/pages/1','/pages/1/preview','/elements/missing/versions'):
        result=client.get(route+suffix)
        assert result.status_code==404 and 'PRIVATE_' not in result.text
    listing=client.get(f'/api/projects/{p}/documents')
    assert listing.status_code==404 if withdrawal=='project' else listing.json()==[]


def test_research_evidence_route_cannot_expose_revoked_project_message(client):
    p,cid,f,_=seeded(client)
    with db.get_db() as c:
        chunk=dict(c.execute('SELECT * FROM knowledge_chunks WHERE file_id=?',(f,)).fetchone())
        mid=db.uid();c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',(mid,cid,'assistant','PRIVATE_ANSWER','complete','fixture',db.now(),json.dumps([chunk])))
    route=f'/api/projects/{p}/research/evidence/{mid}/0'
    assert client.get(route).status_code==200
    revoke(p)
    response=client.get(route)
    assert response.status_code==404 and 'PRIVATE_' not in response.text


@pytest.mark.parametrize('withdrawal',['project','session'])
@pytest.mark.parametrize('phase',['preflight','stream'])
def test_withdrawal_interrupts_stalled_provider_and_releases_job(client,monkeypatch,withdrawal,phase):
    p,cid,_,_=seeded(client)
    cancelled=[]
    def withdraw():
        if withdrawal=='project':revoke(p)
        else:
            with db.get_db() as c:c.execute('UPDATE local_auth_sessions SET revoked_at=1')
    async def stall():
        withdraw()
        try:await asyncio.sleep(1.5)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
    async def preflight(*args):
        if phase=='preflight':await stall()
    async def stream(*args,**kwargs):
        await stall()
        yield {'content':'FORBIDDEN_LATE_PROVIDER_RESULT'}
    monkeypatch.setattr(providers,'preflight',preflight)
    monkeypatch.setattr(providers,'stream',stream)
    with db.get_db() as c:before=c.execute('SELECT count(*) FROM messages WHERE conversation_id=?',(cid,)).fetchone()[0]
    response=client.post(f'/api/conversations/{cid}/generate',json={'content':'hello','use_files':False,'use_knowledge':False})
    assert cancelled==[True], 'Authorization withdrawal must cancel a stalled coroutine, not merely reject its eventual output.'
    assert cid not in service.ACTIVE
    assert 'FORBIDDEN_LATE_PROVIDER_RESULT' not in response.text
    if phase=='preflight':
        # The outer session middleware has the final response authority.
        assert response.status_code==(401 if withdrawal=='session' else 404)
        with db.get_db() as c:assert c.execute('SELECT count(*) FROM messages WHERE conversation_id=?',(cid,)).fetchone()[0]==before
    else:
        assert response.status_code==200
        packets=[json.loads(line) for line in response.text.splitlines()]
        if withdrawal=='project':assert any(row['type']=='error' for row in packets)
        # A revoked session cannot receive even the application's error body;
        # the middleware closes the already-started stream instead.
        assert not any(row['type'] in ('delta','done') for row in packets)
