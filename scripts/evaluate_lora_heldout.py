"""One-time characterization after rejection; never reopens the adoption decision."""
import argparse,hashlib,json,math,os,subprocess,sys,threading,time
from pathlib import Path
import psutil
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from training.scoring import parse,equal
from training.process_guard import run_worker
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):
 tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');tmp.replace(p)
def require(ok,message):
 if not ok:raise ValueError(message)
def main():
 ap=argparse.ArgumentParser()
 for name in ['python','base','run','data','manifest-sha','selection','selection-sha','validation-report','output']:ap.add_argument('--'+name,required=True)
 a=ap.parse_args();out=Path(a.output).resolve();require(not out.exists(),'Refusing existing output');out.mkdir(parents=True)
 selection_path=Path(a.selection);selection_hash=sha(selection_path);require(selection_hash==a.selection_sha,'Decision hash mismatch')
 selection=json.loads(selection_path.read_text(encoding='utf-8'))
 require(selection.get('decision')=='retain_base' and selection.get('candidate_status')=='rejected' and selection.get('deployment_decision_frozen') is True and selection.get('heldout_purpose')=='single_characterization_only','Freeze rejection first')
 vr=json.loads(Path(a.validation_report).read_text(encoding='utf-8'));require(sha(a.validation_report)==selection['validation_report_sha256'] and vr['status']=='complete_keep_base' and vr['comparison_result']['validation_gate_pass'] is False,'Rejection evidence mismatch')
 data=Path(a.data);manifest=json.loads((data/'manifest.json').read_text(encoding='utf-8'));require(sha(data/'manifest.json')==a.manifest_sha==selection['manifest_sha256'] and manifest['version']=='sft-original-v3','Manifest mismatch')
 run_path=Path(a.run)/'run.json';run=json.loads(run_path.read_text(encoding='utf-8'));require(sha(run_path)==selection['run_sha256'] and run['manifest_sha256']==a.manifest_sha,'Run mismatch')
 adapter=Path(run['candidate_checkpoint']).resolve();require(adapter.parent==Path(a.run).resolve(),'Candidate outside frozen run')
 am_path=adapter/'runtime_manifest.json';am=json.loads(am_path.read_text(encoding='utf-8'));require(sha(am_path)==selection['adapter_manifest_sha256'],'Adapter changed')
 require(am['base_report_sha256']==sha(Path(a.base)/'reconstruction.json')==run['base_report_sha256'] and am['manifest_sha256']==a.manifest_sha and am['device_recipe']=='cuda','Base/recipe mismatch')
 claim=selection_path.with_name(selection_path.name+'.heldout-started.json')
 with claim.open('x',encoding='utf-8') as handle:json.dump({'selection_sha256':selection_hash,'output':str(out),'purpose':'single_characterization_only'},handle)
 vp=data/'test.jsonl';require(sha(vp)==manifest['splits']['test']['sha256'],'Heldout changed')
 cases=[json.loads(x) for x in vp.read_text(encoding='utf-8').splitlines()];require(len(cases)==46 and all(x['split']=='test' for x in cases),'Expected46 test rows')
 requests=[]
 for case in cases:
  require([m['role'] for m in case['messages']]==['system','user','assistant'],'Message schema');messages=case['messages'][:-1]
  require(not case['source'] or case['source'] in messages[-1]['content'],'Source missing');requests.append({'id':case['id'],'messages':messages,'max_new_tokens':256})
 require(len({x['id'] for x in requests})==46,'Duplicate IDs');payload=''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in requests)
 files=list((ROOT/'training').glob('runtime_*.py'))+[Path(__file__),ROOT/'training/scoring.py',ROOT/'training/process_guard.py',selection_path,Path(a.validation_report),run_path,am_path];frozen={str(p):sha(p) for p in files}
 report={'version':'lora-heldout-characterization-v1','status':'running','purpose':'single_characterization_of_rejected_candidate','decision_before_test':'retain_base','decision_after_test':'retain_base','selection_sha256':selection_hash,'validation_report_sha256':sha(a.validation_report),'manifest_sha256':a.manifest_sha,'heldout_sha256':sha(vp),'run_sha256':sha(run_path),'adapter_manifest_sha256':sha(am_path),'source_hashes':frozen,'comparison':'same reconstructed BF16 base,NF4 double quant,nonquantized FP32,greedy;base then rejected adapter','heldout_scope':'Test excluded from training and selection; reference data audited separately. No retuning or adoption reconsideration.','citation_scope':'Exact marker serialization with supplied excerpts,not API retrieval','network_scope':'Offline flags/local files,not OS network isolation','memory_scope':'Sampled process-tree RSS;shared pages may double-count;not OS isolation','arms':{},'selected_for_deployment':False}
 save(out/'report.json',report);deadline=time.monotonic()+1800
 try:
  for arm,apath in [('base',None),('adapter',adapter)]:
   require({str(p):sha(p) for p in files}==frozen,'Runtime/grader changed')
   cmd=[a.python,'-X','utf8','-B',str(ROOT/'training/runtime_infer.py'),'--base',str(Path(a.base).resolve()),'--device','cuda','--max-requests','46']
   if apath:cmd+=['--adapter',str(apath)]
   env=os.environ.copy();env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',PYTHONIOENCODING='utf-8')
   started=time.monotonic()
   stdout,stderr,resources=run_worker(cmd,payload,env,deadline,out,arm,ROOT)
   outputs=[json.loads(line) for line in stdout.splitlines() if line.strip()];require([x['id'] for x in outputs]==[x['id'] for x in requests],'Missing/reordered outputs')
   require(all(type(x.get('finished')) is bool and isinstance(x.get('content'),str) for x in outputs),'Invalid envelope')
   if arm=='adapter':require(all(x.get('adapter_reload_verified') is True for x in outputs),'Reload verification failed')
   graded=[]
   for case,result in zip(cases,outputs):
    try:parsed=parse(result['content']);valid_json=isinstance(parsed,dict);exact=bool(result['finished'] and valid_json and equal(parsed,case['expected']))
    except (ValueError,TypeError,RecursionError):valid_json=exact=False
    graded.append({'id':case['id'],'family':case['family'],'json':valid_json,'exact':exact,'response':result})
   times=sorted(float(x['seconds']) for x in outputs);require(all(math.isfinite(x) and x>=0 for x in times),'Invalid timing')
   report['arms'][arm]={'heldout':graded,'correct':sum(x['exact'] for x in graded),'json':sum(x['json'] for x in graded),'n':46,'p95_generation_seconds':times[math.ceil(.95*len(times))-1],'wall_seconds_including_load':time.monotonic()-started,**resources}
   save(out/'report.json',report);print(json.dumps({'arm':arm,'correct':report['arms'][arm]['correct'],'n':46}),flush=True)
  require({str(p):sha(p) for p in files}==frozen,'Runtime/grader changed during evaluation')
  b=report['arms']['base']['heldout'];t=report['arms']['adapter']['heldout'];gain=sum(x['exact'] for x in t)-sum(x['exact'] for x in b)
  lost=[x['id'] for x,y in zip(b,t) if x['exact'] and not y['exact']];gained=[x['id'] for x,y in zip(b,t) if not x['exact'] and y['exact']]
  report['comparison_result']={'net_gain':gain,'lost_correct':lost,'new_correct':gained,'characterization_only':True,'adoption_reconsidered':False}
  report['status']='complete_characterization_base_retained';save(out/'report.json',report)
 except BaseException as exc:report['status']='failed';report['error']=type(exc).__name__+': '+str(exc);save(out/'report.json',report);raise
if __name__=='__main__':main()
