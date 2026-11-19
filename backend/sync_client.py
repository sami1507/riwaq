"""Opt-in encrypted sync gateway; OS keyring holds account session tokens."""
import json
import hashlib
import os
import re
import sqlite3
from urllib.parse import urlparse
import httpx
import keyring
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from backend.db import settings, get_db

router=APIRouter(prefix='/api/sync')
BUSY=set()

def endpoint():
    value=os.environ.get('RIWAQ_SYNC_URL','http://127.0.0.1:8766').rstrip('/')
    u=urlparse(value)
    local=u.hostname in ('127.0.0.1','localhost')
    if u.username or u.password or u.query or u.fragment or u.path not in ('','/') or not (u.scheme=='https' or (local and u.scheme=='http')):
        raise HTTPException(503,'Sync endpoint must be HTTPS, or loopback HTTP for local tests, without credentials or a path.')
    return value

def token():
    # A keyring write and a SQLite commit are not one transaction. The durable
    # fingerprint prevents use of a leftover token after a failed/crashed commit.
    # Legacy keyring-only credentials require explicit sign-in after migration.
    try:
        target=endpoint()
        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_auth_state'").fetchone():return None
            row=conn.execute('SELECT token_hash FROM sync_auth_state WHERE endpoint=?',(target,)).fetchone()
        if not row or not row[0]:return None
        value=keyring.get_password('RiwaqSync',target)
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',value):return None
        if hashlib.sha256(value.encode('ascii')).hexdigest()!=row[0]:return None
        return value
    except Exception: return None


def _auth_state(conn,target,who):
    from backend.local_auth import session_active
    if not session_active(conn,who['owner_id'],who['session_id']):
        raise HTTPException(401,'Local owner session is no longer active')
    conn.execute('''CREATE TABLE IF NOT EXISTS sync_auth_state(
        endpoint TEXT PRIMARY KEY,owner_id TEXT NOT NULL,revision INTEGER NOT NULL,token_hash TEXT)''')
    conn.execute('INSERT OR IGNORE INTO sync_auth_state VALUES(?,?,0,NULL)',(target,who['owner_id']))
    row=conn.execute('SELECT * FROM sync_auth_state WHERE endpoint=?',(target,)).fetchone()
    if row['owner_id']!=who['owner_id']:raise HTTPException(403,'Sync authorization belongs to another local owner')
    return row

@router.get('/status')
def status():
    return {'endpoint':endpoint(),'configured':True,'authenticated':bool(token()),'mode':settings()['mode'],'automatic':False}

async def proxy(method,path,payload=None,auth=True,*,credential=None,target=None):
    if settings()['mode']!='hybrid': raise HTTPException(403,'Sync is blocked in local-only mode.')
    headers={}
    if auth:
        credential=credential if credential is not None else token()
        if not credential: raise HTTPException(401,'Sign in to the configured sync server first.')
        headers['Authorization']='Bearer '+credential
    marker=object();BUSY.add(marker)
    try:
        async with httpx.AsyncClient(timeout=20,trust_env=False,follow_redirects=False) as client:
            async with client.stream(method,(target or endpoint())+path,json=payload,headers=headers) as r:
                parts=[];size=0
                async for part in r.aiter_bytes():
                    size+=len(part)
                    if size>6*1024*1024: raise HTTPException(502,'Sync response exceeds limit.')
                    parts.append(part)
                try: result=json.loads(b''.join(parts)) if parts else {'ok':True}
                except ValueError: raise HTTPException(502,'Invalid sync response')
                if r.status_code>=300:
                    raise HTTPException(r.status_code if 400<=r.status_code<500 else 502,'Sync request rejected by server (HTTP '+str(r.status_code)+').')
                return result
    except httpx.HTTPError:
        raise HTTPException(503,'Configured sync service is unavailable. No data was substituted.')
    finally: BUSY.discard(marker)

class Login(BaseModel):
    email:str=Field(min_length=3,max_length=254)
    password:str=Field(min_length=12,max_length=128)

async def authenticate(kind,body):
    from backend.local_auth import current_principal
    who=current_principal();target=endpoint()
    if settings()['mode']!='hybrid':raise HTTPException(403,'Sync is blocked in local-only mode.')
    try:
        with get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=_auth_state(conn,target,who);revision=row['revision']+1
            conn.execute('UPDATE sync_auth_state SET revision=? WHERE endpoint=?',(revision,target))
    except (OSError,sqlite3.Error):raise HTTPException(503,'Local sync authorization storage is unavailable')
    result=await proxy('POST','/auth/'+kind,body.model_dump(),auth=False,target=target)
    value=result.get('token') if isinstance(result,dict) else None
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',value):
        raise HTTPException(502,'Sync server returned an invalid session')
    try:
        # No await while this cross-process SQLite write lock protects the
        # epoch comparison, keyring mutation and committed fingerprint.
        with get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=_auth_state(conn,target,who)
            if row['revision']!=revision:raise HTTPException(409,'Sync authentication was superseded; the returned session was not retained')
            if settings()['mode']!='hybrid' or endpoint()!=target:
                raise HTTPException(409,'Sync configuration changed; the returned session was not retained')
            try:keyring.set_password('RiwaqSync',target,value)
            except Exception:raise HTTPException(503,'Secure OS token storage failed. Sync is not signed in.')
            conn.execute('UPDATE sync_auth_state SET token_hash=? WHERE endpoint=?',(hashlib.sha256(value.encode('ascii')).hexdigest(),target))
    except (OSError,sqlite3.Error):raise HTTPException(503,'Local sync authorization storage is unavailable')
    return {'authenticated':True,'email':body.email}

@router.post('/register')
async def register(body:Login): return await authenticate('register',body)

@router.post('/login')
async def login(body:Login): return await authenticate('login',body)

@router.post('/logout')
async def logout():
    from backend.local_auth import current_principal
    who=current_principal();target=endpoint();failed_delete=False
    try:
        with get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=_auth_state(conn,target,who)
            # token() only reads the last committed row. Other service instances
            # cannot mutate the keyring until this SQLite write lock is released.
            prior=token()
            conn.execute('UPDATE sync_auth_state SET revision=?,token_hash=NULL WHERE endpoint=?',(row['revision']+1,target))
            try:keyring.delete_password('RiwaqSync',target)
            except keyring.errors.PasswordDeleteError:pass
            except Exception:failed_delete=True
        # Commit revocation even if OS deletion fails: token() must fail closed.
    except (OSError,sqlite3.Error):raise HTTPException(503,'Local sync authorization storage is unavailable')
    if failed_delete:raise HTTPException(503,'Local sync authorization was revoked but the OS credential could not be cleared')
    remote_revoked=False
    try:
        if prior and settings()['mode']=='hybrid':
            await proxy('POST','/auth/logout',credential=prior,target=target)
            remote_revoked=True
    except HTTPException: pass
    return {'signed_out':True,'remote_revoked':remote_revoked}

@router.get('/snapshots')
async def list_snapshots(): return await proxy('GET','/snapshots')

def safe_id(id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',id): raise HTTPException(400,'Invalid snapshot ID')
    return id

@router.get('/snapshots/{id}')
async def get_snapshot(id:str): return await proxy('GET','/snapshots/'+safe_id(id))

@router.put('/snapshots/{id}')
async def put_snapshot(id:str,request:Request):
    body=await request.body()
    if len(body)>4*1024*1024: raise HTTPException(413,'Encrypted snapshot exceeds 4 MB')
    return await proxy('PUT','/snapshots/'+safe_id(id),await request.json())
