"""Actual isolated video HTTP/worker/cancel/restart proof; no models or downloads.

Run with .venv/Scripts/python.exe -B scripts/video_offline_restart.py.
The child --serve mode is test-only and always uses a unique temporary database.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import asyncio
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
REPORT=ROOT/'reports/video-offline.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


async def serve():
    # Import only inside the isolated server. Importing this harness never starts it.
    import uvicorn
    from fastapi.routing import APIRoute
    from backend.app import app
    from backend import video
    loop=asyncio.get_running_loop()
    proof={'instance':os.environ['RIWAQ_VIDEO_TEST_RUN'],'server_pid':os.getpid(),
           'python_executable':sys.executable,'python_prefix':sys.prefix,
           'guard':'Python socket plus active asyncio outbound APIs, including Ollama; not an OS firewall',
           'blocked_attempts':[],'self_test':[]}
    original_resolve=socket.getaddrinfo
    def deny(address):
        host=str(address[0] if isinstance(address,tuple) else address)
        proof['blocked_attempts'].append(host)
        raise OSError('Outbound connection blocked by video test guard')
    def connect(sock,address):return deny(address)
    def create(address,*args,**kwargs):return deny(address)
    async def async_connect(factory,host=None,port=None,**kwargs):return deny((host,port))
    async def sock_connect(sock,address):return deny(address)
    def resolve(host,*args,**kwargs):
        try:ipaddress.ip_address(host)
        except ValueError:return deny(host)
        return original_resolve(host,*args,**kwargs)
    socket.socket.connect=connect
    socket.socket.connect_ex=connect
    socket.create_connection=create
    socket.getaddrinfo=resolve
    loop.create_connection=async_connect
    loop.sock_connect=sock_connect
    for host,port in [('127.0.0.1',11434),('203.0.113.1',443)]:
        try:await loop.create_connection(asyncio.Protocol,host,port)
        except OSError:proof['self_test'].append({'api':'asyncio.create_connection','host':host,'blocked':True})
        else:raise RuntimeError('Async network guard failed')
    for name,action in [('socket.connect',lambda:socket.socket().connect(('127.0.0.1',11434))),
                        ('DNS',lambda:socket.getaddrinfo('example.invalid',443))]:
        try:action()
        except OSError:proof['self_test'].append({'api':name,'blocked':True})
        else:raise RuntimeError('Socket/DNS guard failed')
    proof['blocked_attempts'].clear()
    async def status():
        return proof|{'active_jobs':list(video.ACTIVE)}
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=int(os.environ['RIWAQ_TEST_PORT']),
                                       access_log=False,log_level='warning'))
    async def stop():
        server.should_exit=True
        return {'stopping':True,'instance':proof['instance']}
    app.router.routes.insert(0,APIRoute('/api/video-offline-proof',status,methods=['GET']))
    app.router.routes.insert(0,APIRoute('/api/video-offline-stop',stop,methods=['POST']))
    # Test-only atomic observation of the real production deletion/admission code.
    # No worker patch, fake delay, fake process, or artificial task status is used.
    from fastapi import Request, HTTPException
    globals()['Request']=Request
    async def delete_and_probe(request:Request):
        from backend.video_schema import ExportInput
        payload=await request.json();before=list(video.ACTIVE)
        if payload['running_job_id'] not in before:
            raise HTTPException(409,'Worker was no longer active; no cleanup-gate proof')
        deleted=video.delete_timeline(payload['project_id'],payload['timeline_id'],payload['expected_version'])
        active_after_delete=list(video.ACTIVE)
        try:
            admitted=await video.create_job(payload['project_id'],ExportInput.model_validate(payload['replacement']))
        except HTTPException as exc:
            admission={'status_code':exc.status_code,'detail':exc.detail}
        else:admission={'status_code':202,'job_id':admitted['id']}
        return {'deleted':deleted,'active_before':before,'active_after_delete':active_after_delete,'admission':admission,
                'scope':'Test-only same-event-loop calls to unmodified production delete_timeline/create_job, while an actual supervised worker is active.'}
    app.router.routes.insert(0,APIRoute('/api/video-offline-delete-and-probe',delete_and_probe,methods=['POST']))
    await server.serve()


class Harness:
    def __init__(self,folder):
        import httpx
        self.folder=folder;self.proc=None;self.closer=None;self.log=None;self.starts=[];self.server_chain=set();self.baseline_children=set()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));self.port=probe.getsockname()[1]
        self.env=os.environ|{'RIWAQ_DATA_DIR':str(folder),'PYTHONPATH':str(ROOT),'PYTHONUTF8':'1',
            'RIWAQ_TEST_PORT':str(self.port),
            'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1'}
        self.client=httpx.Client(base_url=f'http://127.0.0.1:{self.port}/api',
                                 headers={'X-Riwaq-Client':'local'},timeout=110,trust_env=False)

    def call(self,method,path,**kwargs):
        response=self.client.request(method,path,**kwargs);response.raise_for_status()
        return response.json()

    def children(self,include_server=False,include_baseline=False):
        import psutil
        if self.proc is None or self.proc.poll() is not None:return []
        try:
            return [p for p in psutil.Process(self.proc.pid).children(recursive=True)
                    if p.is_running() and (include_server or (p.pid,p.create_time()) not in self.server_chain)
                    and (include_baseline or (p.pid,p.create_time()) not in self.baseline_children)]
        except psutil.NoSuchProcess:return []

    def capture_pre_import_baseline(self):
        """Measure persistent console support before any media request.

        No worker is excluded by its name: only this exact PID+creation-time pair
        from two pre-import samples, with its actual System32 executable verified.
        """
        if os.name!='nt':return []
        expected=(Path(os.environ['SystemRoot'])/'System32'/'conhost.exe').resolve()
        first=self.children(include_baseline=True)
        if len(first)>1:raise RuntimeError('Unexpected multiple server baseline children')
        rows=[]
        for process in first:
            executable=Path(process.exe()).resolve()
            if executable!=expected:
                raise RuntimeError('Unexpected pre-import child: '+str(executable))
            rows.append({'pid':process.pid,'created':process.create_time(),'executable':str(executable),
                         'observed_before_media_upload':True})
        time.sleep(.1)
        second=self.children(include_baseline=True)
        identity={(p.pid,p.create_time()) for p in second}
        if identity!={(p.pid,p.create_time()) for p in first}:
            raise RuntimeError('Pre-import console baseline was not stable')
        for process in second:
            if Path(process.exe()).resolve()!=expected:raise RuntimeError('Baseline executable changed')
        self.baseline_children=identity
        return rows

    def bind_server_identity(self,pid):
        """Windows venv redirectors can be the launcher parent of the real server.

        Exclude only this nonce-verified server and its verified parent chain from
        worker counts. Never exclude processes by name, and still kill/check the
        complete owned tree on shutdown.
        """
        import psutil
        if type(pid) is not int:raise RuntimeError('Invalid server process identity')
        process=psutil.Process(pid);chain=[]
        while True:
            chain.append((process.pid,process.create_time()))
            if process.pid==self.proc.pid:break
            process=process.parent()
            if process is None or len(chain)>8:raise RuntimeError('Server is outside our launched process tree')
        self.server_chain=set(chain)
        return [{'pid':pid,'created':created} for pid,created in chain]

    def start(self):
        import httpx,psutil
        from backend.visual_runner import windows_job
        self.server_chain=set()
        self.baseline_children=set()
        self.env['RIWAQ_VIDEO_TEST_RUN']=uuid.uuid4().hex
        logpath=self.folder/f'server-{len(self.starts)+1}.log'
        self.log=logpath.open('wb')
        launch_executable=sys.executable
        if os.name=='nt' and sys.prefix!=sys.base_prefix:
            # Same CPython mechanism as Lib/multiprocessing/popen_spawn_win32.py:
            # launch the actual interpreter while retaining this venv identity.
            # A console venv redirector can allocate conhost before its real child.
            launch_executable=sys._base_executable
            self.env['__PYVENV_LAUNCHER__']=sys.executable
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0)|(0x4 if os.name=='nt' else 0)
        self.proc=subprocess.Popen([launch_executable,'-B',str(Path(__file__).resolve()),'--serve'],
            cwd=ROOT,env=self.env,stdin=subprocess.DEVNULL,stdout=self.log,stderr=self.log,
            creationflags=flags)
        try:
            self.closer=windows_job(self.proc)
            if os.name=='nt':psutil.Process(self.proc.pid).resume()
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                if self.proc.poll() is not None:
                    self.log.flush()
                    raise RuntimeError('Isolated server exited: '+logpath.read_text(encoding='utf-8',errors='replace')[-4000:])
                try:
                    proof=self.call('GET','/video-offline-proof',timeout=1)
                    if proof['instance']!=self.env['RIWAQ_VIDEO_TEST_RUN']:
                        raise RuntimeError('Test port belongs to another instance; no test writes were sent')
                    if Path(proof['python_executable']).resolve()!=Path(sys.executable).resolve() or Path(proof['python_prefix']).resolve()!=Path(sys.prefix).resolve():
                        raise RuntimeError('Test server did not retain the selected virtual environment')
                    chain=self.bind_server_identity(proof['server_pid'])
                    baseline=self.capture_pre_import_baseline()
                    self.starts.append({'instance':proof['instance'],'launcher_pid':self.proc.pid,
                                        'launch_executable':launch_executable,'creationflags':flags,
                                        'python_executable':proof['python_executable'],'python_prefix':proof['python_prefix'],
                                        'server_pid':proof['server_pid'],'verified_server_parent_chain':chain,
                                        'pre_import_console_baseline':baseline,
                                        'windows_kill_on_close':os.name=='nt','self_test':proof['self_test']})
                    return proof
                except httpx.HTTPError:pass
                time.sleep(.1)
            raise RuntimeError('Isolated server startup timed out')
        except BaseException:
            self.stop(graceful=False)
            raise

    def stop(self,graceful=True):
        import psutil
        if self.proc is None:return
        tracked=self.children(include_server=True,include_baseline=True)
        try:
            if graceful and self.proc.poll() is None:
                try:
                    self.call('POST','/video-offline-stop',timeout=3)
                    self.proc.wait(timeout=15)
                except Exception:pass
        finally:
            if self.closer:
                self.closer();self.closer=None
            if self.proc.poll() is None:self.proc.kill()
            self.proc.wait(timeout=10)
            for child in tracked:
                try:
                    if child.is_running():child.kill()
                except psutil.NoSuchProcess:pass
            _,alive=psutil.wait_procs(tracked,timeout=5)
            if self.log:self.log.close();self.log=None
            self.proc=None
            if alive:raise RuntimeError('Owned video test descendants survived cleanup')

    def wait_job(self,pid,jid,wanted,seconds=320):
        deadline=time.monotonic()+seconds;last=None
        while time.monotonic()<deadline:
            last=self.call('GET',f'/projects/{pid}/video/jobs/{jid}')
            if last['status'] in wanted:return last
            if last['status'] in {'failed','interrupted','cancelled'}:
                raise AssertionError('Unexpected real export state: '+json.dumps(last,ensure_ascii=False)[:3000])
            time.sleep(.05)
        raise TimeoutError('Export status timeout: '+str(last))

    def wait_encoding(self,pid,jid,seconds=100):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            job=self.call('GET',f'/projects/{pid}/video/jobs/{jid}');p=job['progress']
            if job['status']=='running' and p.get('stage')=='encode' and 1<=p.get('step',0)<p.get('total',0):
                children=self.children()
                if children:return job,[{'pid':c.pid,'created':c.create_time(),'name':c.name()} for c in children]
            if job['status'] not in {'queued','running'}:
                raise AssertionError('No live encoding cancellation window: '+str(job))
            time.sleep(.02)
        raise TimeoutError('No actual encoding progress observed')

    def wait_idle(self,seconds=15):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            proof=self.call('GET','/video-offline-proof')
            if not proof['active_jobs'] and not self.children():
                return proof|{'additional_worker_children':0,'baseline_console_children':self.starts[-1]['pre_import_console_baseline']}
            time.sleep(.05)
        details=[]
        for child in self.children():
            try:details.append({'pid':child.pid,'created':child.create_time(),'name':child.name(),'command':child.cmdline()[:8]})
            except Exception:details.append({'pid':child.pid,'exited_during_inspection':True})
        raise TimeoutError('Video task or worker still active: '+json.dumps({'proof':proof,'workers':details,'verified_server_chain':list(self.server_chain),'pre_import_console_baseline':list(self.baseline_children)},ensure_ascii=False))


def fixture_png():
    from PIL import Image,ImageDraw
    out=io.BytesIO();im=Image.new('RGB',(640,360),'#172B2A');draw=ImageDraw.Draw(im)
    draw.rectangle((70,70,280,290),fill='#D89B44');draw.ellipse((350,100,550,300),fill='#3DB7A7')
    im.save(out,format='PNG');return out.getvalue()


def timeline_spec(asset_id,duration=60):
    return {'width':1280,'height':720,'fps':24,'background':'#172B2A',
      'clips':[{'id':'image','asset_id':asset_id,'start':0,'duration':duration,'muted':True,'gain':1,'motion':'pan_zoom'}],
      'texts':[{'id':'title'+str(i),'kind':'title','text':f'Offline lifecycle proof {i+1}',
        'start':0,'end':duration,'x':40,'y':25+i*75,'width':1200,'height':60,'language':'en',
        'direction':'ltr','font':'latin','size':26,'weight':400,'color':'#FFFFFF','align':'left','protected':True} for i in range(8)],
      'audio':[]}


def read_binary(h,path):
    response=h.client.get(path);response.raise_for_status();return response.content


def no_partial_files(folder):
    remnants=[str(p.relative_to(folder)) for p in folder.rglob('*')
              if p.is_file() and any(part.startswith(('video-export-','video-probe-')) for part in p.relative_to(folder).parts)]
    assert not remnants,remnants
    return remnants


def run(args,report):
    work=Path(args.work_dir).resolve();work.mkdir(parents=True,exist_ok=True)
    report.update(scope='Real temporary-DB HTTP uploads and immutable timelines; supervised PyAV/Chromium composition, actual frame progress, cancellation, explicit retry, service restart and cascade deletion. No providers, models, downloaded fixtures or simulated video workers.',
        network_scope='Server Python socket and active asyncio outbound APIs denied, including Ollama, plus existing fixed video worker and Chromium routing guards. Not an OS-wide offline/firewall test; no claim that native-library traffic is observed by the Python guard.',
        sources={name:digest((ROOT/name).read_bytes()) for name in ['scripts/video_offline_restart.py','backend/video.py','backend/video_schema.py','backend/video_render.py','scripts/video_render.py','scripts/video_render_text.mjs','backend/visual_runner.py']})
    with tempfile.TemporaryDirectory(prefix='riwaq-video-proof-',dir=work) as temporary:
        folder=Path(temporary).resolve()
        assert folder.is_relative_to(work) and folder.name.startswith('riwaq-video-proof-')
        report['isolated_database']=str(folder/'riwaq.sqlite3');h=Harness(folder)
        try:
            h.start()
            project=h.call('POST','/projects',json={'name':'Isolated video offline lifecycle proof'});pid=project['id']
            foreign=h.call('POST','/projects',json={'name':'Independent ACL proof'});foreign_id=foreign['id']
            report['project_id']=pid;prefix=f'/projects/{pid}/video'
            image=fixture_png();asset=h.call('POST',prefix+'/assets',files={'file':('original-proof.png',image,'image/png')},data={'license':'Original procedural fixture authored for this test; no private media.'})
            aid=asset['id'];assert asset['sha256']==digest(image)
            assert read_binary(h,prefix+f'/assets/{aid}/content')==image
            assert h.client.get(f'/projects/{foreign_id}/video/assets/{aid}/content').status_code==404
            initial=h.call('POST',prefix+'/timelines',json={'title':'Version-one source','spec':timeline_spec(aid)})
            tid=initial['id'];saved_spec=initial['spec'];input_hash=initial['sha256']
            assert initial['version']==1
            request={'timeline_id':tid,'version':1,'idempotency_key':'cancel-real-encoding'}
            first=h.call('POST',prefix+'/jobs',json=request);jid=first['id']
            assert h.call('POST',prefix+'/jobs',json=request)['id']==jid
            observed,children=h.wait_encoding(pid,jid);started=time.monotonic()
            h.call('POST',prefix+f'/jobs/{jid}/cancel')
            cancelled=h.wait_job(pid,jid,{'cancelled'},seconds=25);idle=h.wait_idle(seconds=20)
            assert h.client.get(prefix+f'/jobs/{jid}/content').status_code==404
            assert h.call('POST',prefix+'/jobs',json=request)['id']==jid
            assert h.call('GET',prefix+f'/jobs/{jid}')['status']=='cancelled'
            assert h.call('GET',prefix+f'/timelines/{tid}')['spec']==saved_spec
            report['cancellation']={'status':cancelled['status'],'seconds':time.monotonic()-started,
                'progress_before_cancel':observed['progress'],'actual_children_before_cancel':children,
                'additional_worker_children_after':idle['additional_worker_children'],
                'no_export_published':True,'same_key_returns_cancelled_job_without_rerun':True,
                'partial_files':no_partial_files(folder)}
            # Explicit new request for exactly the immutable version that was cancelled.
            retry_body={'timeline_id':tid,'version':1,'idempotency_key':'explicit-retry-same-v1'}
            retry=h.call('POST',prefix+'/jobs',json=retry_body);rid=retry['id'];assert rid!=jid
            completed=h.wait_job(pid,rid,{'completed'});idle=h.wait_idle()
            assert completed['input_sha256']==first['input_sha256']
            assert completed['report']['spec_sha256']==input_hash
            qc=completed['report']['qc'];assert qc['full_decode'] and qc['video_frames']==1440
            assert completed['report']['resources']['remaining_processes']==0
            movie=read_binary(h,prefix+f'/jobs/{rid}/content');movie_sha=digest(movie)
            assert completed['report']['output_sha256']==movie_sha
            assert h.call('POST',prefix+'/jobs',json=retry_body)['id']==rid
            assert h.client.get(f'/projects/{foreign_id}/video/jobs/{rid}/content').status_code==404
            report['explicit_retry']={'original_job_id':jid,'retry_job_id':rid,'same_version':True,
                'same_input_sha256':completed['input_sha256'],'spec_sha256':input_hash,'status':'completed',
                'output_sha256':movie_sha,'output_bytes':len(movie),'qc':qc,'resources':completed['report']['resources'],
                'text_source_unchanged':completed['report']['text']['source_unchanged'],
                'additional_worker_children':idle['additional_worker_children']}
            assert idle['blocked_attempts']==[],idle['blocked_attempts']
            report['network_before_restart']=idle
            h.stop();h.start()
            restored=h.call('GET',prefix+f'/timelines/{tid}')
            assert restored['spec']==saved_spec and restored['sha256']==input_hash and restored['version']==1
            assert digest(read_binary(h,prefix+f'/assets/{aid}/content'))==digest(image)
            assert digest(read_binary(h,prefix+f'/jobs/{rid}/content'))==movie_sha
            assert h.call('GET',prefix+f'/jobs/{rid}')['status']=='completed'
            assert h.call('POST',prefix+'/jobs',json=retry_body)['id']==rid
            report['restart']={'different_server_instance':h.starts[0]['instance']!=h.starts[1]['instance'],
                'immutable_spec_restored':True,'original_sha256':digest(image),'export_sha256':movie_sha,
                'output_bytes_unchanged':True,'same_key_still_idempotent':True,
                'scope':'Previously persisted MP4 bytes were fetched after restart; no deterministic re-encoding claim.'}
            # Prepare an independent, short replacement while testing deletion of a live export.
            short=h.call('POST',prefix+'/timelines',json={'title':'Replacement after cleanup','spec':timeline_spec(aid,1)})
            doomed=h.call('POST',prefix+'/timelines',json={'title':'Delete only this active timeline','spec':saved_spec})
            doomed_request={'timeline_id':doomed['id'],'version':1,'idempotency_key':'active-before-delete'}
            deletion_job=h.call('POST',prefix+'/jobs',json=doomed_request)
            progress,children=h.wait_encoding(pid,deletion_job['id'])
            replacement={'timeline_id':short['id'],'version':1,'idempotency_key':'after-delete-cleanup'}
            gate=h.call('POST','/video-offline-delete-and-probe',json={'project_id':pid,'timeline_id':doomed['id'],
                'expected_version':1,'running_job_id':deletion_job['id'],'replacement':replacement})
            assert gate['admission']['status_code']==409,gate
            assert deletion_job['id'] in gate['active_after_delete']
            idle=h.wait_idle(seconds=25)
            assert h.client.get(prefix+f'/jobs/{deletion_job["id"]}').status_code==404
            assert h.client.get(prefix+f'/timelines/{doomed["id"]}').status_code==404
            assert read_binary(h,prefix+f'/assets/{aid}/content')==image
            accepted=h.call('POST',prefix+'/jobs',json=replacement)
            replacement_done=h.wait_job(pid,accepted['id'],{'completed'});h.wait_idle()
            assert replacement_done['report']['qc']['video_frames']==24
            report['active_deletion']={'progress_before_delete':progress['progress'],
                'actual_children_before_delete':children,'cleanup_admission_probe':gate,
                'additional_worker_children_after':idle['additional_worker_children'],
                'original_retained':True,'replacement_completed_after_cleanup':True,
                'partial_files':no_partial_files(folder)}
            # Use existing product deletion APIs; there is no project DELETE route.
            # Project/event rows intentionally remain until this temporary DB is removed.
            deleted_timelines=[];deleted_assets=[]
            for project_id in (pid,foreign_id):
                base=f'/projects/{project_id}/video'
                for timeline in h.call('GET',base+'/timelines'):
                    h.call('DELETE',base+f'/timelines/{timeline["id"]}',params={'expected_version':timeline['version']})
                    deleted_timelines.append(timeline['id'])
                for original in h.call('GET',base+'/assets'):
                    h.call('DELETE',base+f'/assets/{original["id"]}')
                    deleted_assets.append(original['id'])
            idle=h.wait_idle()
            for path in [prefix+f'/assets/{aid}/content',prefix+f'/timelines/{tid}',prefix+f'/jobs/{rid}/content']:
                assert h.client.get(path).status_code==404
            tables=['video_assets','video_timelines','video_versions','video_jobs','video_outputs']
            with closing(sqlite3.connect((folder/'riwaq.sqlite3').as_uri()+'?mode=ro',uri=True)) as c:
                counts={table:c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in tables}
                project_rows=c.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
            assert all(value==0 for value in counts.values()),counts
            assert idle['blocked_attempts']==[],idle['blocked_attempts']
            report['deletion']={'remaining_rows':counts,'temporary_media_files':no_partial_files(folder),
                'deleted_timeline_ids':deleted_timelines,'deleted_asset_ids':deleted_assets,
                'project_rows_retained_until_temporary_database_removal':project_rows,
                'scope':'Existing timeline and asset DELETE APIs; media edits/jobs/outputs cascade. No project DELETE endpoint is assumed.'}
            report['network_after_restart']=idle
        finally:
            report['server_instances']=h.starts
            try:h.stop()
            finally:h.client.close()
            report['owned_server_tree_stopped']=True
    report['temporary_database_removed']=not folder.exists();assert report['temporary_database_removed']
    report['passed']=True


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--serve',action='store_true')
    parser.add_argument('--work-dir',default='D:/tmp');args=parser.parse_args()
    if args.serve:asyncio.run(serve());return 0
    report={'version':'video-offline-v1','passed':False,'started_unix':time.time()};started=time.monotonic()
    if REPORT.exists():
        prior=REPORT.with_name('video-offline-prior-'+str(time.time_ns())+'.json')
        prior.write_bytes(REPORT.read_bytes())
    try:run(args,report)
    except BaseException as exc:
        report['error']=type(exc).__name__+': '+str(exc)[:5000];raise
    finally:
        report['seconds']=time.monotonic()-started
        if 'isolated_database' in report:
            report['temporary_database_removed']=not Path(report['isolated_database']).parent.exists()
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':report['passed'],'report':str(REPORT),'seconds':report['seconds']}),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
