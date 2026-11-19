"""Scoped feedback lifecycle over existing identity, memory and SQLite.

Feedback is evidence of a user's report, never proof of factual correctness.
Only bounded typed preferences can become lessons; no arbitrary prompt, tool
permission, code modification, global rollout, or training job is accepted here.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextvars import ContextVar
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import re

from backend import guard, local_auth, context_lineage, reasoning
from backend.db import uid, now

VERSION = 'learning-v1'
MAX_ACTIVE_RULES = 20
MAX_CORRECTION_GROUP = 128
CONSENT_POLICY = 'learning-consent-v1'
PURPOSES = {'current_task', 'project_memory', 'product_improvement', 'training'}
SIGNALS = {'negative', 'positive', 'regenerate', 'abandon', 'correction', 'preference',
           'request_change', 'operational_failure', 'comparison', 'example', 'remember'}
DIMENSIONS = {'correctness', 'style', 'completeness', 'input', 'sources', 'tools', 'latency', 'unspecified'}
_checking_feedback = ContextVar('learning_feedback_validation_stack', default=())


class LearningError(ValueError):
    def __init__(self, status_code, code):
        self.status_code, self.code = status_code, code
        super().__init__(code)


@dataclass(frozen=True)
class BoundTarget:
    owner_id: str
    project_id: str
    target_kind: str
    target_id: str
    version: str
    content: str
    request_text: str
    receipt: dict
    source_refs: list
    conversation_id: str | None = None


def pack(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(pack(value).encode()).hexdigest()


def _strict(value, required=(), optional=()):
    if type(value) is not dict or set(value) - {*required, *optional} or any(k not in value for k in required):
        raise LearningError(422, 'invalid_learning_fields')


def _text(value, maximum=2000, blank=False):
    if type(value) is not str or len(value) > maximum or (not blank and not value.strip()) or any(ord(c) < 32 for c in value):
        raise LearningError(422, 'invalid_learning_text')
    from backend.coding_context import scan_secrets
    if scan_secrets(value):
        raise LearningError(422, 'sensitive_learning_text_rejected')
    return value


def _version(value):
    if type(value) is not int or value < 1:
        raise LearningError(422, 'expected_version_required')


def _write(c):
    if not c.in_transaction:
        c.execute('BEGIN IMMEDIATE')


def _actor(c, actor, pid):
    try:
        guard.check_actor(c, actor, pid)
    except guard.Denied as exc:
        raise LearningError(404, 'learning_resource_unavailable') from exc


def _event(c, actor, pid, kind, resource_id, version):
    c.execute('INSERT INTO learning_events VALUES(?,?,?,?,?,?,?)', (uid(), actor.owner_id, pid, kind, resource_id, version, now()))
    guard.audit(c, actor, pid, resource_id, 'learning_' + kind, 'allow', 'scoped_versioned_operation')


def init_learning(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS learning_feedback(
      id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      conversation_id TEXT,target_kind TEXT NOT NULL,target_id TEXT NOT NULL,target_version TEXT NOT NULL,
      dependencies TEXT NOT NULL,signal TEXT NOT NULL,dimensions TEXT NOT NULL,reason TEXT NOT NULL,
      correction TEXT NOT NULL,alternative TEXT NOT NULL,scope TEXT NOT NULL,purposes TEXT NOT NULL,
      consent_policy TEXT NOT NULL,status TEXT NOT NULL,verification TEXT NOT NULL DEFAULT '{}',version INTEGER NOT NULL,
      request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
      UNIQUE(owner_id,project_id,request_key));
    CREATE TABLE IF NOT EXISTS learning_candidates(
      id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      feedback_id TEXT NOT NULL REFERENCES learning_feedback(id) ON DELETE CASCADE,feedback_version INTEGER NOT NULL,
      scope TEXT NOT NULL,conversation_id TEXT,rule TEXT NOT NULL,rule_key TEXT NOT NULL,
      applicability TEXT NOT NULL,status TEXT NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS learning_evaluations(
      id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL REFERENCES learning_candidates(id) ON DELETE CASCADE,
      candidate_version INTEGER NOT NULL,rule_hash TEXT NOT NULL,source_fingerprint TEXT NOT NULL,
      status TEXT NOT NULL,evidence TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS learning_releases(
      id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      candidate_id TEXT NOT NULL REFERENCES learning_candidates(id) ON DELETE CASCADE,candidate_version INTEGER NOT NULL,
      evaluation_id TEXT NOT NULL REFERENCES learning_evaluations(id),scope_key TEXT NOT NULL,rule_key TEXT NOT NULL,
      memory_id TEXT,memory_hash TEXT,status TEXT NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS learning_one_active_rule ON learning_releases(owner_id,project_id,scope_key,rule_key) WHERE status='active';
    CREATE TABLE IF NOT EXISTS learning_events(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      kind TEXT NOT NULL,resource_id TEXT NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS learning_tombstones(resource_key TEXT PRIMARY KEY,owner_id TEXT NOT NULL,project_id TEXT NOT NULL,
      version INTEGER NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS learning_descendants(id TEXT PRIMARY KEY,feedback_id TEXT NOT NULL REFERENCES learning_feedback(id) ON DELETE CASCADE,
      kind TEXT NOT NULL,resource_ref TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS learning_feedback_target ON learning_feedback(owner_id,project_id,target_kind,target_id,target_version,status);
    CREATE TABLE IF NOT EXISTS learning_conflicts(
      id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      first_feedback TEXT NOT NULL REFERENCES learning_feedback(id) ON DELETE CASCADE,
      second_feedback TEXT NOT NULL REFERENCES learning_feedback(id) ON DELETE CASCADE,
      claim_path TEXT NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL,
      UNIQUE(first_feedback,second_feedback));
    CREATE TRIGGER IF NOT EXISTS learning_conflict_forgotten AFTER UPDATE OF status ON learning_feedback
    WHEN NEW.status='deleted' BEGIN
      DELETE FROM learning_conflicts WHERE first_feedback=NEW.id OR second_feedback=NEW.id;
    END;
    CREATE TRIGGER IF NOT EXISTS learning_feedback_forgotten AFTER UPDATE OF status ON learning_feedback
    WHEN NEW.status='deleted' AND OLD.status!='deleted' BEGIN
      INSERT OR REPLACE INTO learning_tombstones VALUES('feedback:'||NEW.id,NEW.owner_id,NEW.project_id,NEW.version,'forgotten',NEW.updated_at);
      UPDATE learning_candidates SET status='revoked',rule='{}',version=version+1,updated_at=NEW.updated_at WHERE feedback_id=NEW.id;
      UPDATE learning_evaluations SET status='revoked',evidence='{}' WHERE candidate_id IN(SELECT id FROM learning_candidates WHERE feedback_id=NEW.id);
      UPDATE learning_releases SET status='revoked',version=version+1,updated_at=NEW.updated_at WHERE candidate_id IN(SELECT id FROM learning_candidates WHERE feedback_id=NEW.id);
      DELETE FROM memories WHERE id IN(SELECT memory_id FROM learning_releases WHERE candidate_id IN(SELECT id FROM learning_candidates WHERE feedback_id=NEW.id));
      UPDATE learning_descendants SET status=CASE WHEN kind='model' THEN 'weights_unchanged_review_required' ELSE 'revoked' END WHERE feedback_id=NEW.id;
    END;
    CREATE TRIGGER IF NOT EXISTS learning_message_deleted AFTER DELETE ON messages BEGIN
      UPDATE learning_feedback SET status='deleted',reason='',correction='{}',verification='{}',alternative='{}',version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE (target_kind='message' AND target_id=OLD.id) OR json_extract(alternative,'$.id')=OLD.id;
    END;
    CREATE TRIGGER IF NOT EXISTS learning_conversation_deleted BEFORE DELETE ON conversations BEGIN
      UPDATE learning_feedback SET status='deleted',reason='',correction='{}',verification='{}',alternative='{}',version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE conversation_id=OLD.id;
    END;
    CREATE TRIGGER IF NOT EXISTS learning_memory_deleted AFTER DELETE ON memories BEGIN
      UPDATE learning_candidates SET status='stale',version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id IN(SELECT candidate_id FROM learning_releases WHERE memory_id=OLD.id AND status='active');
      UPDATE learning_releases SET status='revoked',memory_id=NULL,version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE memory_id=OLD.id AND status='active';
      UPDATE learning_feedback SET status='deleted',reason='',correction='{}',verification='{}',alternative='{}',version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE EXISTS(SELECT 1 FROM json_each(learning_feedback.dependencies) d WHERE json_extract(d.value,'$.kind')='project_memory' AND json_extract(d.value,'$.id')=OLD.id);
    END;
    CREATE TRIGGER IF NOT EXISTS learning_memory_edited AFTER UPDATE OF content,scope,project_id ON memories BEGIN
      UPDATE learning_candidates SET status='stale',version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id IN(SELECT candidate_id FROM learning_releases WHERE memory_id=OLD.id AND status='active');
      UPDATE learning_releases SET status='revoked',memory_id=NULL,version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE memory_id=OLD.id AND status='active';
    END;
    CREATE TRIGGER IF NOT EXISTS learning_owner_revoked AFTER UPDATE OF owner_id ON guard_projects WHEN NEW.owner_id!=OLD.owner_id BEGIN
      UPDATE learning_feedback SET status='deleted',reason='',correction='{}',verification='{}',alternative='{}',version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE project_id=OLD.project_id AND owner_id=OLD.owner_id;
    END;
    ''')
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'knowledge_files' in tables:
        c.executescript('''CREATE TRIGGER IF NOT EXISTS learning_file_deleted AFTER DELETE ON knowledge_files BEGIN
          UPDATE learning_feedback SET status='deleted',reason='',correction='{}',verification='{}',alternative='{}',version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
          WHERE (target_kind='file' AND target_id=OLD.id) OR EXISTS(SELECT 1 FROM json_each(learning_feedback.dependencies) d WHERE json_extract(d.value,'$.file_id')=OLD.id OR (json_extract(d.value,'$.kind')='file' AND json_extract(d.value,'$.id')=OLD.id)); END;''')
    if 'skill_runs' in tables:
        c.executescript('''CREATE TRIGGER IF NOT EXISTS learning_tool_deleted AFTER DELETE ON skill_runs BEGIN
          UPDATE learning_feedback SET status='deleted',reason='',correction='{}',verification='{}',alternative='{}',version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE target_kind='tool' AND target_id=OLD.id; END;''')
    if 'guard_file_policies' in tables:
        c.executescript('''CREATE TRIGGER IF NOT EXISTS learning_file_revoked AFTER UPDATE OF revoked ON guard_file_policies WHEN NEW.revoked=1 BEGIN
          UPDATE learning_feedback SET status='deleted',reason='',correction='{}',verification='{}',alternative='{}',version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
          WHERE (target_kind='file' AND target_id=NEW.file_id) OR EXISTS(SELECT 1 FROM json_each(learning_feedback.dependencies) d WHERE json_extract(d.value,'$.file_id')=NEW.file_id OR (json_extract(d.value,'$.kind')='file' AND json_extract(d.value,'$.id')=NEW.file_id)); END;''')


def _conversation(c, pid, cid):
    if type(cid) is not str or not c.execute('SELECT 1 FROM conversations WHERE id=? AND project_id=?', (cid, pid)).fetchone():
        raise LearningError(404, 'learning_resource_unavailable')


def bind_target(c, actor, pid, kind, identifier, expected_version=None):
    _actor(c, actor, pid)
    if kind not in ('message', 'file', 'tool') or type(identifier) is not str or not 1 <= len(identifier) <= 100:
        raise LearningError(422, 'invalid_feedback_target')
    conversation_id = None
    if kind == 'message':
        row = c.execute('SELECT m.*,m.rowid AS sequence FROM messages m JOIN conversations v ON v.id=m.conversation_id WHERE m.id=? AND v.project_id=?', (identifier, pid)).fetchone()
        if not row or row['role'] != 'assistant': raise LearningError(404, 'learning_resource_unavailable')
        conversation_id = row['conversation_id']
        prior = c.execute("SELECT content FROM messages WHERE conversation_id=? AND role='user' AND rowid<? ORDER BY rowid DESC LIMIT 1", (conversation_id, row['sequence'])).fetchone()
        content, request_text = row['content'], prior[0] if prior else ''
        receipt = {'target_status': row['status'], 'run': reasoning.read_receipt(c, identifier) or {}}
        refs = context_lineage.read(c, identifier) or []
        version = digest({'content': content, 'status': row['status'], 'role': row['role'], 'model': row['model'],
                          'citations': row['citations'], 'request': request_text, 'receipt': receipt, 'source_refs': refs})
    elif kind == 'file':
        try: snapshot, label, chunks = guard.resource(c, actor, pid, identifier)
        except guard.Denied as exc: raise LearningError(404, 'learning_resource_unavailable') from exc
        content = '\n'.join(v['text'] for v in chunks)
        request_text = ''; receipt = {'target_status': 'current', 'source': snapshot}
        version = digest(snapshot)
        refs = [{'kind': 'file', 'id': identifier, 'file_id': identifier, 'version': version, **snapshot}]
    else:
        row = c.execute('SELECT * FROM skill_runs WHERE id=? AND project_id=? AND owner_id=?', (identifier, pid, actor.owner_id)).fetchone()
        if not row: raise LearningError(404, 'learning_resource_unavailable')
        # Tool results also depend on versioned artifacts, inherited runs,
        # memory and connector bindings outside the documentIds snapshot list.
        # Reuse the exact result-download boundary before exposing cached text
        # or a correction derived from it through any learning endpoint.
        from backend import skills
        from fastapi import HTTPException
        try:
            skills.recheck(c,row,actor)
        except (HTTPException,guard.Denied,ValueError,KeyError,TypeError) as exc:
            raise LearningError(404,'learning_resource_unavailable') from exc
        document, payload, resources = (json.loads(row[k]) for k in ('document', 'payload', 'resources'))
        from backend import skill_files
        for artifact in document.get('result',{}).get('artifacts',[]):
            try:
                stored=c.execute('SELECT content,sha256 FROM skill_artifacts WHERE run_id=? AND name=?',
                                 (identifier,artifact['name'])).fetchone()
                if not stored or stored['sha256']!=artifact['sha256'] or hashlib.sha256(stored['content']).hexdigest()!=artifact['sha256']:
                    raise LearningError(404,'learning_resource_unavailable')
                if artifact.get('artifact_id'):
                    _,version=skill_files.version(c,actor,pid,artifact['artifact_id'],artifact['version'])
                    if version['run_id']!=identifier or version['sha256']!=artifact['sha256']:
                        raise LearningError(404,'learning_resource_unavailable')
            except (HTTPException,guard.Denied,ValueError,KeyError,TypeError) as exc:
                raise LearningError(404,'learning_resource_unavailable') from exc
        refs = []
        for source in resources:
            if type(source) is dict and source.get('file_id'):
                try: snapshot, _, _ = guard.resource(c, actor, pid, source['file_id'])
                except guard.Denied as exc: raise LearningError(404, 'learning_resource_unavailable') from exc
                if any(source.get(k) != snapshot.get(k) for k in ('sha256', 'extraction_sha256', 'policy_revision') if k in source):
                    raise LearningError(409, 'feedback_tool_source_changed')
                refs.append({'kind': 'file', 'id': source['file_id'], 'version': digest(snapshot), **snapshot})
        content = document.get('result', {}).get('text', '')
        if type(content) is not str: content = ''
        request_text = payload.get('request', '')
        receipt = {'target_status': row['state'], 'run': document}
        version = digest({'request_hash': row['request_hash'], 'document': document, 'state': row['state'], 'source_refs': refs})
    if len(content) > 32000 or len(request_text) > 32000:
        raise LearningError(413, 'feedback_target_exceeds_bounded_verification_scope')
    if expected_version is not None and expected_version != version:
        raise LearningError(409, 'feedback_target_changed')
    return BoundTarget(actor.owner_id, pid, kind, identifier, version, content, request_text, receipt, refs, conversation_id)


def target_snapshot(c, actor, pid, kind, identifier):
    target = bind_target(c, actor, pid, kind, identifier)
    return {'kind': kind, 'id': identifier, 'version': target.version, 'conversation_id': target.conversation_id,
            'status': target.receipt.get('target_status'), 'available': True}


def _row(c, actor, pid, table, identifier):
    _actor(c, actor, pid)
    if table not in ('learning_feedback', 'learning_candidates', 'learning_releases'):
        raise RuntimeError('Invalid internal learning table')
    row = c.execute(f'SELECT * FROM {table} WHERE id=? AND owner_id=? AND project_id=?', (identifier, actor.owner_id, pid)).fetchone()
    if not row: raise LearningError(404, 'learning_resource_unavailable')
    return row


def _public(row):
    value = dict(row)
    for key in ('dimensions', 'correction', 'alternative', 'purposes', 'verification', 'rule', 'applicability'):
        if key in value: value[key] = json.loads(value[key])
    if 'target_kind' in value:
        value['target'] = {'kind': value.pop('target_kind'), 'id': value.pop('target_id'), 'version': value.pop('target_version')}
    for key in ('owner_id', 'request_key', 'request_hash', 'dependencies', 'memory_hash'):
        value.pop(key, None)
    return value


def get_feedback(c, actor, pid, identifier):
    row = _row(c, actor, pid, 'learning_feedback', identifier)
    value = _public(row)
    try: _feedback_current(c, actor, pid, row)
    except LearningError as exc:
        value.update(reason='', correction={}, verification={}, alternative={}, target_status='unavailable', content_redacted=True)
        if value['status'] != 'deleted': value['status'] = 'stale'
        value['blocker'] = exc.code
    else:
        value['target_status'] = 'current'
        if row['status'] == 'conflicted':
            # No peer IDs, content or count: a peer source may since be revoked.
            value['conflict'] = {'status': 'quarantined', 'resolution': 'withdraw_conflicting_feedback_then_reverify',
                                 'automatically_selected': False}
    return value


def get_candidate(c, actor, pid, identifier):
    row = _row(c, actor, pid, 'learning_candidates', identifier)
    value = _public(row)
    try: _candidate_current(c, actor, pid, row)
    except LearningError as exc:
        value.update(rule={}, content_redacted=True, blocker=exc.code)
        if value['status'] not in ('revoked', 'rolled_back'): value['status'] = 'stale'
    evidence = c.execute('SELECT id,status,source_fingerprint,candidate_version,created_at FROM learning_evaluations WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1', (identifier,)).fetchone()
    value['latest_evaluation'] = dict(evidence) if evidence else None
    return value
def get_release(c, actor, pid, identifier): return _public(_row(c, actor, pid, 'learning_releases', identifier))


def _list(c, actor, pid, table):
    _actor(c, actor, pid)
    return [_public(r) for r in c.execute(f'SELECT * FROM {table} WHERE owner_id=? AND project_id=? ORDER BY created_at DESC LIMIT 200', (actor.owner_id, pid))]


def list_feedback(c, actor, pid): return [get_feedback(c, actor, pid, r['id']) for r in _list(c, actor, pid, 'learning_feedback')]
def list_candidates(c, actor, pid): return [get_candidate(c, actor, pid, r['id']) for r in _list(c, actor, pid, 'learning_candidates')]
def list_releases(c, actor, pid): return _list(c, actor, pid, 'learning_releases')


def _scope(scope):
    if scope == 'personal': raise LearningError(423, 'personal_memory_security_gate')
    if scope not in ('conversation', 'project'): raise LearningError(422, 'invalid_learning_scope')


def add_feedback(c, actor, pid, payload, idempotency_key):
    _write(c); _actor(c, actor, pid)
    _strict(payload, ('target', 'signal'), ('alternative_target', 'dimensions', 'reason', 'correction', 'scope', 'purposes', 'conversation_id'))
    _text(idempotency_key, 100)
    if len(idempotency_key) < 8: raise LearningError(422, 'invalid_idempotency_key')
    prior = c.execute('SELECT * FROM learning_feedback WHERE owner_id=? AND project_id=? AND request_key=?', (actor.owner_id, pid, idempotency_key)).fetchone()
    request_hash = digest(payload)
    if prior:
        if prior['request_hash'] != request_hash: raise LearningError(409, 'idempotency_payload_conflict')
        if prior['status'] == 'deleted': raise LearningError(410, 'feedback_was_forgotten')
        return get_feedback(c, actor, pid, prior['id'])
    _strict(payload['target'], ('kind', 'id', 'version'))
    target = bind_target(c, actor, pid, payload['target']['kind'], payload['target']['id'], payload['target']['version'])
    if type(payload['signal']) is not str or payload['signal'] not in SIGNALS: raise LearningError(422, 'invalid_feedback_signal')
    dimensions = payload.get('dimensions', ['unspecified'])
    if type(dimensions) is not list or not 1 <= len(dimensions) <= 8 or any(type(v) is not str or v not in DIMENSIONS for v in dimensions) or len(set(dimensions)) != len(dimensions):
        raise LearningError(422, 'invalid_feedback_dimensions')
    purposes = payload.get('purposes', ['current_task'])
    if type(purposes) is not list or not purposes or any(type(v) is not str or v not in PURPOSES for v in purposes) or len(set(purposes)) != len(purposes):
        raise LearningError(422, 'invalid_learning_purposes')
    scope = payload.get('scope', 'conversation'); _scope(scope)
    cid = payload.get('conversation_id') or target.conversation_id
    if cid is not None: _conversation(c, pid, cid)
    if target.conversation_id and cid != target.conversation_id: raise LearningError(409, 'feedback_conversation_mismatch')
    if scope == 'conversation' and not cid: raise LearningError(422, 'conversation_scope_requires_conversation')
    reason = _text(payload.get('reason', ''), 2000, True)
    correction = payload.get('correction')
    dependencies = list(target.source_refs)
    if correction is not None:
        from backend.learning_verification import validate_proposal
        try: correction = validate_proposal(correction)
        except (ValueError, TypeError) as exc: raise LearningError(422, 'invalid_correction_proposal') from exc
        _text(pack(correction), 12000)
        if correction.get('source_ref'):
            source = correction['source_ref']
            bound_source = bind_target(c, actor, pid, source['kind'], source['id'], source['version'])
            dependencies.extend(bound_source.source_refs)
    alternative = {}; status = 'recorded'
    if 'alternative_target' in payload:
        other = payload['alternative_target']; _strict(other, ('kind', 'id', 'version'))
        if other['kind'] != 'message' or target.target_kind != 'message': raise LearningError(422, 'comparison_requires_messages')
        bound = bind_target(c, actor, pid, other['kind'], other['id'], other['version'])
        comparable = bound.conversation_id == target.conversation_id and bound.request_text == target.request_text and bound.source_refs == target.source_refs
        alternative = {**other, 'comparable': comparable}
        if not comparable: status = 'needs_clarification'
    if payload['signal'] == 'comparison' and not alternative: status = 'needs_clarification'
    identifier, timestamp = uid(), now()
    c.execute('INSERT INTO learning_feedback VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (identifier, actor.owner_id, pid, cid, target.target_kind, target.target_id, target.version, pack(dependencies),
         payload['signal'], pack(dimensions), reason, pack(correction or {}), pack(alternative), scope, pack(purposes), CONSENT_POLICY,
         status, '{}', 1, idempotency_key, request_hash, timestamp, timestamp))
    _event(c, actor, pid, 'feedback_recorded', identifier, 1)
    return get_feedback(c, actor, pid, identifier)


def _feedback_current(c, actor, pid, row):
    stack = _checking_feedback.get()
    if row['id'] in stack or len(stack) >= 32:
        raise LearningError(409, 'learning_dependency_cycle_or_depth')
    token = _checking_feedback.set(stack + (row['id'],))
    try: return _feedback_current_inner(c, actor, pid, row)
    finally: _checking_feedback.reset(token)


def _feedback_current_inner(c, actor, pid, row):
    if row['status'] == 'deleted': raise LearningError(410, 'feedback_was_forgotten')
    target = bind_target(c, actor, pid, row['target_kind'], row['target_id'], row['target_version'])
    if row['conversation_id']: _conversation(c, pid, row['conversation_id'])
    refs = json.loads(row['dependencies'])
    contextual = [x for x in refs if x.get('kind') != 'file']
    if contextual and not context_lineage.authorized(c, pid, contextual): raise LearningError(409, 'feedback_source_no_longer_current')
    for ref in refs:
        if ref.get('kind') == 'file':
            bound = bind_target(c, actor, pid, 'file', ref.get('id') or ref.get('file_id'), ref.get('version'))
            if any(ref.get(k) != bound.receipt['source'].get(k) for k in ('sha256', 'extraction_sha256', 'policy_revision') if k in ref):
                raise LearningError(409, 'feedback_source_no_longer_current')
    alternative = json.loads(row['alternative'])
    if alternative:
        bind_target(c, actor, pid, alternative['kind'], alternative['id'], alternative['version'])
    return target


def record_verification(c, actor, pid, identifier, expected_version, verification):
    """Server-only caller: the HTTP API has no report-upload field or route."""
    _write(c); _version(expected_version)
    row = _row(c, actor, pid, 'learning_feedback', identifier)
    if row['version'] != expected_version: raise LearningError(409, 'feedback_version_conflict')
    target = _feedback_current(c, actor, pid, row)
    if type(verification) is not dict or verification.get('status') not in ('verified', 'disputed', 'unresolved'):
        raise LearningError(422, 'invalid_verification_receipt')
    binding = verification.get('target')
    if type(binding) is not dict or (binding.get('kind'), binding.get('id'), binding.get('version')) != (target.target_kind, target.target_id, target.version):
        raise LearningError(409, 'verification_target_mismatch')
    _text(pack(verification), 16000)
    correction = json.loads(row['correction'])
    if correction.get('source_ref'):
        ref = correction['source_ref']; bind_target(c, actor, pid, ref['kind'], ref['id'], ref['version'])
    peers = _correction_peers(c, actor, pid, row)
    c.execute('UPDATE learning_feedback SET verification=?,status=?,version=version+1,updated_at=? WHERE id=? AND version=?',
              (pack(verification), verification['status'], now(), identifier, expected_version))
    _event(c, actor, pid, 'verification_recorded', identifier, expected_version + 1)
    # A new check supersedes this record's former comparison, never a peer's
    # factual claim. Withdrawn peers do not auto-promote a quarantined result.
    c.execute('DELETE FROM learning_conflicts WHERE first_feedback=? OR second_feedback=?', (identifier, identifier))
    claim = _objective_claim(verification)
    if claim:
        for peer in peers:
            peer_claim = _objective_claim(json.loads(peer['verification']))
            if peer_claim and claim[0] == peer_claim[0] and claim[1] != peer_claim[1]:
                first, second = sorted((identifier, peer['id']))
                c.execute('INSERT OR IGNORE INTO learning_conflicts VALUES(?,?,?,?,?,?,?,?)',
                          (uid(), actor.owner_id, pid, first, second, claim[0], 1, now()))
                for fid in (identifier, peer['id']): _quarantine_feedback(c, actor, pid, fid)
    return get_feedback(c, actor, pid, identifier)


def _objective_claim(verification):
    """A narrow exact scalar claim; presentation/provenance are not its value.

    Different result fields remain independent. Different domains or conditions
    on the same field need review, not a last-write-wins factual decision.
    Source quotations and style reports are deliberately not objective claims.
    """
    output = verification.get('corrected_output')
    if verification.get('status') != 'verified' or verification.get('objective_verified') is not True or type(output) is not dict or output.get('kind') != 'exact_math':
        return None
    value, path = output.get('value'), output.get('value_path', [])
    if type(value) is not str or len(value) > 256 or not re.fullmatch(r'-?\d+(?:/[1-9]\d*)?', value): return None
    if type(path) is not list or len(path) > 12 or any(type(v) not in (str, int) for v in path): return None
    return pack(path), digest({'value': str(Fraction(value)), 'domain': output.get('domain'), 'conditions': sorted(output.get('conditions', []))})


def _correction_peers(c, actor, pid, row):
    peers = c.execute("""SELECT * FROM learning_feedback WHERE owner_id=? AND project_id=?
        AND target_kind=? AND target_id=? AND target_version=? AND id!=?
        AND status IN ('verified','conflicted') ORDER BY rowid LIMIT ?""",
        (actor.owner_id, pid, row['target_kind'], row['target_id'], row['target_version'], row['id'], MAX_CORRECTION_GROUP + 1)).fetchall()
    if len(peers) > MAX_CORRECTION_GROUP: raise LearningError(409, 'correction_comparison_limit')
    current = []
    for peer in peers:
        try: _feedback_current(c, actor, pid, peer)
        except LearningError: continue
        current.append(peer)
    return current


def _quarantine_feedback(c, actor, pid, identifier):
    row = _row(c, actor, pid, 'learning_feedback', identifier)
    if row['status'] == 'conflicted': return
    c.execute("UPDATE learning_feedback SET status='conflicted',version=version+1,updated_at=? WHERE id=?", (now(), identifier))
    for candidate in c.execute('SELECT * FROM learning_candidates WHERE feedback_id=?', (identifier,)).fetchall():
        for release in c.execute("SELECT * FROM learning_releases WHERE candidate_id=? AND status='active'", (candidate['id'],)).fetchall():
            _disable_release(c, actor, pid, release, 'revoked')
        c.execute("UPDATE learning_candidates SET status='stale',version=version+1,updated_at=? WHERE id=?", (now(), candidate['id']))
        c.execute("UPDATE learning_evaluations SET status='revoked' WHERE candidate_id=?", (candidate['id'],))
    c.execute("UPDATE learning_descendants SET status=CASE WHEN kind='model' THEN 'weights_unchanged_review_required' ELSE 'revoked' END WHERE feedback_id=?", (identifier,))
    _event(c, actor, pid, 'correction_conflict_quarantined', identifier, row['version'] + 1)


def _has_objective_conflict(c, actor, pid, row):
    """Also blocks old verified rows created before conflict tracking existed."""
    claim = _objective_claim(json.loads(row['verification']))
    if not claim: return False
    for peer in _correction_peers(c, actor, pid, row):
        other = _objective_claim(json.loads(peer['verification']))
        if other and claim[0] == other[0] and claim[1] != other[1]: return True
    return False


def validate_rule(rule):
    if type(rule) is not dict or type(rule.get('kind')) is not str: raise LearningError(422, 'invalid_lesson_rule')
    kind = rule['kind']
    if kind in ('language', 'tone', 'brevity'):
        _strict(rule, ('kind', 'value'))
        choices = {'language': {'ar', 'en', 'he'}, 'tone': {'formal', 'friendly', 'neutral'}, 'brevity': {'short', 'standard', 'detailed'}}
        if type(rule['value']) is not str or rule['value'] not in choices[kind]: raise LearningError(422, 'invalid_lesson_value')
    elif kind == 'terminology':
        _strict(rule, ('kind', 'source', 'target'))
        for key in ('source', 'target'):
            _text(rule[key], 80)
            if not re.fullmatch(r'[A-Za-z0-9\u0590-\u06ff ._/-]{1,80}', rule[key]): raise LearningError(422, 'invalid_terminology_literal')
            if not re.search(r'[A-Za-z\u0590-\u05ff\u0621-\u064a]', rule[key]): raise LearningError(422, 'numeric_terminology_rejected')
            if re.search(r'ignore|instruction|system prompt|secret|password|تجاهل|تعليمات|كلمة مرور|أرسل|ارسل|اكشف', rule[key], re.I):
                raise LearningError(422, 'instruction_like_terminology_rejected')
    else: raise LearningError(422, 'unsupported_lesson_rule')
    return dict(rule)


def validate_applicability(value):
    _strict(value, (), ('task', 'expires_at'))
    task = value.get('task', 'any')
    if task not in ('any', 'comparison', 'translation', 'math'): raise LearningError(422, 'invalid_lesson_task')
    expiry = value.get('expires_at')
    if expiry is not None:
        if type(expiry) is not str: raise LearningError(422, 'invalid_lesson_expiry')
        try:
            parsed = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
            if parsed.tzinfo is None: raise ValueError('zone required')
        except ValueError as exc: raise LearningError(422, 'invalid_lesson_expiry') from exc
    return {'task': task, 'expires_at': expiry}


def rule_key(rule): return rule['kind'] + (':' + digest(rule['source']) if rule['kind'] == 'terminology' else '')
def rule_hash(candidate): return digest({k: candidate[k] for k in ('rule', 'applicability', 'scope', 'conversation_id')})


def propose_candidate(c, actor, pid, feedback_id, expected_version, rule, applicability, scope='conversation', conversation_id=None):
    _write(c); _version(expected_version); _scope(scope)
    feedback = _row(c, actor, pid, 'learning_feedback', feedback_id)
    if feedback['version'] != expected_version: raise LearningError(409, 'feedback_version_conflict')
    _feedback_current(c, actor, pid, feedback)
    if feedback['status'] != 'verified': raise LearningError(409, 'feedback_not_verified_for_lesson')
    if _has_objective_conflict(c, actor, pid, feedback): raise LearningError(409, 'feedback_correction_conflict')
    if feedback['signal'] == 'comparison' and not json.loads(feedback['alternative']).get('comparable'):
        raise LearningError(409, 'comparison_requires_matching_context')
    if scope == 'project' and 'project_memory' not in json.loads(feedback['purposes']): raise LearningError(403, 'project_memory_consent_required')
    cid = conversation_id or feedback['conversation_id'] if scope == 'conversation' else None
    if scope == 'conversation': _conversation(c, pid, cid)
    if feedback['scope'] == 'conversation' and scope != 'conversation': raise LearningError(403, 'feedback_scope_cannot_expand')
    if feedback['scope'] == 'conversation' and cid != feedback['conversation_id']: raise LearningError(403, 'feedback_scope_cannot_expand')
    rule, applicability = validate_rule(rule), validate_applicability(applicability)
    identifier, timestamp = uid(), now()
    c.execute('INSERT INTO learning_candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (identifier, actor.owner_id, pid, feedback_id, expected_version, scope, cid, pack(rule), rule_key(rule), pack(applicability), 'proposed', 1, timestamp, timestamp))
    _event(c, actor, pid, 'candidate_proposed', identifier, 1)
    return get_candidate(c, actor, pid, identifier)


def _candidate_current(c, actor, pid, row):
    if row['status'] in ('revoked', 'stale', 'rolled_back'): raise LearningError(409, 'candidate_not_active_for_change')
    feedback = _row(c, actor, pid, 'learning_feedback', row['feedback_id'])
    if feedback['version'] != row['feedback_version']: raise LearningError(409, 'candidate_feedback_changed')
    _feedback_current(c, actor, pid, feedback)
    if feedback['status'] != 'verified' or _has_objective_conflict(c, actor, pid, feedback): raise LearningError(409, 'feedback_correction_conflict')
    return feedback


def update_candidate(c, actor, pid, identifier, expected_version, rule, applicability, **unused):
    if unused: raise LearningError(422, 'candidate_scope_is_immutable')
    _write(c); _version(expected_version)
    row = _row(c, actor, pid, 'learning_candidates', identifier); _candidate_current(c, actor, pid, row)
    if row['version'] != expected_version or row['status'] == 'active': raise LearningError(409, 'candidate_version_or_active_conflict')
    rule, applicability = validate_rule(rule), validate_applicability(applicability)
    c.execute("UPDATE learning_candidates SET rule=?,rule_key=?,applicability=?,status='proposed',version=version+1,updated_at=? WHERE id=? AND version=?", (pack(rule), rule_key(rule), pack(applicability), now(), identifier, expected_version))
    _event(c, actor, pid, 'candidate_updated', identifier, expected_version + 1)
    return get_candidate(c, actor, pid, identifier)


def record_evaluation(c, actor, pid, identifier, expected_version, evidence):
    """Only the fixed server evaluator calls this; user reports are not evidence."""
    _write(c); _version(expected_version)
    row = _row(c, actor, pid, 'learning_candidates', identifier); _candidate_current(c, actor, pid, row)
    if row['version'] != expected_version or row['status'] == 'active': raise LearningError(409, 'candidate_version_or_active_conflict')
    from backend.learning_evaluation import REQUIRED_CASE_IDS, CRITERIA, VERSION as EVAL_VERSION
    from backend.skill_release import current_source_fingerprint
    from pathlib import Path
    if type(evidence) is not dict or evidence.get('status') not in ('passed', 'failed', 'blocked'):
        raise LearningError(422, 'invalid_learning_evaluation')
    cases = evidence.get('cases', [])
    if type(cases) is not list or {v.get('id') for v in cases if type(v) is dict} != set(REQUIRED_CASE_IDS) or len(cases) != len(REQUIRED_CASE_IDS):
        raise LearningError(422, 'incomplete_learning_evaluation')
    all_pass = all(v.get('status') == 'passed' and v.get('critical') is True for v in cases)
    status = 'passed' if all_pass and evidence['status'] == 'passed' else 'blocked' if evidence['status'] == 'blocked' else 'failed'
    fingerprint = evidence.get('source_fingerprint')
    if type(fingerprint) is not str or not re.fullmatch('[0-9a-f]{64}', fingerprint): raise LearningError(422, 'evaluation_source_fingerprint_required')
    if evidence.get('version') != EVAL_VERSION or evidence.get('criteria_sha256') != digest(CRITERIA):
        raise LearningError(409, 'evaluation_criteria_changed')
    if evidence.get('candidate_sha256') != digest(get_candidate(c, actor, pid, identifier)):
        raise LearningError(409, 'evaluation_candidate_changed')
    baseline = active_rules(c, actor, pid, row['conversation_id'], '')['rules']
    if evidence.get('baseline_sha256') != digest(baseline):
        raise LearningError(409, 'evaluation_baseline_changed')
    if evidence.get('source_changed_during_run') or fingerprint != current_source_fingerprint(Path(__file__).resolve().parents[1]):
        raise LearningError(409, 'evaluation_source_changed')
    _text(pack(evidence), 32000)
    eid = uid()
    c.execute('INSERT INTO learning_evaluations VALUES(?,?,?,?,?,?,?,?)', (eid, identifier, expected_version, rule_hash(_public(row)), fingerprint, status, pack(evidence), now()))
    c.execute("UPDATE learning_candidates SET status=?,updated_at=? WHERE id=?", ('evaluated' if status == 'passed' else 'blocked', now(), identifier))
    _event(c, actor, pid, 'evaluation_recorded', identifier, expected_version)
    return {'id': eid, 'candidate_id': identifier, 'candidate_version': expected_version, 'status': status, 'evidence': evidence}


def _memory_text(rule):
    if rule['kind'] == 'terminology': return 'Project terminology: ' + pack({'source': rule['source'], 'target': rule['target']})
    return 'Project response preference: ' + rule['kind'] + '=' + rule['value']


def activate_candidate(c, actor, pid, identifier, expected_version, evaluation_id, expected_active_id=None):
    _write(c); _version(expected_version)
    row = _row(c, actor, pid, 'learning_candidates', identifier); feedback = _candidate_current(c, actor, pid, row)
    if row['version'] != expected_version or row['status'] != 'evaluated': raise LearningError(409, 'candidate_not_evaluated_at_version')
    evaluation = c.execute('SELECT * FROM learning_evaluations WHERE id=? AND candidate_id=?', (evaluation_id, identifier)).fetchone()
    if not evaluation or evaluation['status'] != 'passed' or evaluation['candidate_version'] != expected_version or evaluation['rule_hash'] != rule_hash(_public(row)):
        raise LearningError(409, 'candidate_evaluation_not_current')
    from backend.learning_evaluation import CRITERIA, REQUIRED_CASE_IDS
    receipt = json.loads(evaluation['evidence'])
    cases = receipt.get('cases', [])
    if receipt.get('criteria_sha256') != digest(CRITERIA) or receipt.get('status') != 'passed' or len(cases) != len(REQUIRED_CASE_IDS) or {x.get('id') for x in cases} != set(REQUIRED_CASE_IDS) or any(x.get('status') != 'passed' or x.get('critical') is not True for x in cases):
        raise LearningError(409, 'evaluation_criteria_or_outcome_changed')
    if receipt.get('baseline_sha256') != digest(active_rules(c, actor, pid, row['conversation_id'], '')['rules']):
        raise LearningError(409, 'evaluation_baseline_changed')
    from backend.skill_release import current_source_fingerprint
    from pathlib import Path
    if evaluation['source_fingerprint'] != current_source_fingerprint(Path(__file__).resolve().parents[1]):
        raise LearningError(409, 'evaluation_source_changed')
    if row['scope'] == 'project' and 'project_memory' not in json.loads(feedback['purposes']): raise LearningError(403, 'project_memory_consent_required')
    applicability = json.loads(row['applicability'])
    if applicability['expires_at'] and datetime.fromisoformat(applicability['expires_at'].replace('Z', '+00:00')) <= datetime.now(timezone.utc):
        raise LearningError(409, 'candidate_expired')
    scope_key = row['conversation_id'] if row['scope'] == 'conversation' else '@project'
    previous = c.execute("SELECT * FROM learning_releases WHERE owner_id=? AND project_id=? AND scope_key=? AND rule_key=? AND status='active'", (actor.owner_id, pid, scope_key, row['rule_key'])).fetchone()
    if (previous['id'] if previous else None) != expected_active_id: raise LearningError(409, 'active_lesson_changed')
    if previous: _disable_release(c, actor, pid, previous, 'superseded')
    rid, memory_id, memory_hash = uid(), None, None
    if row['scope'] == 'project':
        memory_id = uid(); content = _memory_text(json.loads(row['rule'])); memory_hash = digest(content)
        c.execute('INSERT INTO memories VALUES(?,?,?,?,?)', (memory_id, 'project', pid, content, now()))
    c.execute('INSERT INTO learning_releases VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (rid, actor.owner_id, pid, identifier, expected_version, evaluation_id, scope_key, row['rule_key'], memory_id, memory_hash, 'active', 1, now(), now()))
    c.execute("UPDATE learning_candidates SET status='active',updated_at=? WHERE id=?", (now(), identifier))
    _event(c, actor, pid, 'lesson_activated', rid, 1)
    return get_release(c, actor, pid, rid)


def _disable_release(c, actor, pid, row, status):
    c.execute('UPDATE learning_releases SET status=?,version=version+1,updated_at=? WHERE id=?', (status, now(), row['id']))
    if row['memory_id']:
        memory = c.execute('SELECT content FROM memories WHERE id=? AND project_id=?', (row['memory_id'], pid)).fetchone()
        if memory and digest(memory['content']) == row['memory_hash']:
            c.execute('DELETE FROM memories WHERE id=?', (row['memory_id'],))
    # Learning has always supplied versioned bindings. Their authorization now
    # fails; unrelated source answers and independent lessons remain valid.


def rollback_release(c, actor, pid, identifier, expected_version):
    _write(c); _version(expected_version)
    row = _row(c, actor, pid, 'learning_releases', identifier)
    if row['version'] != expected_version or row['status'] != 'active': raise LearningError(409, 'release_version_or_state_conflict')
    _disable_release(c, actor, pid, row, 'rolled_back')
    c.execute("UPDATE learning_candidates SET status='rolled_back',version=version+1,updated_at=? WHERE id=?", (now(), row['candidate_id']))
    _event(c, actor, pid, 'lesson_rolled_back', identifier, expected_version + 1)
    return get_release(c, actor, pid, identifier)


def revoke_candidate(c, actor, pid, identifier, expected_version):
    _write(c); _version(expected_version)
    row = _row(c, actor, pid, 'learning_candidates', identifier)
    if row['version'] != expected_version: raise LearningError(409, 'candidate_version_conflict')
    for release in c.execute("SELECT * FROM learning_releases WHERE candidate_id=? AND status='active'", (identifier,)).fetchall():
        _disable_release(c, actor, pid, release, 'revoked')
    c.execute("UPDATE learning_candidates SET status='revoked',rule='{}',version=version+1,updated_at=? WHERE id=?", (now(), identifier))
    c.execute('INSERT OR REPLACE INTO learning_tombstones VALUES(?,?,?,?,?,?)', ('candidate:' + identifier, actor.owner_id, pid, expected_version + 1, 'forgotten', now()))
    c.execute("UPDATE learning_evaluations SET status='revoked',evidence='{}' WHERE candidate_id=?", (identifier,))
    _event(c, actor, pid, 'candidate_forgotten', identifier, expected_version + 1)
    return get_candidate(c, actor, pid, identifier)


def delete_feedback(c, actor, pid, identifier, expected_version):
    _write(c); _version(expected_version)
    row = _row(c, actor, pid, 'learning_feedback', identifier)
    if row['version'] != expected_version: raise LearningError(409, 'feedback_version_conflict')
    c.execute("UPDATE learning_feedback SET status='deleted',reason='',correction='{}',verification='{}',alternative='{}',version=version+1,updated_at=? WHERE id=?", (now(), identifier))
    _event(c, actor, pid, 'feedback_forgotten', identifier, expected_version + 1)
    return get_feedback(c, actor, pid, identifier)


def managed_memory_ids(c, pid):
    return {r[0] for r in c.execute("SELECT memory_id FROM learning_releases WHERE project_id=? AND memory_id IS NOT NULL", (pid,))}


def resolve_rules(rules, question, conversation_id, *, task=None, at=None):
    """Pure selection over already-authorized typed rows. Never grants access."""
    stamp = datetime.now(timezone.utc) if at is None else datetime.fromisoformat(at.replace('Z', '+00:00')) if type(at) is str else at
    if task is None:
        task = 'comparison' if re.search(r'قارن|compare|comparison', question, re.I) else 'translation' if re.search(r'ترجم|translate|translation', question, re.I) else 'math' if re.search(r'احسب|حل\b|solve|calculate', question, re.I) else 'any'
    overrides = set()
    if re.search(r'بالعربية|بالإنجليزية|بالانجليزية|بالعبرية|\bin (?:Arabic|English|Hebrew)\b', question, re.I): overrides.add('language')
    if re.search(r'رسمي|ودي|ودّي|محايد|\b(?:formal|friendly|neutral)\b', question, re.I): overrides.add('tone')
    if re.search(r'مختصر|بالتفصيل|\b(?:short|detailed)\b', question, re.I): overrides.add('brevity')
    selected, suppressed = {}, []
    # Stable ordering preserves the server's baseline-then-proposal sequence.
    # Versions belong to separate candidates and cannot order their priority.
    for item in sorted(rules, key=lambda r: r.get('scope') == 'conversation'):
        try:
            rule = validate_rule(item['rule']); app = validate_applicability(item.get('applicability', {}))
        except (LearningError, KeyError): continue
        reason = None
        if item.get('scope') not in ('project', 'conversation'): reason = 'unsupported_scope'
        elif item['scope'] == 'conversation' and item.get('conversation_id') != conversation_id: reason = 'conversation_scope'
        elif app['task'] not in ('any', task): reason = 'task_scope'
        elif app['expires_at'] and datetime.fromisoformat(app['expires_at'].replace('Z', '+00:00')) <= stamp: reason = 'expired'
        elif rule['kind'] in overrides: reason = 'current_request_override'
        if reason:
            suppressed.append({'id': item.get('id'), 'reason': reason}); continue
        key = rule_key(rule)
        if key in selected: suppressed.append({'id': selected[key].get('id'), 'reason': 'more_specific_scope'})
        selected[key] = item
    values = list(selected.values())
    if len(values) > MAX_ACTIVE_RULES:
        suppressed.extend({'id': item.get('id'), 'reason': 'context_budget'} for item in values[:-MAX_ACTIVE_RULES])
        values = values[-MAX_ACTIVE_RULES:]
    return {'rules': values, 'suppressed': suppressed}


def _release_current(c, actor, pid, release, conversation_id=None):
    if release['status'] != 'active': raise LearningError(409, 'release_not_active')
    candidate = _row(c, actor, pid, 'learning_candidates', release['candidate_id'])
    feedback = _candidate_current(c, actor, pid, candidate)
    if candidate['version'] != release['candidate_version'] or candidate['status'] != 'active': raise LearningError(409, 'release_candidate_changed')
    if candidate['scope'] == 'conversation' and conversation_id is not None and candidate['conversation_id'] != conversation_id: raise LearningError(404, 'learning_resource_unavailable')
    if candidate['scope'] == 'project':
        if 'project_memory' not in json.loads(feedback['purposes']): raise LearningError(409, 'lesson_consent_withdrawn')
        memory = c.execute("SELECT content FROM memories WHERE id=? AND project_id=? AND scope='project'", (release['memory_id'], pid)).fetchone()
        if not memory or digest(memory['content']) != release['memory_hash']: raise LearningError(409, 'lesson_memory_changed')
    app = json.loads(candidate['applicability'])
    if app['expires_at'] and datetime.fromisoformat(app['expires_at'].replace('Z', '+00:00')) <= datetime.now(timezone.utc): raise LearningError(409, 'lesson_expired')
    return candidate, feedback


def active_rules(c, actor, pid, conversation_id, question):
    _actor(c, actor, pid)
    if conversation_id is not None: _conversation(c, pid, conversation_id)
    rows, bindings = [], {}
    for release in c.execute("SELECT * FROM learning_releases WHERE owner_id=? AND project_id=? AND status='active' ORDER BY created_at", (actor.owner_id, pid)).fetchall():
        try: candidate, feedback = _release_current(c, actor, pid, release, conversation_id)
        except LearningError: continue
        row = _public(candidate); row['release_id'] = release['id']; row['memory_id'] = release['memory_id']; rows.append(row)
        bindings[row['id']] = {'kind': 'learning_release', 'id': release['id'], 'version': release['version'],
            'candidate_version': candidate['version'], 'target_version': feedback['target_version'], 'rule_hash': rule_hash(row)}
    selected = resolve_rules(rows, question, conversation_id)
    return {'rules': selected['rules'], 'bindings': [bindings[r['id']] for r in selected['rules']], 'suppressed': selected['suppressed']}


def authorize_bindings(c, actor, pid, bindings, conversation_id=None):
    try:
        _actor(c, actor, pid)
        if type(bindings) is not list or len(bindings) > 64: return False
        for binding in bindings:
            if type(binding) is dict and binding.get('kind') == 'learning_feedback':
                _strict(binding, ('kind', 'id', 'version', 'target_version', 'conversation_id'))
                feedback = _row(c, actor, pid, 'learning_feedback', binding['id'])
                _feedback_current(c, actor, pid, feedback)
                verified = json.loads(feedback['verification'])
                if feedback['status'] != 'verified' or verified.get('objective_verified') is not True or not verified.get('corrected_output'): return False
                if 'current_task' not in json.loads(feedback['purposes']) or _has_objective_conflict(c, actor, pid, feedback): return False
                if (feedback['version'], feedback['target_version'], feedback['conversation_id']) != (binding['version'], binding['target_version'], binding['conversation_id']): return False
                if conversation_id is not None and feedback['conversation_id'] != conversation_id: return False
                continue
            _strict(binding, ('kind', 'id', 'version', 'candidate_version', 'target_version', 'rule_hash'))
            if binding['kind'] != 'learning_release': return False
            release = _row(c, actor, pid, 'learning_releases', binding['id'])
            candidate, feedback = _release_current(c, actor, pid, release, conversation_id)
            if (release['version'], candidate['version'], feedback['target_version'], rule_hash(_public(candidate))) != (binding['version'], binding['candidate_version'], binding['target_version'], binding['rule_hash']): return False
        return True
    except (LearningError, guard.Denied, TypeError, ValueError): return False


def verified_corrections(c, actor, pid, conversation_id):
    """Five current, objective corrections for this conversation, never memory.

    Raw user explanations are not instructions. Only the fixed server verifier's
    bounded structured result is returned; sources and access are checked now.
    """
    _actor(c, actor, pid); _conversation(c, pid, conversation_id)
    corrections, bindings = [], []
    rows = c.execute("SELECT * FROM learning_feedback WHERE owner_id=? AND project_id=? AND conversation_id=? AND status='verified' ORDER BY rowid DESC LIMIT 100", (actor.owner_id, pid, conversation_id)).fetchall()
    for row in rows:
        try:
            _feedback_current(c, actor, pid, row)
            if _has_objective_conflict(c, actor, pid, row): continue
        except LearningError: continue
        if 'current_task' not in json.loads(row['purposes']): continue
        verification = json.loads(row['verification'])
        output = verification.get('corrected_output')
        if verification.get('objective_verified') is not True or type(output) is not dict or not output or len(pack(output)) > 4000:
            continue
        corrections.append({'id': row['id'], 'target': {'kind': row['target_kind'], 'id': row['target_id'], 'version': row['target_version']},
                            'corrected_output': output, 'verification_scope': verification.get('verification_scope'), 'source': 'server_verified_current_conversation'})
        bindings.append({'kind':'learning_feedback', 'id':row['id'], 'version':row['version'], 'target_version':row['target_version'], 'conversation_id':conversation_id})
        if len(corrections) == 5: break
    return {'corrections': corrections, 'bindings': bindings}


def authorize_context_links(c, pid, bindings, conversation_id=None):
    from fastapi import HTTPException
    try: principal = local_auth.current_principal()
    except HTTPException: return False
    return authorize_bindings(c, guard.Actor(principal['owner_id'], principal['session_id']), pid, bindings, conversation_id)


def withdraw_consent(c, actor, pid, identifier, expected_version, purposes):
    _write(c); _version(expected_version)
    row = _row(c, actor, pid, 'learning_feedback', identifier)
    if row['version'] != expected_version: raise LearningError(409, 'feedback_version_conflict')
    if type(purposes) is not list or any(type(p) is not str or p not in PURPOSES for p in purposes): raise LearningError(422, 'invalid_learning_purposes')
    remaining = set(json.loads(row['purposes'])) - set(purposes)
    c.execute('UPDATE learning_feedback SET purposes=?,version=version+1,updated_at=? WHERE id=?', (pack(sorted(remaining)), now(), identifier))
    for candidate in c.execute('SELECT * FROM learning_candidates WHERE feedback_id=?', (identifier,)).fetchall():
        for release in c.execute("SELECT * FROM learning_releases WHERE candidate_id=? AND status='active'", (candidate['id'],)).fetchall(): _disable_release(c, actor, pid, release, 'revoked')
        c.execute("UPDATE learning_candidates SET status='stale',version=version+1,updated_at=? WHERE id=?", (now(), candidate['id']))
    c.execute("UPDATE learning_descendants SET status=CASE WHEN kind='model' THEN 'weights_unchanged_review_required' ELSE 'revoked' END WHERE feedback_id=?", (identifier,))
    _event(c, actor, pid, 'consent_withdrawn', identifier, expected_version + 1)
    return get_feedback(c, actor, pid, identifier)


def consented_feedback(c, actor, pid, purpose):
    if purpose not in ('training', 'product_improvement'): raise LearningError(422, 'invalid_export_purpose')
    return [row for row in list_feedback(c, actor, pid) if row['status'] not in ('deleted', 'stale') and purpose in row['purposes']]


def register_descendant(c, actor, pid, feedback_id, kind, resource_ref):
    """Register an existing server-created artifact/model reference; starts nothing."""
    _write(c); row = _row(c, actor, pid, 'learning_feedback', feedback_id); _feedback_current(c, actor, pid, row)
    if 'training' not in json.loads(row['purposes']): raise LearningError(403, 'training_consent_required')
    if kind not in ('dataset', 'model'): raise LearningError(422, 'invalid_learning_descendant')
    _text(resource_ref, 100)
    identifier = uid()
    c.execute('INSERT INTO learning_descendants VALUES(?,?,?,?,?,?)', (identifier, feedback_id, kind, resource_ref, 'registered', now()))
    return {'id': identifier, 'kind': kind, 'resource_ref': resource_ref, 'status': 'registered', 'created_training_job': False}
