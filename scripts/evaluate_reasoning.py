"""Frozen, offline-local real-model evaluation. Never executes model-generated code."""
import asyncio,ast,hashlib,json,math,os,re,sys,tempfile,time
from decimal import Decimal
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from evaluate_knowledge import deny_external_connections
DATA=ROOT/'evaluation/reasoning-v1.json';FREEZE=ROOT/'evaluation/reasoning-v1.freeze.json'
D=json.loads(DATA.read_text(encoding='utf-8'));F=json.loads(FREEZE.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
assert sha(DATA)==F['dataset_sha256']
VARIANTS=['baseline','instructions','tools','review'];ORDERS=[['baseline','instructions','review','tools'],['instructions','tools','baseline','review'],['tools','review','instructions','baseline'],['review','baseline','tools','instructions']]
SOURCE_FILES=['backend/app.py','backend/reasoning.py','backend/check_worker.py','backend/reasoning_limits.json','backend/grounding.py','backend/providers.py','backend/knowledge.py','backend/db.py']
SELECTION=ROOT/'reports/reasoning-selection.json'
PROTOCOL={'version':'reasoning-grader-v1','format':'Strict one JSON object; duplicate keys/nonfinite/fences/prose rejected. Exact recursive shape/type/value; numeric Decimal equality. Code replacement expression AST normalization only. No generated code execution.','citations':'Every cited chunk must match frozen file hash/name/full text/line span/project and expected fixture. All required fixtures and needles covered. Whole-file one-chunk preflight.','selection':'repeat0 dev30: correct desc, lost baseline-correct cases asc, model calls asc, output tokens asc, nearest-rank p95 asc, instructions/tools/review tie order. Gain>=2; p95<=90; no integrity failures. If none eligible retain baseline, run top candidate heldout as diagnostic only.','schedule':'Williams4-arm dev; alternating2-arm heldout; analytical depth all closed; research two open. Repeat three preselected dev IDs total3 per arm, plus installed VL baseline3 repetitions each. Fixed seed37; no oracle calculation/hypothesis fields.','open_review':'Two science tasks require five-item engineering inspection, excluded from automatic denominator.','first_token_timing':'Unavailable: buffered ASGITransport. Wall end-to-end only.'}
def unique(pairs):
 result={}
 for k,v in pairs:
  if k in result:raise ValueError('DUPLICATE_KEY:'+k)
  result[k]=v
 return result
def strict_json(text):
 def reject(v):raise ValueError('NONFINITE:'+v)
 result=json.loads(text.strip(),parse_int=Decimal,parse_float=Decimal,parse_constant=reject,object_pairs_hook=unique)
 if not isinstance(result,dict):raise ValueError('NOT_OBJECT')
 return result
def same(actual,expected,path=''):
 if path=='replacement' and isinstance(actual,str) and isinstance(expected,str):
  try:return len(actual)<16384 and ast.dump(ast.parse(actual,mode='eval'),include_attributes=False)==ast.dump(ast.parse(expected,mode='eval'),include_attributes=False)
  except (SyntaxError,ValueError,RecursionError):return False
 if expected is None:return actual is None
 if isinstance(expected,bool):return type(actual) is bool and actual==expected
 if isinstance(expected,(int,float,Decimal)):return isinstance(actual,Decimal) and actual.is_finite() and actual==Decimal(str(expected))
 if isinstance(expected,str):return isinstance(actual,str) and actual==expected
 if isinstance(expected,list):return isinstance(actual,list) and len(actual)==len(expected) and all(same(a,e,path) for a,e in zip(actual,expected))
 if isinstance(expected,dict):return isinstance(actual,dict) and set(actual)==set(expected) and all(same(actual[k],v,k) for k,v in expected.items())
 return False

def grade(case,message,bindings):
 errors=[];result={'format_valid':False,'expected_match':None,'citation_valid':None,'evidence_covered':None,'structured_pass':False,'review_required':case['expected'] is None,'errors':errors}
 try:value=strict_json(message.get('content',''))
 except (ValueError,RecursionError) as error:errors.append('JSON_FORMAT:'+str(error)[:180]);return result
 result['format_valid']=True
 if case['expected'] is None:
  keys={'hypotheses','discriminating_test','prediction_h1','prediction_h2','limitation'}
  shape=set(value)==keys and isinstance(value.get('hypotheses'),list) and len(value['hypotheses'])==2 and all(isinstance(x,str) and x.strip() for x in value['hypotheses']) and all(isinstance(value.get(k),str) and value[k].strip() for k in keys-{'hypotheses'})
  result['open_shape_valid']=bool(shape);errors.append('REVIEW_PENDING');return result
 expected=case['expected'];facts=dict(value)
 if case['category']=='retrieval':
  sources=facts.pop('sources',None);citations=message.get('citations',[]);found=set();texts={};valid=isinstance(sources,list) and bool(sources)
  if valid:
   for marker in sources:
    match=re.fullmatch(r'\[S([1-9][0-9]{0,5})\]',marker) if isinstance(marker,str) else None
    if not match or not 1<=int(match[1])<=len(citations):valid=False;errors.append('CITATION_INVALID');continue
    citation=citations[int(match[1])-1];binding=bindings.get(citation.get('id'))
    if not binding or citation.get('scope')=='public':valid=False;errors.append('CITATION_SCOPE');continue
    chunk=binding['chunk'];fixture=binding['fixture'];fid=fixture['id']
    if any(citation.get(k)!=chunk.get(k) for k in ['id','file_id','project_id','line_start','line_end','text']) or citation.get('filename')!=fixture['name']:
     valid=False;errors.append('CITATION_SPAN_OR_TEXT');continue
    if fid not in case['fixture_ids']:valid=False;errors.append('UNRELATED_CITATION')
    found.add(fid);texts[fid]=chunk['text']
  covered=set(case['fixture_ids'])<=found and all(needle in texts.get(fid,'') for fid,needle in zip(case['fixture_ids'],case['evidence_needles']))
  result['citation_valid']=bool(valid);result['evidence_covered']=bool(covered)
  if not covered:errors.append('MISSING_EVIDENCE')
 result['expected_match']=same(facts,expected)
 if not result['expected_match']:errors.append('VALUE_TYPE_OR_SHAPE')
 result['structured_pass']=bool(result['expected_match'] and (case['category']!='retrieval' or result['citation_valid'] and result['evidence_covered']) and message.get('status')=='complete')
 if message.get('status')!='complete':errors.append('INCOMPLETE')
 return result

def p95(values):return sorted(values)[math.ceil(.95*len(values))-1] if values else None

def summaries(attempts):
 result={}
 for variant in sorted({r['variant'] for r in attempts}):
  rows=[r for r in attempts if r['variant']==variant and r['repeat']==0];closed=[r for r in rows if not r['grade']['review_required']]
  result[variant]={'correct':sum(r['grade']['structured_pass'] for r in closed),'closed_tasks':len(closed),'review_tasks':len(rows)-len(closed),'format_failures':sum(not r['grade']['format_valid'] for r in rows),'complete':sum(r['completion_status']=='complete' for r in rows),'p95_seconds':p95([r['seconds'] for r in rows]),'median_seconds':sorted(r['seconds'] for r in rows)[len(rows)//2] if rows else None,'model_calls':sum(r.get('run_receipt',{}).get('model_calls',0) for r in rows),'output_tokens':sum(r.get('run_receipt',{}).get('actual_output_tokens',0) for r in rows),'by_category':{c:{'correct':sum(r['grade']['structured_pass'] for r in closed if r['category']==c),'n':sum(r['category']==c for r in closed)} for c in {r['category'] for r in rows}}}
 return result

def save(path,value):
 temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temp,path)

def select():
 if SELECTION.exists():raise SystemExit('Selection already frozen')
 report=json.loads((ROOT/'reports/reasoning-dev.json').read_text(encoding='utf-8'));assert report['status']=='complete'
 scores=report['summary'];base={r['task_id']:r['grade']['structured_pass'] for r in report['attempts'] if r['variant']=='baseline' and r['repeat']==0}
 for v in VARIANTS[1:]:
  rows=[r for r in report['attempts'] if r['variant']==v and r['repeat']==0];scores[v]['lost']=sum(base[r['task_id']] and not r['grade']['structured_pass'] for r in rows);scores[v]['gained']=sum(not base[r['task_id']] and r['grade']['structured_pass'] for r in rows)
 ranked=sorted(VARIANTS[1:],key=lambda v:(-scores[v]['correct'],scores[v]['lost'],scores[v]['model_calls'],scores[v]['output_tokens'],scores[v]['p95_seconds'],VARIANTS.index(v)))
 chosen=ranked[0];eligible=scores[chosen]['correct']>=scores['baseline']['correct']+2 and scores[chosen]['p95_seconds']<=90 and all(r['restored_equal'] for r in report['attempts'])
 data={'selected_variant':chosen,'dev_gate':eligible,'adoption':'candidate_pending_heldout' if eligible else 'retain_baseline','scores':scores,'protocol':PROTOCOL,'dev_sha256':sha(ROOT/'reports/reasoning-dev.json'),'source_hashes':report['source_hashes'],'runner_sha256':sha(__file__),'dataset_sha256':sha(DATA),'date':'2026-11-13'};save(SELECTION,data);print(json.dumps(data,ensure_ascii=True))

async def main(phase):
 if phase=='select':select();return
 if phase not in {'dev','heldout','repeat'}:raise SystemExit('Use dev/select/heldout/repeat')
 selection=json.loads(SELECTION.read_text(encoding='utf-8')) if phase in {'heldout','repeat'} else None
 current_hashes={p:sha(ROOT/p) for p in SOURCE_FILES}
 if selection:assert selection['source_hashes']==current_hashes and selection['runner_sha256']==sha(__file__),'Frozen implementation changed'
 path=ROOT/f'reports/reasoning-{phase}.json';report=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
 if report:
  assert report['source_hashes']==current_hashes and report['runner_sha256']==sha(__file__),'Cannot resume changed implementation'
  if report['status']=='complete':raise SystemExit('Phase already complete; refusing repeat/overwrite')
 cases=[c for c in D['cases'] if c['split']==('heldout' if phase=='heldout' else 'dev')]
 schedule=[]
 for i,case in enumerate(cases):
  variants=ORDERS[i%4] if phase=='dev' else (['baseline',selection['selected_variant']] if i%2==0 else [selection['selected_variant'],'baseline'])
  if phase!='repeat':schedule.extend((case,v,0,D['model']) for v in variants)
 if phase=='repeat':
  for case in cases:
   if case['id'] not in {'M-D01','C-D01','R-D01'}:continue
   schedule.extend((case,v,repeat,D['model']) for repeat in [1,2] for v in VARIANTS)
   schedule.extend((case,'baseline',repeat,'qwen3-vl:2b-instruct') for repeat in [0,1,2])
 report=report or {'schema_version':'reasoning-evaluation-v1','phase':phase,'date':'2026-11-13','status':'running','protocol':PROTOCOL,'dataset_sha256':sha(DATA),'source_hashes':current_hashes,'runner_sha256':sha(__file__),'selection_sha256':sha(SELECTION) if selection else None,'attempts':[],'first_token_seconds':None,'paid_cost_usd':0,'production_data_modified':False,'fixture_batches':[]}
 with tempfile.TemporaryDirectory(prefix='riwaq-reasoning-eval-',dir='D:/tmp') as directory:
  os.environ['RIWAQ_DATA_DIR']=directory
  from backend import app as service,providers
  from backend.db import get_db
  with deny_external_connections() as proof:
   async with httpx.AsyncClient(timeout=30) as ollama:
    report['ollama_version']=(await ollama.get('http://127.0.0.1:11434/api/version')).json();report['models']=(await ollama.get('http://127.0.0.1:11434/api/tags')).json()
   async with service.lifespan(service.app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),base_url='http://127.0.0.1:8765',headers={'X-Riwaq-Client':'local'},timeout=240) as client:
     response=await client.patch('/api/settings',json={k:D[k] for k in ['model','context_size','max_tokens','temperature','seed']}|{'mode':'local','provider':'ollama'});response.raise_for_status();report['settings']=response.json()
     clean=(await client.post('/api/projects',json={'name':'Evaluation only - no sources','instructions':''})).json()['id'];rag=(await client.post('/api/projects',json={'name':'Evaluation fixtures only','instructions':''})).json()['id'];bindings={}
     with get_db() as conn:assert conn.execute('SELECT count(*) FROM memories').fetchone()[0]==0
     for fixture in D['fixtures']:
      assert hashlib.sha256(fixture['text'].encode()).hexdigest()==fixture['sha256']
      response=await client.post(f'/api/projects/{rag}/files',files={'file':(fixture['name'],fixture['text'].encode(),'text/plain')});response.raise_for_status();file=response.json()
      with get_db() as conn:
       chunks=[dict(r) for r in conn.execute('SELECT * FROM knowledge_chunks WHERE file_id=?',(file['id'],))];stored=dict(conn.execute('SELECT * FROM knowledge_files WHERE id=?',(file['id'],)).fetchone())
      assert len(chunks)==1 and chunks[0]['text']==fixture['text'] and chunks[0]['line_start']==1 and chunks[0]['line_end']==len(fixture['text'].splitlines()) and stored['sha256']==fixture['sha256']
      bindings[chunks[0]['id']]={'fixture':fixture,'chunk':chunks[0],'file':stored}
     report['fixture_batches'].append(bindings);save(path,report)
     done={r['attempt_id'] for r in report['attempts']}
     for order,(case,variant,repeat,model) in enumerate(schedule):
      attempt_id=f'{case["id"]}:{variant}:{model}:{repeat}'
      if attempt_id in done:continue
      os.environ['RIWAQ_REASONING_VARIANT']=variant
      conversation=(await client.post('/api/conversations',json={'project_id':rag if case['category']=='retrieval' else clean,'title':attempt_id[:100]})).json()['id']
      body={'content':case['prompt'],'provider':'ollama','model':model,'depth':'research' if case['expected'] is None else 'analytical','use_files':case['category']=='retrieval','use_knowledge':False}
      started=time.perf_counter();response=await client.post(f'/api/conversations/{conversation}/generate',json=body);duration=time.perf_counter()-started
      events=[json.loads(line) for line in response.text.splitlines()] if response.status_code==200 else []
      final=next((e['message'] for e in reversed(events) if e['type']=='done'),{'content':'','status':'error','citations':[]});restored=(await client.get(f'/api/conversations/{conversation}')).json()['messages'];equal=bool(restored and restored[-1]==final)
      initial=''.join(e.get('content','') for e in events if e['type']=='delta')
      row={'attempt_id':attempt_id,'task_id':case['id'],'split':case['split'],'category':case['category'],'family':case['family'],'language':case['language'],'variant':variant,'repeat':repeat,'model':model,'order_index':order,'prompt_sha256':hashlib.sha256(case['prompt'].encode()).hexdigest(),'request_body':body,'http_status':response.status_code,'seconds':round(duration,5),'completion_status':final['status'],'initial_output':initial,'final_output':final['content'],'final_saved_message':final,'run_receipt':final.get('run',{}),'events':events,'grade':grade(case,final,bindings),'initial_grade':grade(case,{**final,'content':initial},bindings),'restored_equal':equal}
      if response.status_code!=200:row['error']=response.text[:3000]
      report['attempts'].append(row);report['network_guard']=dict(proof);report['summary']=summaries(report['attempts']);save(path,report)
      print(json.dumps({'attempt':attempt_id,'pass':row['grade']['structured_pass'],'status':final['status'],'seconds':row['seconds'],'errors':row['grade']['errors']},ensure_ascii=True),flush=True)
   report['network_guard']=proof
 report['status']='complete';report['summary']=summaries(report['attempts']);save(path,report);print(json.dumps({'phase':phase,'summary':report['summary']},ensure_ascii=True),flush=True)

if __name__=='__main__':asyncio.run(main(sys.argv[1] if len(sys.argv)>1 else 'dev'))
