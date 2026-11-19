"""Version identities and logical project folders over existing skill artifacts.

Bytes stay in skill_artifacts. No filesystem moves, implicit sharing, or name-
based replacement. Source access always returns to its original Guard boundary.
"""
from __future__ import annotations
from contextvars import ContextVar
import hashlib
import json
from pathlib import PurePosixPath
import re
import unicodedata
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from backend import guard
from backend.db import now,uid


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)


class SourceRef(Strict):
    kind: str=Field(pattern=r'^(document|artifact)$')
    id: str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    version: int=Field(ge=1)
    sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    pinned: bool=False


class OutputVersion(Strict):
    artifactId: str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    expectedVersion: int=Field(ge=1)
    reason: str=Field(min_length=1,max_length=250)


class VersionRequest(Strict):
    action: str=Field(pattern=r'^(list|restore)$')
    artifactId: str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    version: int|None=Field(default=None,ge=1)
    expectedVersion: int|None=Field(default=None,ge=1)
    @model_validator(mode='after')
    def required(self):
        if self.action=='restore' and (self.version is None or self.expectedVersion is None):
            raise ValueError('Restore requires source and expected current versions')
        return self


def logical_name(value,folder=False):
    if not isinstance(value,str) or len(value)>240:raise ValueError('Invalid logical file name')
    value=unicodedata.normalize('NFC',value)
    if not value and folder:return ''
    if not value or '\\' in value or value.startswith('/') or any(ord(c)<32 or c in ':*?<>|"' for c in value):
        raise ValueError('Invalid logical file name')
    parts=value.split('/')
    if (not folder and len(parts)!=1) or len(parts)>6:raise ValueError('Folder depth or filename limit')
    for p in parts:
        if p in {'','.','..'} or p.endswith((' ','.')) or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',p):
            raise ValueError('Reserved or ambiguous logical name')
    return value


class FolderMove(Strict):
    artifactId: str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    expectedRevision: int=Field(ge=1)
    name: str=Field(min_length=1,max_length=180)
    folder: str=Field(default='',max_length=240)
    @field_validator('name')
    @classmethod
    def name_ok(cls,v):return logical_name(v)
    @field_validator('folder')
    @classmethod
    def folder_ok(cls,v):return logical_name(v,True)


class FolderRequest(Strict):
    action: str=Field(default='preview',pattern=r'^(preview|apply|undo)$')
    moves: list[FolderMove]=Field(default_factory=list,max_length=30)
    expectedPlanSha256: str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    operationId: str|None=Field(default=None,pattern=r'^[A-Za-z0-9_-]{1,80}$')
    @model_validator(mode='after')
    def required(self):
        if self.action=='undo':
            if not self.operationId or self.moves:raise ValueError('Undo requires only an organization operation')
        elif not self.moves:raise ValueError('Choose files to organize')
        if len({x.artifactId for x in self.moves})!=len(self.moves):raise ValueError('Duplicate file in organization')
        if self.action=='apply' and not self.expectedPlanSha256:raise ValueError('Apply requires the reviewed plan hash')
        return self


def init_files(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS skill_file_heads(
      id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      owner_id TEXT NOT NULL,name TEXT NOT NULL,folder TEXT NOT NULL DEFAULT '',
      revision INTEGER NOT NULL DEFAULT 1,latest_version INTEGER NOT NULL,deleted INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS skill_file_versions(
      artifact_id TEXT NOT NULL REFERENCES skill_file_heads(id) ON DELETE CASCADE,version INTEGER NOT NULL,
      run_id TEXT NOT NULL,name TEXT NOT NULL,sha256 TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,
      PRIMARY KEY(artifact_id,version),UNIQUE(run_id,name),
      FOREIGN KEY(run_id,name) REFERENCES skill_artifacts(run_id,name) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS skill_folder_operations(
      id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      owner_id TEXT NOT NULL,run_id TEXT NOT NULL,plan_sha256 TEXT NOT NULL,
      changes TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(run_id));
    CREATE TRIGGER IF NOT EXISTS skill_file_current_deleted AFTER DELETE ON skill_file_versions
    BEGIN
      UPDATE skill_file_heads SET deleted=1,revision=revision+1
      WHERE id=OLD.artifact_id AND latest_version=OLD.version;
    END;
    ''')
    # Add identities to historical immutable outputs without copying their bytes.
    rows=c.execute('''SELECT a.*,r.project_id,r.owner_id,r.created_at FROM skill_artifacts a
        JOIN skill_runs r ON r.id=a.run_id LEFT JOIN skill_file_versions v ON v.run_id=a.run_id AND v.name=a.name
        WHERE v.run_id IS NULL AND r.state IN ('completed','partial')''').fetchall()
    for row in rows:
        aid=hashlib.sha256(('legacy:'+row['run_id']+':'+row['name']).encode()).hexdigest()[:32]
        c.execute('INSERT OR IGNORE INTO skill_file_heads VALUES(?,?,?,?,?,1,1,0,?)',
                  (aid,row['project_id'],row['owner_id'],row['name'],'',row['created_at']))
        c.execute('INSERT OR IGNORE INTO skill_file_versions VALUES(?,1,?,?,?,?,?)',
                  (aid,row['run_id'],row['name'],row['sha256'],'Existing immutable output',row['created_at']))


_READ_STACK=ContextVar('skill_file_read_stack',default=())


def head(c,who,pid,aid):
    guard.check_actor(c,who,pid)
    row=c.execute('SELECT * FROM skill_file_heads WHERE id=? AND project_id=? AND owner_id=? AND deleted=0',(aid,pid,who.owner_id)).fetchone()
    if not row:raise HTTPException(404,'File is unavailable')
    return row


def version(c,who,pid,aid,number=None):
    from backend import skills
    h=head(c,who,pid,aid);number=h['latest_version'] if number is None else number
    row=c.execute('''SELECT v.*,a.content,a.mime FROM skill_file_versions v JOIN skill_artifacts a
        ON a.run_id=v.run_id AND a.name=v.name WHERE v.artifact_id=? AND v.version=?''',(aid,number)).fetchone()
    if not row:raise HTTPException(404,'File version is unavailable')
    stack=_READ_STACK.get();key=(aid,number)
    if key in stack or len(stack)>=32:raise HTTPException(409,'File lineage limit')
    token=_READ_STACK.set(stack+(key,))
    try:
        origin=skills.checked_row(c,pid,row['run_id'],who)
        if origin['state'] not in {'completed','partial'}:raise HTTPException(404,'File is unavailable')
        skills.recheck(c,origin,who)
        if hashlib.sha256(row['content']).hexdigest()!=row['sha256']:raise HTTPException(409,'File integrity check failed')
    finally:_READ_STACK.reset(token)
    return h,row


def descriptor(h,v):
    pid=h['project_id'];aid=h['id'];n=v['version']
    return {'id':aid,'kind':'artifact','version':n,'sha256':v['sha256'],'name':h['name'],'folder':h['folder'],
            'revision':h['revision'],'latest_version':h['latest_version'],'mime':v['mime'],'bytes':len(v['content']),
            'url':f'/api/projects/{pid}/skill-files/{aid}/versions/{n}/download',
            'latest_url':f'/api/projects/{pid}/skill-files/{aid}/download'}


def list_files(c,who,pid):
    guard.check_actor(c,who,pid);items=[]
    for h in c.execute('SELECT id FROM skill_file_heads WHERE project_id=? AND owner_id=? AND deleted=0 ORDER BY created_at DESC LIMIT 200',(pid,who.owner_id)).fetchall():
        try:items.append(descriptor(*version(c,who,pid,h['id'])))
        except HTTPException:continue
    return items


def resolve(c,who,pid,ref,*,active=False,writing_run_id=None):
    ref=ref if isinstance(ref,SourceRef) else SourceRef.model_validate(ref)
    if ref.kind=='artifact':
        h,v=version(c,who,pid,ref.id,ref.version)
        if active and not ref.pinned and h['latest_version']!=ref.version:
            own_update=c.execute('SELECT 1 FROM skill_file_versions WHERE artifact_id=? AND version=? AND run_id=?',
                                 (ref.id,h['latest_version'],writing_run_id)).fetchone() if writing_run_id else None
            if not own_update:raise HTTPException(409,'Source changed during the task')
        if v['sha256']!=ref.sha256:raise HTTPException(409,'Source hash differs')
        return {'file_id':ref.id,'kind':'artifact','version':ref.version,'sha256':ref.sha256,'name':h['name'],'content':v['content'],'mime':v['mime']}
    guard.check_actor(c,who,pid)
    p=c.execute('SELECT * FROM guard_file_policies WHERE file_id=? AND project_id=? AND owner_id=? AND revoked=0',(ref.id,pid,who.owner_id)).fetchone()
    row=c.execute('''SELECT f.filename,f.sha256,o.original,o.mime,o.version FROM knowledge_files f
        JOIN document_originals o ON o.file_id=f.id WHERE f.id=? AND f.project_id=?''',(ref.id,pid)).fetchone()
    if not p or not row or 'local' not in json.loads(p['destinations']):raise HTTPException(404,'Source is unavailable')
    if row['version']!=ref.version or row['sha256']!=ref.sha256 or hashlib.sha256(row['original']).hexdigest()!=ref.sha256:raise HTTPException(409,'Source version changed')
    return {'file_id':ref.id,'kind':'document','version':ref.version,'sha256':ref.sha256,'name':row['filename'],'content':row['original'],'mime':row['mime']}


def register(c,who,pid,rid,item,update=None):
    guard.check_actor(c,who,pid)
    old=c.execute('SELECT artifact_id,version FROM skill_file_versions WHERE run_id=? AND name=?',(rid,item['name'])).fetchone()
    if old:return {'artifact_id':old['artifact_id'],'version':old['version']}
    if update:
        update=update if isinstance(update,OutputVersion) else OutputVersion.model_validate(update)
        h=head(c,who,pid,update.artifactId)
        if h['latest_version']!=update.expectedVersion:raise HTTPException(409,'A newer file version exists; no replacement was made')
        _,before=version(c,who,pid,h['id'],h['latest_version'])
        if before['mime']!=item['mime']:raise HTTPException(422,'A different format requires a separate file')
        count=c.execute('SELECT COUNT(*) FROM skill_file_versions WHERE artifact_id=?',(h['id'],)).fetchone()[0]
        if count>=20:raise HTTPException(409,'Twenty retained versions reached; no history was silently removed')
        aid=h['id'];n=h['latest_version']+1;reason=update.reason
        if c.execute('UPDATE skill_file_heads SET latest_version=?,revision=revision+1 WHERE id=? AND latest_version=?',(n,aid,update.expectedVersion)).rowcount!=1:raise HTTPException(409,'File changed')
    else:
        if c.execute('SELECT COUNT(*) FROM skill_file_heads WHERE project_id=? AND owner_id=? AND deleted=0',(pid,who.owner_id)).fetchone()[0]>=200:raise HTTPException(413,'Project output file limit reached')
        aid=uid();n=1;reason='Created by authorized skill task'
        c.execute('INSERT INTO skill_file_heads VALUES(?,?,?,?,?,1,1,0,?)',(aid,pid,who.owner_id,item['name'],'',now()))
    c.execute('INSERT INTO skill_file_versions VALUES(?,?,?,?,?,?,?)',(aid,n,rid,item['name'],item['sha256'],reason,now()))
    return {'artifact_id':aid,'version':n,'url':f'/api/projects/{pid}/skill-files/{aid}/versions/{n}/download'}


def history(c,who,pid,aid):
    h=head(c,who,pid,aid);out=[]
    for row in c.execute('SELECT version,reason,created_at FROM skill_file_versions WHERE artifact_id=? ORDER BY version DESC',(aid,)).fetchall():
        try:
            _,v=version(c,who,pid,aid,row['version']);out.append(descriptor(h,v)|{'reason':row['reason'],'created_at':row['created_at']})
        except HTTPException:continue
    return {'artifact_id':aid,'current_version':h['latest_version'],'versions':out,'retention_limit':20,'historical_permissions':'current ACL applies'}


def organize(c,who,pid,request,run_id):
    request=request if isinstance(request,FolderRequest) else FolderRequest.model_validate(request)
    guard.check_actor(c,who,pid)
    prior=c.execute('SELECT * FROM skill_folder_operations WHERE run_id=? AND owner_id=? AND project_id=?',(run_id,who.owner_id,pid)).fetchone()
    if prior:return {'operation_id':prior['id'],'status':'completed','changes':json.loads(prior['changes']),'replayed':True}
    if request.action=='undo':
        old=c.execute("SELECT * FROM skill_folder_operations WHERE id=? AND project_id=? AND owner_id=? AND state='applied'",(request.operationId,pid,who.owner_id)).fetchone()
        if not old:raise HTTPException(409,'Organization operation unavailable or already undone')
        moves=[FolderMove(artifactId=x['id'],expectedRevision=x['after']['revision'],name=x['before']['name'],folder=x['before']['folder']) for x in json.loads(old['changes'])]
    else:moves=request.moves
    changes=[];destinations=set();moving={m.artifactId for m in moves}
    for m in moves:
        h,v=version(c,who,pid,m.artifactId)
        if h['revision']!=m.expectedRevision:raise HTTPException(409,'A file changed after organization preview')
        if PurePosixPath(m.name).suffix.casefold()!=PurePosixPath(h['name']).suffix.casefold():raise HTTPException(422,'Renaming cannot convert the file format')
        key=(m.folder.casefold(),m.name.casefold())
        if key in destinations:raise HTTPException(409,'Two files have the same destination')
        destinations.add(key)
        for other in c.execute('SELECT id,name,folder FROM skill_file_heads WHERE project_id=? AND owner_id=? AND deleted=0',(pid,who.owner_id)):
            if other['id'] not in moving and (other['folder'].casefold(),other['name'].casefold())==key:raise HTTPException(409,'Destination name exists; no file was replaced')
        changes.append({'id':h['id'],'version':h['latest_version'],'before':{'name':h['name'],'folder':h['folder'],'revision':h['revision']},'after':{'name':m.name,'folder':m.folder,'revision':h['revision']+1}})
    digest=guard.digest({'owner':who.owner_id,'project':pid,'changes':changes})
    if request.action=='preview':return {'status':'preview','plan_sha256':digest,'changes':changes,'scope':'logical project folders only; no host moves or sharing changes'}
    if request.action=='apply' and digest!=request.expectedPlanSha256:raise HTTPException(409,'Organization preview no longer matches')
    for x in changes:
        if c.execute('UPDATE skill_file_heads SET name=?,folder=?,revision=revision+1 WHERE id=? AND revision=?',(x['after']['name'],x['after']['folder'],x['id'],x['before']['revision'])).rowcount!=1:raise HTTPException(409,'Concurrent organization conflict')
    oid=uid();c.execute('INSERT INTO skill_folder_operations VALUES(?,?,?,?,?,?,?,?)',(oid,pid,who.owner_id,run_id,digest,json.dumps(changes,ensure_ascii=False), 'applied',now()))
    if request.action=='undo':c.execute("UPDATE skill_folder_operations SET state='undone' WHERE id=?",(request.operationId,))
    return {'operation_id':oid,'status':'completed','plan_sha256':digest,'changes':changes,'sharing_changed':False,'physical_files_moved':False,'undo_supported':True}
