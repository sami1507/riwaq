"""Authenticated loopback smoke using a new synthetic database, never user data.

The harness owns port18765 and its child process. Pairing secrets stay in memory.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def new_report(value,label):
    path=Path(value).resolve() if value else ROOT/'reports'/'closure-20261116'/'smoke'/(label+'-'+uuid.uuid4().hex[:10]+'.json')
    if path.exists():raise ValueError('Choose a new report path; old evidence is never overwritten')
    path.parent.mkdir(parents=True,exist_ok=True)
    return path


class ScratchService:
    def __init__(self,knowledge=False):
        self.knowledge=knowledge;self.folder=None;self.proc=None;self.closer=None;self.log=None;self.client=None
        self.before={};self.starts=0;self.auth_checks=[]

    def __enter__(self):
        import httpx
        self.temp=tempfile.TemporaryDirectory(prefix='riwaq-authenticated-smoke-')
        self.folder=Path(self.temp.name).resolve()
        changes={'RIWAQ_DATA_DIR':str(self.folder),'RIWAQ_KNOWLEDGE_REGISTRY':str(self.folder/'empty-registry.json'),'RIWAQ_EMBED_MODEL':None,'OLLAMA_NO_CLOUD':'1','RIWAQ_DEV_ORIGIN':'http://127.0.0.1:18765'}
        self.before={key:os.environ.get(key) for key in changes}
        for key,value in changes.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
        try:
            from backend import db
            db.init_db()
            if self.knowledge:self.seed_knowledge()
            self.client=httpx.Client(base_url='http://127.0.0.1:18765',headers={'X-Riwaq-Client':'local'},timeout=150,trust_env=False)
            self.start()
            self.call('PATCH','/api/settings',json={'mode':'local','provider':'ollama','model':'qwen3:4b-instruct','max_tokens':512,'context_size':8192,'temperature':0,'seed':17,'local_fallback_models':[]})
            return self
        except BaseException:
            self.__exit__(*sys.exc_info());raise

    def seed_knowledge(self):
        from backend.db import get_db,now
        text='Synthetic offline acceptance fixture. Request bodies use Pydantic models. The acceptance code is OFFLINE-427.'
        stamp=now();sha=hashlib.sha256(text.encode()).hexdigest()
        with get_db() as c:
            source={'rights_confirmed':True,'license':{'storage_allowed':True},'allowed_hosts':['example.org'],'allowed_prefixes':['/offline-fixture']}
            c.execute('INSERT INTO public_sources(id,data,status,created_at,updated_at) VALUES(?,?,?,?,?)',('scratch-source',json.dumps(source),'allowed',stamp,stamp))
            c.execute('INSERT INTO public_documents(id,source_id,requested_url,url,title,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',('scratch-doc','scratch-source','https://example.org/offline-fixture','https://example.org/offline-fixture','Synthetic offline acceptance',stamp,stamp))
            c.execute('INSERT INTO public_versions(id,document_id,revision,version,sha256,original,extracted_text,byte_size,content_type,license,license_text,language,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',('scratch-version','scratch-doc',1,'synthetic-1',sha,text.encode(),text,len(text),'text/plain','{}','Self-authored synthetic fixture','en',stamp))
            c.execute('INSERT INTO public_chunks VALUES(?,?,?,?,?,?,?,?)',('scratch-chunk','scratch-doc','scratch-version','scratch-source',None,1,1,text))
            c.execute('UPDATE public_documents SET current_version_id=? WHERE id=?',('scratch-version','scratch-doc'))

    def start(self):
        import httpx
        from backend import local_auth
        from backend.visual_runner import windows_job
        # Identity proof binds the readiness response to this temporary database.
        # A busy port is rejected before any pairing code or modifying request.
        with socket.socket() as probe:
            if os.name=='nt':probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            probe.bind(('127.0.0.1',18765))
        self.client.cookies.clear()
        env=os.environ|{'PYTHONPATH':str(ROOT),'PYTHONUTF8':'1'};executable=sys.executable
        if os.name=='nt' and sys.prefix!=sys.base_prefix:
            executable=sys._base_executable;env['__PYVENV_LAUNCHER__']=sys.executable
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0)|(0x4 if os.name=='nt' else 0)
        self.log=(self.folder/'service.log').open('ab')
        self.proc=subprocess.Popen([executable,'-B',str(ROOT/'scripts/offline_server.py')],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=self.log,stderr=self.log,creationflags=flags)
        try:
            self.closer=windows_job(self.proc)
            if os.name=='nt':
                import psutil
                psutil.Process(self.proc.pid).resume()
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                if self.proc.poll() is not None:
                    from backend.learning_verification import redact
                    self.log.flush()
                    detail=redact((self.folder/'service.log').read_text(encoding='utf-8',errors='replace')[-2000:])
                    raise RuntimeError('Scratch service exited before readiness: '+detail)
                try:
                    if self.client.get('/api/auth/status',timeout=1).status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.1)
            else:raise TimeoutError('Scratch service readiness timeout')
            assert self.client.get('/api/projects').status_code==401,'Unauthenticated API was not blocked'
            nonce=secrets.token_urlsafe(32);expected=local_auth._digest('identity',nonce)
            identity=self.client.post('/api/auth/identity',json={'nonce':nonce});identity.raise_for_status()
            if set(identity.json())!={'proof'} or not hmac.compare_digest(expected,identity.json()['proof']):raise RuntimeError('Scratch service identity mismatch')
            code=local_auth.issue_pairing_code()
            response=self.client.post('/api/auth/login',json={'pairing_code':code});code=None
            response.raise_for_status();assert self.client.get('/api/projects').status_code==200
            self.starts+=1;self.auth_checks.append({'start':self.starts,'unauthenticated_status':401,'identity_verified':True,'paired':True})
        except BaseException:self.stop();raise

    def call(self,method,path,**kwargs):
        response=self.client.request(method,path,**kwargs);response.raise_for_status();return response.json()

    def stop(self):
        if self.proc is not None:
            try:
                if self.proc.poll() is None:self.proc.terminate()
                self.proc.wait(timeout=10)
            finally:
                if self.closer:self.closer();self.closer=None
                if self.proc.poll() is None:self.proc.kill();self.proc.wait(timeout=5)
                self.proc=None
        if self.log:self.log.close();self.log=None

    def restart(self):self.stop();self.start()

    def __exit__(self,*args):
        self.stop()
        if self.client:self.client.close()
        for key,value in self.before.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
        if getattr(self,'temp',None):self.temp.cleanup()


def chat(service,project,label,prompt,*,files=False,knowledge=False,cancel=False):
    conv=service.call('POST','/api/conversations',json={'project_id':project,'title':label})
    started=time.perf_counter();first=None;last=None;count=0;cancel_requested=False;notices=[]
    with service.client.stream('POST',f"/api/conversations/{conv['id']}/generate",json={'content':prompt,'use_files':files,'use_knowledge':knowledge}) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:continue
            event=json.loads(line)
            if event['type']=='delta':
                first=first or time.perf_counter();count+=1
                if cancel and count==1:
                    service.call('POST',f"/api/conversations/{conv['id']}/stop",json={});cancel_requested=True
            if event['type']=='error':notices.append(event.get('message','generation_error'))
            if event['type']=='done':last=event
    if last is None:raise RuntimeError('No final streaming envelope')
    saved=service.call('GET',f"/api/conversations/{conv['id']}")['messages'][-1]
    equal=saved['content']==last['message']['content']
    return {'label':label,'conversation_id':conv['id'],'status':saved['status'],'response':saved['content'],'delta_chunks':count,
            'cancel_requested':cancel_requested,'first_token_seconds':round(first-started,3) if first else None,
            'elapsed_seconds':round(time.perf_counter()-started,3),'metrics':last.get('metrics',{}),'citations':saved['citations'],
            'restored_equal':equal,'generation_notices':notices,'expected_status':'cancelled' if cancel else 'complete',
            'passed':equal and saved['status']==('cancelled' if cancel else 'complete')}
