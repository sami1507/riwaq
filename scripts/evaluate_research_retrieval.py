"""Diagnostic retrieval comparison on the exposed, frozen 40-case knowledge set.

One real local retrieval produces each shared eight-candidate pool. Compare its
first six with research.select_evidence on exactly that pool. No generation,
indexing, source download, gold modification, or algorithm tuning is performed.
"""
from __future__ import annotations
import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend import db,public_knowledge as public,research,providers
from scripts.evaluate_knowledge import corpus_fingerprint,check_integrity,deny_external_connections

FACTUAL={'programming','general'}
ARMS=('baseline','selected')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def layout_text(text):
    # Layout-only relaxation: do not fold case, numbers, accents, or Unicode forms.
    return ' '.join(text.split())


def matches(hit,evidence):
    return (hit.get('source_id')==evidence['source_id']
            and hit.get('url')==evidence['document_url']
            and str(hit.get('version'))==str(evidence['source_version'])
            and hit.get('sha256')==evidence['original_sha256']
            and layout_text(evidence['needle']) in layout_text(hit.get('text','')))


def validate_dataset(value):
    cases=value.get('cases',[])
    if value.get('schema_version')!=1 or value.get('status')!='frozen':
        raise ValueError('Expected the existing frozen schema-1 knowledge evaluation')
    if len(cases)!=40 or len({c['id'] for c in cases})!=40:
        raise ValueError('Expected forty unique original cases')
    if Counter(c['category'] for c in cases)!=Counter(programming=20,general=10,absent=5,safety=5):
        raise ValueError('Original category counts changed')
    if Counter(c['split'] for c in cases)!=Counter(tune=10,heldout=30):
        raise ValueError('Original split changed')
    for case in cases:
        if not isinstance(case.get('query'),str) or not case['query'] or not case.get('gold_answer'):
            raise ValueError('Missing original query/gold')
        if bool(case.get('evidence')) != (case['category'] in FACTUAL):
            raise ValueError('Evidence/category mismatch')
    return cases


def snapshot(conn):
    if conn.execute("SELECT 1 FROM public_jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
        raise RuntimeError('Finish or cancel public ingestion before comparing retrieval')
    rows=public.corpus_rows(conn)
    if not rows:
        raise ValueError('No authorized public corpus; no fabricated replacement is permitted')
    versions=sorted({r['version_id'] for r in rows})
    originals=[]
    for identifier in versions:
        row=conn.execute('SELECT id,sha256,original,byte_size FROM public_versions WHERE id=?',(identifier,)).fetchone()
        if not row or sha(row['original'])!=row['sha256'] or len(row['original'])!=row['byte_size']:
            raise ValueError('Retained original failed its SHA-256/size check')
        originals.append({'version_id':identifier,'sha256':row['sha256'],'bytes':row['byte_size']})
    namespaces=[]
    active={r['id'] for r in rows}
    for row in conn.execute('SELECT chunk_id,model,digest,dimensions,vector FROM public_embeddings ORDER BY chunk_id,model,digest,dimensions'):
        if row['chunk_id'] in active:
            namespaces.append({'chunk_id':row['chunk_id'],'model':row['model'],'digest':row['digest'],'dimensions':row['dimensions'],'vector_sha256':sha(row['vector'])})
    registry=[{'id':r['id'],'status':r['status'],'data_sha256':sha(r['data'].encode())} for r in conn.execute('SELECT id,status,data FROM public_sources ORDER BY id')]
    return rows,{'corpus_sha256':corpus_fingerprint(rows),'originals':originals,
                 'vector_index_sha256':sha(json.dumps(namespaces,sort_keys=True).encode()),
                 'registry_sha256':sha(json.dumps(registry,sort_keys=True).encode()),
                 'stored_vector_rows':len(namespaces),'source_registry':registry}


def outcome(case,hits,originals,available):
    errors=[{'id':hit.get('id'),'fields':fields} for hit in hits if (fields:=check_integrity(hit,originals))]
    result={'returned_chunks':len(hits),'citation_integrity_errors':errors,'hits':hits}
    if case['category'] in FACTUAL:
        rank=next((i for i,hit in enumerate(hits,1) if any(matches(hit,e) for e in case['evidence'])),None)
        result.update(evidence_rank=rank if available else None,hit_at_6=rank is not None if available else None,
                      reciprocal_rank_at_6=(1/rank if rank else 0.0) if available else None,
                      scoring_status='scored' if available else 'gold_evidence_unavailable_in_current_corpus')
    else:
        result.update(scoring_status='not_answer_or_safety_scored',empty_retrieval=len(hits)==0)
    return result


def summarize(records,arm):
    factual=[r for r in records if r['category'] in FACTUAL]
    scorable=[r for r in factual if r['gold_evidence_available']]
    values=[r[arm] for r in scorable]
    complete=len(scorable)==len(factual)
    hit=statistics.mean(v['hit_at_6'] for v in values) if values else None
    mrr=statistics.mean(v['reciprocal_rank_at_6'] for v in values) if values else None
    return {'cases':len(records),'factual_cases':len(factual),'scorable_factual_cases':len(scorable),
            'gold_unavailable_cases':[r['id'] for r in factual if not r['gold_evidence_available']],
            'hit_at_6':hit if complete else None,'mrr_at_6':mrr if complete else None,
            'available_subset_diagnostic':None if complete else {'hit_at_6':hit,'mrr_at_6':mrr,'denominator':len(scorable)},
            'hits':sum(v['hit_at_6'] for v in values),'citation_integrity_error_count':sum(len(r[arm]['citation_integrity_errors']) for r in records),
            'mean_shared_retrieval_seconds':statistics.mean(r['shared_retrieval_seconds'] for r in records) if records else None,
            'mean_selection_seconds':statistics.mean(r[arm]['selection_seconds'] for r in records) if records else None,
            'nonfactual_returned_chunks':sum(r[arm]['returned_chunks'] for r in records if r['category'] not in FACTUAL)}


async def evaluate(args):
    code_files=('scripts/evaluate_research_retrieval.py','scripts/evaluate_knowledge.py','backend/public_knowledge.py','backend/research.py','backend/knowledge.py')
    code_hashes={name:sha((ROOT/name).read_bytes()) for name in code_files}
    dataset_path=ROOT/'evaluation'/'knowledge-eval.json'
    raw=dataset_path.read_bytes();dataset=json.loads(raw);cases=validate_dataset(dataset)
    database=Path(args.database).resolve(strict=True)
    @contextmanager
    def readonly_db():
        conn=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=10)
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        try:yield conn
        finally:conn.close()
    with readonly_db() as conn:rows,before=snapshot(conn)
    originals={r['id']:r for r in rows}
    documents={}
    for row in rows:
        documents[row['document_id']]={k:row.get(k) for k in ('document_id','source_id','url','title','version_id','version','sha256','language','domain','published_at','modified_at','fetched_at','chunking')}
    results=[];embedding_requests=0
    real_vectors=public.vectors
    async def counted_vectors(texts,info):
        nonlocal embedding_requests
        embedding_requests+=1
        return await real_vectors(texts,info)
    def forbidden(*args,**kwargs):
        raise AssertionError('This diagnostic must never generate, ingest, or write an index')
    started=time.perf_counter()
    with patch.object(public,'get_db',readonly_db),patch.object(db,'get_db',readonly_db),patch.object(public,'vectors',counted_vectors),patch.object(providers,'stream',forbidden),patch.object(public,'store_document',forbidden),deny_external_connections() as fence:
        embedding=await public.embedding_info()
        for case in cases:
            tick=time.perf_counter()
            candidates=await asyncio.wait_for(public.retrieve_public(case['query'],limit=8),timeout=90)
            shared=time.perf_counter()-tick
            if len(candidates)>8 or any(check_integrity(c,originals) for c in candidates):
                raise RuntimeError('Shared candidates changed or contain unauthorized source metadata')
            frozen=copy.deepcopy(candidates)
            tick=time.perf_counter();baseline=candidates[:6];baseline_seconds=time.perf_counter()-tick
            tick=time.perf_counter();selected=research.select_evidence(case['query'],copy.deepcopy(candidates))[:6];selected_seconds=time.perf_counter()-tick
            if candidates!=frozen or any(c not in frozen for c in selected) or len({c['id'] for c in selected})!=len(selected):
                raise RuntimeError('Selector modified, invented, or duplicated evidence')
            available=all(any(matches(row,e) for row in rows) for e in case.get('evidence',[])) if case['category'] in FACTUAL else None
            record={key:case[key] for key in ('id','category','language','split','query')}
            record.update(gold_evidence_available=available,shared_retrieval_seconds=round(shared,6),shared_candidate_ids=[c['id'] for c in candidates],
                          retrieval_methods=sorted({c.get('retrieval_method','unknown') for c in candidates}),
                          baseline=outcome(case,baseline,originals,available)|{'selection_seconds':baseline_seconds,'pipeline_seconds':shared+baseline_seconds},
                          selected=outcome(case,selected,originals,available)|{'selection_seconds':selected_seconds,'pipeline_seconds':shared+selected_seconds})
            results.append(record)
            print(json.dumps({'id':case['id'],'baseline_rank':record['baseline'].get('evidence_rank'),'selected_rank':record['selected'].get('evidence_rank'),'shared_seconds':round(shared,3)}),flush=True)
        with readonly_db() as conn:after_rows,after=snapshot(conn)
        if before!=after or dataset_path.read_bytes()!=raw or code_hashes!={name:sha((ROOT/name).read_bytes()) for name in code_files}:
            raise RuntimeError('Corpus, namespace, registry, or frozen dataset changed during comparison')
        if fence['external_blocked']:
            raise RuntimeError('Unexpected external connection attempted during local retrieval')
    comparable=[r for r in results if r['category'] in FACTUAL and r['gold_evidence_available']]
    transitions=Counter(('hit' if r['baseline']['hit_at_6'] else 'miss')+'_to_'+('hit' if r['selected']['hit_at_6'] else 'miss') for r in comparable)
    report={'format':'riwaq-research-retrieval-1','created_at':db.now(),'status':'complete','dataset_sha256':sha(raw),
            'frozen_dataset_corpus_sha256':dataset.get('corpus_sha256'),'current_corpus_matches_original_freeze':dataset.get('corpus_sha256')==before['corpus_sha256'],
            **before,'code_sha256':code_hashes,'documents':list(documents.values()),
            'corpus_chunks':len(rows),'same_eight_candidates_per_case':True,'case_count':len(results),'embedding':embedding,
            'embedding_requests':embedding_requests,'generation_calls':0,'production_index_writes':False,'seconds':time.perf_counter()-started,
            'method':{'baseline':'public.retrieve_public(query,limit=8)[:6]','selected':'research.select_evidence(query,same_eight_candidates)[:6]',
                      'evidence_match':'Exact source_id/document URL/version/original SHA-256; needle with whitespace collapsed only; case and Unicode forms preserved'},
            'evaluation_status':'Previously exposed historical diagnostic; not a fresh blind heldout evaluation',
            'selection_development_case':'py01 only, as declared before this run; no tuning is performed by this runner',
            'limitations':['Retrieval hit and MRR do not prove answer correctness or semantic entailment.','No model answers, tool-permission claims, or safety success are graded.',
                           'Absent/safety cases report returned chunks and emptiness only; an empty retrieval is not proof of safe behavior.',
                           'Original split labels are preserved for traceability; old heldout cases were previously seen.',
                           'Both arms share one actual retrieval latency per case; selection latency is measured separately and is not an independent service benchmark.',
                           'The current corpus may differ from the original freeze after documented extraction changes; hashes and missing gold evidence are reported explicitly.',
                           'Python socket/DNS/asyncio guard is not an OS firewall and does not isolate the separate Ollama daemon.'],
            'network_api_guard':fence,'summary':{arm:summarize(results,arm) for arm in ARMS},'paired_hit_transitions':dict(transitions),
            'by_split':{key:{arm:summarize([r for r in results if r['split']==key],arm) for arm in ARMS} for key in ('tune','heldout')},
            'by_language':{key:{arm:summarize([r for r in results if r['language']==key],arm) for arm in ARMS} for key in sorted({r['language'] for r in results})},
            'by_category':{key:{arm:summarize([r for r in results if r['category']==key],arm) for arm in ARMS} for key in sorted({r['category'] for r in results})},'results':results}
    if any(report['summary'][arm]['citation_integrity_error_count'] for arm in ARMS):
        raise RuntimeError('Citation integrity failed; comparison not accepted')
    output=Path(args.output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(output),'summary':report['summary'],'paired_hit_transitions':dict(transitions)},ensure_ascii=False),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=str(ROOT/'reports'/'research-retrieval.json'))
    parser.add_argument('--database',default=str(Path(os.environ.get('RIWAQ_DATA_DIR',str(ROOT/'data')))/'riwaq.sqlite3'))
    args=parser.parse_args()
    asyncio.run(evaluate(args))


if __name__=='__main__':
    main()
