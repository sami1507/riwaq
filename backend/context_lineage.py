"""Source dependency receipts for context, including indirect follow-up answers.
No source text is duplicated here. Known revoked dependencies prevent delivery
and context reuse. Unbound imported history can be read, but is not model context.
"""
import json
from backend import facts,reasoning
MAX_LINKS=64
BLOCKED={'kind':'unbound_history'}
def init_context(conn):
 conn.execute('CREATE TABLE IF NOT EXISTS message_retrieval_options(message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,options TEXT NOT NULL)')
 conn.execute('CREATE TABLE IF NOT EXISTS message_context_links(message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,links TEXT NOT NULL)')
 conn.execute('CREATE TABLE IF NOT EXISTS message_context_compaction(message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,details TEXT NOT NULL)')
 conn.execute("INSERT OR IGNORE INTO message_context_links(message_id,links) SELECT m.id,? FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.role='assistant' AND EXISTS(SELECT 1 FROM events e WHERE e.project_id=c.project_id AND e.kind='snapshot_imported') AND NOT EXISTS(SELECT 1 FROM message_context_links l WHERE l.message_id=m.id)",(facts.packed([BLOCKED]),))
def read(conn,message_id):
 row=conn.execute('SELECT links FROM message_context_links WHERE message_id=?',(message_id,)).fetchone()
 if row is None:return None
 try:
  value=json.loads(row[0])
  return value if isinstance(value,list) and len(value)<=MAX_LINKS else [BLOCKED]
 except (ValueError,TypeError):return [BLOCKED]
def save(conn,message_id,links):
 if not isinstance(links,list) or len(links)>MAX_LINKS:raise ValueError('Context dependency limit exceeded')
 conn.execute('INSERT OR REPLACE INTO message_context_links VALUES(?,?)',(message_id,facts.packed(links)))
def merge(*groups):
 values={facts.packed(x):x for group in groups for x in group}
 return [BLOCKED] if len(values)>MAX_LINKS or facts.packed(BLOCKED) in values else list(values.values())
def source_links(conn,project_id,chunks):
 if not isinstance(chunks,list) or len(chunks)>10 or any(not isinstance(x,dict) for x in chunks):return [BLOCKED]
 links=[]
 for chunk in chunks:
  if chunk.get('provenance')=='stored_excerpt':return [BLOCKED]
  kind='public_chunk' if chunk.get('scope')=='public' else 'private_chunk'
  row,state=facts.source_raw(conn,project_id,kind,chunk.get('id',''))
  if row is None or state!='current' or row['text']!=chunk.get('text'):return [BLOCKED]
  if kind=='public_chunk' and row['version_id']!=chunk.get('version_id'):return [BLOCKED]
  links.append({'kind':'chunk','source_kind':kind,'id':row['id'],'source_sha256':row['source_sha256'],'text_sha256':facts.sha(row['text']),'version_id':row.get('version_id'),'file_id':row.get('file_id')})
 return merge(links)
def fact_links(bindings):return [{'kind':'fact',**x} for x in bindings]

def _project_memory_row(conn,project_id,memory_id):
 # The local service has one authenticated owner, not multi-tenant membership.
 # A matching memory revision is insufficient after project access is revoked.
 # Session validity is checked by the request/stream layer; keep this check usable
 # for stored history across fresh sessions without granting an old task access.
 return conn.execute("""SELECT m.content,r.version FROM memories m
  JOIN memory_revisions r ON r.memory_id=m.id
  JOIN guard_projects p ON p.project_id=m.project_id
  JOIN local_auth_owner o ON o.slot=1 AND o.owner_id=p.owner_id
  WHERE m.id=? AND m.project_id=? AND m.scope='project'""",(memory_id,project_id)).fetchone()

def memory_links(conn,project_id,memories):
 links=[]
 for item in memories:
  row=_project_memory_row(conn,project_id,item['id'])
  if row is None or row['content']!=item['content']:return [BLOCKED]
  links.append({'kind':'project_memory','id':item['id'],'version':row['version'],'content_sha256':facts.sha(row['content'])})
 return merge(links)

def invalidate_legacy_memory_context(conn,project_id):
 # Before revision binding existed, an answer could contain a project memory
 # without recording the dependency. Keep historical text visible, but do not
 # silently reuse such replies after a correction/deletion. No content copied.
 for row in conn.execute("SELECT m.id FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.project_id=? AND m.role='assistant'",(project_id,)).fetchall():
  links=read(conn,row['id'])
  if links is None or not any(isinstance(x,dict) and x.get('kind')=='project_memory' for x in links):save(conn,row['id'],[BLOCKED])

def authorized(conn,project_id,links,conversation_id=None):
 if not isinstance(links,list) or len(links)>MAX_LINKS:return False
 for link in links:
  if not isinstance(link,dict):return False
  if link.get('kind')=='fact':
   if not facts.authorize_bindings(conn,project_id,[{k:v for k,v in link.items() if k!='kind'}]):return False
  elif link.get('kind')=='project_memory':
   row=_project_memory_row(conn,project_id,link.get('id'))
   if row is None or row['version']!=link.get('version') or facts.sha(row['content'])!=link.get('content_sha256'):return False
  elif link.get('kind') in ('learning_release','learning_feedback'):
   from backend.learning import authorize_context_links
   if not authorize_context_links(conn,project_id,[link],conversation_id=conversation_id):return False
  elif link.get('kind')=='personal_memory':
   from backend.personal_memory import authorize_context_links
   if not authorize_context_links(conn,project_id,[link],conversation_id=conversation_id):return False
  elif link.get('kind')=='chunk':
   if link.get('source_kind') not in {'public_chunk','private_chunk'}:return False
   row,state=facts.source_raw(conn,project_id,link['source_kind'],link.get('id',''))
   if row is None or state!='current' or row['source_sha256']!=link.get('source_sha256') or facts.sha(row['text'])!=link.get('text_sha256') or row.get('version_id')!=link.get('version_id') or row.get('file_id')!=link.get('file_id'):return False
  else:return False
 return True

def disclosable(conn,project_id,message):
 """Read/export policy, distinct from permission to reuse text as model context.

 Imported/BLOCKED history has no asserted live provenance and remains readable.
 A known versioned dependency or revoked local source cannot be laundered by
 reading its cached answer. Unknown/foreign legacy citations are left to the
 caller's existing citation sanitization; they do not prove body provenance.
 """
 if message['role']!='assistant':return True
 links=read(conn,message['id']) if message.get('id') else None
 if links is not None:
  bound=[link for link in links if link!=BLOCKED]
  if bound and not authorized(conn,project_id,bound,conversation_id=message.get('conversation_id')):return False
 try:
  citations=message.get('citations',[])
  if isinstance(citations,str):citations=json.loads(citations)
  if not isinstance(citations,list):return False
  for citation in citations:
   if not isinstance(citation,dict):continue
   if citation.get('scope','private')=='private' and isinstance(citation.get('file_id'),str):
    policy=conn.execute('SELECT * FROM guard_file_policies WHERE file_id=? AND project_id=?',(citation['file_id'],project_id)).fetchone()
    if policy is not None:
     current=conn.execute('''SELECT 1 FROM knowledge_files f JOIN guard_projects g ON g.project_id=f.project_id
      JOIN local_auth_owner o ON o.slot=1 AND o.owner_id=g.owner_id
      WHERE f.id=? AND f.project_id=? AND g.owner_id=?''',(citation['file_id'],project_id,policy['owner_id'])).fetchone()
     if policy['revoked'] or current is None:return False
 except (ValueError,TypeError,KeyError):return False
 return True
def filter_history(conn,project_id,history,file_scope=None,conversation_id=None):
 # Older untracked answers conservatively inherit prior source dependencies.
 # A stored empty receipt marks a genuinely source-free newly generated answer.
 carry=[];annotated=[]
 for message in history:
  if message['role']!='assistant' or message.get('status')!='complete':annotated.append((message,[]));continue
  links=read(conn,message['id']) if message.get('id') else None
  if links is None:
   try:
    chunks=message.get('citations','[]');chunks=json.loads(chunks) if isinstance(chunks,str) else chunks
    receipt=reasoning.read_receipt(conn,message['id']) if message.get('id') else None
    links=[BLOCKED] if receipt and receipt.get('origin')=='imported_snapshot' else merge(carry,source_links(conn,project_id,chunks),fact_links(receipt.get('fact_bindings',[])) if receipt else [])
   except (ValueError,TypeError,KeyError):links=[BLOCKED]
  carry=links;annotated.append((message,links))
 # No fixed message window: a suffix window silently dropped the user's
 # earliest constraints after 20 turns (CORE). The context budget in
 # grounding.compose now compacts declared assistant turns or rejects.
 kept=[];used=[]
 for message,links in reversed(annotated):
  if not authorized(conn,project_id,links,conversation_id=conversation_id):continue
  if file_scope:
   selection={s['file_id']:set(s.get('pages') or []) for s in file_scope}
   outside=False
   for link in links:
    if link.get('source_kind')!='private_chunk':continue
    if link.get('file_id') not in selection:outside=True;break
    pages=selection[link['file_id']]
    if pages:
     ref=conn.execute('SELECT payload FROM document_chunk_refs WHERE chunk_id=?',(link['id'],)).fetchone()
     if not ref or json.loads(ref[0]).get('page') not in pages:outside=True;break
   if outside:continue
  combined=merge(used,links)
  if combined==[BLOCKED]:continue
  used=combined;kept.append(message)
 return list(reversed(kept)),used


def read_retrieval(conn,message_id):
 row=conn.execute('SELECT options FROM message_retrieval_options WHERE message_id=?',(message_id,)).fetchone()
 if row is None:return None
 try:return reasoning.RetrievalOptions.model_validate_json(row[0]).model_dump()
 except (ValueError,TypeError):return None

def save_retrieval(conn,message_id,options):
 value=reasoning.RetrievalOptions.model_validate(options).model_dump()
 conn.execute('INSERT OR REPLACE INTO message_retrieval_options VALUES(?,?)',(message_id,facts.packed(value)))


# Declared history compaction (counts and turn numbers only, never text).
COMPACTION_KEYS={'counting','policy','history_messages','user_turns_preserved','assistant_turns','assistant_turns_abbreviated','assistant_turns_omitted','affected_turns','latest_assistant_reply_full'}
def save_compaction(conn,message_id,details):
 if not details:
  conn.execute('DELETE FROM message_context_compaction WHERE message_id=?',(message_id,));return
 if not isinstance(details,dict) or set(details)-COMPACTION_KEYS or len(details.get('affected_turns',[]))>2000:raise ValueError('Invalid context compaction receipt')
 conn.execute('INSERT OR REPLACE INTO message_context_compaction VALUES(?,?)',(message_id,json.dumps(details,ensure_ascii=False,sort_keys=True)))
def read_compaction(conn,message_id):
 try:row=conn.execute('SELECT details FROM message_context_compaction WHERE message_id=?',(message_id,)).fetchone()
 except Exception:return None
 if row is None:return None
 try:
  value=json.loads(row[0])
  return value if isinstance(value,dict) and not set(value)-COMPACTION_KEYS else None
 except (ValueError,TypeError):return None
