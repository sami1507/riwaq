"""Persistent unsent text, scoped to the authenticated owner and project.

This is the composer, not memory/training/retrieval input. Empty rows are version
tombstones so an older browser save cannot revive cleared text. No raw draft is
written to the activity log. Source originals and model requests stay separate.
"""
import json
from fastapi import APIRouter,HTTPException,Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field
from backend.db import get_db,now
from backend.guard import require_project

router=APIRouter(prefix='/api/projects/{project_id}/composer-draft')
EMPTY={'assistant':'','calculator':'','problem':''}

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)

class Contents(Strict):
    assistant:str=Field(default='',max_length=20000)
    calculator:str=Field(default='',max_length=20000)
    problem:str=Field(default='',max_length=20000)

class Save(Strict):
    conversation_id:str|None=Field(default=None,min_length=1,max_length=80)
    expected_version:int=Field(ge=0,le=2**53-2)
    contents:Contents

class Transfer(Strict):
    expected_version:int=Field(ge=0,le=2**53-2)
    target_conversation_id:str=Field(min_length=1,max_length=80)

def init(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS composer_drafts(
      owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      conversation_key TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>=1),
      contents TEXT NOT NULL,updated_at TEXT NOT NULL,
      PRIMARY KEY(owner_id,project_id,conversation_key));
    CREATE TRIGGER IF NOT EXISTS composer_draft_conversation_deleted AFTER DELETE ON conversations BEGIN
      DELETE FROM composer_drafts WHERE conversation_key=OLD.id;
    END;
    ''')

def scope(conn,project_id,conversation_id):
    actor=require_project(conn,project_id)
    if conversation_id is not None:
        row=conn.execute('SELECT project_id FROM conversations WHERE id=?',(conversation_id,)).fetchone()
        if not row or row['project_id']!=project_id:raise HTTPException(404,'Project resource unavailable')
    return actor.owner_id,project_id,conversation_id or ''

def row(conn,key):
    return conn.execute('SELECT version,contents FROM composer_drafts WHERE owner_id=? AND project_id=? AND conversation_key=?',key).fetchone()

def state(value):
    return {'version':value['version'] if value else 0,'contents':json.loads(value['contents']) if value else EMPTY.copy(),'scope':'local_composer_only'}

def deliver(value):return JSONResponse(value,headers={'Cache-Control':'no-store','Pragma':'no-cache'})

def write(conn,key,contents,expected):
    current=row(conn,key)
    version=current['version'] if current else 0
    if expected!=version:raise HTTPException(409,{'code':'draft_version_conflict','message':'The saved draft changed. Your text was not overwritten.','current_version':version})
    conn.execute('''INSERT INTO composer_drafts VALUES (?,?,?,?,?,?) ON CONFLICT(owner_id,project_id,conversation_key)
      DO UPDATE SET version=excluded.version,contents=excluded.contents,updated_at=excluded.updated_at''',
      (*key,version+1,json.dumps(contents,ensure_ascii=False,separators=(',',':')),now()))
    return state(row(conn,key))

@router.get('')
def read_draft(project_id:str,conversation_id:str|None=Query(default=None,min_length=1,max_length=80)):
    with get_db() as conn:return deliver(state(row(conn,scope(conn,project_id,conversation_id))))

@router.patch('')
def save_draft(project_id:str,body:Save):
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        key=scope(conn,project_id,body.conversation_id)
        return deliver(write(conn,key,body.contents.model_dump(),body.expected_version))

@router.post('/transfer')
def transfer(project_id:str,body:Transfer):
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        source=scope(conn,project_id,None);target=scope(conn,project_id,body.target_conversation_id)
        if row(conn,target):raise HTTPException(409,'Target conversation already has a draft')
        old=state(row(conn,source))
        if old['version']!=body.expected_version:raise HTTPException(409,'The source draft changed; transfer was not applied')
        moved=write(conn,target,old['contents'],0)
        write(conn,source,EMPTY,old['version'])
        return deliver(moved)
