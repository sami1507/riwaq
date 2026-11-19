"""Actual Riwaq API evaluation with an isolated DB and loopback-only network.
Schema contains types/field names only, never reference answers. Heldout evaluated once.
"""
import asyncio,ast,hashlib,json,os,sys,tempfile,time,math
from decimal import Decimal
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from evaluate_knowledge import deny_external_connections
DATA=ROOT/'training/data-v3'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(t,p)
def unique(pairs):
 d={}
 for k,v in pairs:
  if k in d:raise ValueError('duplicate key')
  d[k]=v
 return d
def parse(text):
 def reject(x):raise ValueError('nonfinite')
 return json.loads(text,parse_int=Decimal,parse_float=Decimal,parse_constant=reject,object_pairs_hook=unique)
def equal(a,b,key=''):
 if b is None:return a is None
 if type(b) is bool:return type(a) is bool and a==b
 if type(b) in (int,float):return isinstance(a,Decimal) and a==Decimal(str(b))
 if isinstance(b,str):
  if key=='expression' and isinstance(a,str):
   try:return ast.dump(ast.parse(a,mode='eval'))==ast.dump(ast.parse(b,mode='eval'))
   except (SyntaxError,ValueError):return False
  return type(a) is str and a==b
 if isinstance(b,list):return isinstance(a,list) and len(a)==len(b) and all(equal(x,y,key) for x,y in zip(a,b))
 if isinstance(b,dict):return isinstance(a,dict) and set(a)==set(b) and all(equal(a[k],v,k) for k,v in b.items())
 return False
def grade(case,message):
 output={'json':False,'exact':False,'citation_valid':None,'source_coverage':None,'failure':None}
 try:v=parse(message['content']);output['json']=isinstance(v,dict)
 except (ValueError,RecursionError):output['failure']='output_format';return output
 expected=case['expected'];output['exact']=equal(v,expected)
 if case['source']:
  cites=message.get('citations',[]);output['citation_valid']=isinstance(v,dict) and v.get('sources')==['[S1]'] and len(cites)==1
  output['source_coverage']=len(cites)==1 and cites[0].get('text')==case['source'] and cites[0].get('line_start')==1 and cites[0].get('line_end')==len(case['source'].splitlines())
  output['exact'] &= bool(output['citation_valid'] and output['source_coverage'])
 output['exact'] &= message.get('status')=='complete'
 if not output['exact']:output['failure']='citation_serialization' if output['citation_valid'] is False else 'retrieval' if output['source_coverage'] is False else 'value_or_constraint_or_shape'
 return output
async def main(phase):
 if phase not in ['baseline','projection','contract','final']:raise SystemExit('baseline / projection / contract / final')
 manifest=json.loads((DATA/'manifest.json').read_text(encoding='utf-8'));split='test' if phase=='final' else 'validation';path=DATA/(split+'.jsonl')
 assert sha(path)==manifest['splits'][split]['sha256']
 cases=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
 report_path=ROOT/f'reports/training-v3-{phase}.json'
 source_hashes={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'backend').glob('*.py')}
 report=json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else None
 if report:
  if report['status']=='complete':raise SystemExit('Report complete; refusing overwrite')
  assert report['source_hashes']==source_hashes and report['runner_sha256']==sha(__file__),'Changed engine/runner cannot resume'
 if phase=='final':
  selection=ROOT/'reports/training-v3-selection.json';assert selection.exists(),'Freeze development selection first'
  selected=json.loads(selection.read_text(encoding='utf-8'))['selected_arm'];arms=['baseline',selected] if selected!='baseline' else ['baseline']
 else:arms=[phase]
 report=report or {'version':'training-v3-eval','phase':phase,'split':split,'status':'running','dataset_sha256':sha(path),'manifest_sha256':sha(DATA/'manifest.json'),'source_hashes':source_hashes,'runner_sha256':sha(__file__),'attempts':[],'paid_cost_usd':0,'production_data_modified':False,'first_token_seconds':None,'timing_note':'ASGI buffers transport; seconds are full request wall time, not streaming first-token.'}
 with tempfile.TemporaryDirectory(prefix='riwaq-sft-eval-',dir='D:/tmp') as directory:
  os.environ['RIWAQ_DATA_DIR']=directory
  from backend import app as service
  from backend.db import get_db
  with deny_external_connections() as proof:
   async with httpx.AsyncClient(trust_env=False) as local:
    report['models']=(await local.get('http://127.0.0.1:11434/api/tags')).json()
   async with service.lifespan(service.app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),base_url='http://127.0.0.1:8765',headers={'X-Riwaq-Client':'local'},timeout=240) as client:
     settings={'model':'qwen3:4b-instruct','provider':'ollama','mode':'local','context_size':8192,'max_tokens':512,'temperature':0.3,'seed':73}
     (await client.patch('/api/settings',json=settings)).raise_for_status();report['settings']=settings
     done={(r['case_id'],r['arm']) for r in report['attempts']}
     for case in cases:
      for arm in arms if int(case['id'][-3:])%2==0 else reversed(arms):
       if (case['id'],arm) in done:continue
       project=(await client.post('/api/projects',json={'name':case['id'],'instructions':''})).json()['id']
       if case['source']:
        response=await client.post(f'/api/projects/{project}/files',files={'file':(case['source_name'],case['source'].encode(),'text/plain')});response.raise_for_status()
       conversation=(await client.post('/api/conversations',json={'project_id':project,'title':case['id']})).json()['id']
       os.environ['RIWAQ_REASONING_VARIANT']='tools';os.environ['RIWAQ_TOOL_CONTEXT_VERSION']='raw-v1' if arm=='baseline' else 'semantic-v2'
       body={'content':case['prompt'],'depth':'analytical','use_files':bool(case['source'])}
       if arm=='contract':body['output_contract']=case['output_contract']
       started=time.perf_counter();response=await client.post(f'/api/conversations/{conversation}/generate',json=body);seconds=time.perf_counter()-started
       events=[json.loads(line) for line in response.text.splitlines()] if response.status_code==200 else []
       final=next((e['message'] for e in reversed(events) if e['type']=='done'),{'content':'','status':'error','citations':[]})
       restored=(await client.get(f'/api/conversations/{conversation}')).json()['messages']
       row={'case_id':case['id'],'family':case['family'],'category':case['category'],'arm':arm,'seconds':round(seconds,5),'status':response.status_code,'request':body,'message':final,'grade':grade(case,final),'restored_equal':bool(restored and restored[-1]==final)}
       if response.status_code!=200:row['error']=response.text[:2000]
       report['attempts'].append(row);report['network_guard']=dict(proof);save(report_path,report)
       print(json.dumps({'id':case['id'],'arm':arm,'pass':row['grade']['exact'],'seconds':row['seconds']},ensure_ascii=True),flush=True)
   report['network_guard']=dict(proof)
 report['summary']={}
 for arm in arms:
  rows=[r for r in report['attempts'] if r['arm']==arm];times=sorted(r['seconds'] for r in rows)
  report['summary'][arm]={'correct':sum(r['grade']['exact'] for r in rows),'n':len(rows),'json':sum(r['grade']['json'] for r in rows),'restored':sum(r['restored_equal'] for r in rows),'p95_seconds':times[math.ceil(len(times)*.95)-1],'by_family':{f:{'correct':sum(r['grade']['exact'] for r in rows if r['family']==f),'n':sum(r['family']==f for r in rows)} for f in sorted({r['family'] for r in rows})}}
 report['status']='complete';save(report_path,report);print(json.dumps(report['summary'],ensure_ascii=True),flush=True)
if __name__=='__main__':asyncio.run(main(sys.argv[1]))
