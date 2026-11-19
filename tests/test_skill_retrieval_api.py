"""Real HTTP/SQLite execution and citations with explicitly mocked embeddings."""
import json
import time

from backend import public_knowledge as provider
from backend.db import get_db
from test_app import client
from test_skills import pid, submit


def upload(client, project, name, text):
    response=client.post(f'/api/projects/{project}/files',files={'file':(name,text,'text/plain')})
    assert response.status_code==201,response.text
    return response.json()['id']


def configure_mock_local(monkeypatch, seen, on_embed=None):
    async def info():
        return {'available':True,'processing':'local','model':'fixture-embed:v1','digest':'a'*64}
    async def vectors(texts, metadata):
        seen.extend(texts)
        if on_embed:on_embed()
        return [[1.0,0.0] for _ in texts]
    monkeypatch.setattr(provider,'embedding_info',info)
    monkeypatch.setattr(provider,'vectors',vectors)


def test_scoped_candidates_open_real_citation_then_revocation_denies(client,monkeypatch):
    project=pid(client);other=client.post('/api/projects',json={'name':'similar name'}).json()['id']
    selected=upload(client,project,'same.txt','SELECTED_SYNTHETIC_VALUE 42')
    upload(client,project,'unselected.txt','UNSELECTED_MUST_NOT_EMBED 77')
    upload(client,other,'same.txt','OTHER_PROJECT_MUST_NOT_EMBED 99')
    seen=[];configure_mock_local(monkeypatch,seen)
    result=submit(client,project,'hybrid-retrieval',{'documentIds':[selected],'retrievalMode':'hybrid'},request='ابحث عن المعنى')
    assert result['status']=='completed',result
    assert result['result']['retrieval_receipt']['method']=='private-hybrid-rrf'
    assert result['result']['retrieval_kind']=='candidate-excerpts'
    assert result['result']['warnings']
    assert not any('MUST_NOT_EMBED' in text for text in seen)
    refs=result['result']['source_refs'];assert len(refs)==1
    evidence=client.get(refs[0]['url'])
    assert evidence.status_code==200
    assert evidence.json()['file_id']==selected
    assert 'SELECTED_SYNTHETIC_VALUE' in evidence.json()['text']
    with get_db() as conn:conn.execute('UPDATE guard_file_policies SET revoked=1 WHERE file_id=?',(selected,))
    assert client.get(refs[0]['url']).status_code==403
    assert client.get(f'/api/projects/{project}/skill-runs/{result["id"]}').status_code==403


def test_missing_local_model_api_returns_existing_bm25_and_explicit_notice(client,monkeypatch):
    async def absent():return {'available':False,'configured':False}
    async def must_not_embed(*args):raise AssertionError('No model call permitted')
    monkeypatch.setattr(provider,'embedding_info',absent)
    monkeypatch.setattr(provider,'vectors',must_not_embed)
    project=pid(client);selected=upload(client,project,'fee.txt','رمز الخدمة A-731 والرسوم 42 شيكل')
    result=submit(client,project,'hybrid-retrieval',{'documentIds':[selected],'retrievalMode':'hybrid'},request='A-731')
    receipt=result['result']['retrieval_receipt']
    assert receipt['method']=='bm25' and receipt['semantic_status']=='unavailable'
    assert receipt['external_fallback'] is False
    assert '42' in result['result']['text']
    assert any('البحث النصي' in warning for warning in result['result']['warnings'])


def test_revocation_inside_embedding_prevents_saved_evidence(client,monkeypatch):
    project=pid(client);selected=upload(client,project,'private.txt','NEVER_PUBLISH_AFTER_REVOCATION 42')
    def revoke():
        with get_db() as conn:conn.execute('UPDATE guard_file_policies SET revoked=1 WHERE file_id=?',(selected,))
    seen=[];configure_mock_local(monkeypatch,seen,revoke)
    created=client.post(f'/api/projects/{project}/skill-runs',json={
        'request':'معنى المصدر','requestedSkills':['hybrid-retrieval'],
        'inputs':{'documentIds':[selected],'retrievalMode':'hybrid'},'idempotencyKey':'revoke-during-private-embed'})
    assert created.status_code==202,created.text
    rid=created.json()['id'];deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        with get_db() as conn:
            state=conn.execute('SELECT state,document FROM skill_runs WHERE id=?',(rid,)).fetchone()
        if state['state'] not in {'queued','running'}:break
        time.sleep(.02)
    assert state['state']=='failed'
    stored=json.loads(state['document'])
    assert stored['result'].get('source_refs',[])==[]
    assert 'NEVER_PUBLISH_AFTER_REVOCATION' not in json.dumps(stored)
    assert client.get(f'/api/projects/{project}/skill-runs/{rid}').status_code==403


def test_default_lexical_mode_does_not_probe_or_call_model(client,monkeypatch):
    async def must_not_call(*args):raise AssertionError('Default lexical retrieval must not call any model')
    monkeypatch.setattr(provider,'embedding_info',must_not_call)
    monkeypatch.setattr(provider,'vectors',must_not_call)
    project=pid(client);selected=upload(client,project,'default.txt','Synthetic exact R-123 = 42')
    result=submit(client,project,'hybrid-retrieval',{'documentIds':[selected]},request='R-123')
    assert result['status']=='completed',result
    assert result['result']['retrieval_receipt']['method']=='bm25'
    assert '42' in result['result']['text']
