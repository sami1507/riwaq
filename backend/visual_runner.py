"""Bounded supervisor for fixed trusted workers, never arbitrary user code."""
import json,os,subprocess,threading,time,sys
from pathlib import Path
import psutil
LAST_JOB={'ui_restricted':None}  # outcome of the most recent job's user-interface restrictions

def windows_job(proc):
 if os.name!='nt':return None
 import ctypes
 from ctypes import wintypes as w
 class IO(ctypes.Structure):_fields_=[(x,ctypes.c_ulonglong) for x in ['ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount']]
 class BASIC(ctypes.Structure):_fields_=[('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),('LimitFlags',w.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',w.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',w.DWORD),('SchedulingClass',w.DWORD)]
 class EXT(ctypes.Structure):_fields_=[('BasicLimitInformation',BASIC),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
 k=ctypes.WinDLL('kernel32',use_last_error=True);k.CreateJobObjectW.restype=w.HANDLE;k.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];k.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];k.CloseHandle.argtypes=[w.HANDLE]
 handle=k.CreateJobObjectW(None,None);info=EXT();info.BasicLimitInformation.LimitFlags=0x2000
 # User-interface restrictions for a headless worker: no clipboard read or write, no handles
 # owned outside the job, no desktop switching or creation, no display or system-parameter
 # changes, no session exit. Windows refuses this class on some nested jobs, so it is applied
 # when possible and its outcome is recorded; kill-on-close below stays mandatory.
 # RIWAQ_JOB_UI_RESTRICTIONS=0 turns this class off (diagnosis, or a worker that needs the desktop).
 if handle:LAST_JOB['ui_restricted']=False if os.environ.get('RIWAQ_JOB_UI_RESTRICTIONS')=='0' else bool(k.SetInformationJobObject(handle,4,ctypes.byref(w.DWORD(0xFF)),ctypes.sizeof(w.DWORD)))
 if not handle or not k.SetInformationJobObject(handle,9,ctypes.byref(info),ctypes.sizeof(info)) or not k.AssignProcessToJobObject(handle,w.HANDLE(int(proc._handle))):
  if handle:k.CloseHandle(handle)
  raise RuntimeError('Cannot establish Windows kill-on-close job; worker refused')
 return lambda:k.CloseHandle(handle)

def execute(cmd,cwd,folder,stop,progress,seconds=300,memory=12*1024**3,authorize=None):
 folder=Path(folder);env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','USERPROFILE','LOCALAPPDATA','APPDATA','TEMP','TMP','NUMBER_OF_PROCESSORS','USERNAME','PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMW6432','SYSTEMDRIVE'}}
 env.update(PYTHONUTF8='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false')
 if getattr(sys,'frozen',False) and cmd[:3]==[sys.executable,'--fixed-worker','voice_tts']:
  from backend.packaged_workers import worker_environment
  env=worker_environment(env)
 for name in ('RIWAQ_DOCUMENT_RUNTIME','RIWAQ_DOCUMENT_LIMITS'):
  if name in os.environ:env[name]=os.environ[name]
 started=time.monotonic();known={};proc=None;closer=None;reader=None;errors=[];peak=0;reason=None
 if stop.is_set():return {'seconds':0,'peak_tree_rss_bytes':0,'exit_code':None,'abort_reason':'cancelled','owned_processes':0,'remaining_processes':0,'windows_kill_on_close':False,'log_tail':''}
 if authorize is not None and authorize() is not True:return {'seconds':0,'peak_tree_rss_bytes':0,'exit_code':None,'abort_reason':'authorization_revoked','owned_processes':0,'remaining_processes':0,'windows_kill_on_close':False,'log_tail':''}
 def track():
  for p in list(known.values()):
   try:
    for child in p.children(recursive=True):known[(child.pid,child.create_time())]=child
   except psutil.NoSuchProcess:pass
  return [p for p in known.values() if p.is_running()]
 def kill():
  if closer:closer()
  for p in reversed(list(known.values())):
   try:
    if p.is_running():p.kill()
   except psutil.NoSuchProcess:pass
  if proc and proc.poll() is None:proc.kill()
 stderr=folder/'stderr.log'
 try:
  with stderr.open('wb') as err:
   proc=subprocess.Popen(cmd,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=err,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)|(0x4 if os.name=='nt' else 0))
   p=psutil.Process(proc.pid);known[(p.pid,p.create_time())]=p;closer=windows_job(proc)
   if os.name=='nt':p.resume()  # Start only after kill-on-close containment is attached.
   def read():
    total=0
    try:
     while True:
      line=proc.stdout.readline(4097)
      if not line:return
      total+=len(line)
      if len(line)>4096 or total>1024**2:errors.append('worker_output_limit');return
      try:
       value=json.loads(line)
       if isinstance(value,dict):progress({k:value[k] for k in ('stage','step','total','success') if k in value})
      except (ValueError,UnicodeError):pass
    except Exception:errors.append('progress_reader_failed')
   reader=threading.Thread(target=read,daemon=True);reader.start()
   while proc.poll() is None:
    rss=0
    for p in track():
     try:rss+=p.memory_info().rss
     except psutil.NoSuchProcess:pass
    peak=max(peak,rss)
    reason='authorization_revoked' if authorize is not None and authorize() is not True else errors[0] if errors else 'cancelled' if stop.is_set() else 'memory_limit' if rss>memory else 'time_limit' if time.monotonic()-started>seconds else 'log_limit' if stderr.stat().st_size>1024**2 else None
    if reason:break
    time.sleep(.1)
   if proc.poll() is None:kill();closer=None
   proc.wait(timeout=10)
   if reader:reader.join(timeout=2)
   if stderr.stat().st_size>1024**2 and reason!='cancelled':reason='log_limit'
   if errors:reason=errors[0]
   if not reason and proc.returncode:reason='worker_failed'
 finally:
  kill()
  if proc and proc.stdout:proc.stdout.close()
  _,alive=psutil.wait_procs(list(known.values()),timeout=5)
  if alive:raise RuntimeError('Worker process cleanup incomplete')
 tail=''
 if stderr.exists() and reason:
  with stderr.open('rb') as log:
   log.seek(max(0,stderr.stat().st_size-8000));tail=log.read(8000).decode('utf-8',errors='replace')[-2000:]
 receipt={'seconds':time.monotonic()-started,'peak_tree_rss_bytes':peak,'exit_code':proc.returncode if proc else None,'abort_reason':reason,'owned_processes':len(known),'remaining_processes':0,'windows_kill_on_close':os.name=='nt','network_isolation':'Worker socket guard or browser request routing; not OS firewall','log_tail':tail}
 return receipt
