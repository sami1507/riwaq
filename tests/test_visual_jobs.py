"""Controlled job contracts and real process cleanup, not model quality."""
import json,threading,time,sys
from pathlib import Path
import pytest
from pydantic import ValidationError
from backend import visuals as v,visual_jobs as j
from backend.db import init_db,get_db,now
from backend.visual_runner import execute
from visual_auth_fixture import trusted_visual_session
from backend.visual_authority import actor
@pytest.fixture
def database(tmp_path,monkeypatch):
 monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));init_db()
 with trusted_visual_session():
  with get_db() as c:
   c.execute('INSERT INTO projects VALUES(?,?,?,?)',('audit-p','audit','',now()))
   d=v.save_design(c,'audit-p','fixture',v.DesignSpec(width=256,height=256,background='#ffffff'))
  yield d
def queued(d,kind='render'):
 with get_db() as c:
  cfg=j.JobInput(kind=kind,design_id=d['id'],version=1,prompt='A red cup',width=256,height=256).model_dump()
  c.execute('INSERT INTO visual_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',('job','audit-p',kind,'queued',d['id'],1,v.packed(cfg),'{}',None,'{}',None,now(),now()))
 return {'stop':threading.Event(),'actor':actor()}
@pytest.mark.parametrize('payload',[{'width':500},{'width':768,'height':768},{'steps':3},{'seed':-1},{'kind':'render','format':'jpeg'},{'kind':'edit','asset_id':'a','mask_rect':[0,0,512,900]}])
def test_job_rejects_invalid_limits(payload):
 with pytest.raises(ValidationError):j.JobInput.model_validate({'kind':'generate','design_id':'d','version':1,'prompt':'cup',**payload})
def test_worker_zero_exit_without_valid_report_is_not_success(database,monkeypatch):
 state=queued(database)
 monkeypatch.setattr('backend.visual_runner.execute',lambda *a,**k:{'abort_reason':None})
 j.work('job','audit-p',state)
 with get_db() as c:
  job=j.row(c,'audit-p','job');assert job['status']=='failed' and job['error']=='invalid_worker_receipt'
  assert c.execute('SELECT COUNT(*) FROM visual_assets').fetchone()[0]==0
def test_wrong_project_job_never_leaks(database):
 queued(database)
 from fastapi import HTTPException
 with get_db() as c:
  with pytest.raises(HTTPException) as e:j.row(c,'other','job')
  assert e.value.status_code==404
def test_cancelled_queued_job_never_invokes_worker(database,monkeypatch):
 state=queued(database)
 with get_db() as c:c.execute("UPDATE visual_jobs SET status='cancelling'")
 monkeypatch.setattr('backend.visual_runner.execute',lambda *a,**k:pytest.fail('Cancelled job ran'))
 j.work('job','audit-p',state)
 with get_db() as c:assert j.row(c,'audit-p','job')['status']=='cancelled'
def test_restart_marks_unfinished_job_interrupted(database):
 queued(database)
 with get_db() as c:
  j.init_jobs(c);assert j.row(c,'audit-p','job')['status']=='interrupted'
@pytest.mark.parametrize('mode',['cancel','timeout','excessive_output'])
def test_real_worker_tree_cleanup(tmp_path,mode):
 worker=tmp_path/'fixed_worker.py'
 worker.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"+("print('x'*5000,flush=True)\n" if mode=='excessive_output' else "")+"time.sleep(30)\n")
 stop=threading.Event();timer=None
 if mode=='cancel':timer=threading.Timer(.6,stop.set);timer.start()
 try:r=execute([sys.executable,str(worker)],tmp_path,tmp_path,stop,lambda x:None,seconds=.7 if mode=='timeout' else 5)
 finally:
  if timer:timer.cancel()
 assert r['remaining_processes']==0
 assert r['abort_reason']=={'cancel':'cancelled','timeout':'time_limit','excessive_output':'worker_output_limit'}[mode]
 if mode=='excessive_output':assert not stop.is_set()
 assert r['seconds']<8

def test_pre_cancel_does_not_spawn(tmp_path):
 stop=threading.Event();stop.set()
 r=execute(['this-command-must-never-be-executed'],tmp_path,tmp_path,stop,lambda x:None)
 assert r['abort_reason']=='cancelled' and r['owned_processes']==0
def test_fast_stderr_limit_and_bounded_tail(tmp_path):
 worker=tmp_path/'noisy_worker.py';worker.write_text("import sys\nsys.stderr.write('x'*2097152)\nsys.exit(1)\n")
 r=execute([sys.executable,str(worker)],tmp_path,tmp_path,threading.Event(),lambda x:None)
 assert r['abort_reason']=='log_limit' and len(r['log_tail'])<=2000 and r['remaining_processes']==0
@pytest.mark.skipif(sys.platform!='win32',reason='Windows kill-on-close birth containment')
def test_fast_parent_exit_cannot_leave_child(tmp_path):
 import psutil
 worker=tmp_path/'launcher.py';worker.write_text("import subprocess,sys,json\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\nprint(json.dumps({'stage':'child:'+str(p.pid)}),flush=True)\n")
 packets=[];r=execute([sys.executable,str(worker)],tmp_path,tmp_path,threading.Event(),packets.append)
 children=[int(x['stage'].split(':')[1]) for x in packets if x.get('stage','').startswith('child:')]
 assert children and all(not psutil.pid_exists(pid) for pid in children)
 assert r['windows_kill_on_close'] and r['remaining_processes']==0
