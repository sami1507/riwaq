"""Actual matched-runtime LoRA evaluation; no test split is opened here."""
import argparse,hashlib,json,math,os,subprocess,sys,threading,time
from pathlib import Path
import psutil
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from training.scoring import parse,equal
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):
 tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');tmp.replace(p)
def require(ok,message):
 if not ok:raise ValueError(message)
def main():
 ap=argparse.ArgumentParser()
 for name in ['python','base','run','data','manifest-sha','retention','output']:ap.add_argument('--'+name,required=True)
 a=ap.parse_args();out=Path(a.output).resolve();require(not out.exists(),'Refusing existing output');out.mkdir(parents=True)
 data=Path(a.data);manifest=json.loads((data/'manifest.json').read_text(encoding='utf-8'))
 require(sha(data/'manifest.json')==a.manifest_sha and manifest['version']=='sft-original-v3','Manifest mismatch')
 vp=data/'validation.jsonl';require(sha(vp)==manifest['splits']['validation']['sha256'],'Validation changed')
 cases=[json.loads(x) for x in vp.read_text(encoding='utf-8').splitlines()];require(len(cases)==44 and all(x['split']=='validation' for x in cases),'Expected 44 validation rows')
 retention=json.loads(Path(a.retention).read_text(encoding='utf-8-sig'));require(isinstance(retention,list) and len(retention)==6,'Freeze six retention probes')
 run=json.loads((Path(a.run)/'run.json').read_text(encoding='utf-8'));require(run['status']=='trained_candidate_pending_quality_gate' and run['manifest_sha256']==a.manifest_sha,'No qualified training candidate')
 adapter=Path(run['candidate_checkpoint']).resolve();require(adapter.parent==Path(a.run).resolve(),'Candidate outside frozen run')
 am=json.loads((adapter/'runtime_manifest.json').read_text(encoding='utf-8'))
 require(am['base_report_sha256']==sha(Path(a.base)/'reconstruction.json')==run['base_report_sha256'],'Base mismatch')
 require(am['manifest_sha256']==a.manifest_sha and am['device_recipe']=='cuda','Adapter recipe mismatch')
 requests=[]
 for case in cases:
  require([m['role'] for m in case['messages']]==['system','user','assistant'],'Message schema');messages=case['messages'][:-1]
  require(not case['source'] or case['source'] in messages[-1]['content'],'Source missing');requests.append({'id':case['id'],'messages':messages,'max_new_tokens':256})
 for item in retention:
  require(set(item)=={'id','messages','rubric'} and item['messages'][-1]['role']=='user','Retention schema');requests.append({'id':item['id'],'messages':item['messages'],'max_new_tokens':256})
 require(len({x['id'] for x in requests})==50,'Duplicate IDs');payload=''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in requests)
 files=list((ROOT/'training').glob('runtime_*.py'))+[Path(__file__),ROOT/'training/scoring.py'];frozen={str(p):sha(p) for p in files}
 report={'version':'lora-validation-v1','status':'running','manifest_sha256':a.manifest_sha,'validation_sha256':sha(vp),'retention_sha256':sha(a.retention),'run_sha256':sha(Path(a.run)/'run.json'),'adapter_manifest_sha256':sha(adapter/'runtime_manifest.json'),'source_hashes':frozen,'comparison':'same reconstructed BF16 base, NF4 double quant, nonquantized FP32, greedy; base first then adapter','heldout_scope':'Training and this generation evaluation do not read test rows; data audit may have read them.','citation_scope':'Exact source marker serialization with supplied excerpts, not API retrieval/metadata/persistence','network_scope':'Runtime offline flags and local files; not OS network isolation','arms':{},'retention_review':'pending_engineering_inspection','selected_for_deployment':False,'gate':{'minimum_net_gain':2,'maximum_lost_correct':1,'retention_regressions':0}}
 save(out/'report.json',report);deadline=time.monotonic()+1800
 try:
  for arm,apath in [('base',None),('adapter',adapter)]:
   require({str(p):sha(p) for p in files}==frozen,'Runtime/grader changed')
   cmd=[a.python,'-X','utf8','-B',str(ROOT/'training/runtime_infer.py'),'--base',str(Path(a.base).resolve()),'--device','cuda','--max-requests','50']
   if apath:cmd+=['--adapter',str(apath)]
   env=os.environ.copy();env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',PYTHONIOENCODING='utf-8')
   started=time.monotonic();stopped=threading.Event();resources={'peak_rss_bytes':0,'abort_reason':None}
   proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',cwd=ROOT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
   def watch():
    ps=psutil.Process(proc.pid)
    while not stopped.wait(.25):
     try:resources['peak_rss_bytes']=max(resources['peak_rss_bytes'],ps.memory_info().rss)
     except psutil.NoSuchProcess:return
     reason='rss_limit' if resources['peak_rss_bytes']>12*1024**3 else 'total_wall_limit' if time.monotonic()>deadline else None
     if reason:
      resources['abort_reason']=reason
      try:proc.kill()
      except OSError:pass
      return
   watcher=threading.Thread(target=watch,daemon=True);watcher.start();stdout=stderr=''
   try:stdout,stderr=proc.communicate(payload,timeout=max(1,deadline-time.monotonic()))
   except subprocess.TimeoutExpired:resources['abort_reason']='total_wall_limit';proc.kill();stdout,stderr=proc.communicate()
   finally:
    stopped.set();watcher.join(timeout=1)
    if proc.poll() is None:proc.kill();proc.wait(timeout=10)
    (out/(arm+'.stdout.jsonl')).write_text(stdout,encoding='utf-8');(out/(arm+'.stderr.log')).write_text(stderr,encoding='utf-8')
   require(proc.returncode==0 and resources['abort_reason'] is None,'Worker failed: '+str(resources))
   outputs=[json.loads(line) for line in stdout.splitlines() if line.strip()];require([x['id'] for x in outputs]==[x['id'] for x in requests],'Missing/reordered outputs')
   require(all(type(x.get('finished')) is bool and isinstance(x.get('content'),str) for x in outputs),'Invalid envelope')
   if arm=='adapter':require(all(x.get('adapter_reload_verified') is True for x in outputs),'Reload verification failed')
   graded=[]
   for case,result in zip(cases,outputs[:44]):
    try:parsed=parse(result['content']);valid_json=isinstance(parsed,dict);exact=bool(result['finished'] and valid_json and equal(parsed,case['expected']))
    except (ValueError,TypeError,RecursionError):valid_json=exact=False
    graded.append({'id':case['id'],'family':case['family'],'json':valid_json,'exact':exact,'response':result})
   times=sorted(float(x['seconds']) for x in outputs[:44]);require(all(math.isfinite(x) and x>=0 for x in times),'Invalid timing')
   report['arms'][arm]={'validation':graded,'retention':[{'id':item['id'],'rubric':item['rubric'],'response':result,'review':None} for item,result in zip(retention,outputs[44:])],'correct':sum(x['exact'] for x in graded),'json':sum(x['json'] for x in graded),'n':44,'p95_generation_seconds':times[math.ceil(.95*len(times))-1],'wall_seconds_including_load':time.monotonic()-started,**resources}
   save(out/'report.json',report);print(json.dumps({'arm':arm,'correct':report['arms'][arm]['correct'],'n':44}),flush=True)
  require({str(p):sha(p) for p in files}==frozen,'Runtime/grader changed during evaluation')
  b=report['arms']['base']['validation'];t=report['arms']['adapter']['validation'];gain=sum(x['exact'] for x in t)-sum(x['exact'] for x in b)
  lost=[x['id'] for x,y in zip(b,t) if x['exact'] and not y['exact']];gained=[x['id'] for x,y in zip(b,t) if not x['exact'] and y['exact']]
  report['comparison_result']={'net_gain':gain,'lost_correct':lost,'new_correct':gained,'validation_gate_pass':gain>=2 and len(lost)<=1,'required_net_gain':2,'maximum_lost_correct':1};report['status']='complete_pending_retention_review' if gain>=2 and len(lost)<=1 else 'complete_keep_base';save(out/'report.json',report)
 except BaseException as exc:report['status']='failed';report['error']=type(exc).__name__+': '+str(exc);save(out/'report.json',report);raise
if __name__=='__main__':main()
