"""Actual local ASGI/SQLite evidence readback; no network/model or semantic score."""
import copy
from backend import research,public_knowledge as public
from backend.db import get_db
from backend.knowledge_fetch import FetchResult
from test_research_security import evidence_api


def test_private_version_change_with_same_text_cannot_claim_current(evidence_api):
    api=evidence_api
    api.save({'id':'kp1','scope':'private','text':'Private source for p1','sha256':'a'*64})
    assert api.get().status_code==200
    with get_db() as conn:conn.execute("UPDATE knowledge_files SET sha256=? WHERE id='fp1'",('b'*64,))
    response=api.get()
    assert response.status_code==409


def test_unbound_private_legacy_excerpt_is_not_certified_current(evidence_api):
    api=evidence_api
    api.save({'id':'kp1','scope':'private','text':'Private source for p1'})
    response=api.get()
    assert response.status_code==200 and response.json()['current'] is False
    assert 'binding' in response.json()['notice']


def test_stale_public_version_stays_historical_and_current_retrieval_uses_new(evidence_api):
    api=evidence_api;api.save(api.citation)
    with get_db() as conn:
        body=b'Original public evidence reports 20 units in the new source version.'
        item=copy.deepcopy(api.source['urls'][0]);item['version']='fixture-v2'
        public.store_document(conn,api.source,item,FetchResult(item['url'],200,{'content-type':'text/plain'},body),[(None,body.decode())],'Original test data')
        current=public.corpus_rows(conn)
        assert all(row['version_id']!=api.citation['version_id'] for row in current)
        assert any('20 units' in row['text'] for row in current)
    response=api.get()
    assert response.status_code==200 and response.json()['current'] is False
    assert 'Historical' in response.json()['notice']


def test_deleted_or_revoked_source_cannot_be_read_from_citation(evidence_api):
    api=evidence_api;api.save(api.citation)
    with get_db() as conn:conn.execute("UPDATE public_sources SET status='suspended' WHERE id=?",(api.source['id'],))
    assert api.get().status_code==404


def test_cross_project_same_filename_never_returns_other_evidence(evidence_api):
    api=evidence_api;api.save({'id':'kp2','scope':'private','text':'Private source for p2'})
    result=api.get()
    assert result.status_code==404 and 'Private source for p2' not in result.text


def test_fetch_date_does_not_become_publication_or_event_date(evidence_api):
    api=evidence_api;chunk=copy.deepcopy(api.citation)
    chunk['published_at']='2026-11-16';chunk['event_at']='MODEL_UNTRUSTED_DATE'
    receipt={'checked_documents':{chunk['document_id']:{'version_id':chunk['version_id'],'checked_at':'2026-11-17T00:00:00Z'}},'notices':[]}
    with get_db() as conn:research.enrich(conn,[chunk],receipt)
    assert chunk['published_at']=='2026-11-16'
    assert chunk['checked_at']=='2026-11-17T00:00:00Z'
    assert chunk['event_at'] is None


def test_matching_content_is_not_three_independent_confirmations(evidence_api):
    api=evidence_api;chunks=[copy.deepcopy(api.citation) for _ in range(3)]
    for i,chunk in enumerate(chunks):chunk['document_id']='copy-'+str(i)
    receipt={'notices':[]}
    with get_db() as conn:research.enrich(conn,chunks,receipt)
    assert any('not independent' in text for text in receipt['notices'])
    assert receipt['corroboration']['independence_verified'] is False


def test_missing_fingerprints_do_not_imply_matching_content(evidence_api):
    api=evidence_api;chunks=[copy.deepcopy(api.citation) for _ in range(2)]
    for i,chunk in enumerate(chunks):chunk['document_id']='unknown-'+str(i);chunk.pop('sha256',None)
    receipt={'notices':[]}
    with get_db() as conn:research.enrich(conn,chunks,receipt)
    assert not any('Matching content' in text for text in receipt['notices'])
    assert receipt['corroboration']['independence_verified'] is False
