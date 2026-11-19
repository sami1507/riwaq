"""Durable project-scoped local visual jobs; no paid or cloud provider fallback."""
import asyncio,base64,json,os,shutil,sys,threading,tempfile
from pathlib import Path
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import Field,model_validator
from backend.db import ROOT,data_dir,get_db,uid,now,event
from backend import visuals as v
from backend.visual_authority import require_project,worker_scope,allowed
router=APIRouter(prefix='/api/projects/{project_id}/visual',tags=['visual-jobs'])
ACTIVE={}
RUNNING=('queued','running','cancelling')
def paths():
 return Path(os.environ.get('RIWAQ_VISUAL_PYTHON','D:/RiwaqRuntime/visual-v1/venv/Scripts/python.exe')),Path(os.environ.get('RIWAQ_IMAGE_MODEL_DIR','D:/RiwaqRuntime/visual-v1/sd15'))
def init_jobs(c):
 c.executescript("""
 CREATE TABLE IF NOT EXISTS visual_jobs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,kind TEXT NOT NULL,status TEXT NOT NULL,design_id TEXT NOT NULL,version INTEGER NOT NULL,input TEXT NOT NULL,progress TEXT NOT NULL,asset_id TEXT,report TEXT NOT NULL,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
 CREATE UNIQUE INDEX IF NOT EXISTS one_visual_job ON visual_jobs((1)) WHERE status IN ('queued','running','cancelling');
 """)
 c.execute("UPDATE visual_jobs SET status='interrupted',error='Service restarted. Retry explicitly from the saved input.',updated_at=? WHERE status IN ('queued','running','cancelling')",(now(),))
def busy():
 with get_db() as c:
  if not c.execute("SELECT 1 FROM sqlite_master WHERE name='visual_jobs'").fetchone():return False
  return bool(c.execute("SELECT 1 FROM visual_jobs WHERE status IN ('queued','running','cancelling')").fetchone())
def row(c,pid,jid):
 require_project(c,pid)
 r=c.execute('SELECT * FROM visual_jobs WHERE id=? AND project_id=?',(jid,pid)).fetchone()
 if r is None:raise HTTPException(404,'Visual job not found in this project')
 d=dict(r)
 for k in ('input','progress','report'):d[k]=json.loads(d[k])
 return d
def capabilities():
 packaged=getattr(sys,'frozen',False)
 python,model=paths();model_ready=not packaged and python.is_file() and (model/'riwaq-model.json').is_file() and (ROOT/'scripts/visual_generate.py').is_file()
 chrome=any((Path(os.environ.get(k,''))/'Google/Chrome/Application/chrome.exe').is_file() for k in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA')) if os.name=='nt' else bool(shutil.which('google-chrome'))
 render_ready=not packaged and chrome and bool(shutil.which('node')) and (ROOT/'scripts/visual_render.mjs').is_file() and (ROOT/'frontend/public/fonts/visual-coverage.json').is_file() and (ROOT/'tests/browser/node_modules/@playwright/test/index.mjs').is_file()
 reason='packaged_worker_unavailable' if packaged else 'local_worker_dependencies_unavailable'
 result={'generation':{'available':model_ready,'reason_code':None if model_ready else reason,'experimental':True,'model':'Stable Diffusion v1.5','revision':'451f4fe16113bff5a5d2269ed5ad43b0592e9a14','prompt_language':'English; Arabic semantic prompting not validated','cloud':False,'reason':None if model_ready else 'No configured packaged visual worker; source runtime required' if packaged else 'Local model/runtime or fixed worker missing; run documented setup within an authorized budget'},'render':{'available':render_ready,'reason_code':None if render_ready else reason,'engine':'local Chromium + IBM Plex','scope':'Exact source, font coverage and geometry; visual review required','formats':['png','jpeg','webp'],'ocr':False},'limits':{'seconds':300,'rss_bytes':12*1024**3,'active_jobs':1,'corrections':2,'pixels':512*768,'steps':40,'project_bytes':v.PROJECT_BYTES},'unsupported':['native 3D','SVG/vector export','PDF print profiles','outpainting','super-resolution','scene typography','QR/barcode creation'],'editing':{'available':model_ready,'reason_code':None if model_ready else reason,'status':'experimental' if model_ready else 'unavailable','method':'img2img + protected outside-rectangle pixels'},'snapshot_notice':'Ordinary project snapshots exclude visual designs/assets. SQLite backup includes them; each design also exports a separate source bundle.'}
 from backend.visual_codes import status as code_status
 result['codes']=code_status()
 if result['codes']['available']:result['unsupported'].remove('QR/barcode creation')
 return result
@router.get('/status')
def status(project_id:str):
 with get_db() as c:require_project(c,project_id)
 return capabilities()
class JobInput(v.Input):
 kind:Literal['generate','render','edit']
 design_id:str=Field(min_length=1,max_length=80)
 version:int=Field(ge=1,le=100)
 prompt:str=Field(default='',max_length=2500)
 negative_prompt:str=Field(default='text, letters, watermark, signature',max_length=1000)
 seed:int=Field(default=37,ge=0,lt=2**31)
 steps:int=Field(default=24,ge=4,le=40)
 width:int=Field(default=512,ge=256,le=768)
 height:int=Field(default=512,ge=256,le=768)
 format:Literal['png','jpeg','webp']='png'
 matte:str|None=Field(default=None,pattern=v.COLOR)
 print_dpi:int|None=Field(default=None,ge=72,le=600)
 asset_id:str|None=Field(default=None,max_length=80)
 mask_rect:list[int]|None=Field(default=None,min_length=4,max_length=4)
 @model_validator(mode='after')
 def valid(self):
  if self.print_dpi is not None and (self.kind!='render' or self.format!='png'):raise ValueError('Print density is supported for rendered PNG only')
  if self.kind!='render':
   if not self.prompt.strip():raise ValueError('Reviewed visual prompt required')
   if self.width%64 or self.height%64 or self.width*self.height>512*768:raise ValueError('Dimensions must be multiples of64 within393216pixels')
  if self.kind=='render' and self.format=='jpeg' and self.matte is None:raise ValueError('JPEG needs explicit matte')
  if self.kind=='edit':
   if not self.asset_id or not self.mask_rect:raise ValueError('Edit source and rectangle required')
   x,y,r,b=self.mask_rect
   if not(0<=x<r<=self.width and 0<=y<b<=self.height):raise ValueError('Invalid edit rectangle')
  return self
@router.get('/jobs')
def jobs(project_id:str):
 with get_db() as c:
  require_project(c,project_id);return [row(c,project_id,x['id']) for x in c.execute('SELECT id FROM visual_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT100'.replace('LIMIT100','LIMIT 100'),(project_id,)).fetchall()]
@router.get('/jobs/{job_id}')
def get_job(project_id:str,job_id:str):
 with get_db() as c:return row(c,project_id,job_id)
@router.post('/jobs',status_code=202)
async def create(project_id:str,body:JobInput):
 from backend import app,media,voice,coding
 # A packaged build has no visual worker at all: refuse before any database access.
 if getattr(sys,'frozen',False):raise HTTPException(503,'packaged_worker_unavailable: local renderer/model worker unavailable; no cloud fallback')
 # Otherwise ownership first: a caller without access must not learn worker state (busy/unavailable).
 with get_db() as c:acting=require_project(c,project_id)
 if app.ACTIVE or media.ACTIVE_MEDIA or voice.ACTIVE or coding.ACTIVE:raise HTTPException(409,'Stop the active conversation/media/voice/coding task before using the visual worker')
 caps=capabilities()
 selected=caps['render' if body.kind=='render' else 'generation']
 if not selected['available']:raise HTTPException(503,selected['reason_code']+': local renderer/model worker unavailable; no cloud fallback')
 jid=uid()
 with get_db() as c:
  c.execute('BEGIN IMMEDIATE');require_project(c,project_id);d=v.design(c,project_id,body.design_id);v.check_version(d,body.version);v.version(c,body.design_id,body.version)
  if c.execute("SELECT 1 FROM visual_jobs WHERE status IN ('queued','running','cancelling')").fetchone():raise HTTPException(409,'One visual job can run at a time')
  if c.execute('SELECT COUNT(*) FROM visual_jobs WHERE project_id=?',(project_id,)).fetchone()[0]>=100:raise HTTPException(413,'Job history limit100 reached')
  if body.kind=='edit':
   a=v.asset(c,project_id,body.asset_id)
   if (a['width'],a['height'])!=(body.width,body.height):raise HTTPException(422,'Edit dimensions must match the source')
   if json.loads(a['origin']).get('correction_count',0)>=2:raise HTTPException(409,'Two local corrections reached; preserve this result and start a new design')
  c.execute('INSERT INTO visual_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(jid,project_id,body.kind,'queued',body.design_id,body.version,v.packed(body.model_dump()),'{}',None,'{}',None,now(),now()))
  event(c,project_id,'visual_job_queued',jid)
 state={'stop':threading.Event(),'actor':acting}
 ACTIVE[jid]=state;state['task']=asyncio.create_task(run(jid,project_id,state))
 with get_db() as c:return row(c,project_id,jid)
@router.post('/jobs/{job_id}/cancel')
def cancel(project_id:str,job_id:str):
 with get_db() as c:
  c.execute('BEGIN IMMEDIATE');r=row(c,project_id,job_id)
  if r['status'] in RUNNING:c.execute("UPDATE visual_jobs SET status='cancelling',updated_at=? WHERE id=?",(now(),job_id))
 if job_id in ACTIVE:ACTIVE[job_id]['stop'].set()
 with get_db() as c:return row(c,project_id,job_id)
def progress(jid,p,pid,acting):
 with get_db() as c:
  require_project(c,pid,acting)
  c.execute("UPDATE visual_jobs SET progress=?,updated_at=? WHERE id=? AND project_id=? AND status='running'",(v.packed(p),now(),jid,pid))
def work(jid,pid,state):
 if getattr(sys,'frozen',False):raise ValueError('packaged_worker_unavailable')
 with worker_scope(state.get('actor')):return _work(jid,pid,state)

def _work(jid,pid,state):
 from backend.visual_runner import execute
 with get_db() as c:
  c.execute('BEGIN IMMEDIATE');r=row(c,pid,jid)
  if r['status']=='cancelling':
   c.execute("UPDATE visual_jobs SET status='cancelled',updated_at=? WHERE id=?",(now(),jid));return
  c.execute("UPDATE visual_jobs SET status='running',updated_at=? WHERE id=?",(now(),jid))
  d=v.version(c,r['design_id'],r['version']);cfg=r['input'];payload=dict(cfg);corrections=0
  if r['kind']=='render':
   payload={'spec':d['spec'],'format':cfg['format'],'matte':cfg['matte'],'assets':{}}
   aid=d['spec'].get('background_asset_id')
   if aid:
    a=v.asset(c,pid,aid);payload['assets'][aid]='data:'+a['mime']+';base64,'+base64.b64encode(a['data']).decode()
  elif r['kind']=='edit':
   a=v.asset(c,pid,cfg['asset_id']);payload['image_base64']=base64.b64encode(a['data']).decode();corrections=json.loads(a['origin']).get('correction_count',0)+1
 python,model=paths()
 # The image model and the chat models do not fit in memory together on a typical machine: the
 # worker died with MemoryError while the local runtime still held chat models (Windows, 19 Nov
 # 2026). No conversation is active here (create() refuses otherwise), so free them first.
 released=[]
 if r['kind']!='render':
  from backend import providers
  released=providers.release_local_models()
 with tempfile.TemporaryDirectory(prefix='visual-',dir=data_dir()) as directory:
  folder=Path(directory);inp=folder/'input.json';inp.write_text(v.packed(payload),encoding='utf-8');out=folder/'output'
  cmd=[shutil.which('node'),str(ROOT/'scripts/visual_render.mjs'),str(inp),str(out)] if r['kind']=='render' else [str(python),'-X','utf8',str(ROOT/'scripts/visual_generate.py'),str(inp),str(out),str(model)]
  receipt=execute(cmd,ROOT,folder,state['stop'],lambda p:progress(jid,p,pid,state['actor']),seconds=90 if r['kind']=='render' else 300,
      authorize=lambda:allowed(pid,state['actor']))
  if not allowed(pid,state['actor']):raise HTTPException(404,'Visual resource unavailable')
  reportfile=out/'report.json';report=json.loads(reportfile.read_text(encoding='utf-8')) if reportfile.is_file() and reportfile.stat().st_size<=512*1024 else {}
  report['supervision']=receipt;report['local_models_released']=released;report['design_version']=r['version'];report['spec_sha256']=d['provenance']['spec_sha256'];report['input_sha256']=v.digest(inp.read_bytes())
  reason=receipt['abort_reason'];data=None;meta=None
  if not reason:
   verdict=report.get('report') if r['kind']=='render' else report
   if not isinstance(verdict,dict) or verdict.get('success') is not True or (r['kind']!='render' and report.get('safety_flagged') is not False):reason='invalid_worker_receipt'
  if not reason:
   file=out/('image.'+cfg['format'] if r['kind']=='render' else 'image.png')
   if not file.is_file() or file.stat().st_size>v.MAX_STORED_ASSET:reason='missing_or_oversized_output'
   else:
    raw=file.read_bytes()
    expected_mime='image/'+cfg['format'] if r['kind']=='render' else 'image/png'
    expected_size=(d['spec']['width'],d['spec']['height']) if r['kind']=='render' else (cfg['width'],cfg['height'])
    from backend.visual_export import sanitize_worker_export
    data,meta,sanitation=sanitize_worker_export(raw,expected_mime,expected_size,print_dpi=cfg.get('print_dpi') if r['kind']=='render' else None)
    report['export_sanitization']=sanitation
  with get_db() as c:
   c.execute('BEGIN IMMEDIATE');current=row(c,pid,jid)
   terminal='cancelled' if current['status']=='cancelling' or state['stop'].is_set() else 'failed' if reason else 'completed'
   aid=None
   if terminal=='completed':
    item=v.store_asset(c,pid,data,meta,'riwaq-'+jid+('.'+cfg['format'] if r['kind']=='render' else '.png'),{'kind':r['kind'],'job_id':jid,'design_id':r['design_id'],'design_version':r['version'],'correction_count':corrections,'source_asset_id':cfg.get('asset_id'),'report_sha256':v.digest(v.packed(report).encode())});aid=item['id']
   c.execute('UPDATE visual_jobs SET status=?,asset_id=?,report=?,error=?,updated_at=? WHERE id=?',(terminal,aid,v.packed(report),reason,now(),jid));event(c,pid,'visual_job_'+terminal,jid)
async def run(jid,pid,state):
 try:await asyncio.to_thread(work,jid,pid,state)
 except Exception:
  with get_db() as c:c.execute("UPDATE visual_jobs SET status=?,error=?,report='{}',progress='{}',updated_at=? WHERE id=? AND project_id=? AND status IN ('queued','running','cancelling')",('cancelled' if state['stop'].is_set() else 'failed','Visual execution unavailable or authority changed',now(),jid,pid))
 finally:ACTIVE.pop(jid,None)
async def shutdown():
 for state in ACTIVE.values():state['stop'].set()
 if ACTIVE:await asyncio.gather(*(s['task'] for s in list(ACTIVE.values())),return_exceptions=True)
