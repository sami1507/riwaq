import asyncio
import json
import os
import re
import time
import threading
from contextlib import asynccontextmanager, aclosing
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.db import get_db, init_db, uid, now, event, settings, message_dict, DEFAULTS
from backend import providers, knowledge, public_knowledge, workspace, sync_client, snapshots, media, grounding, reasoning
from backend import learning, learning_policy, personal_memory
from backend import code_run, lessons, quran_reference, text_checks, wiki_sources
from backend import generation_requests,conversation_library

ACTIVE = {}
ALLOWED_ORIGINS = {'http://127.0.0.1:8765','http://localhost:8765','http://tauri.localhost','https://tauri.localhost','tauri://localhost'}
# Development origins are an operator choice, never silently trusted on release.
if os.environ.get('RIWAQ_DEV_ORIGIN') == '1':
    ALLOWED_ORIGINS.update({'http://127.0.0.1:5173','http://localhost:5173'})
elif os.environ.get('RIWAQ_DEV_ORIGIN'):
    # Explicit alternate loopback origin for an isolated development/test
    # instance. Never accept wildcards, public hosts, credentials or URL paths.
    dev_origin = os.environ['RIWAQ_DEV_ORIGIN']
    match = re.fullmatch(r'http://(?:127\.0\.0\.1|localhost):([0-9]{4,5})', dev_origin)
    if not match or not 1024 <= int(match[1]) <= 65535:
        raise RuntimeError('RIWAQ_DEV_ORIGIN must be an explicit loopback HTTP origin')
    ALLOWED_ORIGINS.add(dev_origin)

@asynccontextmanager
async def lifespan(app):
    from backend.isolated_locks import service_lease
    with service_lease():
        async with service_lifespan(app):
            yield


@asynccontextmanager
async def service_lifespan(app):
    from backend.environment_audit import validate_startup
    validate_startup()
    init_db()
    with get_db() as c:
        workspace.init_workspace(c)
    from backend import isolated_journal, coding_checks
    # A prior crash creates cleanup obligations, never permission to replay
    # source code or a model action. Unaffected application features still start.
    app.state.isolated_recovery = await asyncio.to_thread(isolated_journal.recover_pending)
    app.state.coding_recovery = await asyncio.to_thread(coding_checks.recover_interrupted)
    voice.initialize()
    voice_reaper=asyncio.create_task(voice.reap())
    from backend import productivity_schedule
    productivity_stop=asyncio.Event()
    productivity_worker=asyncio.create_task(productivity_schedule.scheduler_loop(productivity_stop))
    try:
        yield
    finally:
        productivity_stop.set()
        await asyncio.gather(productivity_worker,return_exceptions=True)
        voice_reaper.cancel()
        await asyncio.gather(voice_reaper,return_exceptions=True)
        await voice.shutdown()
        await coding.shutdown()
        await documents.shutdown()
        await visual_jobs.shutdown()
        await video.shutdown()
        await guard_api.shutdown()
        await skills.shutdown()
        for task in list(ACTIVE.values()):
            task['stop'].set()
        await public_knowledge.shutdown_public_knowledge()

app=FastAPI(title='Riwaq local service',version='0.1.0',lifespan=lifespan,docs_url='/api/docs',openapi_url='/api/openapi.json',redoc_url=None,
            telemetry={'auto_configure':False,'tracing':False,'metrics':False,'logs':False,'operation_spans':False})

@app.exception_handler(RequestValidationError)
async def validation_error(request,exc):
    return JSONResponse({'detail':[{'loc':e['loc'],'msg':e['msg'],'type':e['type']} for e in exc.errors()]},422)
app.add_middleware(CORSMiddleware,allow_origins=list(ALLOWED_ORIGINS),allow_credentials=True,allow_methods=['GET','POST','PATCH','DELETE','OPTIONS'],allow_headers=['Content-Type','X-Riwaq-Client','X-Riwaq-Voice-Token'])
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver','tauri.localhost'])

class RequestGuard:
    """Reject cross-origin requests and bound bodies before multipart parsing."""
    def __init__(self,app): self.app=app;self.slots=threading.BoundedSemaphore(16)
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        async def secured_send(message):
            if message['type']=='http.response.start':
                response_headers=list(message.get('headers',[]))
                response_headers.append((b'x-frame-options',b'DENY'))
                response_headers.append((b'x-content-type-options',b'nosniff'))
                response_headers.append((b'referrer-policy',b'no-referrer'))
                response_headers.append((b'cross-origin-resource-policy',b'same-origin'))
                if scope['path'].startswith('/api') and not any(k.lower()==b'cache-control' for k,v in response_headers):
                    response_headers.append((b'cache-control',b'no-store'))
                if not any(k.lower()==b'content-security-policy' for k,v in response_headers):
                    response_headers.append((b'content-security-policy',b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' http://127.0.0.1:8765; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"))
                message['headers']=response_headers
            await send(message)
        if not self.slots.acquire(blocking=False):
            return await JSONResponse({'detail':'Local request concurrency limit reached'},429,headers={'Retry-After':'2'})(scope,receive,secured_send)
        try:return await self.dispatch(scope,receive,secured_send)
        finally:self.slots.release()
    async def dispatch(self,scope,receive,send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        headers={k.decode().lower():v.decode() for k,v in scope.get('headers',[])}
        if scope['path'].startswith('/api') and scope['method']!='OPTIONS':
            origin=headers.get('origin')
            if (origin and origin not in ALLOWED_ORIGINS) or headers.get('x-riwaq-client')!='local' or headers.get('sec-fetch-site')=='cross-site':
                return await JSONResponse({'detail':'Local client header and trusted origin required.'},403)(scope,receive,send)
        limit=4096 if scope['path'] in {'/api/auth/login','/api/auth/identity'} else 9*1024*1024
        body=[]
        size=0
        deadline=time.monotonic()+15
        while True:
            try:msg=await asyncio.wait_for(receive(),max(.001,deadline-time.monotonic()))
            except TimeoutError:
                return await JSONResponse({'detail':'Request body deadline exceeded'},408)(scope,receive,send)
            if msg['type']=='http.disconnect': return
            size+=len(msg.get('body',b''))
            if size>limit:
                return await JSONResponse({'detail':'Request exceeds the endpoint body limit.'},413)(scope,receive,send)
            body.append(msg)
            if not msg.get('more_body',False): break
        async def replay():
            if body: return body.pop(0)
            return await receive()
        await self.app(scope,replay,send)

from backend import local_auth, guard, guard_api
from backend.guard_rate import GuardRateMiddleware
app.add_middleware(GuardRateMiddleware)
app.add_middleware(local_auth.LocalAuthMiddleware)
app.add_middleware(RequestGuard)
app.include_router(local_auth.router)
app.include_router(guard_api.router)
app.include_router(knowledge.router)
from backend import studio, facts, context_lineage, documents
app.include_router(documents.router)
from backend import document_vision
app.include_router(document_vision.router)
app.include_router(studio.router)
app.include_router(facts.router)
app.include_router(public_knowledge.router)
app.include_router(workspace.router)
app.include_router(sync_client.router)
app.include_router(snapshots.router)
app.include_router(media.router)
from backend import visuals, visual_jobs
app.include_router(visuals.router)
app.include_router(visual_jobs.router)
from backend import video
app.include_router(video.router)
from backend import voice
app.include_router(voice.router)
from backend import coding
app.include_router(coding.router)
from backend import research, research_answer
from backend.research_schema import ResearchOptions, KnowledgeFilters
app.include_router(research.router)
from backend import skills, skill_core
app.include_router(skills.router)
from backend import productivity_api
app.include_router(productivity_api.router)
from backend import skill_runtime_api
app.include_router(skill_runtime_api.router)
from backend import skill_files_api
app.include_router(skill_files_api.router)
from backend import learning_api
app.include_router(learning_api.router)
from backend import personal_memory_api
app.include_router(personal_memory_api.router)
from backend import medical_api
app.include_router(medical_api.router)
app.include_router(conversation_library.router)
from backend import composer_drafts
app.include_router(composer_drafts.router)

class ProjectInput(BaseModel):
    name:str=Field(min_length=1,max_length=120)
    instructions:str=Field(default='',max_length=12000)

class ProjectPatch(BaseModel):
    name:str|None=Field(default=None,min_length=1,max_length=120)
    instructions:str|None=Field(default=None,max_length=12000)

class ConversationInput(BaseModel):
    project_id:str
    title:str=Field(default='محادثة جديدة',max_length=120)

class GenerateInput(BaseModel):
    request_id:str|None=Field(default=None,min_length=8,max_length=96,pattern=r'^[A-Za-z0-9_-]+$')
    answer_mode:Literal['assistant','calculator','problem']='assistant'
    result_language:Literal['ar','en']='ar'
    content:str=Field(default='',max_length=20000)
    model:str|None=Field(default=None,max_length=100)
    provider:Literal['ollama','openai','anthropic']|None=None
    retry_message_id:str|None=None
    edit_message_id:str|None=None
    use_files:bool=False
    file_scope:list[reasoning.FileSelection]=Field(default_factory=list,max_length=20)
    use_knowledge:bool=False
    web_sources:bool=False
    knowledge_filters:KnowledgeFilters|None=None
    research:ResearchOptions|None=None
    depth:Literal['auto','quick','analytical','research']|None=None
    calculation:reasoning.Calculation|None=None
    hypothesis:reasoning.Hypothesis|None=None
    review:bool=False
    output_contract:reasoning.OutputContract|None=None
    fact_ids:list[str]=Field(default_factory=list,max_length=8)

class SettingsPatch(BaseModel):
    mode:Literal['local','hybrid']|None=None
    provider:Literal['ollama','openai','anthropic']|None=None
    model:str|None=Field(default=None,min_length=1,max_length=100)
    max_tokens:int|None=Field(default=None,ge=32,le=4096)
    context_size:int|None=Field(default=None,ge=2048,le=16384)
    local_fallback_models:list[str]|None=Field(default=None,max_length=3)
    temperature:float|None=Field(default=None,ge=0,le=1)
    seed:int|None=Field(default=None,ge=0,le=2147483647)

def require(c,table,id,actor=None):
    assert table in ('projects','conversations','messages','memories')
    row=c.execute(f'SELECT * FROM {table} WHERE id=?',(id,)).fetchone()
    if not row: raise HTTPException(404,'Not found')
    if table=='projects':guard.require_project(c,id,actor)
    elif table in ('conversations','memories'):
        if row['project_id']:guard.require_project(c,row['project_id'],actor)
        elif table=='memories':memory_record(c,id)
    elif table=='messages':require(c,'conversations',row['conversation_id'],actor)
    return row

@app.get('/api/status')
async def status():
    from backend.reasoning_math import capability as math_capability
    cfg=settings()
    local=await providers.ollama_status()
    media_info=await media.media_status()
    local_voices=await voice.voices()
    execution=coding.sandbox.status()['execution']
    return {'version':'0.1.0',**cfg,'ollama':local,'capabilities':{
        'mathematics':math_capability(),
        'vision':media_info['vision'],
        'stt':media_info['stt'],
        'tts':{'available':bool(local_voices),'processing':'local','scope':'Explicit voice sessions; only discovered matching Windows voices','voices':local_voices,'arabic_available':any(v.get('language','').split('-')[0]=='ar' for v in local_voices),'reason':voice.VOICE_CACHE['error'] or 'Voice discovery is not a language quality benchmark'},
        'web_search':{'available':cfg['mode']=='hybrid' and bool(providers.secret('brave')),'reason':'Requires hybrid mode and Brave API key in OS keyring.'},
        'code_execution':{**execution,'reason':execution.get('reason_en'),'scope':'Explicit bounded checks in the coding workbench, and one short Python block in chat when asked what it prints (isolated worker only, never the host); no general model shell tool.'},
        'sync':{'available':cfg['mode']=='hybrid','reason':'Manual encrypted project backup; requires a configured account server and explicit sign-in.'}
    },'providers':{name:{'configured':bool(providers.secret(name)),'tested':False} for name in ('openai','anthropic')}}

@app.get('/api/settings')
def get_settings(): return settings()

@app.patch('/api/settings')
async def update_settings(body:SettingsPatch):
    values=body.model_dump(exclude_none=True)
    if ACTIVE or voice.ACTIVE or coding.ACTIVE or sync_client.BUSY or public_knowledge.BUSY: raise HTTPException(409,'Stop generation, coding, voice, knowledge imports, or sync before changing privacy settings.')
    current=settings();merged=current|values
    if 'local_fallback_models' in values and any(not isinstance(x,str) or not 1<=len(x)<=100 or any(ord(ch)<32 for ch in x) for x in values['local_fallback_models']):
        raise HTTPException(422,'Fallback models must be explicit local model identifiers')
    if merged['context_size'] - merged['max_tokens'] < 768:
        raise HTTPException(422, 'Context must leave at least 768 tokens beyond the output limit.')
    if merged['mode']=='local': values['provider']='ollama'
    with get_db() as c:
        # Returning to the local provider without naming a model restores the
        # last local model instead of sending a cloud model name to Ollama.
        if values.get('provider',current['provider'])=='ollama' and 'model' not in values and current['provider']!='ollama':
            row=c.execute("SELECT value FROM settings WHERE key='_last_local_model'").fetchone()
            values['model']=json.loads(row['value']) if row else DEFAULTS['model']
        for k,v in values.items():
            c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(k,json.dumps(v)))
        if values.get('provider',current['provider'])=='ollama':
            c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('_last_local_model',json.dumps(values.get('model',current['model']))))
    return settings()

@app.get('/api/projects')
def projects():
    with get_db() as c:
        actor=memory_actor(c)
        return [dict(x) for x in c.execute('SELECT p.* FROM projects p JOIN guard_projects g ON g.project_id=p.id WHERE g.owner_id=? ORDER BY p.created_at',(actor.owner_id,))]

@app.post('/api/projects')
def add_project(body:ProjectInput):
    with get_db() as c:
        id=uid(); c.execute('INSERT INTO projects VALUES (?,?,?,?)',(id,body.name,body.instructions,now()))
        event(c,id,'created','Project created: '+body.name)
        return dict(require(c,'projects',id))

@app.get('/api/projects/{id}')
def project(id:str):
    with get_db() as c: return dict(require(c,'projects',id))

@app.patch('/api/projects/{id}')
def update_project(id:str,body:ProjectPatch):
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        require(c,'projects',id)
        for k,v in body.model_dump(exclude_none=True).items():
            c.execute(f'UPDATE projects SET {k}=? WHERE id=?',(v,id))
        event(c,id,'instructions_updated','Project name or instructions updated by user')
        return dict(require(c,'projects',id))

@app.get('/api/conversations')
def conversations(project_id:str|None=None,q:str='',archived:Literal['active','archived','all']='active'):
    with get_db() as c:
        actor=memory_actor(c)
        return conversation_library.visible_rows(c,actor,project_id,q,archived)

@app.post('/api/conversations')
def add_conversation(body:ConversationInput):
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        require(c,'projects',body.project_id)
        id=uid(); stamp=now()
        c.execute('INSERT INTO conversations VALUES (?,?,?,?,?,?)',(id,body.project_id,body.title,stamp,stamp,None))
        event(c,body.project_id,'conversation_created',body.title)
        return dict(require(c,'conversations',id))

@app.get('/api/conversations/{id}')
def conversation(id:str):
    with get_db() as c:
        row=require(c,'conversations',id)
        messages=[]
        for item in c.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY rowid',(id,)):
            if context_lineage.disclosable(c,row['project_id'],dict(item)):
                value=message_dict(item);visible=[]
                for citation in value['citations']:
                    if not isinstance(citation,dict):continue
                    kind='public_chunk' if citation.get('scope')=='public' else 'private_chunk'
                    source,source_state=facts.source_raw(c,row['project_id'],kind,citation.get('id',''))
                    if source and source_state=='current' and source['text']==citation.get('text'):visible.append(citation)
                value['citations']=visible;messages.append(value)
            else:
                messages.append(redacted_message(item))
        return dict(row)|{'messages':messages}


def redacted_message(item):
    return {k:item[k] for k in ('id','conversation_id','role','model','created_at')} | {
        'content':'','citations':[],'status':'error','content_redacted':True,
        'redaction_reason':'Source access or version changed; dependent content is hidden.'}

@app.delete('/api/conversations/{id}')
async def delete_conversation(id:str):
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=require(c,'conversations',id)
        if id in ACTIVE: raise HTTPException(409,'Stop generation first')
        c.execute('DELETE FROM conversations WHERE id=?',(id,))
        event(c,row['project_id'],'conversation_deleted','Conversation deleted by user')
    return {'deleted':True}

@app.get('/api/conversations/{id}/export')
def export(id:str,format:Literal['markdown','json']='markdown'):
    data=conversation(id)
    if format=='json': return JSONResponse(data,headers={'Content-Disposition':'attachment; filename="riwaq-conversation.json"'})
    content='# '+data['title']+'\n\n'+'\n\n'.join('## '+m['role']+'\n\n'+m['content'] for m in data['messages'])
    return PlainTextResponse(content,headers={'Content-Disposition':'attachment; filename="riwaq-conversation.md"'})

def encode(obj): return (json.dumps(obj,ensure_ascii=False)+'\n').encode()

class ManagedStreamingResponse(StreamingResponse):
    def __init__(self,*args,finalizer,**kwargs):
        super().__init__(*args,**kwargs);self.finalizer=finalizer
    async def __call__(self,scope,receive,send):
        try:return await super().__call__(scope,receive,send)
        finally:await self.finalizer()

async def prepare_wait(awaitable,job,request):
    remaining=job['deadline']-time.monotonic()
    if remaining<=0:
        if hasattr(awaitable,'close'):awaitable.close()
        raise HTTPException(504,'Task preparation reached its time budget')
    def permitted():
        try:
            with get_db() as c:guard.require_project(c,job['project_id'],job['actor'])
            return True
        except HTTPException:return False
    try:return await media.await_work(asyncio.create_task(awaitable),job,request,remaining,authorize=permitted)
    except HTTPException as exc:
        if exc.status_code==403 and not permitted():raise HTTPException(404,'Project resource unavailable') from None
        raise

@app.post('/api/conversations/{id}/generate')
async def generate(id:str,body:GenerateInput,request:Request):
    job=None
    try:
        with get_db() as c:
            c.execute('BEGIN IMMEDIATE')
            conv=require(c,'conversations',id)
            actor=guard.require_project(c,conv['project_id'])
            prior=generation_requests.find(c,actor,id,body)
            if prior is None:
                state=conversation_library.row(c,conv['project_id'],id)
                if state['archived']:raise HTTPException(409,'Restore the archived conversation before starting a new response.')
                if ACTIVE or voice.ACTIVE or coding.ACTIVE or visual_jobs.busy() or media.ACTIVE_MEDIA: raise HTTPException(409,'One conversation or coding/visual/media/voice generation can run at a time on this device.')
                generation_requests.reserve(c,actor,id,conv['project_id'],body)
                job={'stop':asyncio.Event(),'deadline':time.monotonic()+180,'started':time.monotonic(),'actor':actor,'project_id':conv['project_id'],'request_id':body.request_id}
                ACTIVE[id]=job
        if prior is not None:
            generation_saved_response(prior,actor)
            async def replay():
                current=generation_saved_response(prior,actor)
                yield encode({'type':'start','conversation_id':current['conversation_id'],'message_id':current['id'],
                    'citations':current.get('citations',[]),'retrieval_options':current.get('retrieval_options'),
                    'replayed':True,**({'run':current['run']} if 'run' in current else {})})
                current=generation_saved_response(prior,actor)
                yield encode({'type':'done','message':current,'replayed':True,'metrics':{}})
            return StreamingResponse(replay(),media_type='application/x-ndjson',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
        return await generate_prepared(id,body,request,job)
    except BaseException:
        if job is not None:
            try:
                with get_db() as c:generation_requests.reject(c,job)
            finally:
                for key,value in list(ACTIVE.items()):
                    if value is job:ACTIVE.pop(key,None)
        raise


def generation_saved_response(receipt,actor):
    with get_db() as c:
        require(c,'conversations',receipt['conversation_id'],actor)
        guard.require_project(c,receipt['project_id'],actor)
        if receipt['state']!='bound' or receipt['message_id'] is None:generation_requests.unavailable(receipt)
        row=c.execute('SELECT * FROM messages WHERE id=? AND conversation_id=?',(receipt['message_id'],receipt['result_conversation_id'])).fetchone()
        if row is None or row['status']=='streaming':generation_requests.unavailable(receipt)
        target=require(c,'conversations',receipt['result_conversation_id'],actor)
        if target['project_id']!=receipt['project_id'] or not context_lineage.disclosable(c,target['project_id'],dict(row)):
            raise HTTPException(404,'Saved response is not available under current source access.')
        return message_dict(row)


@app.get('/api/conversations/{id}/requests/{request_id}')
def generation_request_status(id:str,request_id:str):
    with get_db() as c:
        conv=require(c,'conversations',id);actor=guard.require_project(c,conv['project_id'])
        row=c.execute('SELECT * FROM generation_requests WHERE owner_id=? AND request_id=? AND conversation_id=?',(actor.owner_id,request_id,id)).fetchone()
        if row is None:raise HTTPException(404,'Attempt not found')
    saved=generation_saved_response(dict(row),actor)
    return JSONResponse({'request_id':request_id,'conversation_id':saved['conversation_id'],'message':saved},headers={'Cache-Control':'no-store'})

async def generate_prepared(id,body,request,job):
    cfg=settings(); provider=body.provider or cfg['provider']; model=body.model or cfg['model']
    task_run=None; research_receipt=None; ordinary_grounded=False; grounded_options=None; quran_context=''; check_context=''; code_result=None; lesson_found=[]; web_found=None
    with get_db() as c:
        require(c,'conversations',id,job['actor'])
        prior=[dict(x) for x in c.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY rowid',(id,))]
    lookup=body.content
    target=body.retry_message_id or body.edit_message_id
    if body.retry_message_id and body.edit_message_id:
        raise HTTPException(422,'Choose either retry or edit, not both.')
    if target:
        at=next((i for i,m in enumerate(prior) if m['id']==target),None)
        if at is None:raise HTTPException(404,'Branch message not in this conversation')
        if prior[at]['role']!=('assistant' if body.retry_message_id else 'user'):
            raise HTTPException(400,'Invalid branch target role')
        with get_db() as c:saved_options=context_lineage.read_retrieval(c,target)
        if saved_options is not None:
            inherited=saved_options if body.retry_message_id else {k:v for k,v in saved_options.items() if k in {'answer_mode','result_language','use_files','use_knowledge','file_scope','knowledge_filters','research'}}
            body=GenerateInput.model_validate(body.model_dump()|{k:v for k,v in inherited.items() if k not in body.model_fields_set})
        if body.retry_message_id:
            lookup=next((m['content'] for m in reversed(prior[:at]) if m['role']=='user'),'')
    from backend import request_contract
    if lookup.strip() and len(lookup)<=8000:
        understanding=request_contract.analyze(lookup,has_previous=bool(prior))
        questions=[q for q in understanding['clarifications'] if q['code']=='conflicting_word_limits']
        if questions:raise HTTPException(422,{'code':'request_clarification_required','message':questions[0]['message'],'questions':questions})
    calculator=body.answer_mode in ('calculator','problem')
    if body.research:
        if calculator or body.depth is not None or body.calculation or body.hypothesis or body.review or body.output_contract or body.fact_ids:
            raise HTTPException(422,'Research has its own bounded evidence path; choose it without calculator/reasoning/output-contract options.')
        if provider!='ollama':raise HTTPException(400,'Research currently supports the installed local model only; no paid fallback.')
        body.use_files='files' in body.research.scopes
        body.use_knowledge=any(s in body.research.scopes for s in ('knowledge','web'))
        if body.research.require_current and body.use_files:
            raise HTTPException(422,'Current-event verification is not available for selected private files.')
        research_receipt=research.new_receipt(body.research)
        job['research']=research_receipt
    file_scope=[s.model_dump() if hasattr(s,'model_dump') else s for s in body.file_scope]
    if file_scope and not body.use_files:raise HTTPException(422,'Selected files require use_files')
    if file_scope:
        with get_db() as c:
            current=require(c,'conversations',id)
            documents.validate_scope(c,current['project_id'],file_scope)
    retrieval_options=reasoning.RetrievalOptions(
        use_files=body.use_files,use_knowledge=body.use_knowledge,
        file_scope=file_scope,
        answer_mode=body.answer_mode,result_language=body.result_language if calculator else None,
        research=body.research,knowledge_filters=body.knowledge_filters,
    ).model_dump()
    calculator_content=None
    if calculator:
        if body.use_files or body.use_knowledge or body.depth is not None or body.calculation or body.hypothesis or body.review or body.output_contract or body.fact_ids:
            raise HTTPException(422,'وضع الحاسبة يقبل تعبيرًا حسابيًا فقط، دون مصادر أو خيارات نموذج. / Calculator mode accepts arithmetic only, without sources or model options.')
        mathematical=False
        if body.answer_mode=='problem':
            from backend.reasoning_problem import parse_problem
            from backend.reasoning_math import parse_math
            try:parse_problem(lookup)
            except ValueError:
                try:parse_math(lookup)
                except (ValueError,TypeError) as exc:
                    raise HTTPException(422,'صيغة المسألة غير مدعومة أو ملتبسة؛ راجع التجميع والمجال. / Unsupported or ambiguous problem: '+str(exc)[:160]) from exc
                mathematical=True
                from backend.reasoning_math import capability as math_capability
                if not math_capability()['available']:
                    raise HTTPException(503,'المحرك الرياضي المحدد غير متاح؛ راجع إعداد بيئة المصدر. / Required math engine versions are unavailable; no model fallback.')
        if not lookup.strip() or len(lookup)>(12000 if mathematical else 512):
            raise HTTPException(422,'Math input length exceeds its supported limit.')
        task_run=(reasoning.MathRun if mathematical else reasoning.ProblemRun if body.answer_mode=='problem' else reasoning.CalculatorRun)(uid(),lookup,body.result_language)
        task_run.data['request_options'].update(retrieval_options)
        task_run.started=job['started'];job['deadline']=job['started']+(35 if mathematical else 10)
        task_run.stage('prepare','complete');task_run.stage('calculate')
        task_run.tool()
        try:
            checked=await prepare_wait(reasoning.checker('solve_math' if mathematical else 'solve_problem' if body.answer_mode=='problem' else 'calculate',lookup,timeout=30 if mathematical else 5),job,request)
        except TimeoutError as exc:
            raise HTTPException(504,'انتهت مهلة الحاسبة؛ لم تُحفظ نتيجة. / Calculator timed out; no result was saved.') from exc
        except (ValueError,OSError) as exc:
            raise HTTPException(503,'تعذّر تشغيل الحاسبة المحلية؛ لم يُستخدم نموذج بديل. / Local calculator failed; no model fallback was used.') from exc
        if not isinstance(checked,dict):
            raise HTTPException(503,'Invalid calculator response; no result was saved.')
        if checked.get('status')!='ok':
            code=503 if checked.get('status')=='unavailable' else 422
            if code==422 and body.answer_mode=='problem' and (mathematical or checked.get('error')=='unsupported_or_ambiguous_problem'):
                raise HTTPException(422,'صياغة المسألة غير مدعومة أو أساس النسبة غير محدد. استخدم صيغة واضحة مثل «1200 متر + 0.8 كيلومتر» أو «100 ترتفع20% ثم تنخفض20% من الجديدة». / Unsupported or ambiguous problem; state the units and percentage base explicitly.')
            raise HTTPException(code,'تعذّر الحساب: '+str(checked.get('error','Unsupported expression'))[:400]+' / Calculator could not complete the expression.')
        try:calculator_content=task_run.answer(checked.get('result'),body.result_language)
        except ValueError as exc:raise HTTPException(503,str(exc)) from exc
        if mathematical and checked['result']['request']['operation']=='unit_sum' and checked['result']['result'].get('incompatible_dimensions'):
            # Keep the existing HTTP contract: invalid unit additions must not
            # create a saved answer. The worker still records the mathematical
            # distinction between undefined input and a failed computation.
            raise HTTPException(422,'لا يمكن جمع وحدات ذات أبعاد مختلفة؛ استخدم كميات من البعد نفسه. / Unsupported or ambiguous problem: incompatible unit dimensions.')
        task_run.stage('calculate','complete')
        if mathematical:
            result=checked['result']
            complete=result['execution_status']=='completed' and result['problem_status'] in ('solved','no_solution','inconsistent','undefined')
            task_run.check('solve_math','pass' if complete else 'review_needed','Explicit mathematical formulation, domain and recorded engine checks; no model or formal proof assistant was used.',json.dumps(result,ensure_ascii=False))
            if not complete:task_run.data['remaining'].append('Mathematical status: '+result['problem_status']+'; completeness: '+result['completeness']+'. Review the stated limitations or missing input.')
        elif body.answer_mode=='problem':
            problem_status=checked['result']['status']
            task_run.check('solve_problem','pass' if problem_status=='ok' else 'review_needed','Recognized problem mapping and fixed arithmetic; no model was called. Undefined or rejected calculations retain their individual status.',json.dumps(checked['result'],ensure_ascii=False))
            if problem_status!='ok':task_run.data['remaining'].append('Some requested quantities are undefined or exceed the fixed arithmetic limits; no numeric replacement was invented.')
        else:task_run.check('calculate','pass','Computed the supplied expression only; no model was called.',json.dumps(checked['result'],ensure_ascii=False))
        provider='local-tool';model='riwaq-math-v1' if mathematical else 'riwaq-problem-v1' if body.answer_mode=='problem' else 'riwaq-calculator-v1'
    elif body.depth is not None or body.calculation or body.hypothesis or body.output_contract or body.fact_ids:
        if provider!='ollama':raise HTTPException(400,'Paid reasoning is disabled until an explicit priced budget is configured. Select the local provider.')
        task_run=reasoning.Run(uid(),body.depth or 'auto',lookup,body.calculation,body.hypothesis,body.review,output_contract=body.output_contract,fact_ids=body.fact_ids,reasoning_enabled=bool(body.depth is not None or body.calculation or body.hypothesis))
        task_run.data['request_options'].update(retrieval_options)
        task_run.started=job['started'];job['deadline']=job['started']+task_run.data['ceilings']['wall_seconds']
    ordinary_grounded=bool((body.use_files or body.use_knowledge) and not body.research and task_run is None and provider=='ollama')
    if not body.content.strip() and not body.retry_message_id: raise HTTPException(422,'Message is empty')
    public_chunks=[]
    if body.use_knowledge:
        # Read before awaiting retrieval; never keep a SQLite write transaction open
        # across model I/O. Recheck settings/concurrency before saving any messages.
        with get_db() as c:
            require(c,'conversations',id)
            prior=[dict(x) for x in c.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY rowid',(id,))]
        lookup=body.content
        if body.retry_message_id:
            at=next((i for i,m in enumerate(prior) if m['id']==body.retry_message_id),None)
            if at is None: raise HTTPException(404,'Branch message not in this conversation')
            lookup=next((m['content'] for m in reversed(prior[:at]) if m['role']=='user'),'')
        if task_run:task_run.tool()
        if body.research:
            public_chunks=await prepare_wait(research.prepare(lookup,body.research,job['stop'],research_receipt),job,request)
        else:
            filters=body.knowledge_filters.model_dump(exclude_none=True) if body.knowledge_filters else None
            public_chunks=await prepare_wait(public_knowledge.retrieve_public(lookup,limit=5,filters=filters),job,request)
        if task_run:task_run.stage('retrieve','complete')
        if settings()!=cfg: raise HTTPException(409,'Privacy or model settings changed while preparing. Please send again.')
    if not calculator:
        try:
            await prepare_wait(providers.preflight(provider,model,cfg),job,request)
        except ValueError as e: raise HTTPException(400,str(e))
        except httpx.HTTPError:
            if not research_receipt:raise HTTPException(503,'Local model unavailable. Start Ollama and install the selected model.')
            research_receipt['model_unavailable']=True
            research_receipt['notices'].append('Local generation model unavailable; evidence excerpts only.')
        if settings()!=cfg: raise HTTPException(409,'Privacy or model settings changed while preparing. Please send again.')
    if job['stop'].is_set() or await request.is_disconnected():raise HTTPException(499,'Preparation cancelled')
    if calculator and time.monotonic()>=job['deadline']:
        raise HTTPException(504,'Calculator time budget reached before saving; no result was saved.')
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        if public_chunks:
            current = public_knowledge.authorize_current(c, public_chunks)
            if len(current) != len(public_chunks):
                raise HTTPException(409, 'Knowledge rights or versions changed while preparing. Please send again.')
            public_chunks = current
        conv=require(c,'conversations',id)
        proj=require(c,'projects',conv['project_id'])
        fact_context={'text':'','bindings':[]} if calculator else facts.context_for_ids(c,conv['project_id'],body.fact_ids)
        if task_run and fact_context['bindings']:task_run.data['fact_bindings']=fact_context['bindings']
        history=[dict(x) for x in c.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY rowid',(id,))]
        if body.edit_message_id or body.retry_message_id:
            target=body.edit_message_id or body.retry_message_id
            index=next((i for i,m in enumerate(history) if m['id']==target),None)
            if index is None: raise HTTPException(404,'Branch message not in this conversation')
            expected='user' if body.edit_message_id else 'assistant'
            if history[index]['role']!=expected: raise HTTPException(400,'Invalid branch target role')
            old=id; id=uid(); stamp=now()
            c.execute('INSERT INTO conversations VALUES (?,?,?,?,?,?)',(id,conv['project_id'],conv['title']+' · فرع',stamp,stamp,old))
            history=history[:index]
            for m in history:
                copied_id=uid()
                c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)',(copied_id,id,m['role'],m['content'],m['status'],m['model'],m['created_at'],m['citations']))
                previous_run=reasoning.read_receipt(c,m['id'])
                if previous_run:reasoning.save_receipt(c,copied_id,previous_run)
                previous_links=context_lineage.read(c,m['id'])
                if previous_links is not None:context_lineage.save(c,copied_id,previous_links)
                previous_retrieval=context_lineage.read_retrieval(c,m['id'])
                if previous_retrieval is not None:context_lineage.save_retrieval(c,copied_id,previous_retrieval)
                previous_research=research.read(c,m['id'])
                if previous_research is not None:research.save(c,copied_id,previous_research)
                previous_compaction=context_lineage.read_compaction(c,m['id'])
                if previous_compaction is not None:context_lineage.save_compaction(c,copied_id,previous_compaction)
            event(c,conv['project_id'],'branch_created','Conversation branched; original preserved')
        if not body.retry_message_id:
            user_id=uid()
            c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)',(user_id,id,'user',body.content,'complete',None,now(),'[]'))
            context_lineage.save_retrieval(c,user_id,retrieval_options)
            history.append({'role':'user','content':body.content,'status':'complete'})
        question=next((m['content'] for m in reversed(history) if m['role']=='user'),'')
        if task_run and body.use_files:task_run.tool()
        chunks=knowledge.retrieve(c,conv['project_id'],question,file_scope=file_scope) if body.use_files else []
        if task_run and body.use_files:task_run.stage('retrieve','complete')
        # Different retrieval scores are not comparable. Alternate the two explicitly
        # selected knowledge scopes and cap source text before constructing context.
        candidates=[]
        for index in range(max(len(chunks),len(public_chunks))):
            if index<len(chunks): candidates.append(chunks[index])
            if index<len(public_chunks): candidates.append(public_chunks[index])
        chunks=[]; source_chars=0
        for candidate in candidates:
            size=len(candidate['text'])
            if source_chars+size>12000: continue
            chunks.append(candidate); source_chars+=size
            if len(chunks)==6: break
        if calculator:
            messages=[];memories=[];context_links=[]
        else:
            lesson_context={'rules':[],'bindings':[]}
            personal_context={'rules':[],'bindings':[]}
            correction_context={'corrections':[],'bindings':[]}
            if research_receipt:
                # A selected-source answer contains no previous replies or unrelated
                # memories, even when they remain visible in conversation history.
                history=[{'role':'user','content':question}];history_links=[];memories=[]
            else:
                history,history_links=context_lineage.filter_history(c,conv['project_id'],history,file_scope=file_scope,conversation_id=id)
                # Personal defaults have a distinct fail-closed admission gate.
                # Legacy free text is never included as a presentation rule.
                memories=[dict(x) for x in c.execute("SELECT * FROM memories WHERE scope='project' AND project_id=? AND id NOT IN (SELECT memory_id FROM learning_releases WHERE memory_id IS NOT NULL) ORDER BY created_at LIMIT 30",(conv['project_id'],))]
                principal=local_auth.current_principal()
                lesson_context=learning.active_rules(c,guard.Actor(principal['owner_id'],principal['session_id']),conv['project_id'],id,question)
                personal_context=personal_memory.active_context(c,guard.Actor(principal['owner_id'],principal['session_id']),conv['project_id'],id,question)
                merged=personal_memory.merge_rules(personal_context['rules'],lesson_context['rules'],question,id)
                selected_ids={row['id'] for row in merged['rules']}
                selected_release_ids={row.get('release_id') for row in merged['rules']}
                personal_context['bindings']=[link for link in personal_context['bindings'] if link['id'] in selected_ids]
                lesson_context['bindings']=[link for link in lesson_context['bindings'] if link['id'] in selected_release_ids]
                lesson_context['rules']=merged['rules']
                correction_context=learning.verified_corrections(c,guard.Actor(principal['owner_id'],principal['session_id']),conv['project_id'],id)
            try:
                skill_guidance=skill_core.chat_instructions(question)+learning_policy.scoped_instructions(lesson_context['rules'])+learning_policy.correction_context(correction_context['corrections'])
                if ordinary_grounded:
                    language=request_contract.analyze(question[:8000]).get('constraints',{}).get('language')
                    if not language:language='ar' if re.search('[\u0600-\u06ff]',question) else 'he' if re.search('[\u0590-\u05ff]',question) else 'en'
                    grounded_options=ResearchOptions(scopes=(['files'] if body.use_files else [])+(['knowledge'] if body.use_knowledge else []),response_language=language)
                    # Account for the validator's instructions before selecting
                    # whole source excerpts, rather than overflowing afterward.
                    skill_guidance+='\n'+research_answer.context_instructions(language)
                cfg['_accounting_provider']=provider; cfg['_accounting_model']=model
                quran_context='' if task_run else quran_reference.context(question)
                # A question about what a Python block prints is answered by running it in the isolated worker, never on the host.
                code_result=None if task_run else await code_run.check(question)
                # Lessons the user taught and stored locally are recalled by the application and handed over as data.
                lesson_owner=local_auth.current_principal()['owner_id'];lesson_found=[] if (task_run or research_receipt) else lessons.recall(c,lesson_owner,question)
                lessons.mark_used(c,lesson_owner,lesson_found)
                # Wikipedia sources are fetched only when this message asks for them and the user has allowed internet use.
                if body.web_sources and not task_run and not research_receipt:
                    if settings()['mode']!='hybrid':raise HTTPException(403,'مصادر الإنترنت محجوبة في الوضع المحلي فقط. فعّل الوضع المختلط من الإعدادات. / Internet sources are blocked in local-only mode.')
                    try:web_found=await wiki_sources.fetch(question)
                    except wiki_sources.SourceError:raise HTTPException(502,'تعذّر الوصول إلى ويكيبيديا؛ لم يُولَّد جواب بلا مصادر. / Wikipedia could not be reached; no unsourced answer was generated.')
                # Passages are extra help: when they do not fit beside the conversation, use fewer, then none; the answer is then marked unsourced.
                web_tries=[web_found] if not web_found else [web_found,web_found[:1],[]]
                for web_try in web_tries:
                    web_found=web_try
                    check_context='' if task_run else text_checks.context(question,{**code_run.context(code_result),**lessons.context(lesson_found),**wiki_sources.context(web_found)})
                    try:
                        messages, chunks = grounding.compose(proj, memories, history, question, chunks, cfg, body.use_files or body.use_knowledge, system_extra=(task_run.instructions() if task_run else '')+skill_guidance,tool_context=check_context,fact_context=fact_context['text'],structured_evidence=ordinary_grounded)
                        break
                    except ValueError:
                        if web_try is web_tries[-1]:raise
            except ValueError as exc:
                raise HTTPException(413, str(exc)) from exc
            if ordinary_grounded:
                # Same evidence validator as explicit research, while retaining
                # the already-authorized ordinary conversation and corrections.
                research_receipt=research.new_receipt(grounded_options)
                research_receipt['mode']='ordinary_grounded_conversation'
                research_receipt['limits']['model_calls']=2 if cfg['max_tokens']>=256 else 1
                research_receipt['limits']['output_tokens']=cfg['max_tokens']
                research_receipt['coverage']=cfg.get('_context_budget',{}).get('evidence_coverage',{})
                job['research']=research_receipt
            public_chunks = [chunk for chunk in chunks if chunk.get('scope') == 'public']
            if research_receipt:research.enrich(c,chunks,research_receipt)
            context_links=context_lineage.merge(history_links,context_lineage.source_links(c,conv['project_id'],chunks),context_lineage.fact_links(fact_context['bindings']),context_lineage.memory_links(c,conv['project_id'],memories),lesson_context['bindings'],personal_context['bindings'],correction_context['bindings'])
            if not context_lineage.authorized(c,conv['project_id'],context_links,conversation_id=id):raise HTTPException(409,'Context source authorization changed; refresh sources')
            if provider!='ollama' and any(x.get('kind')=='fact' for x in context_links):raise HTTPException(400,'Reviewed fact context currently supports the local provider only')
            if provider!='ollama' and any(x.get('kind')=='personal_memory' for x in context_links):raise HTTPException(400,'Personal preference context currently supports the local provider only')
        mid=uid()
        c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)',(mid,id,'assistant','','streaming',model,now(),json.dumps(chunks,ensure_ascii=False)))
        generation_requests.bind(c,job,id,mid)
        context_lineage.save(c,mid,context_links)
        context_lineage.save_retrieval(c,mid,retrieval_options)
        context_lineage.save_compaction(c,mid,cfg.get('_context_budget',{}).get('history_compaction'))
        if research_receipt:research.save(c,mid,research_receipt)
        c.execute('UPDATE conversations SET updated_at=?,title=CASE WHEN title=? THEN ? ELSE title END WHERE id=?',(now(),'محادثة جديدة',question[:70],id))
        if calculator and time.monotonic()>=job['deadline']:
            raise HTTPException(504,'Calculator time budget reached while saving; changes were rolled back.')
    for key,value in list(ACTIVE.items()):
        if value is job and key!=id:ACTIVE.pop(key,None)
    stop=job['stop']; ACTIVE[id]=job
    if task_run:
        with get_db() as c:reasoning.save_receipt(c,mid,task_run.data)
    async def run():
        nonlocal messages, chunks, public_chunks
        content=''; state='complete'; metrics={}; error=None; iterator=None; authority_lost=False
        try:
            with get_db() as c:guard.require_project(c,conv['project_id'],job['actor'])
            revoked = False
            if context_links:
                with get_db() as c:
                    if not context_lineage.authorized(c,conv['project_id'],context_links,conversation_id=id):
                        chunks.clear();c.execute("UPDATE messages SET citations='[]' WHERE id=?",(mid,));revoked=True
            if public_chunks:
                with get_db() as c:
                    if len(public_knowledge.authorize_current(c, public_chunks)) != len(public_chunks):
                        chunks.clear()
                        c.execute("UPDATE messages SET citations='[]' WHERE id=?", (mid,))
                        revoked = True
            if revoked:
                raise ValueError('Knowledge rights or versions changed before generation. Please send again.')
            yield encode({'type':'start','conversation_id':id,'message_id':mid,'citations':chunks,'retrieval_options':retrieval_options,**({'run':task_run.data} if task_run else {}),**({'research':research_receipt} if research_receipt else {}),**({'context':cfg['_context_budget']['history_compaction']} if cfg.get('_context_budget',{}).get('history_compaction') else {})})
            with get_db() as c:guard.require_project(c,conv['project_id'],job['actor'])
            if calculator:
                if stop.is_set():
                    state='cancelled'
                else:
                    if time.monotonic()>=job['deadline']:raise reasoning.BudgetExceeded('Calculator time budget reached before emitting the result.')
                    task_run.tick()
                    content=calculator_content
                    with get_db() as c:c.execute('UPDATE messages SET content=? WHERE id=?',(content,mid))
                    yield encode({'type':'delta','content':content})
            elif (body.use_files or body.use_knowledge) and not chunks:
                content=('لم أجد هذه المعلومة في الملفات المحددة. / No relevant evidence was found in the selected files.' if file_scope else 'لم أجد مقاطع ذات صلة في المصادر المختارة. أضف مصدرًا مناسبًا أو عدّل السؤال. / No relevant source excerpts were found in the selected knowledge sources.')
                yield encode({'type':'delta','content':content})
                if research_receipt:research_receipt['status']='insufficient_evidence'
            elif research_receipt and research_receipt.get('model_unavailable'):
                content=research_answer.fallback(chunks,(body.research or grounded_options).response_language)
                research_receipt['status']='evidence_only'
                yield encode({'type':'delta','content':content})
            else:
                if public_chunks:
                    with get_db() as c:
                        if len(public_knowledge.authorize_current(c, public_chunks)) != len(public_chunks):
                            raise ValueError('Knowledge rights or versions changed before model dispatch. Please send again.')
                async def produce():
                    nonlocal messages,chunks,public_chunks
                    runtime_cfg=cfg|{'_runtime_scope':local_auth.current_principal()['owner_id'],'_deadline':job['deadline'],'_evidence_context_prepared':ordinary_grounded,'_evidence_repair_limit':1 if ordinary_grounded else 0}
                    async def authorize_dispatch():
                        if stop.is_set():raise asyncio.CancelledError()
                        with get_db() as c:
                            guard.require_project(c,conv['project_id'],job['actor'])
                            if not context_lineage.authorized(c,conv['project_id'],context_links,conversation_id=id):raise ValueError('Context source authorization changed before dispatch')
                            if fact_context['bindings'] and not facts.authorize_bindings(c,conv['project_id'],fact_context['bindings']):raise ValueError('Selected facts or their source authorization changed before dispatch')
                            if public_chunks and len(public_knowledge.authorize_current(c,public_chunks))!=len(public_chunks):raise ValueError('Knowledge rights changed before dispatch')
                            for chunk in chunks:
                                if chunk.get('scope')!='public' and not c.execute('SELECT 1 FROM knowledge_chunks ch JOIN knowledge_files f ON f.id=ch.file_id WHERE ch.id=? AND ch.project_id=? AND f.project_id=?',(chunk['id'],conv['project_id'],conv['project_id'])).fetchone():raise ValueError('Private source removed before dispatch')
                    if research_receipt:
                        async with aclosing(research_answer.stream(model,question,chunks,body.research or grounded_options,runtime_cfg,research_receipt,authorize_dispatch,context_messages=messages if ordinary_grounded else None)) as output:
                            async for packet in output:yield packet
                    elif task_run:
                        tool_context=''
                        async for preparation in task_run.prepare_events():
                            if 'tool_context' in preparation:tool_context=preparation['tool_context']
                            else:yield preparation
                        if tool_context:
                            messages,chunks=grounding.compose(proj,memories,history,question,chunks,cfg,body.use_files or body.use_knowledge,system_extra=task_run.instructions()+skill_guidance,tool_context=tool_context,fact_context=fact_context['text'])
                            public_chunks=[chunk for chunk in chunks if chunk.get('scope')=='public']
                            compaction=cfg.get('_context_budget',{}).get('history_compaction')
                            with get_db() as c:
                                c.execute('UPDATE messages SET citations=? WHERE id=?',(json.dumps(chunks,ensure_ascii=False),mid))
                                context_lineage.save_compaction(c,mid,compaction)
                            yield {'type':'citations','citations':chunks}
                            yield {'type':'context','context':compaction}
                        async with aclosing(task_run.stream(provider,model,messages,runtime_cfg,chunks,before_dispatch=authorize_dispatch)) as output:
                            async for packet in output:yield packet
                    else:
                        await authorize_dispatch()
                        async with aclosing(providers.stream(provider,model,messages,runtime_cfg)) as output:
                            source=output
                            if text_checks.constraint(question):
                                # A stated word count is checked on the finished answer; one corrective attempt, then an honest note.
                                async def again(draft,feedback):
                                    await authorize_dispatch()
                                    async with aclosing(providers.stream(provider,model,messages+[{'role':'assistant','content':draft},{'role':'user','content':feedback}],runtime_cfg)) as second:
                                        async for packet in second:yield packet
                                source=text_checks.enforce(output,question,again)
                            if code_result is not None:source=code_run.guard_stream(source,code_result)
                            # Qur'an requests: the application, not the model, decides which vocalised wording is released.
                            if quran_context:source=quran_reference.guard_stream(source,question)
                            async for packet in lessons.mark_stream(wiki_sources.cite_stream(text_checks.flag_stream(source),web_found),lesson_found):yield packet
                iterator=produce().__aiter__()
                async def access_changed():
                    while True:
                        try:
                            with get_db() as c:
                                guard.require_project(c,conv['project_id'],job['actor'])
                                if context_links and not context_lineage.authorized(c,conv['project_id'],context_links,conversation_id=id):return
                        except HTTPException:return
                        await asyncio.sleep(.1)
                async with asyncio.timeout(max(.001,job['deadline']-time.monotonic())):
                    while True:
                        if stop.is_set(): state='cancelled'; break
                        next_chunk=asyncio.create_task(anext(iterator))
                        cancelled=asyncio.create_task(stop.wait())
                        authority_changed=asyncio.create_task(access_changed())
                        try:
                            done,_=await asyncio.wait([next_chunk,cancelled,authority_changed],return_when=asyncio.FIRST_COMPLETED)
                            if authority_changed in done:raise ValueError('Source or project access changed; generation was stopped.')
                            if cancelled in done:
                                state='cancelled'; break
                            try: packet=next_chunk.result()
                            except StopAsyncIteration: break
                            with get_db() as c:guard.require_project(c,conv['project_id'],job['actor'])
                            if context_links:
                                with get_db() as c:
                                    if not context_lineage.authorized(c,conv['project_id'],context_links,conversation_id=id):raise ValueError('Source access changed during generation; no further source-dependent output was accepted.')
                            if packet.get('type'):
                                if research_receipt:
                                    with get_db() as c:research.save(c,mid,research_receipt)
                                if task_run:
                                    with get_db() as c:reasoning.save_receipt(c,mid,task_run.data)
                                yield encode(packet)
                                continue
                            if 'replacement' in packet:
                                if len(packet['replacement'])>100000:raise ValueError('Replacement exceeded output limit')
                                content=packet['replacement']
                                with get_db() as c:c.execute('UPDATE messages SET content=? WHERE id=?',(content,mid))
                                yield encode({'type':'replace','content':content})
                                continue
                            delta=packet.get('content','')
                            metrics.update(packet.get('metrics',{}))
                            reason = packet.get('finish_reason')
                            if reason:
                                metrics['finish_reason'] = reason
                                if reason not in {'stop', 'end_turn', 'stop_sequence', 'completed'}:
                                    state = 'interrupted'
                                    error = 'الرد غير مكتمل؛ انتهى التوليد قبل اكتماله. / Response incomplete; generation ended before completion.'
                            if delta:
                                content+=delta
                                with get_db() as c: c.execute('UPDATE messages SET content=? WHERE id=?',(content,mid))
                                yield encode({'type':'delta','content':delta})
                            if len(content)>100000: raise ValueError('Response exceeded output limit.')
                        finally:
                            for task in (next_chunk,cancelled,authority_changed):
                                if not task.done(): task.cancel()
                            await asyncio.gather(next_chunk,cancelled,authority_changed,return_exceptions=True)
                await iterator.aclose()
                if state == 'complete' and not content.strip():
                    state = 'error'
                    error = 'لم يُرجع النموذج نصًا. / The model returned no answer text.'
        except (asyncio.CancelledError,GeneratorExit):
            state='cancelled'
            raise
        except (Exception,TimeoutError) as exc:
            state='interrupted' if isinstance(exc,(reasoning.BudgetExceeded,TimeoutError)) else 'error'
            error=str(exc) if isinstance(exc,ValueError) else 'Generation interrupted by provider failure or time limit. Partial text was saved.'
        finally:
            try:
                if iterator is not None:
                    try:await iterator.aclose()
                    except Exception:
                        state='error';error='Provider cleanup failed; partial text was preserved.'
                # Invalid citation markers cannot become source links. Supported sources remain separate from claims.
                invalid=[n for n in reasoning.citation_markers(content) if not reasoning.valid_marker(n,len(chunks))]
                if invalid and not (task_run and task_run.contract):
                    content=re.sub(r'\[S(\d+)\]',lambda m:m.group() if reasoning.valid_marker(m[1],len(chunks)) else '[unverified citation]',content)
                    error='Model returned an unsupported citation; it was flagged.'
                if task_run and task_run.contract:
                    result=task_run.contract.check(content,len(chunks))
                    task_run.check('output_schema','pass' if result['schema_valid'] else 'fail','Final saved JSON structure only; factual correctness is not certified.',json.dumps(result,ensure_ascii=False))
                    if task_run.contract.citation_field:task_run.check('citation_serialization','pass' if result['citation_format_valid'] else 'fail','Final saved source identifier format only; semantic support is not certified.',json.dumps(result,ensure_ascii=False))
                with get_db() as c:
                    c.execute('BEGIN IMMEDIATE')
                    try:guard.require_project(c,conv['project_id'],job['actor'])
                    except HTTPException:
                        authority_lost=True;content='';chunks.clear();state='error';error='Project access changed; the result was withdrawn.'
                        c.execute("UPDATE messages SET citations='[]' WHERE id=?",(mid,))
                    if context_links and not context_lineage.authorized(c,conv['project_id'],context_links,conversation_id=id):
                        content='';chunks.clear();state='error';error='Evidence access changed before final save; the answer was withdrawn.'
                        c.execute("UPDATE messages SET citations='[]' WHERE id=?",(mid,))
                        if research_receipt:research_receipt.pop('claims',None)
                    if research_receipt:
                        if state!='complete':research_receipt['status']=state
                        research_receipt['seconds']=round(time.monotonic()-job['started'],3)
                        research.save(c,mid,research_receipt)
                    c.execute('UPDATE messages SET content=?,status=? WHERE id=?',(content,state,mid))
                    if task_run:
                        task_run.stage('save','complete');reasoning.save_receipt(c,mid,task_run.finish(state))
                    event(c,conv['project_id'],'response_'+state,f'{provider}/{model}: {state}')
            finally:
                for key,value in list(ACTIVE.items()):
                    if value is job:ACTIVE.pop(key,None)
        if authority_lost:
            yield encode({'type':'error','message':'Project access changed; the result was withdrawn.'})
            return
        with get_db() as c:
            try:
                row=require(c,'messages',mid,job['actor'])
                saved=message_dict(row) if context_lineage.disclosable(c,conv['project_id'],dict(row)) else redacted_message(row)
            except HTTPException:
                yield encode({'type':'error','message':'Project access changed; the result was withdrawn.'})
                return
        if error: yield encode({'type':'error','message':error})
        yield encode({'type':'done','message':saved,'metrics':metrics})
    output=run()
    async def finalize_response():
        try:
            await output.aclose()
        finally:
            try:
                with get_db() as c:
                    row=c.execute('SELECT status FROM messages WHERE id=?',(mid,)).fetchone()
                    if row and row['status']=='streaming':
                        c.execute("UPDATE messages SET status='cancelled' WHERE id=?",(mid,))
                        if task_run:reasoning.save_receipt(c,mid,task_run.finish('cancelled'))
                        if research_receipt:
                            research_receipt['status']='cancelled';research.save(c,mid,research_receipt)
            finally:
                for key,value in list(ACTIVE.items()):
                    if value is job:ACTIVE.pop(key,None)
    return ManagedStreamingResponse(output,finalizer=finalize_response,media_type='application/x-ndjson',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

@app.post('/api/conversations/{id}/stop')
async def stop(id:str):
    with get_db() as c:require(c,'conversations',id)
    active=id in ACTIVE
    if active: ACTIVE[id]['stop'].set()
    return {'stopped':active}

@app.get('/api/conversations/{id}/research-status')
def research_status(id:str):
    with get_db() as c:require(c,'conversations',id)
    job=ACTIVE.get(id)
    return JSONResponse({'active':bool(job and job.get('research')),**({'receipt':job['research']} if job and job.get('research') else {})},headers={'Cache-Control':'no-store'})

class MemoryInput(BaseModel):
    model_config={'extra':'forbid'}
    content:str=Field(min_length=1,max_length=2000)
    scope:Literal['preference','project']='preference'
    project_id:str|None=None
    expected_version:int|None=Field(default=None,ge=1,strict=True)

def memory_actor(c,project_id=None):
    who=local_auth.current_principal()
    actor=guard.Actor(who['owner_id'],who['session_id'])
    if not local_auth.session_active(c,actor.owner_id,actor.session_id):raise HTTPException(401,'Session is no longer active')
    if project_id:
        try:guard.check_actor(c,actor,project_id)
        except guard.Denied:raise HTTPException(404,'Project memory is not available')
    return actor

def memory_record(c,id):
    row=c.execute('SELECT m.*,r.version,r.updated_at FROM memories m JOIN memory_revisions r ON r.memory_id=m.id WHERE m.id=?',(id,)).fetchone()
    if row is None:raise HTTPException(404,'Memory not found')
    memory_actor(c,row['project_id'] if row['scope']=='project' else None)
    if row['scope']=='preference':
        actor=memory_actor(c)
        try:personal_memory.get_entry(c,actor,id)
        except personal_memory.PersonalMemoryError as exc:raise HTTPException(exc.status_code,exc.code) from exc
    return row

def validate_memory_content(content):
    if not content.strip():raise HTTPException(422,'Memory must not be blank')
    from backend.coding_context import scan_secrets
    if scan_secrets(content):raise HTTPException(422,'Possible credential content cannot be saved as memory')

class LessonInput(BaseModel):
    model_config={'extra':'forbid'}
    title:str=Field(min_length=1,max_length=400)
    content:str=Field(min_length=1,max_length=4000)

class LessonPatch(BaseModel):
    model_config={'extra':'forbid'}
    title:str|None=Field(default=None,min_length=1,max_length=400)
    content:str|None=Field(default=None,min_length=1,max_length=4000)
    enabled:bool|None=None

def lesson_call(action):
    """Lessons are the signed-in owner's own notebook; every call runs under that owner."""
    with get_db() as c:
        actor=memory_actor(c)
        try:return action(c,actor.owner_id)
        except KeyError:raise HTTPException(404,'Lesson not found')
        except lessons.LessonError as exc:raise HTTPException(422,str(exc))

@app.get('/api/lessons')
def list_lessons():return lesson_call(lambda c,owner:lessons.list_lessons(c,owner))

@app.post('/api/lessons')
def add_lesson(body:LessonInput):return lesson_call(lambda c,owner:lessons.add(c,owner,body.title,body.content))

@app.patch('/api/lessons/{id}')
def edit_lesson(id:str,body:LessonPatch):return lesson_call(lambda c,owner:lessons.update(c,owner,id,body.title,body.content,body.enabled))

@app.delete('/api/lessons/{id}')
def remove_lesson(id:str):
    lesson_call(lambda c,owner:lessons.delete(c,owner,id));return {'ok':True}

@app.get('/api/memories')
def memories(project_id:str|None=None):
    with get_db() as c:
        actor=memory_actor(c,project_id)
        personal=personal_memory.list_entries(c,actor)
        project=[dict(x) for x in c.execute("SELECT m.*,r.version,r.updated_at FROM memories m JOIN memory_revisions r ON r.memory_id=m.id WHERE m.scope='project' AND m.project_id=? ORDER BY m.created_at",(project_id,))]
        return personal+project

@app.post('/api/memories')
def add_memory(body:MemoryInput):
    if body.scope=='preference':
        raise HTTPException(423,'Personal memory is paused pending the security acceptance gate. Existing entries remain available for review and deletion.')
    if not body.project_id:raise HTTPException(422,'Project memory requires a project')
    if body.expected_version is not None:raise HTTPException(422,'New memory must not specify an existing version')
    validate_memory_content(body.content)
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');actor=memory_actor(c,body.project_id)
        id=uid()
        c.execute('INSERT INTO memories VALUES (?,?,?,?,?)',(id,body.scope,body.project_id if body.scope=='project' else None,body.content,now()))
        guard.audit(c,actor,body.project_id,id,'project_memory_create','allow','explicit_user_write')
        return dict(memory_record(c,id))

@app.patch('/api/memories/{id}')
def edit_memory(id:str,body:MemoryInput):
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');prior=memory_record(c,id)
        if body.scope=='preference' or prior['scope']=='preference':
            raise HTTPException(423,'Personal memory edits are paused pending the security acceptance gate; review or delete the entry instead.')
        if body.project_id!=prior['project_id']:raise HTTPException(409,'Memory cannot be moved to another project during correction')
        if body.expected_version is None:raise HTTPException(422,'expected_version is required for a correction')
        if body.expected_version!=prior['version']:raise HTTPException(409,'Memory changed; reload before correcting it')
        validate_memory_content(body.content)
        actor=memory_actor(c,prior['project_id'])
        c.execute('UPDATE memories SET content=? WHERE id=?',(body.content,id))
        context_lineage.invalidate_legacy_memory_context(c,prior['project_id'])
        guard.audit(c,actor,prior['project_id'],id,'project_memory_correct','allow','expected_version_matched')
        return dict(memory_record(c,id))

@app.delete('/api/memories/{id}')
def delete_memory(id:str,expected_version:int|None=None):
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');prior=memory_record(c,id)
        if prior['scope']=='project' and expected_version is None:raise HTTPException(422,'expected_version is required for project memory deletion')
        if expected_version is not None and expected_version!=prior['version']:raise HTTPException(409,'Memory changed; reload before deleting it')
        actor=memory_actor(c,prior['project_id'] if prior['scope']=='project' else None)
        if prior['scope']=='preference':
            try:return personal_memory.delete_entry(c,actor,id,expected_version if expected_version is not None else prior['version'])
            except personal_memory.PersonalMemoryError as exc:raise HTTPException(exc.status_code,exc.code) from exc
        c.execute('DELETE FROM memories WHERE id=?',(id,))
        if prior['scope']=='project':context_lineage.invalidate_legacy_memory_context(c,prior['project_id'])
        guard.audit(c,actor,prior['project_id'],id,'memory_delete','allow','explicit_user_deletion')
    return {'deleted':True}

class EventInput(BaseModel):
    kind:Literal['goal','decision','completed','blocker','next_step']
    summary:str=Field(min_length=1,max_length=2000)

@app.post('/api/projects/{id}/events')
def add_event(id:str,body:EventInput):
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        require(c,'projects',id); event(c,id,body.kind,body.summary)
    return {'recorded':True}

@app.get('/api/projects/{id}/events')
def events(id:str):
    with get_db() as c:
        require(c,'projects',id)
        return [dict(x) for x in c.execute('SELECT * FROM events WHERE project_id=? ORDER BY created_at DESC LIMIT 200',(id,))]

@app.get('/api/projects/{id}/state')
def state(id:str):
    log=events(id)
    with get_db() as c:
        require(c,'projects',id)
        decisions=[json.loads(row['payload']) | {'id':row['id'],'version':row['version']} for row in c.execute("SELECT * FROM studio_entries WHERE project_id=? AND kind='decision' ORDER BY updated_at DESC",(id,)) if studio.entry_allowed(c,row)]
    def latest(kind,default=''): return next((e['summary'] for e in log if e['kind']==kind),default)
    current=[{'id':d['id'],'summary':d['title']+' — '+d['decision'],'status':d['status'],'version':d['version'],'source':'decision-notebook'} for d in decisions if d['status'] in {'adopted','implemented'}]
    completed=[{'id':d['id'],'summary':d['title']+' — '+d['implementation_evidence'],'status':'implemented','verification':'user-attestation','source':'decision-notebook'} for d in decisions if d['status']=='implemented']
    return {'goal':latest('goal'),'decisions':current+[e for e in log if e['kind']=='decision'],'completed':completed+[e for e in log if e['kind']=='completed'],'blockers':[e for e in log if e['kind']=='blocker'],'next_step':latest('next_step'),'events':log[:20],'source':'event-log+current-decisions','updated_at':log[0]['created_at'] if log else None}

@app.get('/api/search/web')
async def web_search(q:str):
    if settings()['mode']!='hybrid': raise HTTPException(403,'Internet search is blocked in local-only mode.')
    key=providers.secret('brave')
    if not key: raise HTTPException(503,'No search provider configured; no results available.')
    marker=object(); sync_client.BUSY.add(marker)
    try:
        async with httpx.AsyncClient(timeout=15,trust_env=False) as client:
            r=await client.get('https://api.search.brave.com/res/v1/web/search',params={'q':q[:500],'count':5},headers={'X-Subscription-Token':key})
            r.raise_for_status()
            return {'retrieved_at':now(),'content_read':False,'results':[{'title':x['title'],'url':x['url'],'description':x.get('description',''),'published_at':None,'search_age':x.get('age')} for x in r.json().get('web',{}).get('results',[])]}
    except httpx.HTTPError: raise HTTPException(502,'Search provider failed; no synthetic results returned.')
    finally: sync_client.BUSY.discard(marker)

STATIC=Path(os.environ.get('RIWAQ_STATIC_DIR',Path(__file__).resolve().parent.parent/'frontend'/'dist'))

@app.get('/{path:path}')
def spa(path:str):
    if path.startswith('api/'): raise HTTPException(404,'Unknown API endpoint')
    target=(STATIC/path).resolve()
    if target.is_relative_to(STATIC.resolve()) and target.is_file(): return FileResponse(target)
    index=STATIC/'index.html'
    if index.exists(): return FileResponse(index,headers={'Cache-Control':'no-cache','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' http://127.0.0.1:8765; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"})
    return JSONResponse({'name':'Riwaq','detail':'Build frontend with npm --prefix frontend run build.'},503)
