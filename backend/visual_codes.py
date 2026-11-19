"""Project-owned deterministic code assets through the fixed isolated worker."""
import asyncio,hashlib,json,threading,time
from fastapi import HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field,model_validator
from typing import Literal
from backend import isolated_visual_codes as protocol,document_isolation,media
from backend.db import get_db,data_dir,ROOT,now
from backend.isolated_worker import execute,load_config,IsolatedError
from backend.isolated_journal import Journal,assert_ready
from backend.isolated_locks import worker_lease,LeaseError
from backend.visual_authority import require_project,allowed


class CodeInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    kind:Literal['qr','code128']
    text:str=Field(min_length=1,max_length=512)
    scale:int=Field(default=4,ge=3,le=6)
    request_key:str=Field(min_length=8,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')
    @model_validator(mode='after')
    def checked(self):
        protocol.text_input(self.text.encode('utf-8'),self.options())
        return self
    def options(self):return {'operation':'code','kind':self.kind,'scale':self.scale}


def init(c):
    c.execute('CREATE TABLE IF NOT EXISTS visual_code_requests(project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,state TEXT NOT NULL,asset_id TEXT,error TEXT,created_at TEXT NOT NULL,PRIMARY KEY(project_id,request_key))')
    c.execute("UPDATE visual_code_requests SET state='interrupted',error='Service restarted; use a new request key' WHERE state='running'")


def status():
    state=document_isolation.status();reviewed=False
    try:
        record=json.loads((ROOT/'config/visual-code-runtime.json').read_bytes())
        reviewed=record.get('protocol')==protocol.VERSION and record.get('image_id')==state.get('image_id') and record.get('tested') is True
    except (OSError,ValueError):pass
    return {'available':state['available'] and reviewed,'configured':state['configured'],'tested':reviewed,
      'reason':None if reviewed else 'Verified QR/Code128 worker image is not configured','formats':['qr','code128'],
      'scope':'Black/white PNG, literal payload, generated output decoded before saving; no print profile or scene text claim',
      'image_id':state.get('image_id'),'protocol':protocol.VERSION,'host_fallback':False}


def generate(raw,options,stop,authorize):
    protocol.text_input(raw,options)
    try:
        if not authorize():raise HTTPException(404,'Visual resource unavailable')
        if stop.is_set():raise HTTPException(499,'Code creation cancelled')
        with worker_lease():
            cfg=load_config(data_dir()/'isolated-workers.json');assert_ready()
            if 'document_page' not in cfg.images:raise HTTPException(503,'Code worker is not configured')
            journal=Journal(cfg,'document_page')
            receipt=execute({'input.bin':raw,'metadata.json':protocol.dump(options)},'document_page',['input.bin'],config=cfg,
                stop=stop,authorize=authorize,timeout_seconds=25,memory_mb=512,progress=journal.event)
            journal.finish(receipt)
            if stop.is_set() or receipt['status']=='cancelled':raise HTTPException(499,'Code creation cancelled')
            if receipt['status']=='revoked' or not authorize():raise HTTPException(404,'Visual resource unavailable')
            if receipt['status']=='timeout':raise HTTPException(504,'Code creation exceeded its time limit')
            if receipt['status']!='completed' or not receipt.get('cleanup_verified') or not receipt.get('policy_inspected'):raise HTTPException(503,'Code worker failed; no image accepted')
            picture,proof=protocol.response(receipt['stdout'].encode(),raw,options)
            proof['isolation']={k:receipt[k] for k in ('status','cleanup_verified','policy_inspected')}
            proof['isolation'].update(image_id=cfg.images['document_page'],host_fallback=False,protocol=protocol.VERSION)
            return picture,proof
    except HTTPException:raise
    except (ValueError,TypeError,KeyError,OSError,RuntimeError,LeaseError,IsolatedError):raise HTTPException(503,'Code worker or decoded-output verification unavailable') from None


async def create(project_id:str,body:CodeInput,request:Request):
    from backend import visuals as v
    digest=hashlib.sha256(protocol.dump(body.model_dump(exclude={'request_key'}))).hexdigest()
    with get_db() as c:
        acting=require_project(c,project_id)
        old=c.execute('SELECT * FROM visual_code_requests WHERE project_id=? AND request_key=?',(project_id,body.request_key)).fetchone()
        if old:
            if old['request_hash']!=digest:raise HTTPException(409,'Request key was used for different input')
            if old['state']=='completed':return v.asset_metadata(v.asset(c,project_id,old['asset_id']))
            raise HTTPException(409,'The prior attempt did not finish. Start a new attempt after checking saved images.')
    if not status()['available']:raise HTTPException(503,'Verified code worker is unavailable')
    job=media.begin_media('visual-code');stop=threading.Event();task=None;claimed=False
    try:
        with get_db() as c:
            c.execute('BEGIN IMMEDIATE');require_project(c,project_id,acting)
            if c.execute('SELECT 1 FROM visual_code_requests WHERE project_id=? AND request_key=?',(project_id,body.request_key)).fetchone():raise HTTPException(409,'Code request is already running')
            c.execute('INSERT INTO visual_code_requests VALUES(?,?,?,?,?,?,?)',(project_id,body.request_key,digest,'running',None,None,now()));claimed=True
        authorize=lambda:allowed(project_id,acting)
        task=asyncio.create_task(asyncio.to_thread(generate,body.text.encode('utf-8'),body.options(),stop,authorize))
        picture,proof=await media.await_work(asyncio.ensure_future(asyncio.shield(task)),job,request,30)
        with get_db() as c:
            c.execute('BEGIN IMMEDIATE');require_project(c,project_id,acting)
            if job['stop'].is_set():raise HTTPException(499,'Code creation cancelled')
            meta={'mime':'image/png','width':proof['width'],'height':proof['height']}
            result=v.store_asset(c,project_id,picture,meta,body.kind+'-'+digest[:12]+'.png',{'kind':'machine_code','proof':proof,'request_sha256':digest,
                'notice':'Exact generated payload decoded; scan after printing/resizing. No professional print profile certification.'})
            changed=c.execute("UPDATE visual_code_requests SET state='completed',asset_id=? WHERE project_id=? AND request_key=? AND request_hash=? AND state='running'",(result['id'],project_id,body.request_key,digest))
            if changed.rowcount!=1:raise HTTPException(409,'Code request state changed')
        return result
    except BaseException:
        if claimed:
            with get_db() as c:c.execute("UPDATE visual_code_requests SET state='failed',error='No verified image published' WHERE project_id=? AND request_key=? AND state='running'",(project_id,body.request_key))
        raise
    finally:
        stop.set()
        if task:await asyncio.gather(task,return_exceptions=True)
        media.finish_media(job)
