"""Adapters to existing trusted execution boundaries, not a second tool engine."""
from __future__ import annotations
import asyncio
import hashlib
import io
import json
import tempfile
import threading
import wave
from pathlib import Path
from fastapi import HTTPException
from backend import guard,knowledge,context_lineage
from backend.db import get_db,data_dir,now


async def stopped_thread(function,check):
    """Never acknowledge cancellation or remove the workspace before worker exit."""
    if check() is False:raise PermissionError('Execution authorization unavailable')
    stop=threading.Event();task=asyncio.create_task(asyncio.to_thread(function,stop))
    try:
        while not task.done():
            if check() is False:raise PermissionError('Execution authorization unavailable')
            await asyncio.wait({task},timeout=.1)
        result=await task
        if check() is False:raise PermissionError('Execution authorization unavailable')
        return result
    finally:
        if not task.done():
            stop.set()
            # Reviewed workers have their own hard timeout and process-tree cleanup.
            while not task.done():
                try:await asyncio.shield(task)
                except asyncio.CancelledError:continue
            # Consume a worker failure during cancellation without publishing it.
            if not task.cancelled():task.exception()


async def speech(text,options,check):
    from backend import voice,voice_audio
    check();voices=await voice.voices();check()
    chosen=next((x for x in voices if x['id']==options.voiceId and x.get('enabled',True)),None)
    if not chosen or chosen['language'].casefold()!=options.language.casefold():
        raise ValueError('No installed local voice matches the requested language')
    voice_audio.validate_tts(text,options.voiceId,options.language,options.rate,100)
    def render(stop):
        with tempfile.TemporaryDirectory(prefix='skill-speech-',dir=voice.work_dir()) as tmp:
            folder=Path(tmp)
            receipt=voice_audio.synthesize(text,options.voiceId,options.language,options.rate,100,folder,stop)
            raw=(folder/'speech.wav').read_bytes()
            if not 44<len(raw)<=8*1024**2:raise ValueError('Invalid speech size')
            with wave.open(io.BytesIO(raw),'rb') as sound:
                frames=sound.getnframes();rate=sound.getframerate();channels=sound.getnchannels();width=sound.getsampwidth()
                if not frames or not 8000<=rate<=96000 or channels not in (1,2) or width not in (1,2,3,4):raise ValueError('Invalid speech encoding')
                samples=sound.readframes(frames)
                if len(samples)!=frames*channels*width or not any(samples):raise ValueError('Empty or truncated speech frames')
                duration=frames/rate
                if not 0<duration<=60:raise ValueError('Invalid speech duration; maximum 60 seconds')
            digest=hashlib.sha256(raw).hexdigest()
            if receipt.get('audio',{}).get('sha256')!=digest:raise ValueError('Worker audio digest mismatch')
            if (receipt.get('text_sha256')!=hashlib.sha256(text.encode()).hexdigest()
                    or receipt.get('voice',{}).get('id')!=options.voiceId
                    or receipt.get('voice',{}).get('language','').casefold()!=options.language.casefold()):
                raise ValueError('Worker speech request binding mismatch')
            return {'name':'riwaq-speech.wav','mime':'audio/wav','content':raw,'sha256':digest}, {'text':text,'language':options.language,'voice_id':options.voiceId,'duration':duration,'sha256':digest,'format':'wav','sample_rate':rate,'channels':channels,'pronunciation_review':'not_human_reviewed','processing':'local'}
    return await stopped_thread(render,check)


def memory_retrieve(c,who,pid,query,limit=6):
    guard.check_actor(c,who,pid)
    tokens=set(knowledge.tokens(query)) if hasattr(knowledge,'tokens') else set(knowledge.tokenize(query))
    rows=c.execute("SELECT m.*,r.version FROM memories m JOIN memory_revisions r ON r.memory_id=m.id WHERE m.scope='project' AND m.project_id=? ORDER BY r.updated_at DESC LIMIT 200",(pid,)).fetchall()
    scored=[]
    for row in rows:
        content=row['content'];words=set(knowledge.tokens(content)) if hasattr(knowledge,'tokens') else set(knowledge.tokenize(content))
        score=len(tokens & words)
        if score:scored.append((score,dict(row)))
    selected=[];size=0
    for _,row in sorted(scored,key=lambda x:-x[0]):
        if len(selected)>=limit or size+len(row['content'])>3000:continue
        selected.append(row);size+=len(row['content'])
    return {'memories':selected,'links':context_lineage.memory_links(c,pid,selected),'scope':'project only; personal injection remains blocked','ranking':'bounded lexical relevance, not semantic certainty'}


def memory_delete(c,who,pid,ids,versions):
    guard.check_actor(c,who,pid)
    if not ids or set(ids)!=set(versions):raise ValueError('Exact memory IDs and expected versions are required')
    rows=[]
    for mid in ids:
        row=c.execute("SELECT m.id,r.version FROM memories m JOIN memory_revisions r ON r.memory_id=m.id WHERE m.id=? AND m.project_id=? AND m.scope='project'",(mid,pid)).fetchone()
        if not row:raise HTTPException(404,'Memory unavailable')
        if row['version']!=versions[mid]:raise HTTPException(409,'Memory revision changed')
        rows.append(row)
    for row in rows:
        c.execute('DELETE FROM memories WHERE id=?',(row['id'],))
        guard.audit(c,who,pid,row['id'],'memory_delete','allow','explicit_version_bound_skill_deletion')
    context_lineage.invalidate_legacy_memory_context(c,pid)
    return {'deleted_ids':list(ids),'status':'deleted','derivatives':'source bindings invalidated; existing skill derivatives purged','backup_scope':'Existing backups and previously downloaded copies are not physically erased by this operation.'}


async def repository(body,pid,check):
    from backend import coding,coding_context
    if not body.inputs.workspaceId:raise ValueError('Choose an existing authorized coding workspace')
    check()
    with get_db() as c:_,grant=coding.binding(c,pid,body.inputs.workspaceId)
    result=await asyncio.to_thread(coding_context.snapshot,grant,body.inputs.repositoryPaths or None,body.request[:1000],5000)
    check()
    with get_db() as c:_,current=coding.binding(c,pid,body.inputs.workspaceId)
    coding.check_preimages(current,result['bindings'])
    return result


async def unit_checks(body,pid,check):
    from backend import coding,coding_checks,isolated_worker
    if not body.inputs.workspaceId or body.inputs.testRequest is None:raise ValueError('An authorized workspace and typed test request are required')
    check()
    with get_db() as c:_,grant=coding.binding(c,pid,body.inputs.workspaceId)
    intent=body.inputs.testRequest
    files,bindings,kind,digest=coding_checks.snapshot(grant,{'selected_paths':body.inputs.repositoryPaths},intent)
    config=isolated_worker.load_config(data_dir()/'isolated-workers.json')
    def permitted():
        try:
            check()
            with get_db() as c:_,current=coding.binding(c,pid,body.inputs.workspaceId)
            coding.check_preimages(current,bindings)
            return True
        except (Exception,asyncio.CancelledError):return False
    receipt=await stopped_thread(lambda stop:isolated_worker.execute(files,intent.runner,intent.targets,config=config,stop=stop,authorize=permitted,timeout_seconds=intent.timeout_seconds,memory_mb=intent.memory_mb),check)
    return coding_checks.clean_receipt(receipt)|{'snapshot_sha256':digest,'snapshot_kind':kind,'bindings':bindings}


def credential_status():
    from backend import providers
    # Only availability crosses this boundary. Never return value, prefix or length.
    statuses=[]
    for provider in ('openai','anthropic','brave'):
        try: configured=bool(providers.secret(provider))
        except Exception:configured=False
        statuses.append({'provider':provider,'configured':configured,'generation_enabled':False,'storage':'operating-system keyring'})
    return {'credentials':statuses,'connection_verified':False,'cloud_generation':'blocked by existing security gate'}
