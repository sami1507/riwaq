import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def data_dir():
    path = Path(os.environ.get('RIWAQ_DATA_DIR', str(ROOT / 'data')))
    path.mkdir(parents=True, exist_ok=True)
    return path

def now():
    return datetime.now(timezone.utc).isoformat()

def uid():
    return uuid.uuid4().hex

@contextmanager
def get_db():
    conn = sqlite3.connect(data_dir() / 'riwaq.sqlite3', timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=10000')
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

def event(conn, project_id, kind, summary):
    conn.execute('INSERT INTO events VALUES (?,?,?,?,?)', (uid(), project_id, kind, summary, now()))

def init_db():
    with get_db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,instructions TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),title TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,parent_id TEXT);
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,role TEXT NOT NULL,content TEXT NOT NULL,status TEXT NOT NULL,model TEXT,created_at TEXT NOT NULL,citations TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),kind TEXT NOT NULL,summary TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,scope TEXT NOT NULL,project_id TEXT REFERENCES projects(id),content TEXT NOT NULL,created_at TEXT NOT NULL);
        ''')
        # Add revision metadata without changing the legacy five-column memory
        # table used by snapshot import. Versions contain no historical content.
        c.executescript('''
        CREATE TABLE IF NOT EXISTS memory_revisions(memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
          version INTEGER NOT NULL CHECK(version>=1),updated_at TEXT NOT NULL);
        INSERT OR IGNORE INTO memory_revisions SELECT id,1,created_at FROM memories;
        CREATE TRIGGER IF NOT EXISTS memory_revision_insert AFTER INSERT ON memories BEGIN
          INSERT INTO memory_revisions VALUES(NEW.id,1,NEW.created_at);
        END;
        CREATE TRIGGER IF NOT EXISTS memory_revision_update AFTER UPDATE OF content,scope,project_id ON memories BEGIN
          UPDATE memory_revisions SET version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE memory_id=NEW.id;
        END;
        ''')
        c.execute("UPDATE messages SET status='interrupted' WHERE status='streaming'")
        if not c.execute('SELECT 1 FROM projects LIMIT 1').fetchone():
            pid = uid()
            c.execute('INSERT INTO projects VALUES (?,?,?,?)',(pid,'مساحتي الأولى','',now()))
            event(c,pid,'created','تم إنشاء مساحة العمل المحلية')
        from backend.reasoning import init_reasoning
        init_reasoning(c)
        from backend.composer_drafts import init as init_composer_drafts
        init_composer_drafts(c)
        from backend.knowledge import init_knowledge
        init_knowledge(c)
        from backend.documents import init_documents
        init_documents(c)
        from backend.studio import init_studio
        init_studio(c)
        from backend.public_knowledge import init_public_knowledge
        init_public_knowledge(c)
        from backend.facts import init_facts
        init_facts(c)
        from backend.visuals import init_visuals
        init_visuals(c)
        from backend.visual_jobs import init_jobs
        init_jobs(c)
        from backend.context_lineage import init_context
        init_context(c)
        from backend.video import init_video
        init_video(c)
        from backend.voice import init_db as init_voice
        init_voice(c)
        from backend.coding import init_coding
        init_coding(c)
        from backend.research import initialize
        initialize(c)
        from backend import local_auth, guard
        local_auth.init(c)
        guard.init(c)
        from backend.skills import init_skills
        init_skills(c)
        from backend.learning import init_learning
        init_learning(c)
        from backend.personal_memory import init_personal_memory
        init_personal_memory(c)
        from backend import generation_requests,conversation_library
        generation_requests.initialize(c)
        conversation_library.initialize(c)

DEFAULTS = {'mode':'local','provider':'ollama','model':'qwen3:4b-instruct','max_tokens':1024,'context_size':8192,'local_fallback_models':[]}

def settings():
    with get_db() as c:
        # Keys starting with '_' are internal bookkeeping, not user settings.
        return DEFAULTS | {r['key']:json.loads(r['value']) for r in c.execute('SELECT * FROM settings') if not r['key'].startswith('_')}

def message_dict(row):
    result = dict(row)
    result['citations'] = json.loads(result.get('citations','[]'))
    from backend.reasoning import read_receipt
    from backend.context_lineage import read_retrieval
    from backend.research import read as read_research
    with get_db() as conn:
        receipt=read_receipt(conn,result['id'])
        retrieval=read_retrieval(conn,result['id'])
        research=read_research(conn,result['id'])
        from backend.context_lineage import read_compaction
        compaction=read_compaction(conn,result['id'])
    if receipt is not None: result['run']=receipt
    if compaction is not None:result['context']=compaction
    if retrieval is not None:result['retrieval_options']=retrieval
    if research is not None:result['research']=research
    return result
