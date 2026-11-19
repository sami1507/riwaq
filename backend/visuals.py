from __future__ import annotations
import asyncio,base64,hashlib,io,json,threading,time,math
from pathlib import Path
from typing import Literal
from fastapi import APIRouter,File,HTTPException,Query,UploadFile,Request
from fastapi.responses import Response,StreamingResponse
from pydantic import BaseModel,ConfigDict,Field,StrictBool,model_validator,field_validator
from backend.db import get_db,uid,now,event
from backend.knowledge import safe_filename
from backend.visual_authority import require_project
router=APIRouter(prefix='/api/projects/{project_id}/visual',tags=['visual'])
MAX_ASSET=6*1024*1024
MAX_STORED_ASSET=16_500_000
MAX_PIXELS=4000000
PROJECT_BYTES=100*1024*1024
GLOBAL_BYTES=512*1024*1024
COLOR=r'^#[0-9a-fA-F]{6}$'
DECODER_SLOT=threading.BoundedSemaphore(1)
def packed(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(v):return hashlib.sha256(v).hexdigest()
class Input(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 @field_validator('*',mode='after')
 @classmethod
 def unicode_valid(cls,v):
  if isinstance(v,str):
   try:v.encode('utf-8')
   except UnicodeEncodeError:raise ValueError('Valid Unicode required; source was not modified')
  return v
class Rect(Input):
 id:str=Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
 x:float=Field(ge=0,le=2048,allow_inf_nan=False)
 y:float=Field(ge=0,le=2048,allow_inf_nan=False)
 width:float=Field(ge=1,le=2048,allow_inf_nan=False)
 height:float=Field(ge=1,le=2048,allow_inf_nan=False)
class TextLayer(Rect):
 text:str=Field(max_length=4000)
 language:Literal['ar','en','es','he']
 direction:Literal['rtl','ltr','auto']
 font:Literal['arabic','latin','hebrew']
 size:float=Field(ge=8,le=256,allow_inf_nan=False)
 weight:Literal[400,600]
 color:str=Field(pattern=COLOR)
 align:Literal['left','center','right']
 protected:StrictBool=True
 perspective:list[list[float]]|None=Field(default=None,min_length=4,max_length=4)
 @field_validator('perspective',mode='before')
 @classmethod
 def plane(cls,q):
  if q is None:return q
  if type(q) is not list or len(q)!=4 or any(type(p) is not list or len(p)!=2 or any(type(n) not in (int,float) or not math.isfinite(n) or not 0<=n<=2048 for n in p) for p in q):raise ValueError('Four finite canvas corners required')
  for i in range(4):
   a,b,c=q[i],q[(i+1)%4],q[(i+2)%4]
   if (b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0])<16:raise ValueError('Text plane must be convex, clockwise and nondegenerate')
  return q
 @model_validator(mode='after')
 def exact(self):
  if not self.protected:raise ValueError('Text remains protected')
  if any((ord(c)<32 and c not in '\t\r\n') or 127<=ord(c)<160 or ord(c) in {0x061c,0x200e,0x200f,*range(0x202a,0x202f),*range(0x2066,0x206a)} for c in self.text):raise ValueError('Unsupported text control; source was not modified')
  return self
class Shape(Rect):
 kind:Literal['rect','ellipse']
 fill:str=Field(pattern=COLOR)
class Brief(Input):
 task:Literal['image','poster','edit']='poster'
 subject:str=Field(default='',max_length=1500)
 elements:str=Field(default='',max_length=1500)
 medium:str=Field(default='flat illustration',max_length=150)
 composition:str=Field(default='',max_length=500)
 lighting:str=Field(default='',max_length=300)
 palette:str=Field(default='',max_length=300)
 material:str=Field(default='',max_length=300)
 realism:str=Field(default='',max_length=150)
 instruction_language:Literal['ar','en','es','he']='ar'
 text_language:Literal['ar','en','es','he']='ar'
 visual_prompt:str=Field(default='',max_length=2500)
class DesignSpec(Input):
 width:int=Field(ge=256,le=2048)
 height:int=Field(ge=256,le=2048)
 background:str=Field(pattern=r'^(?:transparent|#[0-9a-fA-F]{6})$')
 background_asset_id:str|None=Field(default=None,min_length=1,max_length=80)
 layers:list[TextLayer]=Field(default_factory=list,max_length=16)
 shapes:list[Shape]=Field(default_factory=list,max_length=32)
 brief:Brief=Field(default_factory=Brief)
 @model_validator(mode='after')
 def bounds(self):
  r=[*self.layers,*self.shapes]
  if self.width*self.height>MAX_PIXELS:raise ValueError('Canvas exceeds 4 million pixels')
  if len({x.id for x in r})!=len(r):raise ValueError('Layer identifiers must be unique')
  if sum(len(x.text) for x in self.layers)>16000:raise ValueError('Text limit exceeded')
  if any(x.x+x.width>self.width or x.y+x.height>self.height for x in r):raise ValueError('Layer exceeds canvas')
  if any(p[0]>self.width or p[1]>self.height for layer in self.layers for p in (layer.perspective or [])):raise ValueError('Text plane exceeds canvas')
  return self
class CreateDesign(Input):
 title:str=Field(min_length=1,max_length=120)
 spec:DesignSpec
 @model_validator(mode='after')
 def title_valid(self):
  if not self.title.strip() or any(ord(c)<32 for c in self.title):raise ValueError('Invalid title')
  return self
class UpdateDesign(CreateDesign):
 expected_version:int=Field(ge=1,le=100)
 confirm_text_changes:StrictBool=False
class RestoreDesign(Input):
 expected_version:int=Field(ge=1,le=100)
 from_version:int=Field(ge=1,le=100)
def init_visuals(c):
 from backend.visual_codes import init
 init(c)
 c.executescript('''
 CREATE TABLE IF NOT EXISTS visual_assets(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,filename TEXT NOT NULL,mime TEXT NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,byte_size INTEGER NOT NULL,sha256 TEXT NOT NULL,data BLOB NOT NULL,origin TEXT NOT NULL,created_at TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS visual_asset_project ON visual_assets(project_id);
 CREATE TABLE IF NOT EXISTS visual_designs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,title TEXT NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS visual_versions(design_id TEXT NOT NULL REFERENCES visual_designs(id) ON DELETE CASCADE,version INTEGER NOT NULL,title TEXT NOT NULL,spec TEXT NOT NULL,provenance TEXT NOT NULL,byte_size INTEGER NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(design_id,version));
 ''')
def used_bytes(c,pid=None):
 if pid is None:return c.execute('SELECT COALESCE((SELECT SUM(byte_size) FROM visual_assets),0)+COALESCE((SELECT SUM(byte_size) FROM visual_versions),0)').fetchone()[0]
 return c.execute('SELECT COALESCE((SELECT SUM(byte_size) FROM visual_assets WHERE project_id=?),0)+COALESCE((SELECT SUM(v.byte_size) FROM visual_versions v JOIN visual_designs d ON d.id=v.design_id WHERE d.project_id=?),0)',(pid,pid)).fetchone()[0]
def reserve(c,pid,size):
 if used_bytes(c,pid)+size>PROJECT_BYTES or used_bytes(c)+size>GLOBAL_BYTES:raise HTTPException(413,'Visual storage quota exceeded')
def normalize_image(raw,*,stop=None,authorize=None):
 # Held inside the worker: cancelling an await must not release an active decoder.
 if not DECODER_SLOT.acquire(blocking=False):raise HTTPException(409,'Another image is being decoded; retry after it finishes')
 try:return _normalize_image(raw,stop=stop,authorize=authorize)
 finally:DECODER_SLOT.release()

def _normalize_image(raw,*,stop=None,authorize=None):
 from backend import document_isolation
 if not raw or len(raw)>MAX_ASSET:raise HTTPException(413,'Image limit is 6 MiB')
 try:
  data,dimensions,evidence=document_isolation.normalize_image(raw,stop=stop,authorize=authorize,deadline=time.monotonic()+30,variant='visual')
  return data,dimensions|{'mime':'image/png','normalizer':evidence['protocol'],'original_sha256':digest(raw),'isolation':evidence}
 except document_isolation.IsolationError as exc:raise HTTPException(exc.status_code,exc.code) from None
def asset(c,pid,aid):
 require_project(c,pid)
 row=c.execute('SELECT * FROM visual_assets WHERE id=? AND project_id=?',(aid,pid)).fetchone()
 if row is None:raise HTTPException(404,'Visual asset not found in this project')
 return dict(row)
def asset_metadata(row):return {k:v for k,v in row.items() if k!='data'}|{'origin':json.loads(row['origin'])}
def store_asset(c,pid,data,meta,filename,origin=None):
 require_project(c,pid)
 if not data or len(data)>MAX_STORED_ASSET or meta['width']*meta['height']>MAX_PIXELS:raise HTTPException(413,'Image bounds exceeded')
 if c.execute('SELECT COUNT(*) FROM visual_assets WHERE project_id=?',(pid,)).fetchone()[0]>=128:raise HTTPException(413,'Asset count limit reached')
 reserve(c,pid,len(data));aid=uid();provenance={'kind':'upload','normalizer':meta['normalizer'],'original_sha256':meta['original_sha256']} if origin is None else origin
 if origin is None and 'isolation' in meta:provenance['isolation']=meta['isolation']
 if len(packed(provenance).encode())>16000:raise HTTPException(413,'Provenance too large')
 c.execute('INSERT INTO visual_assets VALUES(?,?,?,?,?,?,?,?,?,?,?)',(aid,pid,safe_filename(filename),meta['mime'],meta['width'],meta['height'],len(data),digest(data),data,packed(provenance),now()))
 return asset_metadata(asset(c,pid,aid))
def design(c,pid,did):
 require_project(c,pid)
 row=c.execute('SELECT * FROM visual_designs WHERE id=? AND project_id=?',(did,pid)).fetchone()
 if row is None:raise HTTPException(404,'Design not found in this project')
 return dict(row)
def version(c,did,n):
 owner=c.execute('SELECT project_id FROM visual_designs WHERE id=?',(did,)).fetchone()
 if owner is None:raise HTTPException(404,'Visual resource unavailable')
 require_project(c,owner['project_id'])
 row=c.execute('SELECT * FROM visual_versions WHERE design_id=? AND version=?',(did,n)).fetchone()
 if row is None:raise HTTPException(404,'Design version not found')
 return dict(row)|{'spec':json.loads(row['spec']),'provenance':json.loads(row['provenance'])}
def design_view(c,row):
 v=version(c,row['id'],row['version']);return row|{'spec':v['spec'],'provenance':v['provenance']}
def check_version(row,n):
 if row['version']!=n:raise HTTPException(409,'Design changed; reload before saving')
def save_design(c,pid,title,spec,row=None,operation='user_saved',source_version=None):
 require_project(c,pid)
 if spec.background_asset_id:asset(c,pid,spec.background_asset_id)
 if row is None and c.execute('SELECT COUNT(*) FROM visual_designs WHERE project_id=?',(pid,)).fetchone()[0]>=50:raise HTTPException(413,'Design count limit reached')
 n=row['version']+1 if row else 1
 if n>100:raise HTTPException(413,'Version count limit reached')
 encoded=packed(spec.model_dump());proof={'operation':operation,'source_version':source_version,'text_sources':[{'layer_id':x.id,'text':x.text,'utf8_sha256':digest(x.text.encode())} for x in spec.layers],'spec_sha256':digest(encoded.encode()),'text_assurance':'Exact source only; pixels and OCR not certified'}
 evidence=packed(proof);size=len(encoded.encode())+len(evidence.encode())
 if size>128*1024:raise HTTPException(413,'Design version exceeds 128 KiB')
 reserve(c,pid,size);stamp=now();did=row['id'] if row else uid()
 if row:
  changed=c.execute('UPDATE visual_designs SET title=?,version=?,updated_at=? WHERE id=? AND project_id=? AND version=?',(title,n,stamp,did,pid,row['version']))
  if changed.rowcount!=1:raise HTTPException(409,'Design changed')
 else:c.execute('INSERT INTO visual_designs VALUES(?,?,?,?,?,?)',(did,pid,title,n,stamp,stamp))
 c.execute('INSERT INTO visual_versions VALUES(?,?,?,?,?,?,?)',(did,n,title,encoded,evidence,size,stamp))
 event(c,pid,'visual_design_saved',f'Design {did}, version {n}')
 return design_view(c,design(c,pid,did))
def require_idle(c,pid):
 if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='visual_jobs'").fetchone() and c.execute("SELECT 1 FROM visual_jobs WHERE project_id=? AND status IN ('queued','running','cancelling')",(pid,)).fetchone():raise HTTPException(409,'Stop the active visual job before deletion')
@router.get('/assets')
def list_assets(project_id:str):
 with get_db() as c:
  require_project(c,project_id);rows=c.execute('SELECT id,project_id,filename,mime,width,height,byte_size,sha256,origin,created_at FROM visual_assets WHERE project_id=? ORDER BY created_at DESC,id',(project_id,)).fetchall()
  return {'items':[asset_metadata(dict(r)) for r in rows],'storage_bytes':used_bytes(c,project_id),'limit_bytes':PROJECT_BYTES}
@router.post('/assets',status_code=201)
async def upload_asset(project_id:str,request:Request,file:UploadFile=File(...)):
 from backend.visual_authority import allowed
 with get_db() as c:acting=require_project(c,project_id)
 try:filename=safe_filename(file.filename);raw=await file.read(MAX_ASSET+1)
 finally:await file.close()
 stop=threading.Event()
 task=asyncio.create_task(asyncio.to_thread(normalize_image,raw,stop=stop,authorize=lambda:allowed(project_id,acting)))
 try:
  while not task.done():
   if await request.is_disconnected():stop.set();raise HTTPException(499,'Image upload disconnected')
   if not allowed(project_id,acting):stop.set();raise HTTPException(404,'Visual resource unavailable')
   await asyncio.wait({task},timeout=.1)
  data,meta=await task
 finally:
  stop.set()
  await asyncio.shield(asyncio.gather(task,return_exceptions=True))
 filename=Path(filename).stem+'.png'
 with get_db() as c:
  c.execute('BEGIN IMMEDIATE');result=store_asset(c,project_id,data,meta,filename);event(c,project_id,'visual_asset_uploaded',result['id'])
 return result
@router.get('/assets/{asset_id}')
def get_asset(project_id:str,asset_id:str):
 with get_db() as c:return asset_metadata(asset(c,project_id,asset_id))
@router.get('/assets/{asset_id}/content')
def asset_content(project_id:str,asset_id:str):
 with get_db() as c:row=asset(c,project_id,asset_id)
 return Response(row['data'],media_type=row['mime'],headers={'Cache-Control':'private, no-store','ETag':'"'+row['sha256']+'"','X-Content-Type-Options':'nosniff'})
@router.delete('/assets/{asset_id}')
def delete_asset(project_id:str,asset_id:str,confirm:bool=False):
 if not confirm:raise HTTPException(422,'Explicit deletion confirmation required')
 with get_db() as c:
  c.execute('BEGIN IMMEDIATE');asset(c,project_id,asset_id);require_idle(c,project_id)
  for row in c.execute('SELECT v.spec FROM visual_versions v JOIN visual_designs d ON d.id=v.design_id WHERE d.project_id=?',(project_id,)):
   if json.loads(row['spec']).get('background_asset_id')==asset_id:raise HTTPException(409,'Asset is used by design history; delete that design first')
  c.execute('DELETE FROM visual_assets WHERE id=? AND project_id=?',(asset_id,project_id));event(c,project_id,'visual_asset_deleted',asset_id)
 return {'deleted':True,'secure_erasure':False}
@router.get('/designs')
def list_designs(project_id:str):
 with get_db() as c:
  require_project(c,project_id);return [dict(r) for r in c.execute('SELECT * FROM visual_designs WHERE project_id=? ORDER BY updated_at DESC,id',(project_id,))]
@router.post('/designs',status_code=201)
def create_design(project_id:str,body:CreateDesign):
 with get_db() as c:
  c.execute('BEGIN IMMEDIATE');return save_design(c,project_id,body.title,body.spec)
@router.get('/designs/{design_id}')
def get_design(project_id:str,design_id:str):
 with get_db() as c:return design_view(c,design(c,project_id,design_id))
@router.patch('/designs/{design_id}')
def update_design(project_id:str,design_id:str,body:UpdateDesign):
 with get_db() as c:
  c.execute('BEGIN IMMEDIATE');row=design(c,project_id,design_id);check_version(row,body.expected_version);old=version(c,design_id,row['version'])['spec'];current={x.id:x.text for x in body.spec.layers}
  if any(current.get(x['id'])!=x['text'] for x in old['layers']) and not body.confirm_text_changes:raise HTTPException(409,'Protected text changed; explicit confirmation required')
  return save_design(c,project_id,body.title,body.spec,row,source_version=row['version'])
@router.get('/designs/{design_id}/versions')
def list_versions(project_id:str,design_id:str):
 with get_db() as c:
  design(c,project_id,design_id);return [version(c,design_id,r['version']) for r in c.execute('SELECT version FROM visual_versions WHERE design_id=? ORDER BY version DESC',(design_id,)).fetchall()]
@router.post('/designs/{design_id}/restore')
def restore_design(project_id:str,design_id:str,body:RestoreDesign):
 with get_db() as c:
  c.execute('BEGIN IMMEDIATE');row=design(c,project_id,design_id);check_version(row,body.expected_version);old=version(c,design_id,body.from_version)
  return save_design(c,project_id,old['title'],DesignSpec.model_validate(old['spec']),row,'user_restored',body.from_version)
@router.delete('/designs/{design_id}')
def delete_design(project_id:str,design_id:str,expected_version:int=Query(ge=1,le=100),confirm:bool=False):
 if not confirm:raise HTTPException(422,'Explicit deletion confirmation required')
 with get_db() as c:
  c.execute('BEGIN IMMEDIATE');row=design(c,project_id,design_id);check_version(row,expected_version);require_idle(c,project_id)
  references={json.loads(r['spec']).get('background_asset_id') for r in c.execute('SELECT spec FROM visual_versions WHERE design_id=?',(design_id,))}
  job_ids=set();output_ids=set();jobs_deleted=0
  if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='visual_jobs'").fetchone():
   jobs=c.execute('SELECT id,asset_id FROM visual_jobs WHERE project_id=? AND design_id=?',(project_id,design_id)).fetchall()
   job_ids={r['id'] for r in jobs};output_ids={r['asset_id'] for r in jobs};references.update(output_ids)
   jobs_deleted=c.execute('DELETE FROM visual_jobs WHERE project_id=? AND design_id=?',(project_id,design_id)).rowcount
  retained=[];reports_removed=0
  for stored in c.execute('SELECT id,origin FROM visual_assets WHERE project_id=?',(project_id,)).fetchall():
   try:
    origin=json.loads(stored['origin'])
    if not isinstance(origin,dict):raise ValueError('Invalid asset provenance')
   except (ValueError,TypeError) as exc:raise HTTPException(409,'Stored asset provenance is invalid; deletion was not completed') from exc
   derived=origin.get('design_id')==design_id or origin.get('job_id') in job_ids
   if derived or stored['id'] in references:
    retained.append(stored['id'])
    if derived or stored['id'] in output_ids:
     if 'report' in origin:
      report=origin.pop('report');origin.setdefault('report_sha256',digest(packed(report).encode('utf-8')));reports_removed+=1
     origin['design_deleted']=True
     c.execute('UPDATE visual_assets SET origin=? WHERE id=? AND project_id=?',(packed(origin),stored['id'],project_id))
  c.execute('DELETE FROM visual_designs WHERE id=? AND project_id=?',(design_id,project_id));event(c,project_id,'visual_design_deleted',design_id)
 return {'deleted':True,'history_deleted':True,'jobs_deleted':jobs_deleted,'asset_reports_removed':reports_removed,'retained_asset_ids':retained,'assets_retained':bool(retained),'retention_notice':'Image assets may still contain visible design text and are retained; delete them separately. Existing exports/backups are not erased.','secure_erasure':False}
@router.get('/designs/{design_id}/bundle')
def design_bundle(project_id:str,design_id:str):
 from backend.visual_authority import allowed
 with get_db() as c:
  acting=require_project(c,project_id);design(c,project_id,design_id)
 def recheck():
  if not allowed(project_id,acting):raise HTTPException(404,'Visual resource unavailable')
 async def stream():
  with get_db() as c:
   c.execute('BEGIN');row=design(c,project_id,design_id);revisions=[version(c,design_id,r['version']) for r in c.execute('SELECT version FROM visual_versions WHERE design_id=? ORDER BY version',(design_id,)).fetchall()]
   refs=sorted({v['spec']['background_asset_id'] for v in revisions if v['spec']['background_asset_id']})
   header={'format':'riwaq-visual-bundle','version':1,'design':row,'versions':revisions,'limitations':['No model/font binaries.','Sanitized raster assets; original metadata removed.','Separate from ordinary project snapshot.']}
   recheck();yield packed(header).encode()[:-1]+b',"assets":['
   for i,aid in enumerate(refs):
    item=asset(c,project_id,aid)
    if digest(item['data'])!=item['sha256']:raise ValueError('Stored checksum mismatch')
    if i:recheck();yield b','
    recheck();yield packed(asset_metadata(item)).encode()[:-1]+b',"data_base64":"'
    for at in range(0,len(item['data']),48*1024):
     recheck();yield base64.b64encode(item['data'][at:at+48*1024])
    recheck();yield b'"}'
   recheck();yield b']}'
 return StreamingResponse(stream(),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="riwaq-design-{design_id}.json"','Cache-Control':'no-store'})

from backend.visual_codes import CodeInput,create as create_code,status as code_status
from fastapi import Request

@router.get('/codes/status')
def codes_status(project_id:str):
 with get_db() as c:require_project(c,project_id)
 return code_status()

@router.post('/codes',status_code=201)
async def codes_create(project_id:str,body:CodeInput,request:Request):
 return await create_code(project_id,body,request)
