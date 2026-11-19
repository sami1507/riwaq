"""Project-scoped conversation organization; no copied sources or permissions.

Metadata is a sidecar to preserve the existing conversation schema. Moves are
deliberately limited to conversations with positively empty source receipts and
no dependent state. Historical provenance is never rewritten by a move.
"""
import json
import unicodedata
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.db import get_db, now, event
from backend import context_lineage
from backend.guard import require_project

router = APIRouter(prefix='/api/projects/{project_id}/conversation-library')


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_version: int = Field(ge=1, le=2**53-2)


class Patch(Input):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None

    @model_validator(mode='after')
    def valid_changes(self):
        if self.title is None and self.archived is None:
            raise ValueError('Choose a title or archive change')
        if self.title is not None:
            self.title = self.title.strip()
            if not self.title or any(unicodedata.category(ch) in ('Cc', 'Cs') for ch in self.title):
                raise ValueError('Title must be readable text without control characters')
        return self


class Move(Input):
    target_project_id: str = Field(min_length=1, max_length=80)
    acknowledge_context_change: bool


def initialize(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS conversation_library_state(
      conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
      version INTEGER NOT NULL CHECK(version>=1), archived INTEGER NOT NULL CHECK(archived IN (0,1)),
      updated_at TEXT NOT NULL);
    INSERT OR IGNORE INTO conversation_library_state
      SELECT id,1,0,updated_at FROM conversations;
    CREATE TRIGGER IF NOT EXISTS conversation_library_created AFTER INSERT ON conversations BEGIN
      INSERT OR IGNORE INTO conversation_library_state VALUES(NEW.id,1,0,NEW.updated_at);
    END;
    CREATE TRIGGER IF NOT EXISTS conversation_library_changed AFTER UPDATE OF title,project_id ON conversations
      WHEN NEW.title IS NOT OLD.title OR NEW.project_id IS NOT OLD.project_id BEGIN
      UPDATE conversation_library_state SET version=version+1,updated_at=NEW.updated_at WHERE conversation_id=NEW.id;
    END;
    ''')


def deliver(value):
    return JSONResponse(value, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'})


def conflict(code, message):
    raise HTTPException(409, {'code': code, 'message': message})


def row(conn, project_id, conversation_id):
    value = conn.execute('''SELECT c.*,s.version,s.archived FROM conversations c
        JOIN conversation_library_state s ON s.conversation_id=c.id WHERE c.id=? AND c.project_id=?''',
        (conversation_id, project_id)).fetchone()
    if value is None:
        raise HTTPException(404, 'Project resource unavailable')
    return {**dict(value), 'archived': bool(value['archived'])}


def busy(conn, conversation_id):
    # Request-time import shares the real execution registry, not a second copy.
    from backend.app import ACTIVE
    return conversation_id in ACTIVE or conn.execute(
        "SELECT 1 FROM messages WHERE conversation_id=? AND status='streaming' LIMIT 1", (conversation_id,)).fetchone() is not None


def mutation_scope(conn, project_id, conversation_id, version):
    actor = require_project(conn, project_id)
    value = row(conn, project_id, conversation_id)
    if value['version'] != version:
        conflict('version_conflict', 'تغيرت المحادثة. حدّث القائمة وراجع التعديل قبل الحفظ. / Conversation changed; refresh and review before saving.')
    if busy(conn, conversation_id):
        conflict('conversation_busy', 'أوقف الرد الجاري قبل تنظيم المحادثة. / Stop the active response before organizing this conversation.')
    return actor, value


def visible_rows(conn, actor, project_id=None, q='', archived='active', limit=200):
    """Shared with the main list. A revoked source cannot become a search hit."""
    if project_id is not None:
        require_project(conn, project_id, actor)
    rows = conn.execute('''SELECT c.*,s.version,s.archived FROM conversations c
        JOIN guard_projects g ON g.project_id=c.project_id
        JOIN conversation_library_state s ON s.conversation_id=c.id
        WHERE g.owner_id=? AND (? IS NULL OR c.project_id=?)
        AND (?='all' OR s.archived=?) ORDER BY c.updated_at DESC,c.id''',
        (actor.owner_id, project_id, project_id, archived, int(archived == 'archived')))
    needle = q.strip().casefold()
    found = []
    for item in rows:
        # Revalidates session even for the global list, whose owner join alone
        # would not be sufficient if access were revoked during a request.
        require_project(conn, item['project_id'], actor)
        match = not needle or needle in item['title'].casefold()
        if not match:
            for message in conn.execute('SELECT * FROM messages WHERE conversation_id=?', (item['id'],)):
                if needle in message['content'].casefold() and context_lineage.disclosable(conn, item['project_id'], dict(message)):
                    match = True
                    break
        if match:
            found.append({**dict(item), 'archived': bool(item['archived'])})
        if len(found) >= limit:
            break
    return found


def table_exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def parse(value):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def references(value, ids):
    if isinstance(value, str):
        return value in ids
    if isinstance(value, list):
        return any(references(item, ids) for item in value)
    if isinstance(value, dict):
        return any(references(item, ids) for item in value.values())
    return False


def move_blocker(conn, value):
    """Return a public reason without disclosing dependent content."""
    cid, pid = value['id'], value['project_id']
    if busy(conn, cid):
        return 'conversation_busy'
    if value['parent_id'] or conn.execute('SELECT 1 FROM conversations WHERE parent_id=? LIMIT 1', (cid,)).fetchone():
        return 'branch_history'
    messages = conn.execute('SELECT * FROM messages WHERE conversation_id=?', (cid,)).fetchall()
    ids = {cid, *(m['id'] for m in messages)}
    for message in messages:
        citations = parse(message['citations'])
        if citations != [] and not (citations is None and message['role'] != 'assistant' and message['citations'] is None):
            return 'source_dependencies'
        links = context_lineage.read(conn, message['id'])
        if (message['role'] == 'assistant' and links != []) or links not in (None, []):
            return 'source_dependencies'
        if table_exists(conn, 'message_retrieval_options'):
            retrieval = conn.execute('SELECT options FROM message_retrieval_options WHERE message_id=?', (message['id'],)).fetchone()
            if retrieval:
                options = parse(retrieval['options'])
                if not isinstance(options, dict) or options.get('file_scope') or options.get('fact_ids'):
                    return 'source_dependencies'
        receipt = conn.execute('SELECT receipt FROM reasoning_runs WHERE message_id=?', (message['id'],)).fetchone()
        if receipt:
            payload = parse(receipt['receipt'])
            if not isinstance(payload, dict) or payload.get('fact_bindings') or payload.get('origin') == 'imported_snapshot':
                return 'source_dependencies'
    if table_exists(conn, 'generation_requests') and conn.execute('''SELECT 1 FROM generation_requests
        WHERE conversation_id=? OR result_conversation_id=? OR message_id IN
        (SELECT id FROM messages WHERE conversation_id=?) LIMIT 1''', (cid, cid, cid)).fetchone():
        return 'generation_history'
    for table in ('studio_entry_dependencies', 'studio_artifact_dependencies'):
        if table_exists(conn, table) and conn.execute(f'SELECT 1 FROM {table} WHERE conversation_id=? LIMIT 1', (cid,)).fetchone():
            return 'saved_output_dependencies'
    # Historical output provenance is relevant even when a newer version no
    # longer quotes the conversation. Do not rewrite past source attribution.
    for query, column in (
        ('SELECT v.payload FROM studio_versions v JOIN studio_entries e ON e.id=v.entry_id WHERE e.project_id=?', 'payload'),
        ('SELECT provenance FROM studio_artifact_sources WHERE project_id=?', 'provenance'),
        ('SELECT v.payload FROM fact_versions v JOIN fact_entries e ON e.id=v.fact_id WHERE e.project_id=?', 'payload'),
    ):
        for dependency in conn.execute(query, (pid,)):
            if references(parse(dependency[column]), ids):
                return 'saved_output_dependencies'
    if conn.execute('''SELECT 1 FROM learning_feedback WHERE conversation_id=? OR
        (target_kind='message' AND target_id IN (SELECT id FROM messages WHERE conversation_id=?)) LIMIT 1''', (cid, cid)).fetchone():
        return 'learning_dependencies'
    for feedback in conn.execute('SELECT alternative,dependencies FROM learning_feedback WHERE project_id=?', (pid,)):
        if references(parse(feedback['alternative']), ids) or references(parse(feedback['dependencies']), ids):
            return 'learning_dependencies'
    if conn.execute('SELECT 1 FROM learning_candidates WHERE conversation_id=? LIMIT 1', (cid,)).fetchone():
        return 'learning_dependencies'
    if conn.execute('SELECT 1 FROM learning_releases WHERE scope_key=? LIMIT 1', (cid,)).fetchone():
        return 'learning_dependencies'
    if table_exists(conn, 'composer_drafts'):
        for draft in conn.execute('SELECT contents FROM composer_drafts WHERE project_id=? AND conversation_key=?', (pid, cid)):
            contents = parse(draft['contents'])
            if not isinstance(contents, dict) or any(contents.values()):
                return 'unsent_draft'
    return None


REASONS = {
    'conversation_busy': 'أوقف الرد الجاري أولًا. / Stop the active response first.',
    'branch_history': 'ترتبط المحادثة بفروع محفوظة. يبقى النقل محجوبًا لحماية سياقها. / Saved branches require the current project context.',
    'source_dependencies': 'توجد مصادر أو ذاكرة مرتبطة، أو لم يثبت خلو السجل القديم منها. لا تُنقل صلاحياتها. / Source or memory dependencies, or an unverified legacy receipt, prevent moving.',
    'generation_history': 'ترتبط المحادثة بسجل محاولة توليد يمنع تكرار الطلب؛ يبقى في مشروعه الأصلي. / A durable generation receipt is bound to the original project.',
    'saved_output_dependencies': 'توجد مخرجات أو قرارات أو حقائق مرتبطة بالمحادثة. / Saved outputs, decisions or facts depend on this conversation.',
    'learning_dependencies': 'توجد ملاحظات أو دروس مرتبطة بهذه المحادثة. / Feedback or lessons depend on this conversation.',
    'unsent_draft': 'احفظ نص المسودة في مكان مناسب ثم أفرغ المحرر قبل النقل. / Preserve and clear the unsent draft before moving.',
}


@router.get('')
def listing(project_id: str, q: str = Query(default='', max_length=300), archived: Literal['active', 'archived', 'all'] = 'active', limit: int = Query(default=200, ge=1, le=200)):
    with get_db() as conn:
        actor = require_project(conn, project_id)
        items = visible_rows(conn, actor, project_id, q, archived, limit)
        return deliver({'items': items, 'limit': limit, 'search_scope': 'current_project_titles_and_disclosable_messages'})


@router.get('/{conversation_id}')
def inspect(project_id: str, conversation_id: str):
    with get_db() as conn:
        require_project(conn, project_id)
        value = row(conn, project_id, conversation_id)
        blocker = move_blocker(conn, value)
        return deliver({**value, 'move': {'eligible': blocker is None, 'reason': blocker, 'message': REASONS.get(blocker, '')}})


@router.patch('/{conversation_id}')
def change(project_id: str, conversation_id: str, body: Patch):
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        actor, previous = mutation_scope(conn, project_id, conversation_id, body.expected_version)
        title = previous['title'] if body.title is None else body.title
        archived = previous['archived'] if body.archived is None else body.archived
        stamp = now()
        # The title trigger increments once. Archive-only changes increment here;
        # combining both edits is still one reviewable version transition.
        if title != previous['title']:
            conn.execute('UPDATE conversations SET title=?,updated_at=? WHERE id=?', (title, stamp, conversation_id))
        if archived != previous['archived']:
            conn.execute('UPDATE conversation_library_state SET archived=?,version=version+?,updated_at=? WHERE conversation_id=?',
                         (int(archived), int(title == previous['title']), stamp, conversation_id))
            conn.execute('UPDATE conversations SET updated_at=? WHERE id=?', (stamp, conversation_id))
        if title != previous['title'] or archived != previous['archived']:
            event(conn, project_id, 'conversation_organized', 'Conversation title or archive state updated')
        require_project(conn, project_id, actor)
        return deliver(row(conn, project_id, conversation_id))


@router.post('/{conversation_id}/move')
def move(project_id: str, conversation_id: str, body: Move):
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        actor, value = mutation_scope(conn, project_id, conversation_id, body.expected_version)
        require_project(conn, body.target_project_id, actor)
        if not body.acknowledge_context_change:
            raise HTTPException(422, 'Acknowledge the new project context before moving')
        if project_id == body.target_project_id:
            raise HTTPException(422, 'Choose a different project')
        blocker = move_blocker(conn, value)
        if blocker:
            conflict(blocker, REASONS[blocker])
        conn.execute('UPDATE conversations SET project_id=?,updated_at=? WHERE id=?', (body.target_project_id, now(), conversation_id))
        event(conn, project_id, 'conversation_moved_out', 'Conversation moved to an authorized project; no sources or permissions moved')
        event(conn, body.target_project_id, 'conversation_moved_in', 'Conversation moved from an authorized project; future requests use this project context')
        require_project(conn, project_id, actor)
        require_project(conn, body.target_project_id, actor)
        return deliver(row(conn, body.target_project_id, conversation_id))
