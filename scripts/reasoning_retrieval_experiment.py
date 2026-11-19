"""Small explicit hypothesis experiment. Synthetic fixtures only; no production mutation."""
import hashlib,json,sqlite3,statistics,sys,time,tracemalloc
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend import knowledge
D=json.loads((ROOT/'evaluation/reasoning-v1.json').read_text(encoding='utf-8'))
conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
conn.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)');conn.execute("INSERT INTO projects VALUES ('evaluation')");knowledge.init_knowledge(conn)
for i,f in enumerate(D['fixtures']):
 conn.execute('INSERT INTO knowledge_files VALUES (?,?,?,?,?,?,?,?,?)',(f['id'],'evaluation',f['name'],len(f['text'].encode()),f['sha256'],1,0,len(f['text']),'2026-11-13'))
 conn.execute('INSERT INTO knowledge_chunks VALUES (?,?,?,?,?,?,?)',('chunk-'+f['id'],f['id'],'evaluation',None,1,len(f['text'].splitlines()),f['text']))
result={'hypothesis':'Returning3 rather than5 BM25 chunks reduces context while preserving required evidence.','prediction':'All required fixture files still retrieved, fewer returned characters.','refutation':'Any required evidence lost, or no context reduction.','cost':'No additional model call; same full scan and sort.','classification':'measured synthetic retrieval experiment','not_blind':'These task families were already evaluated for generation; this is a mechanism check, not new independent validation.','changes_applied_to_production':False,'cases':[]}
for split in ['dev','heldout']:
 for case in [c for c in D['cases'] if c['split']==split and c['category']=='retrieval']:
  entry={'id':case['id'],'split':split,'arms':{}}
  for limit in [5,3]:
   samples=[];tracemalloc.start()
   for repeat in range(30):
    start=time.perf_counter();chunks=knowledge.retrieve(conn,'evaluation',case['prompt'],limit=limit);samples.append(time.perf_counter()-start)
   _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
   entry['arms'][str(limit)]={'required_evidence_retrieved':set(case['fixture_ids'])<={c['file_id'] for c in chunks},'chunks':len(chunks),'characters':sum(len(c['text']) for c in chunks),'median_seconds':statistics.median(samples),'peak_python_bytes':peak}
  result['cases'].append(entry)
result['summary']={split:{str(limit):{'success':sum(r['arms'][str(limit)]['required_evidence_retrieved'] for r in result['cases'] if r['split']==split),'n':sum(r['split']==split for r in result['cases']),'characters':sum(r['arms'][str(limit)]['characters'] for r in result['cases'] if r['split']==split),'median_seconds':statistics.median(r['arms'][str(limit)]['median_seconds'] for r in result['cases'] if r['split']==split)} for limit in [5,3]} for split in ['dev','heldout']}
result['decision']='Keep default5. This small synthetic mechanism check does not establish production answer accuracy; no automatic parameter rewrite.'
(ROOT/'reports/reasoning-retrieval-experiment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result['summary']))
