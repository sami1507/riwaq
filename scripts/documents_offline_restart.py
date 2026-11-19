"""Actual isolated document HTTP/worker/cancel/restart proof; no provider or OCR model downloads.

Run with .venv/Scripts/python.exe -B scripts/documents_offline_restart.py.
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
REPORT=ROOT/'reports/documents-offline.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


async def serve():
    # Import only inside the isolated server. Importing this harness never starts it.
    import uvicorn
    from fastapi.routing import APIRoute
    from backend.app import app
    from backend import documents
    loop=asyncio.get_running_loop()
    proof={'instance':os.environ['RIWAQ_DOCUMENT_TEST_RUN'],'server_pid':os.getpid(),
           'python_executable':sys.executable,'python_prefix':sys.prefix,
           'guard':'Python socket plus active asyncio outbound APIs, including Ollama; not an OS firewall',
           'blocked_attempts':[],'self_test':[]}
    original_resolve=socket.getaddrinfo
    def deny(address):
        host=str(address[0] if isinstance(address,tuple) else address)
        proof['blocked_attempts'].append(host)
        raise OSError('Outbound connection blocked by document test guard')
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
        return proof|{'active_jobs':[fid for fid,j in documents.JOBS.items() if not j['task'].done()]}
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=int(os.environ['RIWAQ_TEST_PORT']),
                                       access_log=False,log_level='warning'))
    async def stop():
        server.should_exit=True
        return {'stopping':True,'instance':proof['instance']}
    app.router.routes.insert(0,APIRoute('/api/documents-offline-proof',status,methods=['GET']))
    app.router.routes.insert(0,APIRoute('/api/documents-offline-stop',stop,methods=['POST']))
    await server.serve()


def native_pdf(page_count):
    """Original small text fixture: standard PDF font, never an external document."""
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=PdfWriter()
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),
                           NameObject('/BaseFont'):NameObject('/Helvetica')})
    fontref=writer._add_object(font)
    for number in range(1,page_count+1):
        page=writer.add_blank_page(width=320,height=240)
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):fontref})})
        content=DecodedStreamObject()
        content.set_data(f'BT /F1 18 Tf 20 180 Td (Original document proof page {number}.) Tj ET\n'.encode('ascii'))
        page[NameObject('/Contents')]=writer._add_object(content)
    out=io.BytesIO();writer.write(out)
    return out.getvalue()


class Harness:
    def __init__(self,folder,runtime):
        import httpx
        self.folder=folder;self.proc=None;self.closer=None;self.log=None;self.starts=[];self.server_chain=set();self.baseline_children=set()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));self.port=probe.getsockname()[1]
        self.env=os.environ|{'RIWAQ_DATA_DIR':str(folder),'PYTHONPATH':str(ROOT),'PYTHONUTF8':'1',
            'RIWAQ_TEST_PORT':str(self.port),'RIWAQ_DOCUMENT_RUNTIME':str(runtime),
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
        """Measure persistent console support before any document request.

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
                         'observed_before_document_upload':True})
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
        self.env['RIWAQ_DOCUMENT_TEST_RUN']=uuid.uuid4().hex
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
                    proof=self.call('GET','/documents-offline-proof',timeout=1)
                    if proof['instance']!=self.env['RIWAQ_DOCUMENT_TEST_RUN']:
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
                    self.call('POST','/documents-offline-stop',timeout=3)
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
            if alive:raise RuntimeError('Owned document test descendants survived cleanup')

    def wait_status(self,pid,fid,wanted,seconds=180):
        deadline=time.monotonic()+seconds;last=None
        while time.monotonic()<deadline:
            last=self.call('GET',f'/projects/{pid}/documents/{fid}')
            if last['status'] in wanted:return last
            if last['status']=='failed':raise AssertionError('Real document worker failed: '+str(last['job']))
            time.sleep(.05)
        raise TimeoutError('Document status timeout: '+str(last))

    def wait_idle(self,seconds=15):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            proof=self.call('GET','/documents-offline-proof')
            if not proof['active_jobs'] and not self.children():
                return proof|{'additional_worker_children':0,'baseline_console_children':self.starts[-1]['pre_import_console_baseline']}
            time.sleep(.05)
        details=[]
        for child in self.children():
            try:details.append({'pid':child.pid,'created':child.create_time(),'name':child.name(),'command':child.cmdline()[:8]})
            except Exception:details.append({'pid':child.pid,'exited_during_inspection':True})
        raise TimeoutError('Document task or worker still active: '+json.dumps({'proof':proof,'workers':details,'verified_server_chain':list(self.server_chain),'pre_import_console_baseline':list(self.baseline_children)},ensure_ascii=False))


def run(args,report):
    work=Path(args.work_dir).resolve();work.mkdir(parents=True,exist_ok=True)
    runtime=Path(args.runtime).resolve()
    if not (runtime/'runtime-manifest.json').is_file():raise RuntimeError('Prepared local document runtime is missing')
    report.update(scope='Real HTTP, immutable original bytes, supervised fixed parser workers, PDFium, cancellation/resume, SQLite restart and cascade deletion; no model responses or simulated extraction.',
                  network_scope='Server Python outbound guard plus existing Python document-worker and Node OCR guards; not an OS-wide offline/firewall test.',
                  runtime_manifest_sha256=digest((runtime/'runtime-manifest.json').read_bytes()),
                  sources={name:digest((ROOT/name).read_bytes()) for name in ['scripts/document_worker.py','scripts/document_ocr.mjs','backend/documents.py','backend/visual_runner.py']})
    with tempfile.TemporaryDirectory(prefix='riwaq-documents-proof-',dir=work) as temporary:
        folder=Path(temporary).resolve()
        assert folder.is_relative_to(work) and folder.name.startswith('riwaq-documents-proof-')
        report['isolated_database']=str(folder/'riwaq.sqlite3')
        h=Harness(folder,runtime)
        try:
            h.start()
            project=h.call('POST','/projects',json={'name':'Document offline lifecycle proof'})
            pid=project['id'];report['project_id']=pid
            fixtures={'proof.txt':'نص عربي أصلي للاختبار فقط.\nA preserved original text.\n'.encode('utf-8'),
                      'proof.pdf':native_pdf(args.pages)}
            fileids={};original_hashes={}
            text=h.call('POST',f'/projects/{pid}/documents',files={'file':('proof.txt',fixtures['proof.txt'],'text/plain')},data={'languages':'ara+eng+heb'})
            tid=text['id'];fileids['text']=tid
            h.wait_status(pid,tid,{'complete'})
            tp=h.call('GET',f'/projects/{pid}/documents/{tid}/pages/1')
            assert 'نص عربي أصلي' in '\n'.join(e['text_raw'] for e in tp['elements'])
            assert tp['elements'][0]['locator']['page']==1
            h.wait_idle()
            pdf=h.call('POST',f'/projects/{pid}/documents',files={'file':('proof.pdf',fixtures['proof.pdf'],'application/pdf')},data={'languages':'ara+eng+heb'})
            fid=pdf['id'];fileids['pdf']=fid
            deadline=time.monotonic()+120
            while True:
                current=h.call('GET',f'/projects/{pid}/documents/{fid}')
                if 1<=current['pages_done']<args.pages:break
                if current['pages_done']==args.pages:raise AssertionError('Document finished before cancellation; no cancellation claim')
                if current['status']=='failed':raise AssertionError(str(current['job']))
                if time.monotonic()>deadline:raise TimeoutError('No first page produced')
                time.sleep(.025)
            observed_children=[{'pid':p.pid,'created':p.create_time()} for p in h.children()]
            cancel_at=current['pages_done'];started=time.monotonic()
            h.call('POST',f'/projects/{pid}/documents/{fid}/cancel')
            cancelled=h.wait_status(pid,fid,{'cancelled'},seconds=20)
            h.wait_idle()
            assert 1<=cancelled['pages_done']<args.pages
            saved_pages={p['number']:h.call('GET',f'/projects/{pid}/documents/{fid}/pages/{p["number"]}')
                         for p in cancelled['pages'] if p['status'] in {'complete','partial'}}
            assert saved_pages and all(p['elements'] for p in saved_pages.values())
            report['cancellation']={'requested_after_pages':cancel_at,'retained_pages':len(saved_pages),
                'seconds_to_idle':time.monotonic()-started,'observed_children_at_request':observed_children,
                'additional_worker_children_after_cancel':len(h.children()),'baseline_console_children':h.starts[-1]['pre_import_console_baseline'],'worker_count_scope':'Zero added workers beyond the nonce-verified server chain and exact PID/create-time System32 console helper measured before any document upload; full tree is still killed and checked on stop','status':cancelled['status']}
            h.call('POST',f'/projects/{pid}/documents/{fid}/resume')
            completed=h.wait_status(pid,fid,{'complete'},seconds=240)
            h.wait_idle()
            for number,before in saved_pages.items():
                after=h.call('GET',f'/projects/{pid}/documents/{fid}/pages/{number}')
                assert before==after, 'Resume rewrote an already completed page'
            all_pages={n:h.call('GET',f'/projects/{pid}/documents/{fid}/pages/{n}') for n in range(1,args.pages+1)}
            for n,p in all_pages.items():
                assert p['status']=='complete' and f'page {n}.' in '\n'.join(e['text_raw'] for e in p['elements'])
                assert any(e['bbox'] and e['locator']['page']==n for e in p['elements'])
            receipts=completed['job']['resources']
            assert len(receipts)==args.pages and all(r['exit_code']==0 and not r['abort_reason'] and r['remaining_processes']==0 for r in receipts)
            if os.name=='nt':assert all(r['windows_kill_on_close'] for r in receipts)
            report['resume']={'page_count':args.pages,'completed_page_payloads_unchanged':True,
                'retained_element_ids':[e['id'] for p in saved_pages.values() for e in p['elements']],
                'worker_receipts':receipts,'peak_tree_rss_bytes':max(r['peak_tree_rss_bytes'] for r in receipts)}
            for kind,document_id in fileids.items():
                response=h.client.get(f'/projects/{pid}/documents/{document_id}/original');response.raise_for_status()
                assert response.content==fixtures['proof.txt' if kind=='text' else 'proof.pdf']
                original_hashes[kind]=digest(response.content)
            before={'text':tp,'pdf':all_pages}
            before_hash=digest(json.dumps(before,ensure_ascii=False,sort_keys=True).encode())
            fence=h.call('GET','/documents-offline-proof');assert fence['blocked_attempts']==[] and not fence['active_jobs']
            report['network_before_restart']=fence
            h.stop();h.start()
            after={'text':h.call('GET',f'/projects/{pid}/documents/{tid}/pages/1'),
                   'pdf':{n:h.call('GET',f'/projects/{pid}/documents/{fid}/pages/{n}') for n in all_pages}}
            assert before==after
            for kind,document_id in fileids.items():
                response=h.client.get(f'/projects/{pid}/documents/{document_id}/original');response.raise_for_status()
                assert digest(response.content)==original_hashes[kind]
            report['restart']={'different_server_instance':h.starts[0]['instance']!=h.starts[1]['instance'],
                'original_sha256':original_hashes,'all_page_element_metadata_equal':True,'page_metadata_sha256':before_hash,
                'original_bytes_preserved':True}
            for document_id in fileids.values():
                h.call('DELETE',f'/projects/{pid}/files/{document_id}')
                for suffix in ('original','pages/1','pages/1/preview'):
                    assert h.client.get(f'/projects/{pid}/documents/{document_id}/{suffix}').status_code==404
            tables=['knowledge_files','knowledge_chunks','document_originals','document_pages','document_elements',
                    'document_element_versions','document_chunk_refs','document_jobs']
            with closing(sqlite3.connect((folder/'riwaq.sqlite3').as_uri()+'?mode=ro',uri=True)) as c:
                counts={table:c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in tables}
            assert all(count==0 for count in counts.values()),counts
            remnants=[str(p.relative_to(folder)) for p in (folder/'document_work').rglob('*') if p.is_file()]
            assert not remnants,remnants
            fence=h.wait_idle();assert fence['blocked_attempts']==[]
            report['deletion']={'remaining_rows':counts,'temporary_parser_files':remnants}
            report['network_after_restart']=fence
            report['server_instances']=h.starts
        finally:
            report['server_instances']=h.starts
            h.stop();h.client.close()
    report['temporary_database_removed']=not folder.exists()
    assert report['temporary_database_removed']
    report['passed']=True


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--work-dir',default='D:/tmp')
    parser.add_argument('--runtime',default='D:/RiwaqRuntime/documents-v1')
    parser.add_argument('--pages',type=int,default=24)
    args=parser.parse_args()
    if args.serve:
        asyncio.run(serve());return 0
    if not 8<=args.pages<=32:parser.error('--pages must be 8..32')
    report={'version':'documents-offline-v1','passed':False,'started_unix':time.time()}
    started=time.monotonic()
    try:run(args,report)
    except BaseException as exc:
        report['error']=type(exc).__name__+': '+str(exc)[:5000]
        raise
    finally:
        report['seconds']=time.monotonic()-started
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':report['passed'],'report':str(REPORT),'seconds':report['seconds']}),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
