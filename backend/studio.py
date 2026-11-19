"""Explicit local project records. No model, network, filesystem or tool calls.
Provenance is saved answer context, not independent claim verification.
Implementation evidence is a user's attestation, not a tool result.
"""
from __future__ import annotations
import hashlib,json,sqlite3,unicodedata
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator
from backend.db import event,get_db,now,uid
from backend.knowledge import require_project
router=APIRouter()
MAX_ENTRIES=1000
MAX_VERSIONS=500
GLOSSARY_FIELDS=('term','term_ar','definition','language','example','source_label')
DECISION_FIELDS=('title','decision','rationale','alternatives')
class Input(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 @field_validator('*',mode='after')
 @classmethod
 def plain_text(cls,value,info):
  if isinstance(value,str):
   if any(ord(ch)<32 and ch not in '\t\r\n' for ch in value):raise ValueError('Control characters are not accepted')
   if info.field_name in {'term','title','definition','decision','selection','source_excerpt'} and not value.strip():raise ValueError('Text must not be blank')
  return value
class SourceInput(Input):
 source_message_id:str|None=Field(default=None,min_length=1,max_length=80)
 source_excerpt:str|None=Field(default=None,min_length=1,max_length=20000)
class GlossaryCreate(SourceInput):
 term:str=Field(min_length=1,max_length=200)
 term_ar:str=Field(default='',max_length=200)
 definition:str=Field(min_length=1,max_length=12000)
 language:Literal['ar','en','mixed']='mixed'
 example:str=Field(default='',max_length=4000)
 source_label:str=Field(default='',max_length=500)
class GlossaryPatch(GlossaryCreate):
 expected_version:int=Field(ge=1,le=MAX_VERSIONS)
class DecisionCreate(SourceInput):
 title:str=Field(min_length=1,max_length=200)
 decision:str=Field(min_length=1,max_length=12000)
 rationale:str=Field(default='',max_length=12000)
 alternatives:str=Field(default='',max_length=12000)
 status:Literal['proposal']='proposal'
class DecisionPatch(DecisionCreate):
 expected_version:int=Field(ge=1,le=MAX_VERSIONS)
 status:Literal['proposal','adopted','implemented']
 confirm_adopted:StrictBool=False
 confirm_implemented:StrictBool=False
 implementation_evidence:str=Field(default='',max_length=4000)
class RestoreInput(Input):
 expected_version:int=Field(ge=1,le=MAX_VERSIONS)
 from_version:int=Field(ge=1,le=MAX_VERSIONS)
class AnswerArtifactInput(Input):
 title:str=Field(min_length=1,max_length=200)
 selection:str|None=Field(default=None,min_length=1,max_length=190000)
 template:Literal['note','brief','checklist']='note'
 language:Literal['ar','en']='ar'
def init_studio(conn:sqlite3.Connection):
 conn.executescript('''
 CREATE TABLE IF NOT EXISTS studio_entries(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,kind TEXT NOT NULL CHECK(kind IN ('glossary','decision')),payload TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,term_key TEXT,UNIQUE(project_id,kind,term_key));
 CREATE INDEX IF NOT EXISTS studio_project ON studio_entries(project_id,kind);
 CREATE TABLE IF NOT EXISTS studio_versions(entry_id TEXT NOT NULL REFERENCES studio_entries(id) ON DELETE CASCADE,version INTEGER NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(entry_id,version));
 CREATE TABLE IF NOT EXISTS studio_artifact_sources(artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id) ON DELETE CASCADE,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,provenance TEXT NOT NULL,request_key TEXT NOT NULL,template TEXT NOT NULL,initial_content_sha256 TEXT NOT NULL,UNIQUE(project_id,request_key));
 CREATE TABLE IF NOT EXISTS studio_entry_dependencies(entry_id TEXT NOT NULL,version INTEGER NOT NULL,project_id TEXT NOT NULL,conversation_id TEXT,links TEXT NOT NULL,
   PRIMARY KEY(entry_id,version),FOREIGN KEY(entry_id,version) REFERENCES studio_versions(entry_id,version) ON DELETE CASCADE);
 CREATE TABLE IF NOT EXISTS studio_artifact_dependencies(artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id) ON DELETE CASCADE,project_id TEXT NOT NULL,conversation_id TEXT,links TEXT NOT NULL);
 ''')
 # Existing receipts can be migrated only while the matching local parent is
 # still available. Missing/deleted parents never acquire invented provenance.
 if conn.execute("SELECT 1 FROM sqlite_master WHERE name='message_context_links'").fetchone():
  for row in conn.execute('''SELECT e.id,e.project_id,v.version,v.payload FROM studio_entries e JOIN studio_versions v ON v.entry_id=e.id
    LEFT JOIN studio_entry_dependencies d ON d.entry_id=v.entry_id AND d.version=v.version WHERE d.entry_id IS NULL''').fetchall():
   try:value=json.loads(row['payload'])
   except (ValueError,TypeError):continue
   if isinstance(value,dict):record_dependency(conn,'entry',row['id'],row['version'],row['project_id'],value.get('provenance'))
  for row in conn.execute('''SELECT s.* FROM studio_artifact_sources s LEFT JOIN studio_artifact_dependencies d ON d.artifact_id=s.artifact_id WHERE d.artifact_id IS NULL''').fetchall():
   try:value=json.loads(row['provenance'])
   except (ValueError,TypeError):continue
   record_dependency(conn,'artifact',row['artifact_id'],None,row['project_id'],value)
def packed(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))
def digest(value:str):return hashlib.sha256(value.encode('utf-8')).hexdigest()
def entry_dict(row):return json.loads(row['payload'])|{key:row[key] for key in ('id','project_id','kind','version','created_at','updated_at')}

def record_dependency(conn,kind,identifier,version,project_id,provenance):
 """Copy only a trusted server receipt; portable provenance is inert."""
 from backend import context_lineage
 if not isinstance(provenance,dict) or provenance.get('origin')!='local_message':return
 # A later edit or restore may happen after the parent conversation was
 # deleted. Preserve an existing receipt only for the exact same provenance
 # of this same entry; client/imported metadata cannot create that receipt.
 if kind=='entry':
  for old in conn.execute('''SELECT d.*,v.payload FROM studio_entry_dependencies d
    JOIN studio_versions v ON v.entry_id=d.entry_id AND v.version=d.version
    WHERE d.entry_id=? AND d.project_id=? AND d.version<? ORDER BY d.version DESC''',(identifier,project_id,version)):
   try:payload=json.loads(old['payload'])
   except (ValueError,TypeError):continue
   if not isinstance(payload,dict) or payload.get('provenance')!=provenance:continue
   if not dependency_allowed(conn,project_id,old):raise HTTPException(404,'Project record not found')
   conn.execute('INSERT OR IGNORE INTO studio_entry_dependencies VALUES(?,?,?,?,?)',(identifier,version,project_id,old['conversation_id'],old['links']))
   return
 parent=conn.execute('''SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id
   WHERE m.id=? AND c.project_id=?''',(provenance.get('message_id'),project_id)).fetchone()
 if parent is None or parent['conversation_id']!=provenance.get('conversation_id') or digest(parent['content'])!=provenance.get('answer_sha256'):return
 links=context_lineage.read(conn,parent['id'])
 if links is None:return
 if not isinstance(links,list) or len(links)>context_lineage.MAX_LINKS:raise HTTPException(409,'Stored source dependency receipt is invalid')
 if kind=='entry':
  conn.execute('INSERT OR IGNORE INTO studio_entry_dependencies VALUES(?,?,?,?,?)',(identifier,version,project_id,parent['conversation_id'],packed(links)))
 elif kind=='artifact':
  conn.execute('INSERT OR IGNORE INTO studio_artifact_dependencies VALUES(?,?,?,?)',(identifier,project_id,parent['conversation_id'],packed(links)))
 else:raise ValueError('Unsupported trusted dependency destination')

def dependency_allowed(conn,project_id,row):
 from backend import context_lineage
 if row is None:return True
 try:links=json.loads(row['links'])
 except (ValueError,TypeError):raise HTTPException(409,'Stored source dependency receipt is invalid') from None
 if row['project_id']!=project_id or not isinstance(links,list) or len(links)>context_lineage.MAX_LINKS:raise HTTPException(409,'Stored source dependency receipt is invalid')
 # BLOCKED is imported/unbound history, not a claimed live dependency. Reading
 # that history stays separate from permission to feed it into a model.
 bound=[link for link in links if link!=context_lineage.BLOCKED]
 return not bound or context_lineage.authorized(conn,project_id,bound,conversation_id=row['conversation_id'])

def provenance_allowed(conn,project_id,provenance,*,dependency=None):
 from backend import context_lineage
 if provenance is None:return True
 if not isinstance(provenance,dict):raise HTTPException(409,'Stored provenance is invalid')
 if not isinstance(provenance.get('citations',[]),list):raise HTTPException(409,'Stored provenance is invalid')
 message=None
 if provenance.get('origin')=='local_message' and provenance.get('message_id'):
  message=conn.execute('SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.id=? AND c.project_id=?',(provenance['message_id'],project_id)).fetchone()
 if provenance.get('origin')=='local_message' and message is None and dependency is None:
  # A pre-upgrade orphan has no surviving authority receipt. Its copied text
  # must not become permanently readable merely because its parent was deleted.
  # Imported historical excerpts remain a separate, explicitly unbound case.
  return False
 if message is not None and not context_lineage.disclosable(conn,project_id,dict(message)):return False
 # Retain source policy even after the original message has been deleted or
 # an explicitly imported excerpt no longer has a local message identifier.
 cached={'role':'assistant','id':None,'citations':provenance.get('citations',[])}
 return context_lineage.disclosable(conn,project_id,cached)

def entry_allowed(conn,row):
 dependencies={d['version']:d for d in conn.execute('SELECT * FROM studio_entry_dependencies WHERE entry_id=?',(row['id'],))}
 if not all(dependency_allowed(conn,row['project_id'],d) for d in dependencies.values()):return False
 def allowed(raw,version):
  try:value=json.loads(raw)
  except (ValueError,TypeError):raise HTTPException(409,'Stored record is invalid') from None
  if not isinstance(value,dict):raise HTTPException(409,'Stored record is invalid')
  return provenance_allowed(conn,row['project_id'],value.get('provenance'),dependency=dependencies.get(version))
 if not allowed(row['payload'],row['version']):return False
 # Detaching the displayed provenance cannot erase the lineage of older copies.
 return all(allowed(v['payload'],v['version']) for v in conn.execute('SELECT version,payload FROM studio_versions WHERE entry_id=?',(row['id'],)))

def artifact_allowed(conn,project_id,artifact_id):
 dependency=conn.execute('SELECT * FROM studio_artifact_dependencies WHERE artifact_id=?',(artifact_id,)).fetchone()
 if not dependency_allowed(conn,project_id,dependency):return False
 row=conn.execute('SELECT provenance FROM studio_artifact_sources WHERE artifact_id=? AND project_id=?',(artifact_id,project_id)).fetchone()
 if row is None:return True
 try:provenance=json.loads(row['provenance'])
 except (ValueError,TypeError):raise HTTPException(409,'Stored document provenance is invalid') from None
 return provenance_allowed(conn,project_id,provenance,dependency=dependency)
def require_entry(conn,project_id,entry_id,kind):
 require_project(conn,project_id)
 row=conn.execute('SELECT * FROM studio_entries WHERE id=? AND project_id=? AND kind=?',(entry_id,project_id,kind)).fetchone()
 if row is None or not entry_allowed(conn,row):raise HTTPException(404,'Project record not found')
 return row
def check_version(row,expected):
 if row['version']!=expected:raise HTTPException(409,'Record changed; reload before saving')
def capture_answer(conn,project_id,message_id,excerpt=None,*,whole=False):
 from backend import context_lineage
 require_project(conn,project_id)
 from backend.snapshots import PublicCitationData,citation_from_chunk,remap_citation_markers
 message=conn.execute('SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.id=? AND c.project_id=?',(message_id,project_id)).fetchone()
 if message is None:raise HTTPException(404,'Answer not found in this project')
 if not context_lineage.disclosable(conn,project_id,dict(message)):raise HTTPException(404,'Answer unavailable')
 if message['role']!='assistant' or message['status']!='complete':raise HTTPException(409,'Choose a completed assistant answer')
 original=message['content']
 if not original.strip():raise HTTPException(409,'The answer is empty')
 if excerpt is not None and (not excerpt.strip() or excerpt not in original):raise HTTPException(422,'The selected text must occur exactly in the saved answer')
 text=excerpt if excerpt is not None else (original if whole else original[:4000])
 if len(text)>190000:raise HTTPException(413,'Selected answer exceeds the document limit')
 try:saved=json.loads(message['citations'])
 except (ValueError,TypeError):raise HTTPException(409,'The saved answer has invalid source metadata')
 if not isinstance(saved,list) or len(saved)>10:raise HTTPException(409,'The saved answer has invalid source metadata')
 citations,positions=[],{}
 for index,reference in enumerate(saved,1):
  if not isinstance(reference,dict):continue
  if reference.get('scope')=='public':
   try:citation=PublicCitationData.model_validate(reference).model_dump()
   except ValidationError:continue
  elif reference.get('scope','private')=='private':
   if not all(isinstance(reference.get(key),str) for key in ('id','file_id')):continue
   chunk=conn.execute('SELECT k.*,f.filename FROM knowledge_chunks k JOIN knowledge_files f ON f.id=k.file_id WHERE k.id=? AND k.file_id=? AND k.project_id=? AND f.project_id=?',(reference.get('id'),reference.get('file_id'),project_id,project_id)).fetchone()
   if chunk is None:continue
   citation=citation_from_chunk(chunk,chunk['file_id'],chunk['filename'],reference.get('score'))
  else:continue
  citations.append(citation);positions[index]=len(citations)
 text=remap_citation_markers(text,positions)
 provenance={'origin':'local_message','message_id':message['id'],'conversation_id':message['conversation_id'],'model':message['model'],'message_created_at':message['created_at'],'answer_sha256':digest(original),'excerpt':text[:20000],'citations':citations,'captured_at':now()}
 return provenance,text
def source_for(conn,project_id,body,previous=None):
 if 'source_message_id' not in body.model_fields_set:
  if 'source_excerpt' in body.model_fields_set:raise HTTPException(422,'Choose a source message with the excerpt')
  return previous
 if body.source_message_id is None:
  if body.source_excerpt is not None:raise HTTPException(422,'An excerpt requires a source message')
  return None
 return capture_answer(conn,project_id,body.source_message_id,body.source_excerpt)[0]
def save_entry(conn,project_id,kind,data,row=None,action='saved'):
 entry_id,stamp=(row['id'] if row else uid()),now();version=row['version']+1 if row else 1
 if version>MAX_VERSIONS:raise HTTPException(413,'Record history limit reached')
 if row is None and conn.execute('SELECT COUNT(*) FROM studio_entries WHERE project_id=?',(project_id,)).fetchone()[0]>=MAX_ENTRIES:raise HTTPException(413,'Project notebook limit reached')
 term_key=' '.join(unicodedata.normalize('NFKC',data['term']).casefold().split()) if kind=='glossary' else None
 try:
  if row is None:conn.execute('INSERT INTO studio_entries VALUES(?,?,?,?,?,?,?,?)',(entry_id,project_id,kind,packed(data),version,stamp,stamp,term_key))
  else:
   changed=conn.execute('UPDATE studio_entries SET payload=?,version=?,updated_at=?,term_key=? WHERE id=? AND project_id=? AND version=?',(packed(data),version,stamp,term_key,entry_id,project_id,row['version']))
   if changed.rowcount!=1:raise HTTPException(409,'Record changed; reload before saving')
 except sqlite3.IntegrityError as exc:
  if kind=='glossary':raise HTTPException(409,'That term already exists in this project') from exc
  raise
 conn.execute('INSERT INTO studio_versions VALUES(?,?,?,?)',(entry_id,version,packed(data),stamp))
 record_dependency(conn,'entry',entry_id,version,project_id,data.get('provenance'))
 label=data['term'] if kind=='glossary' else data['title'];event_kind='glossary_saved' if kind=='glossary' else 'decision_updated'
 if kind=='decision' and action=='adopted':event_kind='studio_decision_adopted'
 elif kind=='decision' and action=='implemented':event_kind='studio_decision_implemented';action='implementation attested by user (not independently verified)'
 event(conn,project_id,event_kind,f'{label}: {action} (record {entry_id}, v{version})')
 return entry_dict(require_entry(conn,project_id,entry_id,kind))
@router.post('/api/projects/{project_id}/glossary',status_code=201)
def create_glossary(project_id:str,body:GlossaryCreate):
 with get_db() as conn:
  conn.execute('BEGIN IMMEDIATE');require_project(conn,project_id)
  data={key:getattr(body,key) for key in GLOSSARY_FIELDS};data['provenance']=source_for(conn,project_id,body)
  return save_entry(conn,project_id,'glossary',data,action='created by user')
@router.patch('/api/projects/{project_id}/glossary/{entry_id}')
def patch_glossary(project_id:str,entry_id:str,body:GlossaryPatch):
 with get_db() as conn:
  conn.execute('BEGIN IMMEDIATE');row=require_entry(conn,project_id,entry_id,'glossary');check_version(row,body.expected_version);previous=json.loads(row['payload'])
  data={key:getattr(body,key) for key in GLOSSARY_FIELDS};data['provenance']=source_for(conn,project_id,body,previous.get('provenance'))
  return save_entry(conn,project_id,'glossary',data,row)
@router.post('/api/projects/{project_id}/decisions',status_code=201)
def create_decision(project_id:str,body:DecisionCreate):
 with get_db() as conn:
  conn.execute('BEGIN IMMEDIATE');require_project(conn,project_id)
  data={key:getattr(body,key) for key in DECISION_FIELDS};data.update(status='proposal',implementation_evidence='',implementation_attested_at=None,provenance=source_for(conn,project_id,body))
  return save_entry(conn,project_id,'decision',data,action='proposed by user')
@router.patch('/api/projects/{project_id}/decisions/{entry_id}')
def patch_decision(project_id:str,entry_id:str,body:DecisionPatch):
 with get_db() as conn:
  conn.execute('BEGIN IMMEDIATE');row=require_entry(conn,project_id,entry_id,'decision');check_version(row,body.expected_version);previous=json.loads(row['payload'])
  data={key:getattr(body,key) for key in DECISION_FIELDS};data['provenance']=source_for(conn,project_id,body,previous.get('provenance'));changed=any(data.get(key)!=previous.get(key) for key in (*DECISION_FIELDS,'provenance'))
  if body.status=='adopted' and (previous['status']!='adopted' or changed) and not body.confirm_adopted:raise HTTPException(422,'Explicit review confirmation is required to adopt this decision')
  if body.status=='implemented' and changed:raise HTTPException(409,'Save and adopt changed decision text before recording implementation')
  evidence,attested='',None
  if body.status=='implemented':
   if previous['status'] not in {'adopted','implemented'}:raise HTTPException(409,'Adopt the decision before recording implementation')
   if not body.confirm_implemented or not body.implementation_evidence.strip():raise HTTPException(422,'Implementation requires explicit confirmation and user-provided evidence')
   evidence,attested=body.implementation_evidence.strip(),now()
  data.update(status=body.status,implementation_evidence=evidence,implementation_attested_at=attested)
  return save_entry(conn,project_id,'decision',data,row,action=body.status)
def register_record_routes(collection,kind):
 base=f'/api/projects/{{project_id}}/{collection}'
 def listing(project_id:str):
  with get_db() as conn:
   require_project(conn,project_id)
   return [entry_dict(row) for row in conn.execute('SELECT * FROM studio_entries WHERE project_id=? AND kind=? ORDER BY updated_at DESC,id',(project_id,kind)) if entry_allowed(conn,row)]
 def detail(project_id:str,entry_id:str):
  with get_db() as conn:return entry_dict(require_entry(conn,project_id,entry_id,kind))
 def history(project_id:str,entry_id:str):
  with get_db() as conn:
   require_entry(conn,project_id,entry_id,kind)
   return [{'entry_id':row['entry_id'],'version':row['version'],'data':json.loads(row['payload']),'created_at':row['created_at']} for row in conn.execute('SELECT * FROM studio_versions WHERE entry_id=? ORDER BY version DESC',(entry_id,))]
 def restore(project_id:str,entry_id:str,body:RestoreInput):
  with get_db() as conn:
   conn.execute('BEGIN IMMEDIATE');row=require_entry(conn,project_id,entry_id,kind);check_version(row,body.expected_version)
   saved=conn.execute('SELECT payload FROM studio_versions WHERE entry_id=? AND version=?',(entry_id,body.from_version)).fetchone()
   if saved is None:raise HTTPException(404,'Record version not found')
   data=json.loads(saved['payload'])
   if kind=='decision':data.update(status='proposal',implementation_evidence='',implementation_attested_at=None)
   return save_entry(conn,project_id,kind,data,row,action=f'restored v{body.from_version}')
 def delete(project_id:str,entry_id:str,expected_version:int=Query(ge=1,le=MAX_VERSIONS)):
  with get_db() as conn:
   conn.execute('BEGIN IMMEDIATE');require_project(conn,project_id)
   # Forgetting an owned record must not disclose or require its withdrawn text.
   row=conn.execute('SELECT version FROM studio_entries WHERE id=? AND project_id=? AND kind=?',(entry_id,project_id,kind)).fetchone()
   if row is None:raise HTTPException(404,'Project record not found')
   check_version(row,expected_version)
   conn.execute('DELETE FROM studio_entries WHERE id=? AND project_id=?',(entry_id,project_id));event(conn,project_id,'studio_record_deleted',f'User deleted {kind} record {entry_id} and its history')
  return {'deleted':True}
 for suffix,method,endpoint,name in [('', 'GET',listing,'list'),('/{entry_id}','GET',detail,'get'),('/{entry_id}/versions','GET',history,'versions'),('/{entry_id}/restore','POST',restore,'restore'),('/{entry_id}','DELETE',delete,'delete')]:
  operation=f'studio_{collection}_{name}';router.add_api_route(base+suffix,endpoint,methods=[method],name=operation,operation_id=operation)
register_record_routes('glossary','glossary')
register_record_routes('decisions','decision')

def artifact_text(body,source_text,citations):
 if body.template=='note':text=source_text
 else:
  source_heading='الإجابة الأصلية' if body.language=='ar' else 'Source answer'
  edit_heading=('موجز يحرره المستخدم' if body.language=='ar' else 'User-written brief') if body.template=='brief' else ('مهام يحددها المستخدم' if body.language=='ar' else 'User-defined tasks')
  text=f'# {body.title.strip()}\n\n## {source_heading}\n\n{source_text}\n\n## {edit_heading}\n\n'
 if citations:
  heading='سياق المصادر المحفوظ — لا يثبت صحة الادعاءات' if body.language=='ar' else 'Saved source context — not verification of claims'
  lines=[]
  for index,citation in enumerate(citations,1):
   name=citation.get('title') or citation.get('filename') or 'Source'
   location=f"lines {citation['line_start']}–{citation['line_end']}"
   if citation.get('page') is not None:location+=f", page {citation['page']}"
   url=citation.get('url','');lines.append(f'- [S{index}] {name} ({location})'+(f' — {url}' if url else ''))
  text+=f'\n\n## {heading}\n\n'+'\n'.join(lines)
 if len(text)>200000:raise HTTPException(413,'Document with source notes exceeds 200000 characters')
 return text
@router.post('/api/projects/{project_id}/answers/{message_id}/artifact',status_code=201)
def answer_artifact(project_id:str,message_id:str,body:AnswerArtifactInput):
 with get_db() as conn:
  conn.execute('BEGIN IMMEDIATE');require_project(conn,project_id)
  provenance,selected=capture_answer(conn,project_id,message_id,body.selection,whole=True)
  request_key=digest(packed({'message_id':message_id,'answer_sha256':provenance['answer_sha256'],'selected_sha256':digest(selected),**body.model_dump()}))
  existing=conn.execute('SELECT a.*,s.provenance AS saved_provenance FROM studio_artifact_sources s JOIN artifacts a ON a.id=s.artifact_id WHERE s.project_id=? AND a.project_id=? AND s.request_key=?',(project_id,project_id,request_key)).fetchone()
  if existing is not None:
   artifact=dict(existing);saved=json.loads(artifact.pop('saved_provenance'));return {'artifact':artifact,'provenance':saved,'reused':True}
  if conn.execute('SELECT COUNT(*) FROM artifacts WHERE project_id=?',(project_id,)).fetchone()[0]>=100:raise HTTPException(413,'Project document limit is 100')
  content=artifact_text(body,selected,provenance['citations']);artifact_id,stamp=uid(),now()
  conn.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(artifact_id,project_id,body.title.strip(),content,1,stamp,stamp))
  conn.execute('INSERT INTO artifact_versions VALUES(?,?,?,?)',(artifact_id,1,content,stamp))
  conn.execute('INSERT INTO studio_artifact_sources VALUES(?,?,?,?,?,?)',(artifact_id,project_id,packed(provenance),request_key,body.template,digest(content)))
  record_dependency(conn,'artifact',artifact_id,None,project_id,provenance)
  event(conn,project_id,'artifact_created',f'User converted a saved answer to {body.template}: {body.title.strip()}')
  artifact=dict(conn.execute('SELECT * FROM artifacts WHERE id=? AND project_id=?',(artifact_id,project_id)).fetchone())
  return {'artifact':artifact,'provenance':provenance,'reused':False}
@router.get('/api/projects/{project_id}/artifacts/{artifact_id}/provenance')
def artifact_provenance(project_id:str,artifact_id:str):
 with get_db() as conn:
  require_project(conn,project_id)
  artifact=conn.execute('SELECT * FROM artifacts WHERE id=? AND project_id=?',(artifact_id,project_id)).fetchone()
  if artifact is None or not artifact_allowed(conn,project_id,artifact_id):raise HTTPException(404,'Document not found in this project')
  row=conn.execute('SELECT * FROM studio_artifact_sources WHERE artifact_id=? AND project_id=?',(artifact_id,project_id)).fetchone()
  if row is None:return {'provenance':None,'template':None,'initial_content_sha256':None,'content_changed':False}
  return {'provenance':json.loads(row['provenance']),'template':row['template'],'initial_content_sha256':row['initial_content_sha256'],'content_changed':digest(artifact['content'])!=row['initial_content_sha256']}
