"""Sampled process-tree supervision for fixed trusted evaluation workers."""
import os,signal,subprocess,threading,time
from pathlib import Path
import psutil

def run_worker(cmd,payload,env,deadline,out,arm,cwd):
 resources={'peak_tree_rss_bytes':0,'abort_reason':None,'tracked_pids':[],'surviving_pids':[]}
 stopped=threading.Event();lock=threading.RLock();known={};proc=None;watcher=None;interrupted=None
 input_path=out/(arm+'.requests.jsonl');input_path.write_text(payload,encoding='utf-8')
 stdout_path=out/(arm+'.stdout.jsonl');stderr_path=out/(arm+'.stderr.log')
 def snapshot():
  with lock:
   for parent in list(known.values()):
    try:
     if parent.is_running():
      for child in parent.children(recursive=True):known[(child.pid,child.create_time())]=child
    except psutil.NoSuchProcess:pass
   alive=[]
   for process in known.values():
    try:
     if process.is_running() and process.status()!=psutil.STATUS_ZOMBIE:alive.append(process)
    except psutil.NoSuchProcess:pass
   resources['tracked_pids']=sorted({p.pid for p in known.values()});return alive
 def kill_tree():
  with lock:
   try:alive=snapshot()
   except psutil.Error:alive=list(known.values())
   for process in reversed(alive):
    try:
     if process.is_running():process.kill()
    except psutil.NoSuchProcess:pass
    except psutil.Error as e:resources.setdefault('cleanup_errors',[]).append(str(e))
   if proc is not None:
    try:
     if proc.poll() is None:proc.kill()
    except OSError:pass
 def watch():
  while not stopped.wait(.1):
   try:
    rss=0
    for process in snapshot():
     try:rss+=process.memory_info().rss
     except psutil.NoSuchProcess:pass
    resources['peak_tree_rss_bytes']=max(resources['peak_tree_rss_bytes'],rss)
    reason='rss_limit' if rss>12*1024**3 else 'total_wall_limit' if time.monotonic()>=deadline else None
   except psutil.Error as e:reason='resource_monitor_failed';resources['monitor_error']=str(e)
   if reason:resources['abort_reason']=reason;kill_tree();return
 with input_path.open('r',encoding='utf-8') as input_file,stdout_path.open('x',encoding='utf-8') as stdout_file,stderr_path.open('x',encoding='utf-8') as stderr_file:
  try:
   proc=subprocess.Popen(cmd,stdin=input_file,stdout=stdout_file,stderr=stderr_file,cwd=cwd,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
   root=psutil.Process(proc.pid);known[(root.pid,root.create_time())]=root
   watcher=threading.Thread(target=watch,daemon=True);watcher.start();proc.wait(timeout=max(.1,deadline-time.monotonic()))
  except BaseException as e:interrupted=e;resources['abort_reason']='total_wall_limit' if isinstance(e,subprocess.TimeoutExpired) else type(e).__name__
  finally:
   old=signal.signal(signal.SIGINT,signal.SIG_IGN)
   try:
    stopped.set()
    if watcher is not None:watcher.join(timeout=1)
    kill_tree()
    with lock:tracked=list(known.values())
    _,alive=psutil.wait_procs(tracked,timeout=5)
    for process in alive:
     try:process.kill()
     except psutil.NoSuchProcess:pass
     except psutil.Error as e:resources.setdefault('cleanup_errors',[]).append(str(e))
    _,alive=psutil.wait_procs(alive,timeout=5);resources['surviving_pids']=[p.pid for p in alive]
    if proc is not None:
     try:proc.wait(timeout=5)
     except subprocess.TimeoutExpired:resources['surviving_pids'].append(proc.pid)
   finally:signal.signal(signal.SIGINT,old)
 if resources['surviving_pids']:raise RuntimeError('Worker cleanup incomplete: '+str(resources))
 if interrupted is not None:raise interrupted
 if proc is None or proc.returncode!=0 or resources['abort_reason']:raise RuntimeError('Worker failed: '+str(resources))
 if max(stdout_path.stat().st_size,stderr_path.stat().st_size)>4*1024**2:raise ValueError('Unexpected log size')
 return stdout_path.read_text(encoding='utf-8'),stderr_path.read_text(encoding='utf-8',errors='replace'),resources
