"""Bounded private hybrid ranking over an already authorized immutable snapshot.

No database/cache, no new index and no cloud fallback. The caller supplies its
existing BM25 ranking and a synchronous guard callback that rechecks the source
snapshot and session. Scores are ranking measures, never confidence in answers.
"""
from __future__ import annotations

import asyncio
import inspect
import math
import re
import time

import httpx

MAX_CANDIDATES=64
MAX_CHARACTERS=64000
MIN_COSINE=0.50  # Fixed before the synthetic/live evaluation, not a probability.
RRF_K=60
LOCAL_ENDPOINT='http://127.0.0.1:11434'


class RetrievalAuthorizationError(PermissionError):
    pass


def _scope(query,candidates,project_id,file_ids,lexical_ranked,limit):
    if not isinstance(query,str) or not query.strip() or len(query)>4000:
        raise ValueError('Private retrieval requires a question of at most4000 characters')
    if not isinstance(project_id,str) or not project_id or not isinstance(file_ids,list) or not 1<=len(file_ids)<=8 or any(not isinstance(x,str) or not x for x in file_ids):
        raise ValueError('Private retrieval requires an explicit project and file selection')
    if len(set(file_ids))!=len(file_ids) or not isinstance(candidates,list) or len(candidates)>MAX_CANDIDATES:
        raise ValueError('Private semantic scope exceeds64 chunks; choose a smaller source scope')
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=10:
        raise ValueError('Private retrieval limit must be1–10')
    allowed=set(file_ids);snapshot={};characters=0
    for row in candidates:
        if not isinstance(row,dict) or row.get('project_id')!=project_id or row.get('file_id') not in allowed:
            raise RetrievalAuthorizationError('Candidate is outside the authorized snapshot')
        if not isinstance(row.get('id'),str) or not row['id'] or row['id'] in snapshot or not isinstance(row.get('text'),str):
            raise ValueError('Invalid or duplicate private chunk')
        characters+=len(row['text']);snapshot[row['id']]=dict(row)
    if characters>MAX_CHARACTERS:
        raise ValueError('Private semantic scope exceeds64000 characters; nothing was silently truncated')
    if not isinstance(lexical_ranked,list) or len(lexical_ranked)>MAX_CANDIDATES:
        raise ValueError('Invalid bounded lexical ranking')
    ids=[]
    for row in lexical_ranked:
        if not isinstance(row,dict) or row.get('id') not in snapshot or row.get('text')!=snapshot[row['id']]['text']:
            raise RetrievalAuthorizationError('Lexical evidence differs from the authorized snapshot')
        if row['id'] in ids: raise ValueError('Duplicate lexical evidence')
        ids.append(row['id'])
    return snapshot,ids,characters


def _vectors(values,count,dimensions=None):
    if not isinstance(values,list) or len(values)!=count: raise ValueError('Embedding count mismatch')
    normalized=[]
    for vector in values:
        if not isinstance(vector,list) or not 1<=len(vector)<=4096 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in vector):
            raise ValueError('Invalid local vector')
        if dimensions is None: dimensions=len(vector)
        if len(vector)!=dimensions: raise ValueError('Embedding dimensions changed')
        norm=math.hypot(*vector)
        if not math.isfinite(norm) or norm<=0: raise ValueError('Invalid vector norm')
        normalized.append([value/norm for value in vector])
    return normalized,dimensions


async def retrieve_private(query,candidates,*,project_id,file_ids,lexical_ranked,check,limit=8):
    """Return original source rows and a bounded retrieval receipt.

    `check` MUST revalidate session/ACL and the complete selected source snapshot;
    callbacks are invoked before/after every await and while waiting. No returned
    row may be published without the caller's final existing guard check.
    """
    started=time.monotonic();deadline=started+90
    def checked():
        try:
            result=check()
            if result is False:raise PermissionError('Guard denied access')
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):result.close()
                raise TypeError('A synchronous guard callback is required')
        except asyncio.CancelledError: raise
        except Exception as exc: raise RetrievalAuthorizationError('Private retrieval permission or source snapshot changed') from exc
    checked()
    snapshot,lexical_ids,characters=_scope(query,candidates,project_id,file_ids,lexical_ranked,limit)
    receipt={'method':'bm25','candidate_chunks':len(snapshot),'candidate_characters':characters,
             'candidate_coverage':'complete supplied snapshot; caller must bound selected scope',
             'semantic_status':'not_started','semantic_reason':None,'embedding_model':None,'embedding_digest':None,'dimensions':None,
             'embedding_batches_attempted':0,'local_endpoint':LOCAL_ENDPOINT,'external_fallback':False,
             'semantic_cosine_cutoff':MIN_COSINE,'score_meaning':'ranking only; not answer confidence','rrf_k':RRF_K,
             'semantic_support':'not_certified','retrieval_kind':'candidate-excerpts',
             'no_answer_detection':'not calibrated; candidate presence or absence does not prove an answer',
             'threshold_status':'fixed engineering filter, not calibrated relevance or truth probability'}
    lexical_scores={row['id']:row.get('score') for row in lexical_ranked}
    def finish(ids,method,scores=None,semantic=None):
        checked()
        rows=[]
        for key in ids[:limit]:
            row=dict(snapshot[key]);row['retrieval_method']=method;row['evidence_status']='candidate_excerpt'
            row['lexical_score']=lexical_scores.get(key)
            if scores is not None:row['score']=scores[key]
            elif key in lexical_scores:row['score']=lexical_scores[key]
            if semantic and key in semantic:row['semantic_similarity']=semantic[key]
            rows.append(row)
        receipt['method']=method;receipt['returned_chunks']=len(rows);receipt['elapsed_ms']=round((time.monotonic()-started)*1000,2)
        return {'chunks':rows,'receipt':receipt}
    if not snapshot:
        receipt.update(semantic_status='not_needed',semantic_reason='No authorized extracted chunks')
        return finish([],'bm25')
    async def guarded(call):
        checked()
        task=asyncio.create_task(call())
        try:
            while not task.done():
                checked()
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('Private embedding budget exceeded')
                await asyncio.wait({task},timeout=min(.1,remaining))
            checked()
            return task.result()
        finally:
            if not task.done():
                task.cancel();await asyncio.gather(task,return_exceptions=True)
    from backend import public_knowledge as provider
    if provider.OLLAMA!=LOCAL_ENDPOINT:
        receipt.update(semantic_status='unavailable',semantic_reason='Embedding endpoint is outside the fixed loopback policy')
        return finish(lexical_ids,'bm25')
    try:
        info=await guarded(provider.embedding_info)
        if not info.get('available'):
            receipt.update(semantic_status='unavailable',semantic_reason='Local embedding model is not configured or unavailable')
            return finish(lexical_ids,'bm25')
        model=info.get('model');digest=info.get('digest')
        if info.get('processing')!='local' or not isinstance(model,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',model) or 'cloud' in model.lower() or not isinstance(digest,str) or not re.fullmatch(r'(?:sha256:)?[a-fA-F0-9]{64}',digest):
            raise ValueError('Only an installed pinned local embedding model is allowed')
        receipt.update(embedding_model=model,embedding_digest=digest)
        receipt['embedding_batches_attempted']+=1
        values=await guarded(lambda:provider.vectors(['task: search result | query: '+query],info))
        query_vectors,dimensions=_vectors(values,1);query_vector=query_vectors[0]
        receipt['dimensions']=dimensions;ids=list(snapshot);similarities={}
        for start in range(0,len(ids),8):
            batch=ids[start:start+8];receipt['embedding_batches_attempted']+=1
            values=await guarded(lambda:provider.vectors(['title: '+str(snapshot[key].get('filename','Document'))[:200]+' | text: '+snapshot[key]['text'] for key in batch],info))
            vectors,_=_vectors(values,len(batch),dimensions)
            for key,vector in zip(batch,vectors):
                similarity=sum(x*y for x,y in zip(query_vector,vector))
                if similarity>=MIN_COSINE: similarities[key]=similarity
        current=await guarded(provider.embedding_info)
        if not current.get('available') or current.get('processing')!='local' or (current.get('model'),current.get('digest'))!=(model,digest):
            raise ValueError('Embedding model changed during retrieval')
        semantic_ids=sorted(similarities,key=lambda key:(-similarities[key],key))
        scores={}
        for ranking in (lexical_ids,semantic_ids):
            for rank,key in enumerate(ranking,1):scores[key]=scores.get(key,0)+1/(RRF_K+rank)
        ordered=sorted(scores,key=lambda key:(-scores[key],key))
        receipt.update(semantic_status='completed',semantic_candidates=len(semantic_ids))
        return finish(ordered,'private-hybrid-rrf',scores,similarities)
    except (httpx.HTTPError,ValueError,TimeoutError,KeyError,TypeError):
        receipt.update(semantic_status='unavailable',semantic_reason='Local embedding failed validation or its time budget; BM25 results only')
        return finish(lexical_ids,'bm25')
