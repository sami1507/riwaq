"""Trusted process-tree fixtures; no model or dataset is loaded."""
import json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import psutil
from training.process_guard import run_worker
out=Path('D:/RiwaqRuntime/training-v1/evaluation/supervisor-check');out.mkdir(exist_ok=False)
fixture="import subprocess,sys,os,json,time;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);print(json.dumps([os.getpid(),p.pid]),flush=True);time.sleep(30)"
checks=[]
for mode in ['timeout','interrupt']:
 original=subprocess.Popen.wait
 if mode=='interrupt':
  fired=[False]
  def wait(self,*args,**kwargs):
   if not fired[0]:fired[0]=True;time.sleep(1);raise KeyboardInterrupt()
   return original(self,*args,**kwargs)
  subprocess.Popen.wait=wait
 try:
  try:run_worker([sys.executable,'-c',fixture],'',os.environ.copy(),time.monotonic()+(1.5 if mode=='timeout' else 30),out,mode,ROOT)
  except (subprocess.TimeoutExpired,KeyboardInterrupt,RuntimeError):pass
  else:raise AssertionError('Expected interruption')
 finally:subprocess.Popen.wait=original
 pids=json.loads((out/(mode+'.stdout.jsonl')).read_text(encoding='utf-8').strip());assert len(pids)==2 and all(not psutil.pid_exists(pid) for pid in pids)
 checks.append({'mode':mode,'fixture_pids':pids,'survivors':[]})
stdout,_,resources=run_worker([sys.executable,'-c',"print('normal completion')"],'',os.environ.copy(),time.monotonic()+10,out,'normal',ROOT)
assert stdout.strip()=='normal completion' and resources['surviving_pids']==[]
report={'passed':True,'checks':checks,'normal':resources,'scope':'Trusted sleeping child fixtures only; no adversarial OS sandbox claim'}
(ROOT/'reports/training-supervisor.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))
