"""Private ranking contracts with synthetic vectors; no real model quality claim."""
import asyncio
import math

import httpx
import pytest

from backend import public_knowledge as provider
from backend import skill_retrieval as retrieval


INFO = {'available': True, 'processing': 'local', 'model': 'fixture-embed:v1', 'digest': 'a'*64}


def chunk(identifier, text, **extra):
    return {'id': identifier, 'project_id': 'project-one', 'file_id': 'file-one',
            'filename': 'Synthetic document', 'text': text, 'page': 1, **extra}


@pytest.fixture
def local_vectors(monkeypatch):
    calls = []
    async def info():
        return dict(INFO)
    async def vectors(texts, _info):
        calls.append(list(texts))
        return [[1.0, 0.0] if 'synonym' in text or text.startswith('task:') else [0.0, 1.0]
                for text in texts]
    monkeypatch.setattr(provider, 'embedding_info', info)
    monkeypatch.setattr(provider, 'vectors', vectors)
    return calls


def run(rows, lexical=None, check=lambda: None, query='Related query', **kwargs):
    return asyncio.run(retrieval.retrieve_private(query, rows, project_id='project-one',
                       file_ids=['file-one'], lexical_ranked=lexical or [], check=check, **kwargs))


def test_merges_existing_lexical_rank_and_semantic_synonym_without_mutation(local_vectors):
    rows = [chunk('exact', 'Exact identifier 001'), chunk('meaning', 'synonym concept')]
    result = run(rows, [dict(rows[0], score=900.0)])
    assert {row['id'] for row in result['chunks']} == {'exact', 'meaning'}
    assert result['receipt']['method'] == 'private-hybrid-rrf'
    assert result['receipt']['dimensions'] == 2
    assert result['receipt']['semantic_support'] == 'not_certified'
    assert all(row['evidence_status'] == 'candidate_excerpt' for row in result['chunks'])
    assert result['chunks'][0]['score'] < 1  # Not a raw BM25/cosine sum.
    assert 'score' not in rows[0]
    assert len(local_vectors) == 2


@pytest.mark.parametrize('change', [{'project_id':'other'}, {'file_id':'other'}])
def test_rejects_cross_scope_before_any_embedding(local_vectors, change):
    with pytest.raises(retrieval.RetrievalAuthorizationError):
        run([chunk('one','private', **change)])
    assert local_vectors == []


@pytest.mark.parametrize('rows', [
    [chunk(str(n),'text') for n in range(65)],
    [chunk('one','x'*64001)],
])
def test_oversized_scope_is_rejected_not_silently_truncated(local_vectors, rows):
    with pytest.raises(ValueError, match='scope'):
        run(rows)
    assert local_vectors == []


def test_changed_lexical_text_is_rejected(local_vectors):
    with pytest.raises(retrieval.RetrievalAuthorizationError):
        run([chunk('one','new text')], [chunk('one','old text')])
    assert local_vectors == []


def test_false_guard_is_a_denial_not_a_success(local_vectors):
    with pytest.raises(retrieval.RetrievalAuthorizationError):
        run([chunk('one','synonym')], check=lambda: False)
    assert local_vectors == []


def test_missing_model_returns_actual_lexical_rows_without_calling_vectors(monkeypatch, local_vectors):
    async def absent(): return {'available':False, 'configured':False}
    monkeypatch.setattr(provider, 'embedding_info', absent)
    rows = [chunk('one','literal match'), chunk('two','other')]
    result = run(rows, [dict(rows[0],score=2.5)])
    assert [row['id'] for row in result['chunks']] == ['one']
    assert result['chunks'][0]['score'] == 2.5
    assert result['receipt']['method'] == 'bm25'
    assert result['receipt']['semantic_status'] == 'unavailable'
    assert result['receipt']['external_fallback'] is False
    assert local_vectors == []


@pytest.mark.parametrize('bad', [[[True,False]], [[math.nan,0]], [[0,0]], [[1,2,3]], []])
def test_invalid_or_changed_vectors_fall_back_to_real_bm25(monkeypatch, local_vectors, bad):
    count=0
    async def vectors(texts, info):
        nonlocal count
        count+=1
        return [[1,0]] if count==1 else bad
    monkeypatch.setattr(provider,'vectors',vectors)
    row=chunk('one','literal')
    result=run([row], [dict(row,score=4)])
    assert result['receipt']['method']=='bm25'
    assert result['receipt']['semantic_status']=='unavailable'
    assert result['chunks'][0]['score']==4


def test_model_digest_changed_discards_all_semantic_results(monkeypatch, local_vectors):
    count=0
    async def info():
        nonlocal count
        count+=1
        return dict(INFO,digest=('a' if count==1 else 'b')*64)
    monkeypatch.setattr(provider,'embedding_info',info)
    result=run([chunk('one','synonym')])
    assert result['chunks']==[]
    assert result['receipt']['method']=='bm25'


def test_network_error_does_not_invent_semantic_results(monkeypatch, local_vectors):
    async def vectors(texts, info): raise httpx.ReadTimeout('synthetic failure')
    monkeypatch.setattr(provider,'vectors',vectors)
    result=run([chunk('one','synonym')])
    assert result['chunks']==[]
    assert result['receipt']['semantic_status']=='unavailable'


def test_no_match_remains_candidates_not_a_certified_negative(local_vectors):
    result=run([chunk('one','unrelated content')],query='No answer here')
    assert result['chunks']==[]
    assert result['receipt']['semantic_support']=='not_certified'


def test_revocation_during_embedding_cancels_request_without_returning_fallback(monkeypatch):
    revoked=False
    stopped=False
    async def info(): return dict(INFO)
    async def vectors(texts, info):
        nonlocal revoked,stopped
        revoked=True
        try: await asyncio.sleep(5)
        finally: stopped=True
    def check():
        if revoked: raise PermissionError('Synthetic revocation')
    monkeypatch.setattr(provider,'embedding_info',info)
    monkeypatch.setattr(provider,'vectors',vectors)
    with pytest.raises(retrieval.RetrievalAuthorizationError):
        run([chunk('one','synonym')],check=check)
    assert stopped


def test_local_endpoint_cannot_be_replaced_with_external_host(monkeypatch, local_vectors):
    monkeypatch.setattr(provider,'OLLAMA','https://example.org')
    row=chunk('one','literal')
    result=run([row],[dict(row,score=2)])
    assert result['receipt']['method']=='bm25'
    assert 'loopback' in result['receipt']['semantic_reason']
    assert local_vectors==[]
