"""Project-scoped immutable originals, bounded page jobs and inspectable evidence.

The local service has one OS user. Project IDs scope every read/write; these are
not cloud account credentials. Fixed parsers run in supervised child processes.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import threading
import time
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from backend.db import ROOT, data_dir, get_db, now, uid
from backend import knowledge, document_isolation, local_auth
from backend.document_extract import VERSION, languages, load_limits, column_name
from backend.reasoning import FileSelection

router = APIRouter()
JOBS: dict[str, dict] = {}
SLOTS = threading.BoundedSemaphore(1)
INSPECTION_SLOTS = threading.BoundedSemaphore(2)
DISPATCH_SLOT = threading.BoundedSemaphore(1)
MAX_DISPATCH_WAIT = 30
PROJECT_ORIGINAL_BYTES = 128 * 1024**2
PROJECT_PREVIEW_BYTES = 128 * 1024**2
MAX_JOB_SECONDS = 1800


def pack(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def worker_status():
    return document_isolation.status()


def require_worker():
    status = worker_status()
    if not status['available']:
        raise HTTPException(503, status['reason_code'] + ': no configured fixed extraction worker; no fallback was started')


def extraction_fingerprint():
    return document_isolation.fingerprint()


def init_documents(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS document_originals(
      file_id TEXT PRIMARY KEY REFERENCES knowledge_files(id) ON DELETE CASCADE,
      original BLOB NOT NULL,mime TEXT NOT NULL,version INTEGER NOT NULL,
      config TEXT NOT NULL,metadata TEXT NOT NULL,status TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS document_pages(
      file_id TEXT NOT NULL REFERENCES document_originals(file_id) ON DELETE CASCADE,
      number INTEGER NOT NULL,payload TEXT NOT NULL,preview BLOB,
      PRIMARY KEY(file_id,number));
    CREATE TABLE IF NOT EXISTS document_elements(
      id TEXT PRIMARY KEY,file_id TEXT NOT NULL,page INTEGER NOT NULL,
      revision INTEGER NOT NULL,payload TEXT NOT NULL,
      FOREIGN KEY(file_id,page) REFERENCES document_pages(file_id,number) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS document_element_versions(
      element_id TEXT NOT NULL REFERENCES document_elements(id) ON DELETE CASCADE,
      revision INTEGER NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,
      method TEXT NOT NULL,PRIMARY KEY(element_id,revision));
    CREATE TABLE IF NOT EXISTS document_chunk_refs(
      chunk_id TEXT PRIMARY KEY REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
      element_id TEXT NOT NULL REFERENCES document_elements(id) ON DELETE CASCADE,
      payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS document_jobs(
      id TEXT PRIMARY KEY,file_id TEXT UNIQUE NOT NULL REFERENCES document_originals(file_id) ON DELETE CASCADE,
      status TEXT NOT NULL,pages_done INTEGER NOT NULL,page_count INTEGER NOT NULL,
      error TEXT,updated_at TEXT NOT NULL,resources TEXT NOT NULL DEFAULT '[]');
    CREATE INDEX IF NOT EXISTS idx_document_element_file ON document_elements(file_id,page);
    ''')
    c.execute("UPDATE document_jobs SET status='interrupted',error='Service stopped; resume the unfinished pages.' WHERE status IN ('queued','running','cancelling')")
    c.execute("UPDATE document_originals SET status='interrupted' WHERE file_id IN (SELECT file_id FROM document_jobs WHERE status='interrupted')")


def owned(c, pid, fid, actor=None):
    knowledge.require_file(c, pid, fid, actor)
    row = c.execute('SELECT f.*,o.mime,o.version,o.config,o.metadata,o.status FROM knowledge_files f JOIN document_originals o ON o.file_id=f.id WHERE f.project_id=? AND f.id=?', (pid, fid)).fetchone()
    if not row:
        raise HTTPException(404, 'Original document not available in this project; legacy text-only imports require re-upload.')
    return row


def describe(c, pid, fid):
    value = dict(owned(c, pid, fid))
    value['config'] = json.loads(value['config']); value['metadata'] = json.loads(value['metadata'])
    job = c.execute('SELECT * FROM document_jobs WHERE file_id=?', (fid,)).fetchone()
    value['job'] = dict(job) if job else None
    if value['job']: value['job']['resources'] = json.loads(value['job']['resources'])
    pages = []
    for row in c.execute('SELECT number,payload FROM document_pages WHERE file_id=? ORDER BY number', (fid,)):
        page = json.loads(row['payload'])
        pages.append({k:page.get(k) for k in ('number','status','kind','width','height','warnings','method','language')})
    value['pages'] = pages
    value['pages_done'] = len(pages)
    return value


def page_data(c, pid, fid, number):
    owned(c, pid, fid)
    row = c.execute('SELECT payload FROM document_pages WHERE file_id=? AND number=?', (fid, number)).fetchone()
    if not row: raise HTTPException(404, 'Page has not been processed')
    page = json.loads(row[0])
    page['elements'] = [json.loads(r[0]) for r in c.execute('SELECT payload FROM document_elements WHERE file_id=? AND page=? ORDER BY rowid', (fid, number))]
    return page


def parse(data, filename, page, language, stop, authorize=None):
    status = worker_status()
    if not status['available']:
        raise document_isolation.IsolationError(status['reason_code'],503)
    if authorize is None:raise document_isolation.IsolationError('document_authority_required',401)
    started=time.monotonic();deadline=started+(90 if page else 30)
    queue_deadline=min(deadline,started+MAX_DISPATCH_WAIT)
    acquired=False
    try:
        while not acquired:
            if stop.is_set():raise document_isolation.IsolationError('document_cancelled',499)
            if authorize() is not True:raise document_isolation.IsolationError('document_authority_revoked',403)
            remaining=queue_deadline-time.monotonic()
            if remaining<=0:raise document_isolation.IsolationError('document_dispatch_busy',503)
            acquired=DISPATCH_SLOT.acquire(timeout=min(.1,remaining))
        queued=time.monotonic()-started
        result,preview,receipt=document_isolation.extract(data,filename,page,language,stop,authorize=authorize,deadline=deadline)
        receipt['dispatch_queue_seconds']=round(queued,3)
        return result,preview,receipt
    finally:
        if acquired:DISPATCH_SLOT.release()


def authorized(c,pid,principal,fid=None,identity=None):
    """Trusted owner/session plus current project and optional immutable source."""
    try:
        if not isinstance(principal,dict) or not local_auth.session_active(c,principal['owner_id'],principal['session_id']):return False
        from backend.guard import Actor
        actor=Actor(principal['owner_id'],principal['session_id'])
        knowledge.require_project(c,pid,actor)
        if fid is not None:
            row=owned(c,pid,fid,actor)
            if identity is not None and (row['sha256'],row['version'])!=identity:return False
        return True
    except (KeyError,HTTPException,sqlite3.Error):return False


def authority(pid,principal,stop,fid=None,identity=None):
    def check():
        if stop.is_set():return False
        try:
            with get_db() as c:return authorized(c,pid,principal,fid,identity)
        except (sqlite3.Error,OSError):return False
    return check


def index_element(c, pid, fid, mime, page, element):
    c.execute('DELETE FROM knowledge_chunks WHERE id IN (SELECT chunk_id FROM document_chunk_refs WHERE element_id=?)', (element['id'],))
    if element.get('indexable') is False:return
    locator = element.get('locator',{})
    # Non-PDF pages are logical processing units; only document.locator describes
    # the actual sheet/slide/paragraph. Never invent Office printed page numbers.
    cited_page = page if mime == 'application/pdf' else None
    for chunk in knowledge.make_chunks([(cited_page, element.get('text',''))]):
        cid = uid()
        c.execute('INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?)', (cid,fid,pid,cited_page,chunk['line_start'],chunk['line_end'],chunk['text']))
        version=c.execute('SELECT version FROM document_originals WHERE file_id=?',(fid,)).fetchone()[0]
        ref = {'file_id':fid,'version':version,'element_id':element['id'],'revision':element['revision'],
               'page':page,'bbox':element.get('bbox'),'locator':locator,'mime':mime,
               'method':element.get('correction',{}).get('method') or element.get('quality',{}).get('method','extractor'),
               'review_required':bool(element.get('quality',{}).get('review_required') or element.get('correction'))}
        c.execute('INSERT INTO document_chunk_refs VALUES(?,?,?)',(cid,element['id'],pack(ref)))


def recount(c, fid):
    count, size = c.execute('SELECT COUNT(*),COALESCE(SUM(length(text)),0) FROM knowledge_chunks WHERE file_id=?',(fid,)).fetchone()
    if count > knowledge.MAX_CHUNKS or size > knowledge.MAX_EXTRACTED:
        raise ValueError('Document extracted text or chunk limit exceeded')
    c.execute('UPDATE knowledge_files SET chunk_count=?,char_count=? WHERE id=?',(count,size,fid))


def structured_quota(c,pid):
    total=0
    for table in ('document_pages','document_elements'):
        total+=c.execute(f'SELECT COALESCE(SUM(length(CAST(d.payload AS BLOB))),0) FROM {table} d JOIN knowledge_files f ON f.id=d.file_id WHERE f.project_id=?',(pid,)).fetchone()[0]
    total+=c.execute('SELECT COALESCE(SUM(length(CAST(v.payload AS BLOB))),0) FROM document_element_versions v JOIN document_elements e ON e.id=v.element_id JOIN knowledge_files f ON f.id=e.file_id WHERE f.project_id=?',(pid,)).fetchone()[0]
    if total>64*1024**2:raise HTTPException(413,'Project structured extraction/history limit is 64 MiB')


def store_page(c, pid, fid, mime, page, preview):
    page = dict(page); elements = page.pop('elements',[])
    number = page['number']
    old = c.execute('SELECT id FROM document_elements WHERE file_id=? AND page=?',(fid,number)).fetchall()
    for e in old:
        c.execute('DELETE FROM knowledge_chunks WHERE id IN (SELECT chunk_id FROM document_chunk_refs WHERE element_id=?)',(e[0],))
    c.execute('DELETE FROM document_pages WHERE file_id=? AND number=?',(fid,number))
    c.execute('INSERT INTO document_pages VALUES(?,?,?,?)',(fid,number,pack(page),preview))
    for element in elements:
        element = dict(element); element['id'] = fid+'-'+element['id']; element['revision']=1
        c.execute('INSERT INTO document_elements VALUES(?,?,?,?,?)',(element['id'],fid,number,1,pack(element)))
        c.execute('INSERT INTO document_element_versions VALUES(?,?,?,?,?)',(element['id'],1,pack(element),now(),element.get('method',page.get('method','extractor'))))
        index_element(c,pid,fid,mime,number,element)
    recount(c,fid)
    structured_quota(c,pid)
    total = c.execute('SELECT COALESCE(SUM(char_count),0) FROM knowledge_files WHERE project_id=?',(pid,)).fetchone()[0]
    preview_size = c.execute('SELECT COALESCE(SUM(length(p.preview)),0) FROM document_pages p JOIN knowledge_files f ON f.id=p.file_id WHERE f.project_id=?',(pid,)).fetchone()[0]
    if total > knowledge.MAX_PROJECT_EXTRACTED or preview_size > PROJECT_PREVIEW_BYTES:
        raise ValueError('Project extraction/preview storage limit exceeded')


def set_status(c, fid, status, error=None):
    c.execute('UPDATE document_originals SET status=? WHERE file_id=?',(status,fid))
    c.execute('UPDATE document_jobs SET status=?,error=?,updated_at=? WHERE file_id=?',(status,error,now(),fid))


def process_job(pid, fid, stop, principal=None):
    acquired = False; identity=None
    from backend.guard import Actor
    actor=Actor(principal['owner_id'],principal['session_id']) if isinstance(principal,dict) else None
    def terminal(status,error=None):
        if identity is None:return
        with get_db() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute('SELECT f.sha256,o.version FROM knowledge_files f JOIN document_originals o ON o.file_id=f.id WHERE f.id=? AND f.project_id=?',(fid,pid)).fetchone()
            if current is None:return
            if (current['sha256'],current['version'])==identity:set_status(c,fid,status,error)
    try:
        with get_db() as c:
            if not authorized(c,pid,principal,fid):return
            initial=owned(c,pid,fid,actor);identity=(initial['sha256'],initial['version'])
        while not acquired:
            if stop.is_set(): return
            acquired = SLOTS.acquire(timeout=.2)
        started = time.monotonic()
        with get_db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not authorized(c,pid,principal,fid,identity):return
            row = owned(c,pid,fid,actor)
            identity=(row['sha256'],row['version'])
            data = c.execute('SELECT original FROM document_originals WHERE file_id=?',(fid,)).fetchone()[0]
            config = json.loads(row['config']); set_status(c,fid,'running')
        permit=authority(pid,principal,stop,fid,identity)
        for number in range(1,row['page_count']+1):
            if stop.is_set(): break
            if not permit():
                terminal('interrupted','Authorization changed; no further page was accepted.');return
            if config.get('extractor')!=VERSION or config.get('extraction_fingerprint')!=extraction_fingerprint() or config.get('limits')!=asdict(load_limits()):
                with get_db() as c:set_status(c,fid,'interrupted','Extraction recipe changed; keep this version and import a separately named revision.')
                return
            if time.monotonic()-started >= MAX_JOB_SECONDS:
                with get_db() as c:set_status(c,fid,'interrupted','Time budget reached; resume remaining pages.')
                return
            with get_db() as c:
                if not c.execute('SELECT 1 FROM document_originals WHERE file_id=?',(fid,)).fetchone(): return
                done = c.execute('SELECT payload FROM document_pages WHERE file_id=? AND number=?',(fid,number)).fetchone()
                # Partial OCR is a result requiring inspection, not a failed worker.
                if done and json.loads(done[0])['status'] in {'complete','partial'}: continue
            try:
                result, preview, receipt = parse(data,row['filename'],number,config['languages'],stop,authorize=permit)
                page = result['page']
                with get_db() as c:
                    c.execute('BEGIN IMMEDIATE')
                    if stop.is_set() or not authorized(c,pid,principal,fid,identity):
                        # No derived content is persisted after authority loss.
                        return
                    store_page(c,pid,fid,row['mime'],page,preview)
                    resources = json.loads(c.execute('SELECT resources FROM document_jobs WHERE file_id=?',(fid,)).fetchone()[0])
                    resources.append({'page':number,**receipt})
                    c.execute('UPDATE document_jobs SET pages_done=(SELECT COUNT(*) FROM document_pages WHERE file_id=?),resources=?,updated_at=? WHERE file_id=?',(fid,pack(resources[-400:]),now(),fid))
            except (ValueError,OSError,HTTPException) as exc:
                if stop.is_set(): break
                if isinstance(exc,document_isolation.IsolationError) and exc.status_code==503:
                    terminal('interrupted','Isolated worker unavailable or busy; no failed page was invented. Resume explicitly.');return
                with get_db() as c:
                    c.execute('BEGIN IMMEDIATE')
                    if not authorized(c,pid,principal,fid,identity):return
                    failure = {'number':number,'kind':'unknown','method':'unavailable','status':'failed','warnings':[str(exc)[:500]],'elements':[]}
                    store_page(c,pid,fid,row['mime'],failure,None)
                    c.execute('UPDATE document_jobs SET pages_done=(SELECT COUNT(*) FROM document_pages WHERE file_id=?),error=?,updated_at=? WHERE file_id=?',(fid,str(exc)[:500],now(),fid))
        with get_db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not authorized(c,pid,principal,fid,identity):return
            pages = [json.loads(r[0]) for r in c.execute('SELECT payload FROM document_pages WHERE file_id=?',(fid,))]
            if stop.is_set(): status='cancelled'
            elif len(pages)==row['page_count'] and all(p['status']=='complete' for p in pages): status='complete'
            elif any(p['status']!='failed' for p in pages): status='partial'
            else: status='failed'
            set_status(c,fid,status)
    except Exception:
        terminal('failed','Document task failed; the original is retained. Resume is available.')
    finally:
        try:
            if stop.is_set():
                terminal('cancelled')
            elif identity is not None:
                with get_db() as c:still_allowed=authorized(c,pid,principal,fid,identity)
                if not still_allowed:terminal('interrupted','Authorization changed; no further page was accepted.')
        finally:
            if acquired:SLOTS.release()


def enqueue(pid,fid,principal):
    with get_db() as c:
        if not authorized(c,pid,principal,fid):raise HTTPException(403,'Document processing authority changed')
    current=JOBS.get(fid)
    if current and not current['task'].done():raise HTTPException(409,'Document task is already active')
    if sum(not value['task'].done() for value in JOBS.values())>=3:raise HTTPException(429,'At most three queued/running document tasks')
    stop=threading.Event()
    principal={key:principal[key] for key in ('owner_id','session_id')}
    task=asyncio.create_task(asyncio.to_thread(process_job,pid,fid,stop,principal))
    JOBS[fid]={'stop':stop,'task':task}
    task.add_done_callback(lambda completed:JOBS.pop(fid,None) if JOBS.get(fid,{}).get('task') is completed else None)


async def shutdown():
    pending=list(JOBS.values())
    for job in pending:job['stop'].set()
    if pending:await asyncio.gather(*(job['task'] for job in pending),return_exceptions=True)


def cancel_file(fid):
    job=JOBS.get(fid)
    if job and not job['task'].done():job['stop'].set();return True
    return False


@router.get('/api/projects/{pid}/documents')
def list_documents(pid:str):
    with get_db() as c:
        actor=knowledge.require_project(c,pid)
        ids=[r[0] for r in c.execute('''SELECT f.id FROM knowledge_files f JOIN document_originals o ON o.file_id=f.id
            JOIN guard_file_policies p ON p.file_id=f.id WHERE f.project_id=? AND p.project_id=f.project_id
            AND p.owner_id=? AND p.revoked=0 ORDER BY f.created_at DESC''',(pid,actor.owner_id))]
        return [describe(c,pid,fid) for fid in ids]


@router.post('/api/projects/{pid}/documents',status_code=202)
async def upload(pid:str,file:UploadFile=File(...),languages_:str=Form(default='ara+eng+heb',alias='languages')):
    try:return await _upload(pid,file,languages_)
    finally:await file.close()


async def _upload(pid,file,languages_):
    principal=local_auth.current_principal()
    if worker_status().get('reason_code')=='packaged_worker_unavailable':require_worker()  # packaged build without the worker: refuse before any database access
    with get_db() as c:knowledge.require_project(c,pid)  # otherwise authority before availability
    if not worker_status()['available']:
        require_worker()
    filename=knowledge.safe_filename(file.filename)
    try:language=languages(languages_)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    limits=load_limits(); config={'languages':language,'extractor':VERSION,'limits':asdict(limits),'extraction_fingerprint':extraction_fingerprint()}
    with get_db() as c:
        if not authorized(c,pid,principal):raise HTTPException(403,'Document processing authority unavailable')
    data=await file.read(limits.max_input_bytes+1);await file.close()
    if not data or len(data)>limits.max_input_bytes:raise HTTPException(413,'File is empty or exceeds upload byte limit')
    digest=hashlib.sha256(data).hexdigest()
    if sum(not j['task'].done() for j in JOBS.values())>=3:raise HTTPException(429,'Document queue is full')
    if not INSPECTION_SLOTS.acquire(blocking=False):raise HTTPException(429,'Two file validations are already running')
    try:
        stop=threading.Event()
        result,_,_=await asyncio.to_thread(parse,data,filename,0,language,stop,authorize=authority(pid,principal,stop))
    except document_isolation.IsolationError as exc:raise HTTPException(exc.status_code,exc.code) from exc
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc
    finally:INSPECTION_SLOTS.release()
    # Inspection yields the event loop; recheck before the no-await commit/enqueue.
    if sum(not j['task'].done() for j in JOBS.values())>=3:raise HTTPException(429,'Document queue filled while validating; no original was saved')
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        if not authorized(c,pid,principal):raise HTTPException(403,'Document processing authority changed; no original was saved')
        previous=c.execute('SELECT id FROM knowledge_files WHERE project_id=? AND sha256=? AND filename=?',(pid,digest,filename)).fetchone()
        if previous:
            knowledge.require_file(c,pid,previous[0])
            doc=c.execute('SELECT config FROM document_originals WHERE file_id=?',(previous[0],)).fetchone()
            if doc:
                if json.loads(doc[0])!=config:raise HTTPException(409,'Original already exists with different extraction settings; preserve it and use a different filename for a new revision.')
                return describe(c,pid,previous[0])
            # Upgrade a legacy text-only import without overwriting its immutable bytes/hash.
            fid=previous[0]
        else:fid=uid()
        count,size=c.execute('SELECT COUNT(*),COALESCE(SUM(byte_size),0) FROM knowledge_files WHERE project_id=?',(pid,)).fetchone()
        if (not previous and count>=knowledge.MAX_PROJECT_FILES) or size+(0 if previous else len(data))>PROJECT_ORIGINAL_BYTES:raise HTTPException(413,'Project file count/original byte limit reached')
        if not previous:
            c.execute('INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)',(fid,pid,filename,len(data),digest,0,result['page_count'],0,now()))
        else:
            c.execute('DELETE FROM knowledge_chunks WHERE file_id=?',(fid,))
            c.execute('UPDATE knowledge_files SET page_count=?,chunk_count=0,char_count=0 WHERE id=?',(result['page_count'],fid))
        version=c.execute('SELECT COALESCE(MAX(o.version),0)+1 FROM document_originals o JOIN knowledge_files f ON f.id=o.file_id WHERE f.project_id=? AND f.filename=?',(pid,filename)).fetchone()[0]
        c.execute('INSERT INTO document_originals VALUES(?,?,?,?,?,?,?)',(fid,data,result['mime'],version,pack(config),pack(result['metadata']),'queued'))
        c.execute('INSERT INTO document_jobs(id,file_id,status,pages_done,page_count,error,updated_at) VALUES(?,?,?,?,?,?,?)',(uid(),fid,'queued',0,result['page_count'],None,now()))
    enqueue(pid,fid,principal)
    with get_db() as c:return describe(c,pid,fid)


@router.get('/api/projects/{pid}/documents/{fid}')
def get_document(pid:str,fid:str):
    with get_db() as c:return describe(c,pid,fid)


@router.get('/api/projects/{pid}/documents/{fid}/pages/{number}')
def get_page(pid:str,fid:str,number:int):
    with get_db() as c:return page_data(c,pid,fid,number)


@router.get('/api/projects/{pid}/documents/{fid}/original')
def original(pid:str,fid:str):
    with get_db() as c:
        row=owned(c,pid,fid);data=c.execute('SELECT original FROM document_originals WHERE file_id=?',(fid,)).fetchone()[0]
    return Response(data,media_type=row['mime'],headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row['filename']),'Cache-Control':'no-store'})


@router.get('/api/projects/{pid}/documents/{fid}/pages/{number}/preview')
def preview(pid:str,fid:str,number:int):
    with get_db() as c:
        owned(c,pid,fid);row=c.execute('SELECT preview FROM document_pages WHERE file_id=? AND number=?',(fid,number)).fetchone()
        if not row or not row[0]:raise HTTPException(404,'No rendered preview; use the structured text/cell evidence')
    return Response(row[0],media_type='image/png',headers={'Cache-Control':'no-store'})


@router.post('/api/projects/{pid}/documents/{fid}/cancel')
async def cancel(pid:str,fid:str):
    with get_db() as c:
        owned(c,pid,fid)
        signalled=cancel_file(fid)
        set_status(c,fid,'cancelling' if signalled else 'cancelled')
    # The worker may have finished and recorded 'cancelled' before the line above ran; the
    # write above would then leave the job in 'cancelling' forever. Settle it here.
    job=JOBS.get(fid)
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        owned(c,pid,fid)
        current=c.execute('SELECT status FROM document_jobs WHERE file_id=?',(fid,)).fetchone()
        if signalled and current and current[0]=='cancelling' and (job is None or job['task'].done()):set_status(c,fid,'cancelled')
        return describe(c,pid,fid)


@router.post('/api/projects/{pid}/documents/{fid}/resume')
async def resume(pid:str,fid:str):
    principal=local_auth.current_principal()
    if worker_status().get('reason_code')=='packaged_worker_unavailable':require_worker()  # packaged build without the worker: refuse before any database access
    with get_db() as c:owned(c,pid,fid)  # otherwise authority before availability: a revoked project learns nothing about the worker
    require_worker()
    with get_db() as c:
        row=owned(c,pid,fid)
        if row['status']=='complete':return describe(c,pid,fid)
        config=json.loads(row['config'])
        if config['limits']!=asdict(load_limits()) or config.get('extractor')!=VERSION or config.get('extraction_fingerprint')!=extraction_fingerprint():raise HTTPException(409,'Extraction recipe changed; do not mix page configurations. Import a separately named revision.')
    enqueue(pid,fid,principal)
    with get_db() as c:
        set_status(c,fid,'queued');return describe(c,pid,fid)


class Correction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text:str=Field(max_length=100000)
    expected_revision:int=Field(ge=1)


@router.patch('/api/projects/{pid}/documents/{fid}/elements/{eid}')
def correct(pid:str,fid:str,eid:str,body:Correction):
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=owned(c,pid,fid)
        e=c.execute('SELECT * FROM document_elements WHERE id=? AND file_id=?',(eid,fid)).fetchone()
        if not e:raise HTTPException(404,'Element not in document')
        if e['revision']!=body.expected_revision:raise HTTPException(409,'Element was changed; refresh before correcting')
        if e['revision']>=200:raise HTTPException(413,'Element correction history limit is 200 revisions')
        element=json.loads(e['payload'])
        if element['type']=='table':raise HTTPException(422,'Table cell correction is not available; free-text edits would invalidate cell evidence')
        element.update(text=body.text,revision=e['revision']+1,correction={'method':'user_correction','created_at':now()})
        element['indexable']=True  # Explicit user text, visibly versioned; raw source is unchanged.
        c.execute('UPDATE document_elements SET revision=?,payload=? WHERE id=?',(element['revision'],pack(element),eid))
        c.execute('INSERT INTO document_element_versions VALUES(?,?,?,?,?)',(eid,element['revision'],pack(element),now(),'user_correction'))
        index_element(c,pid,fid,row['mime'],e['page'],element);recount(c,fid)
        structured_quota(c,pid)
        if c.execute('SELECT COALESCE(SUM(char_count),0) FROM knowledge_files WHERE project_id=?',(pid,)).fetchone()[0]>knowledge.MAX_PROJECT_EXTRACTED:
            raise HTTPException(413,'Project extracted-text limit reached; correction was not saved')
        return element


@router.get('/api/projects/{pid}/documents/{fid}/elements/{eid}/versions')
def versions(pid:str,fid:str,eid:str):
    with get_db() as c:
        owned(c,pid,fid)
        if not c.execute('SELECT 1 FROM document_elements WHERE id=? AND file_id=?',(eid,fid)).fetchone():raise HTTPException(404,'Element not in document')
        return [{**json.loads(r['payload']),'created_at':r['created_at'],'method':r['method']} for r in c.execute('SELECT * FROM document_element_versions WHERE element_id=? ORDER BY revision',(eid,))]


class TableCalculation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    element_id:str=Field(min_length=1,max_length=100)
    operation:str=Field(pattern='^(sum|mean|count)$')
    column:str=Field(pattern='^[A-Z]{1,3}$')
    row_start:int=Field(ge=1,le=20000)
    row_end:int=Field(ge=1,le=20000)
    unit:str=Field(default='',max_length=24)


def calculate_table(cells,body):
    if not 0<=body.row_end-body.row_start<500:raise HTTPException(422,'Choose an ordered range of at most 500 cells')
    # Word/PowerPoint retain RnCn locations. Letter columns here are calculation
    # coordinates only; receipts always return the original native address.
    def cell_key(cell):
        native=cell['address'];match=re.fullmatch(r'R([1-9][0-9]*)C([1-9][0-9]*)',native)
        return column_name(int(match[2]))+match[1] if match else native
    lookup={cell_key(cell):cell for cell in cells}
    if len(lookup)!=len(cells):raise HTTPException(422,'Duplicate cell coordinates cannot be used for arithmetic')
    selected=[];numbers=[];warnings=[];units=set()
    unit_columns={re.sub(r'\d+$','',cell_key(cell)) for cell in cells if cell.get('row')==1 and cell.get('value','').strip().casefold() in {'unit','units','currency','الوحدة','العملة'}}
    for row in range(body.row_start,body.row_end+1):
        address=body.column+str(row);cell=lookup.get(address)
        if cell is None:raise HTTPException(422,'Missing cell in requested range: '+address)
        selected.append(cell);raw=str(cell.get('value',''))
        unit=cell.get('unit') or next((str(lookup.get(str(col)+str(row),{}).get('value','')).strip() for col in unit_columns),'')
        if unit:units.add(unit)
        if cell.get('formula') is not None or cell.get('kind')=='formula':raise HTTPException(422,'Formula caches are not verified or recalculated: '+address)
        if body.operation=='count':
            if raw!='':numbers.append(Fraction(1))
            continue
        if cell.get('kind') in {'boolean','date','error','unknown','formula'}:
            raise HTTPException(422,'Cell type is not a verified decimal amount: '+address)
        if 'style_index' in cell and (cell.get('number_format_id')!=0 or cell.get('number_format')!='General'):
            raise HTTPException(422,'Date, percentage or unverified number formatting requires inspection: '+address)
        normalized=raw.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫','01234567890123456789.'))
        if len(normalized)>64 or not re.fullmatch(r'[+-]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?',normalized):
            raise HTTPException(422,'Blank, identifier or non-decimal cell requires inspection: '+address)
        numbers.append(Fraction(normalized))
    if len(units)>1 or (units and body.unit and body.unit not in units):raise HTTPException(422,'Mixed or conflicting units; no conversion was performed')
    unit=next(iter(units),body.unit)
    if body.unit and not units:warnings.append('Unit supplied by the user; source cells contain no independently verified unit.')
    value=sum(numbers,Fraction(0))
    if body.operation=='mean':value/=len(numbers)
    if body.operation=='count':unit='cells';warnings.append('Count includes nonempty cells only; blanks are not zero.')
    with localcontext() as ctx:
        ctx.prec=100
        decimal=Decimal(value.numerator)/Decimal(value.denominator)
        rendered=format(decimal,'f')
    exact=str(value.numerator) if value.denominator==1 else f'{value.numerator}/{value.denominator}'
    return {'exact':exact,'decimal':rendered,'decimal_exact':Fraction(rendered)==value,'unit':unit,'operation':body.operation},selected,warnings


@router.post('/api/projects/{pid}/documents/{fid}/table-calculate')
def table_calculate(pid:str,fid:str,body:TableCalculation):
    with get_db() as c:
        document=owned(c,pid,fid)
        row=c.execute('SELECT * FROM document_elements WHERE id=? AND file_id=?',(body.element_id,fid)).fetchone()
        if not row:raise HTTPException(404,'Table not in document')
        element=json.loads(row['payload'])
        if element['type']!='table':raise HTTPException(422,'Element is not a structured table')
        result,selected,warnings=calculate_table(element.get('cells',[]),body)
    return {'result':result,'cellrefs':[{'file_id':fid,'version':document['version'],'page':row['page'],'element_id':element['id'],'revision':element['revision'],'address':cell['address'],'value':cell['value']} for cell in selected],
            'scope':{'row_start':body.row_start,'row_end':body.row_end,'column':body.column,'all_requested_cells':True},'warnings':warnings}


def validate_scope(c,pid,scope,*,actor=None):
    if len({s['file_id'] for s in scope or []})!=len(scope or []):raise HTTPException(422,'Each selected file may occur only once')
    for choice in scope or []:
        fid=choice['file_id'];row=owned(c,pid,fid,actor)
        pages=choice.get('pages') or []
        if len(set(pages))!=len(pages) or any(n>row['page_count'] for n in pages):raise HTTPException(422,'Selected page outside original document')


def filter_scope(c,pid,rows,scope,*,actor=None):
    if not scope:return rows
    validate_scope(c,pid,scope,actor=actor)
    selection={s['file_id']:set(s.get('pages') or []) for s in scope}
    selected=[]
    for row in rows:
        if row['file_id'] not in selection:continue
        pages=selection[row['file_id']]
        ref=c.execute('SELECT payload FROM document_chunk_refs WHERE chunk_id=?',(row['id'],)).fetchone()
        if ref and (not pages or json.loads(ref[0])['page'] in pages):selected.append(row)
    return selected


def enrich(c,items):
    for item in items:
        row=c.execute('SELECT payload FROM document_chunk_refs WHERE chunk_id=?',(item['id'],)).fetchone()
        if row:item['document']=json.loads(row[0])
    return items


class Comparison(BaseModel):
    model_config=ConfigDict(extra='forbid')
    left:FileSelection
    right:FileSelection


@router.post('/api/projects/{pid}/documents/compare')
def compare(pid:str,body:Comparison):
    """Full selected structured text/cells, never RAG snippets or inferred conflict."""
    with get_db() as c:
        def read(c,choice):
            selected=choice.model_dump();validate_scope(c,pid,[selected]);row=owned(c,pid,choice.file_id)
            numbers=sorted(choice.pages or range(1,row['page_count']+1));pages=[];coverage={'selected':len(numbers),'processed':0,'failed':0,'partial':0}
            for n in numbers:
                try:page=page_data(c,pid,choice.file_id,n)
                except HTTPException:page={'number':n,'status':'unprocessed','elements':[]}
                if page['status'] in {'complete','partial'}:coverage['processed']+=1
                else:coverage['failed']+=1
                if page['status']=='partial':coverage['partial']+=1
                pages.append(page)
            return row,pages,coverage
        left,lp,lc=read(c,body.left);right,rp,rc=read(c,body.right)
    fingerprints=[hashlib.sha256(pack(pages).encode()).hexdigest() for pages in (lp,rp)]
    differences=[];total=0;shortened=False
    def ref(doc,page,element,address=None):
        if not element:return None
        locator=dict(element.get('locator',{}))
        if address:locator['cells']=[address]
        return {'file_id':doc['id'],'version':doc['version'],'element_id':element['id'],'revision':element['revision'],
                'page':page,'bbox':element.get('bbox'),'locator':locator,'mime':doc['mime']}
    def difference(location,before,after,lr,rr):
        nonlocal total,shortened
        if before==after:return
        total+=1
        if len(differences)>=100:return
        if len(before)>2000 or len(after)>2000:shortened=True
        differences.append({'location':location,'before':before[:2000],'after':after[:2000],'left':lr,'right':rr})
    for pos in range(max(len(lp),len(rp))):
        l=lp[pos] if pos<len(lp) else {'number':None,'status':'absent','elements':[]}
        r=rp[pos] if pos<len(rp) else {'number':None,'status':'absent','elements':[]}
        if any(p['status'] in {'failed','unprocessed'} for p in (l,r)):continue
        le=[e for e in l['elements'] if e.get('indexable') is not False];re_=[e for e in r['elements'] if e.get('indexable') is not False]
        for i in range(max(len(le),len(re_))):
            a=le[i] if i<len(le) else None;b=re_[i] if i<len(re_) else None
            location=f"unit {l['number']} / {r['number']}, element {i+1}"
            lr=ref(left,l['number'],a);rr=ref(right,r['number'],b)
            if (a or {}).get('type')=='table' and (b or {}).get('type')=='table':
                ac={x['address']:x for x in a.get('cells',[])};bc={x['address']:x for x in b.get('cells',[])}
                for address in sorted(ac.keys()|bc.keys()):
                    def value(cell):return pack({k:cell.get(k) for k in ('value','formula','cached_value','kind','number_format_id','number_format')}) if cell else '[missing cell]'
                    difference(location+' '+address,value(ac.get(address)),value(bc.get(address)),ref(left,l['number'],a,address),ref(right,r['number'],b,address))
            else:difference(location,(a or {}).get('text',''),(b or {}).get('text',''),lr,rr)
    try:
        with get_db() as c:
            current_left,clp,_=read(c,body.left);current_right,crp,_=read(c,body.right)
            current_fingerprints=[hashlib.sha256(pack(pages).encode()).hexdigest() for pages in (clp,crp)]
            if current_fingerprints!=fingerprints or any(current['sha256']!=old['sha256'] or current['version']!=old['version'] for current,old in ((current_left,left),(current_right,right))):
                raise HTTPException(409,'Documents changed during comparison; run it again')
    except HTTPException as exc:
        if exc.status_code==404:raise HTTPException(409,'Document access changed during comparison; no result was returned') from exc
        raise
    return {'kind':'structural_difference','left':{k:left[k] for k in ('id','filename','version','sha256')},
            'right':{k:right[k] for k in ('id','filename','version','sha256')},'coverage':{'left':lc,'right':rc},
            'differences':differences,'total_differences':total,'truncated':total>100 or shortened,
            'warnings':['Compares processing units/elements by selected order and table cell addresses. Does not align moved paragraphs or compare image pixels.',
                        'A difference is not a factual contradiction. Dates, versions, units and scope still require review. Missing/failed processing is excluded, not labelled deleted content.',
                        'Only the first 100 differences and 2000 characters per side are displayed; counts cover the complete selected extracted content.']}


@router.get('/api/document-capabilities')
async def capabilities():
    worker=worker_status()
    # These are the reviewed image recipe's declared local languages, not a
    # host-directory probe or a claim that every installed image passed QA.
    available=['ara','eng','heb'] if worker['available'] else []
    from backend import media
    try:vision={**await media.inspect_vision(),'available':True,'experimental':True}
    except (HTTPException,ValueError,OSError):vision={'available':False,'model':None,'experimental':True}
    return {**worker,'worker':worker,'formats':[{'extension':ext,'available':worker['available'],'tested':worker.get('tested',False),'status':'experimental' if worker['available'] else 'unavailable','reason_code':worker['reason_code'],'detail':detail} for ext,detail in [
      ('pdf','Native text, raster page preview, local OCR fallback; stored reading order; no inferred table grid'),
      ('png','Local OCR and source preview'),('jpeg','Local OCR and source preview'),('webp','Local OCR and source preview'),
      ('txt','UTF-8 lines'),('md','UTF-8 source lines'),('csv','UTF-8 strings, rows/cells; blanks preserved'),
      ('docx','Body paragraphs and tables; no printed pagination'),('xlsx','Sheet cells, formulas and cached values; no recalculation'),
      ('pptx','Slide text/tables; no rendered Office layout')]],
      'ocr':{'available':worker['available'] and len(available)==3,'reason_code':worker['reason_code'],'languages':available,'engine':'tesseract.js 7.0.0 / tessdata_fast','experimental':True},
      'vision':vision,'limits':asdict(load_limits()),
      'notice':'Local extraction; no training. OCR scores are engine signals, not answer confidence. Runtime availability is not a language quality guarantee.'}
