"""Riwaq Guard v1: durable, single-use operation permits and inherited data policy.

This is an application boundary, not a network sandbox or a multi-user host.
Permits never leave this module's server-side caller. Audit records omit payloads,
URLs, filenames, credentials and permit values. SQLite is the shared arbiter.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass

from fastapi import HTTPException
from backend.db import get_db, now, uid

POLICY_VERSION = 'riwaq-guard-1'
LOCAL_DESTINATION = 'local:ollama'
MAX_SUMMARY_BYTES = 10000


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(packed(value).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class Actor:
    owner_id: str
    session_id: str


class Denied(Exception):
    def __init__(self, reason, decision_id=None):
        self.reason, self.decision_id = reason, decision_id
        super().__init__(reason)


def init(conn):
    from backend.local_auth import owner_id
    owner = owner_id(conn)
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS guard_projects(project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,owner_id TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS guard_file_policies(file_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,owner_id TEXT NOT NULL,
      sensitivity TEXT NOT NULL,trust_origin TEXT NOT NULL,destinations TEXT NOT NULL,retention TEXT NOT NULL,
      revision INTEGER NOT NULL DEFAULT 1,revoked INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS guard_control(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS guard_rate_state(owner_id TEXT PRIMARY KEY,window INTEGER NOT NULL,count INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS guard_permits(token_hash TEXT PRIMARY KEY,owner_id TEXT NOT NULL,session_id TEXT NOT NULL,
      project_id TEXT NOT NULL,task_id TEXT NOT NULL,operation TEXT NOT NULL,destination TEXT NOT NULL,
      parameters_hash TEXT NOT NULL,resources TEXT NOT NULL,budget INTEGER NOT NULL,expires REAL NOT NULL,
      policy_version TEXT NOT NULL,state TEXT NOT NULL,decision_id TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS guard_audit(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,project_id TEXT,
      task_id TEXT NOT NULL,operation TEXT NOT NULL,decision TEXT NOT NULL,reason TEXT NOT NULL,
      policy_version TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS guard_results(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,session_id TEXT NOT NULL,
      project_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,request_hash TEXT NOT NULL,resources TEXT NOT NULL,
      policy TEXT NOT NULL,status TEXT NOT NULL,content TEXT NOT NULL DEFAULT '',decision_id TEXT,
      model TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
      UNIQUE(owner_id,idempotency_key));
    ''')
    conn.execute("INSERT OR IGNORE INTO guard_control VALUES('local_summary','enabled')")
    conn.execute('INSERT OR IGNORE INTO guard_projects SELECT id,? FROM projects', (owner,))
    # Triggers cover old and new upload APIs, snapshots and late deletion in the
    # same transaction as the original. A deleted ID cannot silently reactivate.
    conn.executescript('''
    CREATE TRIGGER IF NOT EXISTS guard_project_insert AFTER INSERT ON projects BEGIN
      INSERT INTO guard_projects(project_id,owner_id) SELECT NEW.id,value FROM guard_control WHERE key='local_owner';
    END;
    CREATE TRIGGER IF NOT EXISTS guard_file_insert AFTER INSERT ON knowledge_files BEGIN
      INSERT OR IGNORE INTO guard_file_policies(file_id,project_id,owner_id,sensitivity,trust_origin,destinations,retention)
      SELECT NEW.id,NEW.project_id,owner_id,'private','user_file','["local:ollama"]','until_user_deletion' FROM guard_projects WHERE project_id=NEW.project_id;
    END;
    CREATE TRIGGER IF NOT EXISTS guard_file_delete AFTER DELETE ON knowledge_files BEGIN
      UPDATE guard_file_policies SET revoked=1,revision=revision+1 WHERE file_id=OLD.id;
      UPDATE guard_results SET status='revoked',content='' WHERE id IN
        (SELECT r.id FROM guard_results r,json_each(r.resources) j WHERE json_extract(j.value,'$.file_id')=OLD.id);
    END;
    ''')
    conn.execute("INSERT OR REPLACE INTO guard_control VALUES('local_owner',?)", (owner,))
    conn.execute('''INSERT OR IGNORE INTO guard_file_policies(file_id,project_id,owner_id,sensitivity,trust_origin,destinations,retention)
      SELECT f.id,f.project_id,p.owner_id,'private','user_file','["local:ollama"]','until_user_deletion'
      FROM knowledge_files f JOIN guard_projects p ON p.project_id=f.project_id''')
    reap_expired(conn)


def reap_expired(conn):
    # Lease expiry, not process startup, decides abandonment: another service
    # process may still own a valid claim. Never replay an uncertain operation.
    conn.execute("UPDATE guard_results SET status='interrupted',content='',updated_at=? WHERE status IN ('pending','running') AND (id IN (SELECT task_id FROM guard_permits WHERE expires<=?) OR julianday(created_at)<julianday('now','-120 seconds'))",(now(),time.time()))
    conn.execute("UPDATE guard_permits SET state=CASE WHEN state='claimed' THEN 'unknown' ELSE 'expired' END WHERE state IN ('pending','claimed') AND expires<=?",(time.time(),))


def audit(conn, actor, project, task, operation, decision, reason):
    identifier = uid()
    conn.execute('INSERT INTO guard_audit VALUES(?,?,?,?,?,?,?,?,?)',
                 (identifier, actor.owner_id, project, task, operation, decision, reason, POLICY_VERSION, now()))
    return identifier


def check_actor(conn, actor, project):
    from backend.local_auth import session_active
    if not session_active(conn, actor.owner_id, actor.session_id):
        raise Denied('session_inactive')
    row = conn.execute('SELECT owner_id FROM guard_projects WHERE project_id=?', (project,)).fetchone()
    if not row or row[0] != actor.owner_id:
        raise Denied('resource_not_authorized')


def require_project(conn, project, actor=None):
    """Current trusted authority, including session revocation, for project APIs.

    Worker callers may carry the Actor captured by the authenticated request.
    Client/model payloads never supply this argument.
    """
    if actor is None:
        from backend.local_auth import current_principal
        principal = current_principal()
        actor = Actor(principal['owner_id'], principal['session_id'])
    if not isinstance(actor, Actor):
        raise HTTPException(401, 'Authenticated project authority required')
    try:
        check_actor(conn, actor, project)
    except Denied:
        raise HTTPException(404, 'Project resource unavailable') from None
    if not conn.execute('SELECT 1 FROM projects WHERE id=?', (project,)).fetchone():
        raise HTTPException(404, 'Project resource unavailable')
    return actor


def resource(conn, actor, project, file_id):
    check_actor(conn, actor, project)
    file = conn.execute('SELECT * FROM knowledge_files WHERE id=? AND project_id=?', (file_id, project)).fetchone()
    policy = conn.execute('SELECT * FROM guard_file_policies WHERE file_id=?', (file_id,)).fetchone()
    if not file or not policy or policy['revoked'] or policy['owner_id'] != actor.owner_id or policy['project_id'] != project:
        raise Denied('resource_not_authorized')
    chunks = [dict(r) for r in conn.execute('SELECT id,page,line_start,line_end,text FROM knowledge_chunks WHERE file_id=? AND project_id=? ORDER BY rowid', (file_id, project))]
    if not chunks:
        raise Denied('no_extracted_evidence')
    snapshot = {'file_id':file_id, 'sha256':file['sha256'], 'extraction_sha256':digest(chunks), 'policy_revision':policy['revision']}
    label = {'owner_id':actor.owner_id,'project_id':project,'sensitivity':policy['sensitivity'],
             'trust_origin':policy['trust_origin'],'allowed_destinations':json.loads(policy['destinations']),
             'retention':policy['retention'],'policy_version':POLICY_VERSION,'permission_revision':policy['revision']}
    return snapshot, label, chunks


def validate_resources(conn, actor, project, snapshots, destination):
    if not snapshots or len(snapshots) > 8:
        raise Denied('invalid_resource_scope')
    labels = []
    for expected in snapshots:
        actual, label, _ = resource(conn, actor, project, expected['file_id'])
        if actual != expected:
            raise Denied('resource_or_permission_changed')
        if destination not in label['allowed_destinations']:
            raise Denied('data_destination_forbidden')
        labels.append(label)
    return labels


def combine_policy(labels):
    if not labels or len({(x['owner_id'],x['project_id']) for x in labels}) != 1:
        raise Denied('mixed_ownership')
    destinations = set(labels[0]['allowed_destinations'])
    for label in labels[1:]: destinations.intersection_update(label['allowed_destinations'])
    return {'owner_id':labels[0]['owner_id'],'project_id':labels[0]['project_id'],
            'sensitivity':'private','trust_origin':'derived_from_untrusted_sources',
            'allowed_destinations':sorted(destinations),'retention':'until_any_source_revoked',
            'policy_version':POLICY_VERSION,'permission_revisions':[x['permission_revision'] for x in labels]}


def issue(actor, project, task, operation, destination, parameters, resources, budget, ttl=120):
    """Server-only. No route accepts or returns a permit or a caller-set identity."""
    token = secrets.token_urlsafe(32)
    failure = None
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        try:
            check_actor(conn, actor, project)
            control = conn.execute("SELECT value FROM guard_control WHERE key='local_summary'").fetchone()
            if not control or control[0] != 'enabled': raise Denied('tool_disabled')
            if operation != 'summarize_file' or destination != LOCAL_DESTINATION:
                raise Denied('operation_or_destination_forbidden')
            if not isinstance(budget,int) or not 1 <= budget <= MAX_SUMMARY_BYTES or not 0 < ttl <= 120:
                raise Denied('budget_exceeded')
            validate_resources(conn, actor, project, resources, destination)
            decision = audit(conn,actor,project,task,operation,'allow','bound_local_operation')
            conn.execute('INSERT INTO guard_permits VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (digest(token),actor.owner_id,actor.session_id,project,task,operation,destination,
                          digest(parameters),packed(resources),budget,time.time()+ttl,POLICY_VERSION,'pending',decision,now()))
        except Denied as exc:
            decision = audit(conn,actor,project,task,operation,'deny',exc.reason)
            failure = Denied(exc.reason,decision)
    if failure: raise failure
    return token, decision


def consume(token, actor, project, task, operation, destination, parameters, resources, byte_count):
    """Atomic claim across processes. A claimed/unknown action is NEVER retried."""
    failure = None
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        try:
            check_actor(conn,actor,project)
            row = conn.execute('SELECT * FROM guard_permits WHERE token_hash=?',(digest(token),)).fetchone()
            if not row: raise Denied('permit_missing')
            if row['state'] != 'pending': raise Denied('permit_already_used_or_revoked')
            if row['expires'] <= time.time(): raise Denied('permit_expired')
            if row['policy_version'] != POLICY_VERSION: raise Denied('policy_changed')
            expected=(row['owner_id'],row['session_id'],row['project_id'],row['task_id'],row['operation'],row['destination'],row['parameters_hash'],row['resources'])
            actual=(actor.owner_id,actor.session_id,project,task,operation,destination,digest(parameters),packed(resources))
            if actual != expected: raise Denied('execution_does_not_match_authorization')
            if not isinstance(byte_count,int) or byte_count < 0 or byte_count > row['budget']: raise Denied('budget_exceeded')
            control=conn.execute("SELECT value FROM guard_control WHERE key='local_summary'").fetchone()
            if not control or control[0]!='enabled':raise Denied('tool_disabled')
            validate_resources(conn,actor,project,resources,destination)
            conn.execute("UPDATE guard_permits SET state='claimed' WHERE token_hash=?",(digest(token),))
            decision=audit(conn,actor,project,task,operation,'execute','atomic_claim')
        except Denied as exc:
            decision=audit(conn,actor,project,task,operation,'deny',exc.reason)
            failure=Denied(exc.reason,decision)
    if failure:raise failure
    return decision


def finish(token, state):
    if state not in {'completed','failed','cancelled','unknown'}:raise ValueError('Invalid execution outcome')
    with get_db() as conn:
        conn.execute("UPDATE guard_permits SET state=? WHERE token_hash=? AND state='claimed'",(state,digest(token)))


def deny_external_generation(provider):
    """All existing cloud model call sites share this final dispatch boundary.

    Paid account identity/cost limits and per-resource declassification are not
    verified yet. Hybrid mode alone is not authorization to send private data.
    This quarantine cannot be lifted by prompt, request, model or env flag.
    """
    if provider != 'ollama':
        raise ValueError('Riwaq Guard: cloud generation is quarantined until destination, data release and priced budget are verified. Use the local model.')


def receipt(conn, actor, decision_id):
    row=conn.execute('SELECT * FROM guard_audit WHERE id=? AND owner_id=?',(decision_id,actor.owner_id)).fetchone()
    if not row:raise HTTPException(404,'Decision not found')
    if row['project_id']:check_actor(conn,actor,row['project_id'])
    return {key:row[key] for key in ('id','operation','decision','reason','policy_version','created_at')}
