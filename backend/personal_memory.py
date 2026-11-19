"""Owner-bound personal presentation preferences, staged behind Stage8.

There is no activation flag, environment bypass, report-upload endpoint, or
automatic claim of independent security review. The production admission gate
is closed. Tests may explicitly simulate admission to exercise the otherwise
independent persistence, consent, invalidation and selection contracts.
"""
from __future__ import annotations

import json
from backend import guard, learning, local_auth
from backend.db import now, uid

VERSION = 'personal-memory-v1'
POLICY_VERSION = 'personal-consent-v1'
MAX_ENTRIES = 100


class PersonalMemoryError(ValueError):
    def __init__(self, status_code, code):
        self.status_code, self.code = status_code, code
        super().__init__(code)


def gate_status():
    return {'available':False, 'code':'stage8_security_acceptance_required',
            'scope':'personal_memory_write_and_model_context', 'policy_version':POLICY_VERSION,
            'blockers':[
                {'id':'independent_security_review','status':'not_evidenced','source':'docs/RIWAQ_SECURITY_ACCEPTANCE.md'},
                {'id':'complete_scoped_worker_and_data_policy_acceptance','status':'not_accepted','source':'docs/RIWAQ_SECURITY.md'},
                {'id':'native_acceptance_where_required','status':'user_deferred','source':'docs/DESKTOP.md'}],
            'read_and_delete_existing':True, 'environment_override':False}


def _actor(c, actor, project_id=None):
    if not isinstance(actor, guard.Actor) or not local_auth.session_active(c, actor.owner_id, actor.session_id):
        raise PersonalMemoryError(401, 'personal_memory_session_inactive')
    if project_id is not None:
        try: guard.check_actor(c, actor, project_id)
        except guard.Denied as exc: raise PersonalMemoryError(404, 'personal_memory_resource_unavailable') from exc


def require_admission(c, actor):
    _actor(c, actor)
    raise PersonalMemoryError(423, 'stage8_security_acceptance_required')


def _write(c):
    if not c.in_transaction: c.execute('BEGIN IMMEDIATE')


def _version(value):
    if type(value) is not int or value < 1: raise PersonalMemoryError(422, 'expected_version_required')


def _rule(value):
    try: return learning.validate_rule(value)
    except learning.LearningError as exc: raise PersonalMemoryError(exc.status_code, exc.code) from exc


def _audit(c, actor, identifier, operation):
    guard.audit(c, actor, None, identifier or 'personal_settings', 'personal_memory_' + operation, 'allow', 'explicit_versioned_owner_action')


def init_personal_memory(c):
    """Add metadata without altering the legacy memories table or its values."""
    c.executescript('''
    CREATE TABLE IF NOT EXISTS personal_memory_settings(
      owner_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN(0,1)),
      version INTEGER NOT NULL DEFAULT 1,consent_epoch INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS personal_memory_entries(
      memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,owner_id TEXT NOT NULL,
      rule TEXT NOT NULL,content_sha256 TEXT NOT NULL,status TEXT NOT NULL,consent_epoch INTEGER NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS personal_memory_requests(
      owner_id TEXT NOT NULL,request_key TEXT NOT NULL,request_sha256 TEXT NOT NULL,memory_id TEXT NOT NULL,
      created_at TEXT NOT NULL,PRIMARY KEY(owner_id,request_key));
    CREATE TABLE IF NOT EXISTS personal_memory_tombstones(
      memory_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,version INTEGER NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TRIGGER IF NOT EXISTS personal_memory_inserted AFTER INSERT ON memories
    WHEN NEW.scope='preference' AND NEW.project_id IS NULL BEGIN
      INSERT OR IGNORE INTO personal_memory_entries
        SELECT NEW.id,owner_id,'{}','','legacy_read_only',0,NEW.created_at FROM local_auth_owner WHERE slot=1;
    END;
    CREATE TRIGGER IF NOT EXISTS personal_memory_deleted BEFORE DELETE ON memories
    WHEN OLD.scope='preference' BEGIN
      INSERT OR REPLACE INTO personal_memory_tombstones
        SELECT OLD.id,p.owner_id,COALESCE(r.version,0)+1,'deleted',strftime('%Y-%m-%dT%H:%M:%fZ','now')
        FROM personal_memory_entries p LEFT JOIN memory_revisions r ON r.memory_id=p.memory_id WHERE p.memory_id=OLD.id;
    END;
    CREATE TRIGGER IF NOT EXISTS personal_memory_edited AFTER UPDATE OF content,scope,project_id ON memories BEGIN
      UPDATE personal_memory_entries SET status='stale',rule='{}' WHERE memory_id=OLD.id;
    END;
    ''')
    owner = c.execute('SELECT owner_id FROM local_auth_owner WHERE slot=1').fetchone()
    if not owner: return
    c.execute('INSERT OR IGNORE INTO personal_memory_settings VALUES(?,0,1,1,?)', (owner[0],now()))
    # Existing local-only legacy rows are attached once to the already-established
    # installation owner, never to each newly authenticated session. No auto-use.
    for row in c.execute("SELECT * FROM memories WHERE scope='preference' AND project_id IS NULL").fetchall():
        if c.execute('SELECT 1 FROM personal_memory_tombstones WHERE memory_id=?', (row['id'],)).fetchone(): continue
        c.execute('INSERT OR IGNORE INTO personal_memory_entries VALUES(?,?,?,?,?,?,?)',
            (row['id'],owner[0],'{}',learning.digest(row['content']),'legacy_read_only',0,row['created_at']))


def get_settings(c, actor):
    _actor(c, actor)
    row = c.execute('SELECT * FROM personal_memory_settings WHERE owner_id=?', (actor.owner_id,)).fetchone()
    return {'enabled':bool(row['enabled']) if row else False,'version':row['version'] if row else 1,
            'consent_epoch':row['consent_epoch'] if row else 1,'policy_version':POLICY_VERSION,
            'automatic_saving':False,'gate':gate_status()}


def set_enabled(c, actor, enabled, expected_version):
    _write(c); _actor(c, actor); _version(expected_version)
    if type(enabled) is not bool: raise PersonalMemoryError(422, 'enabled_must_be_boolean')
    if enabled: require_admission(c, actor)
    prior = get_settings(c, actor)
    if prior['version'] != expected_version: raise PersonalMemoryError(409, 'personal_consent_version_conflict')
    c.execute('INSERT OR IGNORE INTO personal_memory_settings VALUES(?,0,1,1,?)', (actor.owner_id,now()))
    c.execute('UPDATE personal_memory_settings SET enabled=?,version=version+1,consent_epoch=consent_epoch+1,updated_at=? WHERE owner_id=? AND version=?', (int(enabled),now(),actor.owner_id,expected_version))
    _audit(c, actor, None, 'enabled' if enabled else 'disabled')
    return get_settings(c, actor)


def _consent(c, actor, expected):
    _version(expected)
    policy = get_settings(c, actor)
    if policy['version'] != expected: raise PersonalMemoryError(409, 'personal_consent_version_conflict')
    if not policy['enabled']: raise PersonalMemoryError(409, 'personal_memory_opted_out')
    return policy


def _row(c, actor, identifier):
    _actor(c, actor)
    row = c.execute('''SELECT m.*,r.version,r.updated_at,p.owner_id,p.rule,p.content_sha256,p.status,p.consent_epoch
      FROM memories m JOIN memory_revisions r ON r.memory_id=m.id JOIN personal_memory_entries p ON p.memory_id=m.id
      WHERE m.id=? AND p.owner_id=? AND m.scope='preference' AND m.project_id IS NULL''', (identifier,actor.owner_id)).fetchone()
    if row is None: raise PersonalMemoryError(404, 'personal_memory_resource_unavailable')
    return row


def get_entry(c, actor, identifier):
    row = _row(c, actor, identifier)
    valid = learning.digest(row['content']) == row['content_sha256'] or (row['status']=='legacy_read_only' and row['content_sha256']=='')
    return {'id':row['id'],'scope':'preference','project_id':None,'content':row['content'],
            'rule':json.loads(row['rule']) if valid else {},'status':row['status'] if valid else 'stale',
            'version':row['version'],'updated_at':row['updated_at'],'created_at':row['created_at'],
            'consent_epoch':row['consent_epoch'],'managed':True,'gate':gate_status()}


def list_entries(c, actor):
    _actor(c, actor)
    ids = [r[0] for r in c.execute('SELECT memory_id FROM personal_memory_entries WHERE owner_id=? ORDER BY rowid LIMIT ?', (actor.owner_id,MAX_ENTRIES))]
    result=[]
    for identifier in ids:
        try: result.append(get_entry(c,actor,identifier))
        except PersonalMemoryError as exc:
            if exc.status_code != 404: raise
    return result


def create_entry(c, actor, rule, idempotency_key, consent_version):
    _write(c); _actor(c, actor); require_admission(c, actor)
    rule = _rule(rule)
    if type(idempotency_key) is not str or not 8 <= len(idempotency_key) <= 100 or any(ord(ch)<33 for ch in idempotency_key):
        raise PersonalMemoryError(422, 'invalid_personal_request_key')
    request_sha = learning.digest({'rule':rule,'consent_version':consent_version})
    request_key = learning.digest(idempotency_key)
    prior = c.execute('SELECT * FROM personal_memory_requests WHERE owner_id=? AND request_key=?', (actor.owner_id,request_key)).fetchone()
    if prior:
        if prior['request_sha256'] != request_sha: raise PersonalMemoryError(409, 'personal_request_payload_conflict')
        if c.execute('SELECT 1 FROM personal_memory_tombstones WHERE memory_id=?', (prior['memory_id'],)).fetchone(): raise PersonalMemoryError(410, 'personal_memory_was_deleted')
        return get_entry(c,actor,prior['memory_id'])
    policy = _consent(c,actor,consent_version)
    _unique_rule(c,actor,rule)
    if c.execute('SELECT count(*) FROM personal_memory_entries WHERE owner_id=?', (actor.owner_id,)).fetchone()[0] >= MAX_ENTRIES:
        raise PersonalMemoryError(409, 'personal_memory_limit')
    identifier,stamp = uid(),now(); content = learning.pack({'personal_rule':rule})
    c.execute('INSERT INTO memories VALUES(?,?,?,?,?)', (identifier,'preference',None,content,stamp))
    c.execute('INSERT OR REPLACE INTO personal_memory_entries VALUES(?,?,?,?,?,?,?)', (identifier,actor.owner_id,learning.pack(rule),learning.digest(content),'active',policy['consent_epoch'],stamp))
    c.execute('INSERT INTO personal_memory_requests VALUES(?,?,?,?,?)', (actor.owner_id,request_key,request_sha,identifier,stamp))
    _audit(c,actor,identifier,'created')
    return get_entry(c,actor,identifier)


def update_entry(c, actor, identifier, rule, expected_version, consent_version):
    _write(c); _actor(c,actor); require_admission(c,actor); _version(expected_version)
    policy = _consent(c,actor,consent_version); row = _row(c,actor,identifier)
    if row['version'] != expected_version: raise PersonalMemoryError(409, 'personal_memory_version_conflict')
    rule = _rule(rule); _unique_rule(c,actor,rule,identifier); content = learning.pack({'personal_rule':rule})
    c.execute('UPDATE memories SET content=? WHERE id=?', (content,identifier))
    c.execute("UPDATE personal_memory_entries SET rule=?,content_sha256=?,status='active',consent_epoch=? WHERE memory_id=? AND owner_id=?", (learning.pack(rule),learning.digest(content),policy['consent_epoch'],identifier,actor.owner_id))
    _audit(c,actor,identifier,'updated')
    return get_entry(c,actor,identifier)


def _unique_rule(c,actor,rule,identifier=None):
    for row in c.execute("SELECT memory_id,rule FROM personal_memory_entries WHERE owner_id=? AND status='active'",(actor.owner_id,)):
        if row['memory_id']!=identifier and learning.rule_key(json.loads(row['rule']))==learning.rule_key(rule):
            raise PersonalMemoryError(409,'personal_rule_exists_edit_existing')


def delete_entry(c, actor, identifier, expected_version):
    _write(c); _actor(c,actor); _version(expected_version)
    row = _row(c,actor,identifier)
    if row['version'] != expected_version: raise PersonalMemoryError(409, 'personal_memory_version_conflict')
    c.execute('DELETE FROM memories WHERE id=?', (identifier,))
    _audit(c,actor,identifier,'deleted')
    return {'deleted':True,'id':identifier,'version':expected_version+1,'weights_changed':False}


def active_context(c, actor, project_id, conversation_id, question):
    _actor(c,actor,project_id)
    if not c.execute('SELECT 1 FROM conversations WHERE id=? AND project_id=?',(conversation_id,project_id)).fetchone():
        raise PersonalMemoryError(404,'personal_memory_resource_unavailable')
    try: require_admission(c,actor)
    except PersonalMemoryError as exc:
        if exc.status_code != 423: raise
        return {'rules':[],'bindings':[],'suppressed':[],'gate':gate_status()}
    policy = get_settings(c,actor)
    if not policy['enabled']: return {'rules':[],'bindings':[],'suppressed':[],'gate':gate_status()}
    rows=[]; bindings={}
    for row in list_entries(c,actor):
        if row['status'] != 'active': continue
        try: rule = _rule(row['rule'])
        except PersonalMemoryError: continue
        item={'id':row['id'],'version':row['version'],'scope':'personal','conversation_id':None,
              'rule':rule,'applicability':{'task':'any','expires_at':None}}
        rows.append(item)
        bindings[row['id']]={'kind':'personal_memory','id':row['id'],'version':row['version'],
                            'content_sha256':learning.digest(row['content']),'consent_epoch':policy['consent_epoch']}
    selected=merge_rules(rows,[],question,conversation_id)
    return {'rules':selected['rules'],'bindings':[bindings[r['id']] for r in selected['rules']],
            'suppressed':selected['suppressed'],'gate':gate_status()}


def merge_rules(personal_rules, scoped_rules, question, conversation_id):
    """Lowest-priority personal defaults, then project and conversation rules."""
    originals={r['id']:r for r in personal_rules+scoped_rules}
    defaults=[dict(r,scope='project') for r in personal_rules]
    chosen=learning.resolve_rules(defaults+scoped_rules,question,conversation_id)
    return {'rules':[originals[r['id']] for r in chosen['rules']],'suppressed':chosen['suppressed']}


def authorize_bindings(c, actor, project_id, bindings, conversation_id=None):
    try:
        _actor(c,actor,project_id); require_admission(c,actor)
        policy=get_settings(c,actor)
        if not policy['enabled'] or type(bindings) is not list or len(bindings)>20: return False
        for binding in bindings:
            if type(binding) is not dict or set(binding)!={'kind','id','version','content_sha256','consent_epoch'} or binding['kind']!='personal_memory': return False
            row=_row(c,actor,binding['id'])
            if row['status']!='active' or row['version']!=binding['version'] or row['content_sha256']!=binding['content_sha256'] or learning.digest(row['content'])!=binding['content_sha256'] or policy['consent_epoch']!=binding['consent_epoch']: return False
            _rule(json.loads(row['rule']))
        return True
    except (PersonalMemoryError,ValueError,TypeError): return False


def authorize_context_links(c, project_id, bindings, conversation_id=None):
    from fastapi import HTTPException
    try: principal=local_auth.current_principal()
    except HTTPException: return False
    return authorize_bindings(c,guard.Actor(principal['owner_id'],principal['session_id']),project_id,bindings,conversation_id)


def quarantine(c):
    """Used only by isolated recovery; later deletion reconciliation is separate."""
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    counts={}
    if 'personal_memory_settings' in tables:
        counts['settings']=c.execute('UPDATE personal_memory_settings SET enabled=0,version=version+1,consent_epoch=consent_epoch+1').rowcount
    if 'personal_memory_entries' in tables:
        counts['entries']=c.execute("UPDATE personal_memory_entries SET status='quarantined' WHERE status='active'").rowcount
    return counts
