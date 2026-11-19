"""Paired local real-model diagnostic. No source ingestion, no production writes."""
import asyncio, hashlib, json, os, re, sqlite3, sys, tempfile, time
from contextlib import closing
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from evaluate_knowledge import deny_external_connections, audit_originals
DATASET=ROOT/'evaluation/grounding-v2.json'
DESTINATION=ROOT/'reports/grounding-v2-answers.json'
if DESTINATION.exists():raise SystemExit('Refuse to overwrite previous diagnostic results')
dataset=json.loads(DATASET.read_text(encoding='utf-8'))
assert dataset['status']=='frozen_before_generation' and len(dataset['cases'])==16
normal=lambda value:' '.join(value.casefold().split())

async def main():
    with tempfile.TemporaryDirectory(prefix='riwaq-quality-',dir=os.environ.get('TEMP')) as directory:
        with closing(sqlite3.connect((ROOT/'data/riwaq.sqlite3').as_uri()+'?mode=ro',uri=True)) as source:
            with closing(sqlite3.connect(Path(directory)/'riwaq.sqlite3')) as target:source.backup(target)
        os.environ['RIWAQ_DATA_DIR']=directory
        os.environ['RIWAQ_EMBED_MODEL']='embeddinggemma-2:270m-q8_0-text'
        from backend import app as service, public_knowledge as public, grounding
        from backend.db import get_db
        report={'dataset_sha256':hashlib.sha256(DATASET.read_bytes()).hexdigest(),'protocol':dataset['generation_protocol'],'independence_caveat':dataset['independence_caveat'],'pairs':[],'production_data_modified':False,'transport':'Actual ASGI app and real local provider, NDJSON events buffered by ASGITransport; not a browser or time-to-first-token measurement.',
          'policies':{key:{'text':value,'sha256':hashlib.sha256(value.encode()).hexdigest()} for key,value in grounding.POLICIES.items()}}
        with deny_external_connections() as proof:
            try:await asyncio.get_running_loop().create_connection(asyncio.Protocol,'203.0.113.1',443)
            except OSError:assert proof['external_blocked']==['203.0.113.1']
            else:raise RuntimeError('Network guard did not reject external probe')
            async with service.lifespan(service.app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),base_url='http://127.0.0.1:8765',headers={'X-Riwaq-Client':'local'},timeout=240) as client:
                    async def request(method,path,body=None):
                        response=await client.request(method,'/api'+path,json=body)
                        response.raise_for_status();return response.json()
                    config=await request('GET','/settings')
                    assert config['mode']=='local' and config['provider']=='ollama'
                    report['settings']=config
                    with get_db() as conn:
                        rows=public.corpus_rows(conn)
                        report['corpus_sha256']=hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                        report['replay']=audit_originals(conn,rows,public)
                    for case in dataset['cases']:
                        for evidence in case['evidence']:
                            assert any(row['source_id']==evidence['source_id'] and row['sha256']==evidence['original_sha256'] and normal(evidence['needle']) in normal(row['text']) for row in rows),case['id']
                    project=await request('POST','/projects',{'name':'Grounding policy paired diagnostic','instructions':''})
                    original_retrieve=public.retrieve_public
                    for index,case in enumerate(dataset['cases']):
                        retrieved=await original_retrieve(case['query'],limit=5)
                        async def frozen_retrieve(*args,**kwargs):return [dict(row) for row in retrieved]
                        public.retrieve_public=frozen_retrieve
                        pair={'id':case['id'],'query':case['query'],'gold_answer':case['gold_answer'],'category':case['category'],'language':case['language'],'retrieved_ids':[row['id'] for row in retrieved],'outputs':{}}
                        policies=['legacy-v1','concise-v2'] if index%2==0 else ['concise-v2','legacy-v1']
                        for policy in policies:
                            os.environ['RIWAQ_GROUNDED_POLICY']=policy
                            conv=await request('POST','/conversations',{'project_id':project['id'],'title':case['id']+' '+policy})
                            started=time.perf_counter()
                            response=await client.post('/api/conversations/'+conv['id']+'/generate',json={'content':case['query'],'use_knowledge':True})
                            response.raise_for_status()
                            events=[json.loads(line) for line in response.text.splitlines() if line]
                            assert events[-1]['type']=='done'
                            answer=events[-1]['message']
                            saved=(await request('GET','/conversations/'+conv['id']))['messages'][-1]
                            markers=[int(n) for n in re.findall(r'\[S(\d+)\]',answer['content'])]
                            valid=[n for n in markers if 1<=n<=len(answer['citations'])]
                            gold=any(citation.get('source_id')==e['source_id'] and citation.get('sha256')==e['original_sha256'] and normal(e['needle']) in normal(citation['text']) for n in valid for citation in [answer['citations'][n-1]] for e in case['evidence'])
                            pair['outputs'][policy]={'answer':answer['content'],'status':answer['status'],'citations':answer['citations'],'recognized_markers':markers,'has_valid_marker':bool(valid),'no_invalid_markers':len(valid)==len(markers) and '[unverified citation]' not in answer['content'],'cites_gold_span':gold,'restored_equal':saved==answer,'seconds':time.perf_counter()-started,'metrics':events[-1].get('metrics',{}),'notices':[event['message'] for event in events if event['type']=='error']}
                        public.retrieve_public=original_retrieve
                        report['pairs'].append(pair)
                        DESTINATION.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                        print(json.dumps({'case':case['id'],'results':{key:{'state':value['status'],'gold':value['cites_gold_span'],'seconds':round(value['seconds'],2)} for key,value in pair['outputs'].items()}}),flush=True)
                    assert await request('GET','/settings')==config
                    report['settings_unchanged']=True
                    # Independent real-model cutoff proof on this temporary database.
                    await request('PATCH','/settings',{'max_tokens':32})
                    conv=await request('POST','/conversations',{'project_id':project['id'],'title':'Actual output cutoff'})
                    response=await client.post('/api/conversations/'+conv['id']+'/generate',json={'content':'اكتب ثلاثين فقرة مطولة عن تاريخ البرمجة، مع أمثلة وتفاصيل في كل فقرة.'})
                    response.raise_for_status()
                    events=[json.loads(line) for line in response.text.splitlines() if line]
                    answer=events[-1]['message']
                    restored=(await request('GET','/conversations/'+conv['id']))['messages'][-1]
                    report['real_cutoff']={'status':answer['status'],'answer':answer['content'],'metrics':events[-1].get('metrics',{}),'restored_equal':answer==restored}
                    assert answer['status']=='interrupted' and answer==restored,report['real_cutoff']
            report['network_guard']=proof
        report['summary']={'pairs':len(report['pairs']),'answers':sum(len(pair['outputs']) for pair in report['pairs']),'persistence_failures':sum(not output['restored_equal'] for pair in report['pairs'] for output in pair['outputs'].values()),'quality':'Requires blinded engineering review; marker metrics are not answer accuracy.'}
        DESTINATION.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        blinded=[]
        for pair in report['pairs']:
            order=['legacy-v1','concise-v2'] if int(hashlib.sha256(pair['id'].encode()).hexdigest(),16)%2==0 else ['concise-v2','legacy-v1']
            blinded.append({key:pair[key] for key in ('id','query','gold_answer','category','language')}|{'A':pair['outputs'][order[0]],'B':pair['outputs'][order[1]]})
        (ROOT/'reports/grounding-v2-blinded.json').write_text(json.dumps({'reviewer_notice':dataset['independence_caveat'],'cases':blinded},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report['summary']),flush=True)
if __name__=='__main__':asyncio.run(main())
