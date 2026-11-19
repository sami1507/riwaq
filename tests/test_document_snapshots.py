"""Lightweight snapshot boundaries for actual document metadata and selection state."""
import copy
import hashlib
import json

from authority_fixture import fixture_owner
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import context_lineage, db, documents, reasoning, snapshots


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    db.init_db()
    app = FastAPI()
    app.include_router(snapshots.router)
    app.include_router(documents.router)
    with fixture_owner(), TestClient(app) as session:
        yield session


def metadata(fid='file-one'):
    return {'file_id':fid,'version':1,'element_id':'element-one','revision':2,
            'page':1,'bbox':[.1,.2,.8,.4],
            'locator':{'page':1,'pdf_char_start':0,'pdf_char_end':15},
            'mime':'application/pdf'}


def seed():
    stamp=db.now()
    source='A saved source fact.'
    citation={'id':'chunk-one','file_id':'file-one','filename':'source.pdf','page':1,
              'line_start':1,'line_end':1,'text':source,'document':metadata()}
    options={'use_files':True,'use_knowledge':True,'file_scope':[{'file_id':'file-one','pages':[1]}],
             'answer_mode':'assistant','result_language':'ar'}
    with db.get_db() as c:
        for pid in ('one','other'):
            c.execute('INSERT INTO projects VALUES(?,?,?,?)',(pid,pid,'',stamp))
            fid='file-'+pid
            c.execute('INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)',
                      (fid,pid,'source.pdf',31,'a'*64,1,1,len(source),stamp))
            c.execute('INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?)',
                      ('chunk-'+pid,fid,pid,1,1,1,source if pid=='one' else 'FOREIGN SOURCE'))
            c.execute('INSERT INTO document_originals VALUES(?,?,?,?,?,?,?)',
                      (fid,b'ORIGINAL_PRIVATE_BYTES_'+pid.encode(),'application/pdf',1,'{}','{}','complete'))
            c.execute('INSERT INTO document_pages VALUES(?,?,?,?)',
                      (fid,1,'{"number":1}',b'PREVIEW_PRIVATE_BYTES_'+pid.encode()))
        c.execute('INSERT INTO conversations VALUES(?,?,?,?,?,?)',('chat','one','chat',stamp,stamp,None))
        for mid,role in [('user','user'),('answer','assistant')]:
            c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',
                      (mid,'chat',role,'Fact [S1]' if role=='assistant' else 'Question','complete','test',stamp,
                       json.dumps([citation]) if role=='assistant' else '[]'))
            context_lineage.save_retrieval(c,mid,options)
        reasoning.save_receipt(c,'answer',reasoning.RunReceipt(id='run',requested_depth='quick',effective_depth='quick',
            route_reason='test',policy_version='test',goal='Question',status='complete',request_options=options).model_dump())
        provenance={'origin':'local_message','message_id':'answer','conversation_id':'chat','model':'test',
            'message_created_at':stamp,'answer_sha256':hashlib.sha256(b'Fact [S1]').hexdigest(),
            'excerpt':'Fact [S1]','citations':[citation],'captured_at':stamp}
        payload={'term':'Term','definition':'Original definition','provenance':provenance}
        later=payload|{'definition':'Updated definition'}
        c.execute('INSERT INTO studio_entries VALUES(?,?,?,?,?,?,?,?)',
                  ('entry','one','glossary',json.dumps(later),2,stamp,stamp,'term'))
        for version,data in [(1,payload),(2,later)]:
            c.execute('INSERT INTO studio_versions VALUES(?,?,?,?)',('entry',version,json.dumps(data),stamp))
        c.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',('artifact','one','Saved','Fact [S1]',1,stamp,stamp))
        c.execute('INSERT INTO artifact_versions VALUES(?,?,?,?)',('artifact',1,'Fact [S1]',stamp))
        c.execute('INSERT INTO studio_artifact_sources VALUES(?,?,?,?,?,?)',
                  ('artifact','one',json.dumps(provenance),'request-one','note',hashlib.sha256(b'Fact [S1]').hexdigest()))
    return citation


def exported(client,pid='one'):
    response=client.get(f'/api/projects/{pid}/snapshot')
    assert response.status_code==200,response.text
    return response.json()


def snapshot_citations(snapshot):
    for conversation in snapshot['conversations']:
        for message in conversation['messages']:
            yield from message['citations']
    for entry in snapshot['studio_entries']:
        yield from entry['payload']['provenance']['citations']
        for version in entry['versions']:
            yield from version['payload']['provenance']['citations']
    for artifact in snapshot['artifacts']:
        yield from artifact['studio_source']['provenance']['citations']


def test_document_roundtrip_strips_original_locators_everywhere_and_keeps_scoped_text(client,monkeypatch):
    original_citation=seed()
    snapshot=exported(client)
    assert snapshots.DOCUMENT_SNAPSHOT_NOTICE in snapshot['warnings']
    expected=snapshots.DocumentCitationData.model_validate(original_citation['document']).model_dump()
    assert all(snapshots.DocumentCitationData.model_validate(c['document']).model_dump()==expected for c in snapshot_citations(snapshot))
    assert 'ORIGINAL_PRIVATE_BYTES' not in json.dumps(snapshot) and 'PREVIEW_PRIVATE_BYTES' not in json.dumps(snapshot)
    assert 'FOREIGN SOURCE' not in json.dumps(snapshot)
    # Snapshot IO cannot run a parser, resolve originals, or reach a model/network.
    def denied(*args,**kwargs): raise AssertionError('Snapshot must not dispatch')
    monkeypatch.setattr(documents,'parse',denied)
    import socket
    monkeypatch.setattr(socket,'create_connection',denied)
    result=client.post('/api/projects/import',json={'snapshot':snapshot,'confirm':True})
    assert result.status_code==201,result.text
    body=result.json();restored=exported(client,body['id'])
    assert body['stripped_document_metadata']==5  # message, current+two histories, artifact origin
    assert body['reset_file_scopes']==3  # two message selections and one historical receipt
    assert snapshots.FILE_SCOPE_RESET_NOTICE in body['warnings']
    file_id=restored['files'][0]['id'];chunk_id=restored['files'][0]['chunks'][0]['id']
    assert file_id!='file-one' and chunk_id!='chunk-one'
    for citation in snapshot_citations(restored):
        assert 'document' not in citation
        assert citation['file_id']==file_id and citation['id']==chunk_id
        assert citation['text']==original_citation['text']
    for message in restored['conversations'][0]['messages']:
        options=message['retrieval_options']
        assert not options.get('file_scope') and options['use_files'] is False
        assert options['use_knowledge'] is True and options['result_language']=='ar'
    run=restored['conversations'][0]['messages'][1]['run']
    assert run['origin']=='imported_snapshot'
    assert not run['request_options'].get('file_scope') and not run['request_options']['use_files']
    assert snapshots.FILE_SCOPE_RESET_NOTICE in run['remaining']
    for fid in (file_id,'file-one','file-other'):
        assert client.get(f"/api/projects/{body['id']}/documents/{fid}/original").status_code==404
    with db.get_db() as c:
        assert c.execute('SELECT COUNT(*) FROM document_originals').fetchone()[0]==2
        assert c.execute('SELECT COUNT(*) FROM document_pages').fetchone()[0]==2
        assert c.execute('SELECT COUNT(*) FROM document_elements').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM document_chunk_refs').fetchone()[0]==0
    assert exported(client)==snapshot


def test_export_does_not_preserve_foreign_document_pointer_or_file_scope(client):
    seed()
    with db.get_db() as c:
        refs=json.loads(c.execute("SELECT citations FROM messages WHERE id='answer'").fetchone()[0])
        refs[0]['document']['file_id']='file-other'
        c.execute("UPDATE messages SET citations=? WHERE id='answer'",(json.dumps(refs),))
        context_lineage.save_retrieval(c,'user',{'use_files':True,'file_scope':[{'file_id':'file-other','pages':[1]}]})
        run=reasoning.read_receipt(c,'answer')
        run['request_options']['file_scope']=[{'file_id':'file-other','pages':[1]}]
        reasoning.save_receipt(c,'answer',run)
    snapshot=exported(client)
    messages=snapshot['conversations'][0]['messages']
    assert 'document' not in messages[1]['citations'][0]
    assert not messages[0]['retrieval_options'].get('file_scope') and not messages[0]['retrieval_options']['use_files']
    assert not messages[1]['run']['request_options'].get('file_scope')
    assert 'file-other' not in json.dumps(snapshot)


@pytest.mark.parametrize('mutate',[
    lambda d:d.update(file_id='file-other'),
    lambda d:d.update(original='secret bytes'),
    lambda d:d.update(bbox=[.8,.1,.2,.4]),
    lambda d:d.update(bbox=[0,0,2,1]),
    lambda d:d.update(page=2),
    lambda d:d['locator'].update(url='http://127.0.0.1/private'),
    lambda d:d['locator'].update(pdf_char_start=20,pdf_char_end=1),
    lambda d:d['locator'].update(cells=['A1','../../secret']),
])
def test_malformed_document_metadata_is_rejected_atomically(client,mutate):
    seed();snapshot=exported(client)
    mutate(snapshot['conversations'][0]['messages'][1]['citations'][0]['document'])
    with db.get_db() as c:before=c.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
    response=client.post('/api/projects/import',json={'snapshot':snapshot,'confirm':True})
    assert response.status_code==422,response.text
    with db.get_db() as c:assert c.execute('SELECT COUNT(*) FROM projects').fetchone()[0]==before


def test_legacy_snapshot_without_document_fields_keeps_unrestricted_file_option(client):
    seed();snapshot=exported(client)
    snapshot.pop('warnings')
    for citation in snapshot_citations(snapshot):citation.pop('document')
    for message in snapshot['conversations'][0]['messages']:
        message['retrieval_options'].pop('file_scope')
        if message.get('run'):message['run']['request_options'].pop('file_scope')
    response=client.post('/api/projects/import',json={'snapshot':snapshot,'confirm':True})
    assert response.status_code==201,response.text
    assert response.json()['stripped_document_metadata']==0 and response.json()['reset_file_scopes']==0
    restored=exported(client,response.json()['id'])
    assert all(m['retrieval_options']['use_files'] for m in restored['conversations'][0]['messages'])
