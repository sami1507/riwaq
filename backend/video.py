"""Local project-scoped media originals, immutable edits and bounded export jobs.

No network provider, model download, speech synthesis or paid fallback occurs here.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from backend.db import data_dir, get_db, uid, now, event
from backend.knowledge import require_project, safe_filename
from backend.visuals import packed
from backend import video_schema as s

router=APIRouter(prefix='/api/projects/{project_id}/video',tags=['video'])
ACTIVE={}
RUNNING=('queued','running','cancelling')
PROBE_SLOT=threading.BoundedSemaphore(1)
ASSET_METADATA_COLUMNS='id,project_id,filename,mime,extension,kind,sha256,byte_size,metadata,origin,created_at'

def digest(raw): return hashlib.sha256(raw).hexdigest()

def init_video(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS video_assets(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,filename TEXT NOT NULL,mime TEXT NOT NULL,extension TEXT NOT NULL,kind TEXT NOT NULL,sha256 TEXT NOT NULL,byte_size INTEGER NOT NULL,data BLOB NOT NULL,metadata TEXT NOT NULL,origin TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS video_asset_dedup ON video_assets(project_id,sha256,filename);
    CREATE TABLE IF NOT EXISTS video_timelines(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,title TEXT NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS video_versions(timeline_id TEXT NOT NULL REFERENCES video_timelines(id) ON DELETE CASCADE,version INTEGER NOT NULL,title TEXT NOT NULL,spec TEXT NOT NULL,sha256 TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(timeline_id,version));
    CREATE TABLE IF NOT EXISTS video_jobs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,timeline_id TEXT NOT NULL REFERENCES video_timelines(id) ON DELETE CASCADE,version INTEGER NOT NULL,idempotency_key TEXT NOT NULL,input_sha256 TEXT NOT NULL,status TEXT NOT NULL,progress TEXT NOT NULL,report TEXT NOT NULL,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(project_id,idempotency_key));
    CREATE UNIQUE INDEX IF NOT EXISTS one_video_export ON video_jobs((1)) WHERE status IN ('queued','running','cancelling');
    CREATE TABLE IF NOT EXISTS video_outputs(job_id TEXT PRIMARY KEY REFERENCES video_jobs(id) ON DELETE CASCADE,movie BLOB NOT NULL,poster BLOB NOT NULL,sha256 TEXT NOT NULL,byte_size INTEGER NOT NULL);
    ''')
    c.execute("UPDATE video_jobs SET status='interrupted',error='Service restarted. Retry this saved version explicitly.',updated_at=? WHERE status IN ('queued','running','cancelling')",(now(),))

def used(c,pid=None):
    queries=[('SELECT COALESCE(SUM(byte_size+length(CAST(metadata AS BLOB))+length(CAST(origin AS BLOB))),0) FROM video_assets','project_id'),
             ('SELECT COALESCE(SUM(o.byte_size),0) FROM video_outputs o JOIN video_jobs j ON j.id=o.job_id','j.project_id'),
             ('SELECT COALESCE(SUM(length(CAST(v.spec AS BLOB))),0) FROM video_versions v JOIN video_timelines t ON t.id=v.timeline_id','t.project_id'),
             ('SELECT COALESCE(SUM(length(CAST(report AS BLOB))+length(CAST(progress AS BLOB))),0) FROM video_jobs','project_id')]
    return sum(c.execute(query+(' WHERE '+column+'=?' if pid is not None else ''),(pid,) if pid is not None else ()).fetchone()[0] for query,column in queries)

def reserve(c,pid,size):
    if used(c,pid)+size>s.PROJECT_BYTES or used(c)+size>s.GLOBAL_BYTES: raise HTTPException(413,'Video storage quota reached; remove unneeded exports or originals explicitly')

def asset(c,pid,aid):
    r=c.execute('SELECT * FROM video_assets WHERE project_id=? AND id=?',(pid,aid)).fetchone()
    if r is None: raise HTTPException(404,'Media original is unavailable in this project')
    return r

def asset_meta(r):
    return {**json.loads(r['metadata']),'id':r['id'],'filename':r['filename'],'mime':r['mime'],'kind':r['kind'],'sha256':r['sha256'],'bytes':r['byte_size'],'origin':json.loads(r['origin']),'created_at':r['created_at']}

def metadata(c,pid,aid):
    r=c.execute('SELECT '+ASSET_METADATA_COLUMNS+' FROM video_assets WHERE project_id=? AND id=?',(pid,aid)).fetchone()
    if r is None:raise HTTPException(404,'Media original is unavailable in this project')
    return asset_meta(r)

def timeline(c,pid,tid):
    r=c.execute('SELECT * FROM video_timelines WHERE project_id=? AND id=?',(pid,tid)).fetchone()
    if r is None: raise HTTPException(404,'Timeline not found in this project')
    return dict(r)

def version(c,pid,tid,v=None):
    t=timeline(c,pid,tid)
    r=c.execute('SELECT * FROM video_versions WHERE timeline_id=? AND version=?',(tid,v or t['version'])).fetchone()
    if r is None: raise HTTPException(404,'Timeline version not found')
    d=dict(r);d['spec']=json.loads(d['spec']);d['id']=tid;d['project_id']=pid;d['current_version']=t['version'];d['updated_at']=t['updated_at']
    d['missing_assets']=[aid for aid in s.asset_ids(d['spec']) if not c.execute('SELECT 1 FROM video_assets WHERE id=? AND project_id=?',(aid,pid)).fetchone()]
    return d

def check_assets(c,pid,spec,export=False):
    items={aid:metadata(c,pid,aid) for aid in s.asset_ids(spec)}
    if export and not spec['clips']: raise HTTPException(422,'Add at least one image or video clip')
    for clip in spec['clips']:
        a=items[clip['asset_id']]
        if a['kind'] not in ('image','video'): raise HTTPException(422,'Visual clip needs an image or video')
        if a['kind']=='image':
            if clip['start']!=0 or not clip['muted']: raise HTTPException(422,'An image has no source trim or original audio')
        else:
            if clip['motion']!='none': raise HTTPException(422,'Pan/zoom applies only to still images')
            if clip['start']+clip['duration']>a['duration']+.001: raise HTTPException(422,'Clip exceeds original duration; trim explicitly')
            if not clip['muted'] and not a['has_audio']: raise HTTPException(422,'Original video has no audio stream')
    for track in spec['audio']:
        a=items[track['asset_id']]
        if a['kind'] not in ('audio','video') or not a['has_audio']: raise HTTPException(422,'Audio track needs an actual audio stream')
        if track['source_start']+track['duration']>a['duration']+.001: raise HTTPException(422,'Audio exceeds original duration; trim explicitly')
    return items

def sniff(raw,name):
    ext=Path(name).suffix.lower()
    if raw.startswith(b'\x89PNG\r\n\x1a\n') and ext=='.png': return 'image','image/png','.png'
    if raw.startswith(b'\xff\xd8\xff') and ext in ('.jpg','.jpeg'): return 'image','image/jpeg','.jpg'
    if raw[:4]==b'RIFF' and raw[8:12]==b'WEBP' and ext=='.webp': return 'image','image/webp','.webp'
    if raw[4:8]==b'ftyp' and ext in ('.mp4','.m4v'): return 'video','video/mp4','.mp4'
    if raw.startswith(b'\x1aE\xdf\xa3') and ext=='.webm': return 'video','video/webm','.webm'
    if raw[:4]==b'RIFF' and raw[8:12]==b'WAVE' and ext=='.wav': return 'audio','audio/wav','.wav'
    if (raw.startswith(b'ID3') or len(raw)>2 and raw[0]==255 and raw[1]&224==224) and ext=='.mp3': return 'audio','audio/mpeg','.mp3'
    raise HTTPException(422,'Unsupported or mismatched media signature. Use PNG, JPEG, WebP, MP4, WebM, WAV or MP3')

@router.get('/status')
def status(project_id:str):
    from backend.video_capabilities import status as capabilities
    with get_db() as c:
        require_project(c,project_id);storage=used(c,project_id)
    return {**capabilities(),'storage_bytes':storage,'snapshot_notice':'Lightweight project snapshots exclude video. Full SQLite backup includes originals, versions and exports.'}

@router.get('/assets')
def assets(project_id:str):
    with get_db() as c:
        require_project(c,project_id)
        return [asset_meta(r) for r in c.execute('SELECT '+ASSET_METADATA_COLUMNS+' FROM video_assets WHERE project_id=? ORDER BY created_at DESC',(project_id,))]

@router.post('/assets',status_code=201)
async def upload(project_id:str,file:UploadFile=File(...),license:str=Form(default='User-provided; rights not independently verified',max_length=1000)):
    from backend.video_render import probe
    with get_db() as c: require_project(c,project_id)
    raw=await file.read(s.MAX_ASSET+1)
    if not raw or len(raw)>s.MAX_ASSET: raise HTTPException(413,'Upload must be between 1 byte and 8 MiB')
    name=safe_filename(file.filename or 'media');kind,mime,ext=sniff(raw,name);sha=digest(raw)
    with get_db() as c:
        old=c.execute('SELECT * FROM video_assets WHERE project_id=? AND sha256=? AND filename=?',(project_id,sha,name)).fetchone()
        if old: return asset_meta(old)
        reserve(c,project_id,len(raw))
    if not PROBE_SLOT.acquire(False): raise HTTPException(429,'One media inspection is already running; retry after it finishes')
    stop=threading.Event()
    try:
        with tempfile.TemporaryDirectory(prefix='video-probe-',dir=data_dir()) as tmp:
            folder=Path(tmp);path=folder/('original'+ext);path.write_bytes(raw)
            task=asyncio.create_task(asyncio.to_thread(probe,path,folder,stop))
            try: meta=await asyncio.shield(task)
            except asyncio.CancelledError:
                stop.set();await task;raise
        if meta.get('kind')!=kind: raise HTTPException(422,'Actual media streams do not match the requested file type')
        if kind!='image' and (not isinstance(meta.get('duration'),(int,float)) or not 0<meta['duration']<=120): raise HTTPException(422,'Media duration must be known and at most 120 seconds')
        if kind!='audio' and (not 0<meta.get('width',0)<=4096 or not 0<meta.get('height',0)<=4096 or meta['width']*meta['height']>4000000): raise HTTPException(422,'Media dimensions exceed 4 million pixels')
        with get_db() as c:
            c.execute('BEGIN IMMEDIATE');require_project(c,project_id)
            old=c.execute('SELECT * FROM video_assets WHERE project_id=? AND sha256=? AND filename=?',(project_id,sha,name)).fetchone()
            if old:return asset_meta(old)
            if c.execute('SELECT COUNT(*) FROM video_assets WHERE project_id=?',(project_id,)).fetchone()[0]>=100:raise HTTPException(413,'100 originals per project reached')
            origin=packed({'type':'user_upload','rights':license,'training_allowed':False});metadata=packed(meta)
            reserve(c,project_id,len(raw)+len(metadata.encode())+len(origin.encode()));aid=uid()
            c.execute('INSERT INTO video_assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(aid,project_id,name,mime,ext,kind,sha,len(raw),raw,metadata,origin,now()))
            event(c,project_id,'video_original_uploaded',aid)
            return asset_meta(asset(c,project_id,aid))
    except HTTPException:raise
    except Exception as exc: raise HTTPException(422,'Media inspection failed or hit its resource limit; original was not accepted') from exc
    finally: PROBE_SLOT.release()

def binary(data,mime,name):
    from urllib.parse import quote
    return Response(data,media_type=mime,headers={'Cache-Control':'no-store','Content-Disposition':"attachment; filename*=UTF-8''"+quote(name,safe='')})

@router.get('/assets/{asset_id}/content')
def original(project_id:str,asset_id:str):
    with get_db() as c:
        a=asset(c,project_id,asset_id);return binary(a['data'],a['mime'],a['filename'])

@router.delete('/assets/{asset_id}')
def delete_asset(project_id:str,asset_id:str):
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');asset(c,project_id,asset_id)
        affected=[]
        for r in c.execute('SELECT j.id,v.spec FROM video_jobs j JOIN video_versions v ON v.timeline_id=j.timeline_id AND v.version=j.version WHERE j.project_id=?',(project_id,)):
            if asset_id in s.asset_ids(json.loads(r['spec'])): affected.append(r['id'])
        for jid in affected:
            if jid in ACTIVE:ACTIVE[jid]['stop'].set()
            c.execute('DELETE FROM video_jobs WHERE id=?',(jid,))
        c.execute('DELETE FROM video_assets WHERE id=? AND project_id=?',(asset_id,project_id))
        event(c,project_id,'video_original_deleted',asset_id)
    return {'deleted':True,'invalidated_exports':len(affected),'notice':'Edit histories retain IDs; originals cannot be restored from history. Earlier backups are not erased.'}

def save_version(c,pid,tid,title,spec,v):
    text=packed(spec);reserve(c,pid,len(text.encode()))
    c.execute('INSERT INTO video_versions VALUES(?,?,?,?,?,?)',(tid,v,title,text,digest(text.encode()),now()))

@router.get('/timelines')
def timelines(project_id:str):
    with get_db() as c:
        require_project(c,project_id)
        return [dict(r) for r in c.execute('SELECT * FROM video_timelines WHERE project_id=? ORDER BY updated_at DESC',(project_id,))]

@router.post('/timelines',status_code=201)
def create_timeline(project_id:str,body:s.CreateTimeline):
    spec=body.spec.model_dump();tid=uid()
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');require_project(c,project_id);check_assets(c,project_id,spec)
        if c.execute('SELECT COUNT(*) FROM video_timelines WHERE project_id=?',(project_id,)).fetchone()[0]>=30:raise HTTPException(413,'30 timelines per project reached')
        c.execute('INSERT INTO video_timelines VALUES(?,?,?,?,?,?)',(tid,project_id,body.title,1,now(),now()))
        save_version(c,project_id,tid,body.title,spec,1);event(c,project_id,'video_timeline_created',tid)
        return version(c,project_id,tid)

@router.get('/timelines/{timeline_id}')
def get_timeline(project_id:str,timeline_id:str):
    with get_db() as c:return version(c,project_id,timeline_id)

def expect(t,expected):
    if t['version']!=expected:raise HTTPException(409,'Timeline changed. Reload before saving; your draft was not applied')
    if t['version']>=100:raise HTTPException(413,'100 immutable versions reached; create a new timeline')

@router.patch('/timelines/{timeline_id}')
def update_timeline(project_id:str,timeline_id:str,body:s.UpdateTimeline):
    spec=body.spec.model_dump()
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');t=timeline(c,project_id,timeline_id);expect(t,body.expected_version);check_assets(c,project_id,spec)
        n=t['version']+1;save_version(c,project_id,timeline_id,body.title,spec,n)
        c.execute('UPDATE video_timelines SET title=?,version=?,updated_at=? WHERE id=?',(body.title,n,now(),timeline_id))
        return version(c,project_id,timeline_id)

@router.get('/timelines/{timeline_id}/versions')
def versions(project_id:str,timeline_id:str):
    with get_db() as c:
        timeline(c,project_id,timeline_id)
        return [dict(r) for r in c.execute('SELECT version,title,sha256,created_at FROM video_versions WHERE timeline_id=? ORDER BY version DESC',(timeline_id,))]

@router.post('/timelines/{timeline_id}/restore')
def restore(project_id:str,timeline_id:str,body:s.RestoreTimeline):
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');t=timeline(c,project_id,timeline_id);expect(t,body.expected_version)
        previous=version(c,project_id,timeline_id,body.from_version);check_assets(c,project_id,previous['spec'])
        n=t['version']+1;save_version(c,project_id,timeline_id,previous['title'],previous['spec'],n)
        c.execute('UPDATE video_timelines SET title=?,version=?,updated_at=? WHERE id=?',(previous['title'],n,now(),timeline_id))
        return version(c,project_id,timeline_id)

@router.get('/timelines/{timeline_id}/source')
def source(project_id:str,timeline_id:str,version_number:int|None=Query(default=None,alias='version',ge=1,le=100)):
    with get_db() as c:
        v=version(c,project_id,timeline_id,version_number)
        originals=[metadata(c,project_id,aid) for aid in s.asset_ids(v['spec']) if aid not in v['missing_assets']]
    payload={'format':s.VERSION,'timeline':v,'originals':originals,'notice':'Edit source only. Download originals separately or use full SQLite backup. No embedded media or permission grants.'}
    return binary(json.dumps(payload,ensure_ascii=False,indent=2).encode(),'application/json','riwaq-video-source.json')

@router.get('/timelines/{timeline_id}/subtitles')
def subtitle_file(project_id:str,timeline_id:str,format:str=Query('srt',pattern='^(srt|vtt)$'),version_number:int|None=Query(default=None,alias='version',ge=1,le=100)):
    with get_db() as c:spec=version(c,project_id,timeline_id,version_number)['spec']
    return binary(s.subtitles(spec,format).encode(),'text/vtt' if format=='vtt' else 'application/x-subrip','subtitles.'+format)

@router.delete('/timelines/{timeline_id}')
def delete_timeline(project_id:str,timeline_id:str,expected_version:int=Query(ge=1,le=100)):
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');t=timeline(c,project_id,timeline_id)
        if t['version']!=expected_version:raise HTTPException(409,'Timeline changed; reload before deletion')
        for r in c.execute('SELECT id FROM video_jobs WHERE timeline_id=?',(timeline_id,)):
            if r['id'] in ACTIVE:ACTIVE[r['id']]['stop'].set()
        c.execute('DELETE FROM video_timelines WHERE id=?',(timeline_id,));event(c,project_id,'video_timeline_deleted',timeline_id)
    return {'deleted':True,'originals_retained':True}

def job(c,pid,jid):
    r=c.execute('SELECT j.*,t.version AS current_version FROM video_jobs j JOIN video_timelines t ON t.id=j.timeline_id WHERE j.id=? AND j.project_id=?',(jid,pid)).fetchone()
    if r is None:raise HTTPException(404,'Export job not found in this project')
    d=dict(r);d['progress']=json.loads(d['progress']);d['report']=json.loads(d['report']);d['outdated']=d['version']!=d.pop('current_version')
    return d

@router.get('/jobs')
def jobs(project_id:str):
    with get_db() as c:
        require_project(c,project_id)
        return [job(c,project_id,r['id']) for r in c.execute('SELECT id FROM video_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 100',(project_id,))]

@router.get('/jobs/{job_id}')
def get_job(project_id:str,job_id:str):
    with get_db() as c:return job(c,project_id,job_id)

@router.post('/jobs',status_code=202)
async def create_job(project_id:str,body:s.ExportInput):
    from backend.video_capabilities import status as capabilities
    jid=uid();fingerprint=digest(packed({'timeline_id':body.timeline_id,'version':body.version}).encode())
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');require_project(c,project_id)
        old=c.execute('SELECT id,input_sha256 FROM video_jobs WHERE project_id=? AND idempotency_key=?',(project_id,body.idempotency_key)).fetchone()
        if old:
            if old['input_sha256']!=fingerprint:raise HTTPException(409,'Idempotency key already belongs to different export input')
            return job(c,project_id,old['id'])
        v=version(c,project_id,body.timeline_id,body.version);check_assets(c,project_id,v['spec'],export=True)
        if not capabilities()['render']['available']:raise HTTPException(503,'Local video compositor unavailable; no network fallback')
        if ACTIVE or c.execute("SELECT 1 FROM video_jobs WHERE status IN ('queued','running','cancelling')").fetchone():raise HTTPException(409,'One export can run at a time; wait for cancelled workers to exit')
        if c.execute('SELECT COUNT(*) FROM video_jobs WHERE project_id=?',(project_id,)).fetchone()[0]>=100:raise HTTPException(413,'100 export attempts reached; remove unneeded timelines')
        reserve(c,project_id,s.MAX_OUTPUT)
        c.execute('INSERT INTO video_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(jid,project_id,body.timeline_id,body.version,body.idempotency_key,fingerprint,'queued','{}','{}',None,now(),now()))
    state={'stop':threading.Event()};ACTIVE[jid]=state;state['task']=asyncio.create_task(run_job(project_id,jid,state))
    with get_db() as c:return job(c,project_id,jid)

@router.post('/jobs/{job_id}/cancel')
def cancel(project_id:str,job_id:str):
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');r=job(c,project_id,job_id)
        if r['status'] in RUNNING:c.execute("UPDATE video_jobs SET status=?,updated_at=? WHERE id=?",('cancelling' if job_id in ACTIVE else 'cancelled',now(),job_id))
        if job_id in ACTIVE:ACTIVE[job_id]['stop'].set()
    with get_db() as c:return job(c,project_id,job_id)

@router.get('/jobs/{job_id}/content')
def output(project_id:str,job_id:str):
    with get_db() as c:
        j=job(c,project_id,job_id);r=c.execute('SELECT movie FROM video_outputs WHERE job_id=?',(job_id,)).fetchone()
        if j['status']!='completed' or r is None:raise HTTPException(404,'No completed export')
        return binary(r['movie'],'video/mp4',f"riwaq-video-v{j['version']}.mp4")

@router.get('/jobs/{job_id}/poster')
def poster(project_id:str,job_id:str):
    with get_db() as c:
        job(c,project_id,job_id);r=c.execute('SELECT poster FROM video_outputs WHERE job_id=?',(job_id,)).fetchone()
        if r is None:raise HTTPException(404,'No completed preview')
        return binary(r['poster'],'image/png','preview.png')

@router.get('/jobs/{job_id}/report')
def report_file(project_id:str,job_id:str):
    with get_db() as c:r=job(c,project_id,job_id)
    return binary(json.dumps(r['report'],ensure_ascii=False,indent=2).encode(),'application/json','video-export-report.json')

def progress(jid,value):
    with get_db() as c:c.execute("UPDATE video_jobs SET progress=?,updated_at=? WHERE id=? AND status='running'",(packed(value),now(),jid))

def render_job(pid,jid,state):
    from backend.video_render import render
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');j=job(c,pid,jid)
        if state['stop'].is_set():return
        v=version(c,pid,j['timeline_id'],j['version']);check_assets(c,pid,v['spec'],True)
        rows={aid:dict(asset(c,pid,aid)) for aid in s.asset_ids(v['spec'])}
        c.execute("UPDATE video_jobs SET status='running',updated_at=? WHERE id=?",(now(),jid))
    with tempfile.TemporaryDirectory(prefix='video-export-',dir=data_dir()) as tmp:
        folder=Path(tmp);manifest={}
        for aid,r in rows.items():
            path=folder/(aid+r['extension']);path.write_bytes(r.pop('data'))
            manifest[aid]={**asset_meta(r),'path':str(path)}
        payload={'spec':v['spec'],'assets':manifest}
        report=render(payload,folder,state['stop'],lambda value:progress(jid,value))
        movie=folder/'movie.mp4';preview=folder/'poster.png'
        if state['stop'].is_set():return
        if report.get('success') is not True or not movie.is_file() or not preview.is_file():raise ValueError('Worker did not complete a verified export')
        if not 0<movie.stat().st_size<=s.MAX_OUTPUT or preview.stat().st_size>6*1024**2:raise ValueError('Export size limit')
        raw=movie.read_bytes();png=preview.read_bytes()
        report.update({'timeline_version':j['version'],'spec_sha256':v['sha256'],'output_sha256':digest(raw),'output_bytes':len(raw),'provider_cost':0,'currency':'USD','cost_scope':'No paid provider used. Electricity and hardware cost not measured.','generated_visuals':False,'manual_review_required':True,'text_source':[{'id':x['id'],'text':x['text'],'start':x['start'],'end':x['end']} for x in v['spec']['texts']]})
        if len(packed(report).encode())>512*1024:raise ValueError('Receipt size limit')
        with get_db() as c:
            c.execute('BEGIN IMMEDIATE');latest=job(c,pid,jid)
            if state['stop'].is_set() or latest['status']=='cancelling':return
            check_assets(c,pid,v['spec'],True);reserve(c,pid,len(raw)+len(png)+len(packed(report).encode()))
            c.execute('INSERT INTO video_outputs VALUES(?,?,?,?,?)',(jid,raw,png,digest(raw),len(raw)+len(png)))
            c.execute("UPDATE video_jobs SET status='completed',report=?,updated_at=? WHERE id=?",(packed(report),now(),jid))
            event(c,pid,'video_export_completed',jid)

async def run_job(pid,jid,state):
    error=None;failure='export_failed'
    try:await asyncio.to_thread(render_job,pid,jid,state)
    except Exception as exc:
        # Do not publish decoder stderr, document text, host paths or environment.
        message=str(exc).lower()
        if 'text' in message and any(word in message for word in ('layout','renderer','glyph','font')):
            failure='text_layout';error='Exact text could not fit or render. Enlarge its box, reduce font size, or inspect supported characters; no words were removed.'
        elif 'time_limit' in message:
            failure='time_limit';error='The export reached its 300-second processing limit. Try a shorter timeline or smaller supported canvas.'
        elif 'memory_limit' in message:
            failure='memory_limit';error='The worker reached its memory limit. Use smaller sources or a shorter timeline.'
        elif isinstance(exc,HTTPException) and exc.status_code==413:
            failure='storage_limit';error='Video storage quota reached; remove unneeded exports or originals explicitly.'
        else:error='Export failed, a source was removed or could not be decoded. Saved edits and remaining originals are available.'
    finally:
        with get_db() as c:
            r=c.execute('SELECT status FROM video_jobs WHERE id=?',(jid,)).fetchone()
            if r and r['status'] in RUNNING:c.execute('UPDATE video_jobs SET status=?,error=?,report=?,updated_at=? WHERE id=?',('cancelled' if state['stop'].is_set() else 'failed',None if state['stop'].is_set() else error or 'Export did not complete',packed({'success':False,'failure_code':'cancelled' if state['stop'].is_set() else failure,'provider_cost':0,'generated_visuals':False}),now(),jid))
        ACTIVE.pop(jid,None)

async def shutdown():
    tasks=[]
    for state in list(ACTIVE.values()):state['stop'].set();tasks.append(state['task'])
    if tasks:await asyncio.gather(*tasks,return_exceptions=True)
