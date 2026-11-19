"""Persistent lifecycle controls for the existing skill_runs executor.

Server-only helpers: callers supply a trusted Guard actor, never model identity.
Every mutation shares the caller's SQLite transaction; no worker or network is
created here. Integer budgets are native units, not guessed monetary prices.
Checkpoint bodies contain references/statuses only; text remains in skill_runs.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import time

from backend import guard
from backend.db import now, uid

VERSION = 'riwaq-skill-checkpoint-1'
MAX_AMOUNT = 10**12
UNITS = frozenset({'work_units', 'tokens', 'requests', 'bytes', 'milliseconds', 'money_microunits'})
SAFE_REPLAY = frozenset({'intent-routing', 'task-decomposition', 'acceptance-criteria',
    'tool-routing', 'tool-result-verification', 'permission-enforcement',
    'skill-routing', 'pdf-reading', 'citation-grounding'})
STEP_STATES = frozenset({'pending', 'running', 'cancelling', 'completed', 'partial', 'failed', 'cancelled', 'unknown', 'blocked', 'skipped'})


class LifecycleError(ValueError):
    """A stable, public-safe reason; no inputs or document text in errors."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _lock(c):
    # If a caller has already started a transaction, it owns its lock/lifetime.
    # Busy/locked/corrupt database errors propagate; never allow on failure.
    if not c.in_transaction:
        c.execute('BEGIN IMMEDIATE')


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value):
        raise LifecycleError('invalid_lifecycle_identifier')
    return value


def _amount(value):
    if type(value) is not int or not 0 <= value <= MAX_AMOUNT:
        raise LifecycleError('invalid_budget_amount')
    return value


def _run(c, who, run_id, *, check_sources=False):
    row = c.execute('SELECT * FROM skill_runs WHERE id=?', (_key(run_id),)).fetchone()
    if not row or row['owner_id'] != who.owner_id:
        raise LifecycleError('task_not_available')
    guard.check_actor(c, who, row['project_id'])
    if check_sources:
        try:
            sources = json.loads(row['resources'])
            if not isinstance(sources, list) or len(sources) > 8:
                raise ValueError()
            for source in sources:
                actual, _, _ = guard.resource(c, who, row['project_id'], source['file_id'])
                if actual != source:
                    raise LifecycleError('checkpoint_source_changed')
        except (ValueError, TypeError, KeyError) as exc:
            raise LifecycleError('checkpoint_source_unavailable') from exc
    return row


def init_lifecycle(c):
    """Additive tables; call after skill_runs exists. No automatic replay."""
    c.executescript('''
    CREATE TABLE IF NOT EXISTS skill_budget_accounts(
      id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
      project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      budget_key TEXT NOT NULL, unit TEXT NOT NULL, currency TEXT,
      limit_amount INTEGER NOT NULL CHECK(limit_amount>=0 AND limit_amount<=1000000000000),
      created_at TEXT NOT NULL, UNIQUE(owner_id,project_id,budget_key));
    CREATE TABLE IF NOT EXISTS skill_budget_reservations(
      id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES skill_budget_accounts(id) ON DELETE CASCADE,
      run_id TEXT REFERENCES skill_runs(id) ON DELETE SET NULL,
      original_run_id TEXT NOT NULL, reservation_key TEXT NOT NULL,
      reserved INTEGER NOT NULL CHECK(reserved>=0 AND reserved<=1000000000000),
      actual INTEGER CHECK(actual>=0 AND actual<=1000000000000),
      state TEXT NOT NULL CHECK(state IN ('reserved','in_flight','unknown','settled','released')),
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      UNIQUE(account_id,original_run_id,reservation_key));
    CREATE TABLE IF NOT EXISTS skill_checkpoints(
      run_id TEXT PRIMARY KEY REFERENCES skill_runs(id) ON DELETE CASCADE,
      schema_version TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=1),
      binding_hash TEXT NOT NULL, document_hash TEXT NOT NULL,
      body TEXT NOT NULL, body_hash TEXT NOT NULL,
      lease_hash TEXT, lease_expires REAL, lease_epoch INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL);
    ''')


def ensure_budget(c, who, project_id, key, unit, limit, currency=None):
    """Create an immutable server-configured cap; conflicts never widen it."""
    _lock(c); guard.check_actor(c, who, project_id); _key(key); _amount(limit)
    if unit not in UNITS:
        raise LifecycleError('invalid_budget_unit')
    if unit == 'money_microunits':
        if not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency):
            raise LifecycleError('currency_required')
    elif currency is not None:
        raise LifecycleError('currency_requires_monetary_unit')
    row = c.execute('SELECT * FROM skill_budget_accounts WHERE owner_id=? AND project_id=? AND budget_key=?',
        (who.owner_id, project_id, key)).fetchone()
    if row:
        if (row['unit'], row['limit_amount'], row['currency']) != (unit, limit, currency):
            raise LifecycleError('budget_definition_conflict')
        return dict(row)
    identifier = uid()
    c.execute('INSERT INTO skill_budget_accounts VALUES(?,?,?,?,?,?,?,?)',
        (identifier, who.owner_id, project_id, key, unit, currency, limit, now()))
    return dict(c.execute('SELECT * FROM skill_budget_accounts WHERE id=?', (identifier,)).fetchone())


def _account(c, who, project_id, key):
    guard.check_actor(c, who, project_id)
    row = c.execute('SELECT * FROM skill_budget_accounts WHERE owner_id=? AND project_id=? AND budget_key=?',
        (who.owner_id, project_id, _key(key))).fetchone()
    if not row:
        raise LifecycleError('budget_not_configured')
    return row


def _totals(c, account_id):
    row = c.execute('''SELECT
      coalesce(sum(CASE WHEN state='settled' THEN actual ELSE 0 END),0) AS spent,
      coalesce(sum(CASE WHEN state IN ('reserved','in_flight','unknown') THEN reserved ELSE 0 END),0) AS held,
      coalesce(sum(CASE WHEN state='unknown' THEN 1 ELSE 0 END),0) AS unknown_count,
      coalesce(sum(CASE WHEN state IN ('reserved','in_flight','unknown') THEN 1 ELSE 0 END),0) AS unsettled_count
      FROM skill_budget_reservations WHERE account_id=?''', (account_id,)).fetchone()
    return dict(row)


def budget_status(c, who, project_id, key):
    _lock(c)
    account = _account(c, who, project_id, key); totals = _totals(c, account['id'])
    committed = totals['spent'] + totals['held']
    return {'key': key, 'unit': account['unit'], 'currency': account['currency'],
        'limit': account['limit_amount'], **totals,
        'available': max(0, account['limit_amount'] - committed),
        'over_budget': committed > account['limit_amount'],
        'actual_known': totals['unsettled_count'] == 0}


def reserve_budget(c, who, run_id, key, reservation_key, amount):
    """Hold a bounded upper allowance before work, atomically across workers."""
    _lock(c); row = _run(c, who, run_id); _key(reservation_key); _amount(amount)
    account = _account(c, who, row['project_id'], key)
    previous = c.execute('SELECT * FROM skill_budget_reservations WHERE account_id=? AND original_run_id=? AND reservation_key=?',
        (account['id'], run_id, reservation_key)).fetchone()
    if previous:
        if previous['reserved'] != amount:
            raise LifecycleError('reservation_conflict')
        return dict(previous)
    if row['state'] not in {'queued', 'running'}:
        raise LifecycleError('task_not_active')
    totals = _totals(c, account['id'])
    if totals['spent'] + totals['held'] + amount > account['limit_amount']:
        raise LifecycleError('budget_exhausted')
    identifier = uid(); stamp = now()
    c.execute('INSERT INTO skill_budget_reservations VALUES(?,?,?,?,?,?,?,?,?,?)',
        (identifier, account['id'], run_id, run_id, reservation_key, amount, None, 'reserved', stamp, stamp))
    return dict(c.execute('SELECT * FROM skill_budget_reservations WHERE id=?', (identifier,)).fetchone())


def _reservation(c, who, reservation_id):
    row = c.execute('''SELECT r.*,a.owner_id,a.project_id FROM skill_budget_reservations r
      JOIN skill_budget_accounts a ON a.id=r.account_id WHERE r.id=?''', (_key(reservation_id),)).fetchone()
    if not row or row['owner_id'] != who.owner_id:
        raise LifecycleError('reservation_not_available')
    guard.check_actor(c, who, row['project_id'])
    return row


def claim_reservation(c, who, reservation_id):
    """True for the sole first dispatcher. False never authorizes a retry."""
    _lock(c); row = _reservation(c, who, reservation_id)
    if row['run_id'] is None:
        raise LifecycleError('task_not_available')
    run = _run(c, who, row['run_id'])
    if run['state'] not in {'queued', 'running'}:
        raise LifecycleError('task_not_active')
    return c.execute("UPDATE skill_budget_reservations SET state='in_flight',updated_at=? WHERE id=? AND state='reserved'",
        (now(), reservation_id)).rowcount == 1


def settle_budget(c, who, reservation_id, actual=None):
    """Unknown usage keeps its hold. Verified actual may reveal an overrun.

    Actuals are trusted monitor measurements, never model/stdout claims. An
    overrun is recorded honestly and blocks later reservations, not discarded.
    """
    _lock(c); row = _reservation(c, who, reservation_id)
    if actual is not None:
        _amount(actual)
    if row['state'] == 'released':
        raise LifecycleError('reservation_already_released')
    if row['state'] == 'settled':
        if actual != row['actual']:
            raise LifecycleError('settlement_conflict')
        return {'id': reservation_id, 'state': 'settled', 'actual': actual, 'reserved': row['reserved']}
    state = 'unknown' if actual is None else 'settled'
    c.execute('UPDATE skill_budget_reservations SET state=?,actual=?,updated_at=? WHERE id=?',
        (state, actual, now(), reservation_id))
    return {'id': reservation_id, 'state': state, 'actual': actual, 'reserved': row['reserved']}


def release_reservation(c, who, reservation_id):
    """Release only work that has not been dispatched. Never erase unknown use."""
    _lock(c); row = _reservation(c, who, reservation_id)
    if row['state'] not in {'reserved', 'released'}:
        raise LifecycleError('dispatched_usage_requires_settlement')
    c.execute("UPDATE skill_budget_reservations SET state='released',actual=0,updated_at=? WHERE id=?",
        (now(), reservation_id))
    return {'id': reservation_id, 'state': 'released', 'actual': 0}


def _document(row):
    try:
        doc = json.loads(row['document'])
        if not isinstance(doc, dict) or not isinstance(doc.get('steps'), list) or len(doc['steps']) > 8:
            raise ValueError()
        return doc
    except (ValueError, TypeError) as exc:
        raise LifecycleError('checkpoint_document_invalid') from exc


def _binding(row, doc):
    try:
        return _hash({'request_hash': row['request_hash'], 'resources': json.loads(row['resources']),
            'policy_version': doc.get('policy_version'), 'schema': VERSION})
    except (ValueError, TypeError) as exc:
        raise LifecycleError('checkpoint_binding_invalid') from exc


def _checkpoint_body(row, doc):
    steps = []; seen = set()
    for step in doc['steps']:
        if not isinstance(step, dict):
            raise LifecycleError('checkpoint_step_invalid')
        identifier = _key(step.get('id')); slug = _key(step.get('skill'))
        if identifier in seen or step.get('status') not in STEP_STATES:
            raise LifecycleError('checkpoint_step_invalid')
        seen.add(identifier)
        # Do not copy names/URLs, text, model messages or content to checkpoints.
        # The content is already persisted and permission-bound in skill_runs.
        steps.append({'id': identifier, 'skill': slug, 'status': step['status'],
            'output_refs_hash': _hash(step.get('output_refs', []))})
    return {'schema': VERSION, 'document_ref': 'skill_runs:' + row['id'], 'steps': steps}


def save_checkpoint(c, who, run_id, expected_revision=None):
    _lock(c); row = _run(c, who, run_id, check_sources=True); doc = _document(row)
    previous = c.execute('SELECT * FROM skill_checkpoints WHERE run_id=?', (run_id,)).fetchone()
    revision = previous['revision'] if previous else 0
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision != revision):
        raise LifecycleError('checkpoint_revision_conflict')
    body = _checkpoint_body(row, doc); next_revision = revision + 1
    values = (VERSION, next_revision, _binding(row, doc), _hash(doc), _json(body), _hash(body), now())
    if previous:
        c.execute('''UPDATE skill_checkpoints SET schema_version=?,revision=?,binding_hash=?,document_hash=?,
          body=?,body_hash=?,updated_at=? WHERE run_id=?''', (*values, run_id))
    else:
        c.execute('''INSERT INTO skill_checkpoints(run_id,schema_version,revision,binding_hash,document_hash,body,body_hash,updated_at)
          VALUES(?,?,?,?,?,?,?,?)''', (run_id, *values))
    return {'run_id': run_id, 'revision': next_revision, 'document_ref': body['document_ref'], 'schema': VERSION}


def _read_checkpoint(c, row, doc, expected_revision):
    checkpoint = c.execute('SELECT * FROM skill_checkpoints WHERE run_id=?', (row['id'],)).fetchone()
    if not checkpoint or checkpoint['schema_version'] != VERSION:
        raise LifecycleError('checkpoint_unavailable_or_incompatible')
    if type(expected_revision) is not int or checkpoint['revision'] != expected_revision:
        raise LifecycleError('checkpoint_revision_conflict')
    try:
        body = json.loads(checkpoint['body'])
        if (_hash(body) != checkpoint['body_hash'] or body != _checkpoint_body(row, doc)
                or checkpoint['document_hash'] != _hash(doc) or checkpoint['binding_hash'] != _binding(row, doc)):
            raise LifecycleError('checkpoint_content_changed')
    except (ValueError, TypeError) as exc:
        raise LifecycleError('checkpoint_corrupt') from exc
    return checkpoint, body


def acquire_resume(c, who, run_id, expected_revision, ttl_seconds=60):
    """Explicit recovery only. Return a server-private lease, never an API token.

    Caller first rechecks all lineage/public evidence with skills.recheck. This
    helper also rechecks Guard/private snapshots. A new session can recover the
    owner's run, but only one transaction can change unknown -> queued.
    """
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
        raise LifecycleError('invalid_lease_duration')
    _lock(c); row = _run(c, who, run_id, check_sources=True); doc = _document(row)
    checkpoint, body = _read_checkpoint(c, row, doc, expected_revision)
    if row['state'] != 'unknown':
        raise LifecycleError('task_not_resumable')
    if doc.get('status') in {'cancelling', 'cancelled'} or doc.get('receipt', {}).get('status') in {'cancelling', 'cancelled'}:
        raise LifecycleError('cancellation_cannot_resume_as_work')
    stamp = time.time()
    if checkpoint['lease_hash']:
        expiry = checkpoint['lease_expires']
        if not isinstance(expiry, (int, float)) or not math.isfinite(expiry):
            raise LifecycleError('resume_lease_corrupt')
        if expiry > stamp: raise LifecycleError('resume_already_leased')
    for step in body['steps']:
        if step['status'] == 'cancelling':
            raise LifecycleError('cancellation_cannot_resume_as_work')
        if step['status'] in {'running', 'unknown'} and step['skill'] not in SAFE_REPLAY:
            raise LifecycleError('unknown_effect_requires_reconciliation')
    token = secrets.token_urlsafe(32); expires = stamp + ttl_seconds
    c.execute('UPDATE skill_checkpoints SET lease_hash=?,lease_expires=?,lease_epoch=lease_epoch+1,updated_at=? WHERE run_id=?',
        (hashlib.sha256(token.encode()).hexdigest(), expires, now(), run_id))
    changed = c.execute("UPDATE skill_runs SET state='queued',session_id=?,updated_at=? WHERE id=? AND state='unknown'",
        (who.session_id, now(), run_id)).rowcount
    if changed != 1:
        raise LifecycleError('resume_claim_conflict')
    return {'run_id': run_id, 'revision': expected_revision, 'lease_token': token, 'lease_expires': expires,
        'lease_epoch': checkpoint['lease_epoch'] + 1, 'document_ref': body['document_ref'],
        'completed_step_ids': [s['id'] for s in body['steps'] if s['status'] == 'completed']}


def check_lease(c, who, run_id, token):
    _lock(c)
    row = _run(c, who, run_id, check_sources=True)
    lease = c.execute('SELECT lease_hash,lease_expires FROM skill_checkpoints WHERE run_id=?', (run_id,)).fetchone()
    valid = (isinstance(token, str) and len(token) <= 128 and lease and lease['lease_hash']
        and isinstance(lease['lease_expires'], (int, float)) and math.isfinite(lease['lease_expires'])
        and lease['lease_expires'] > time.time()
        and hmac.compare_digest(lease['lease_hash'], hashlib.sha256(token.encode()).hexdigest()))
    if not valid or row['state'] not in {'queued', 'running'}:
        raise LifecycleError('resume_lease_invalid')
    return True


def renew_lease(c, who, run_id, token, ttl_seconds=60):
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
        raise LifecycleError('invalid_lease_duration')
    _lock(c); check_lease(c, who, run_id, token); expires = time.time() + ttl_seconds
    c.execute('UPDATE skill_checkpoints SET lease_expires=?,updated_at=? WHERE run_id=?', (expires, now(), run_id))
    return expires


def release_lease(c, who, run_id, token):
    """Token-matched cleanup may follow a terminal update in this transaction."""
    _lock(c); _run(c, who, run_id)
    lease = c.execute('SELECT lease_hash FROM skill_checkpoints WHERE run_id=?', (run_id,)).fetchone()
    if (not isinstance(token, str) or len(token) > 128 or not lease or not lease['lease_hash']
            or not hmac.compare_digest(lease['lease_hash'], hashlib.sha256(token.encode()).hexdigest())):
        raise LifecycleError('resume_lease_invalid')
    c.execute('UPDATE skill_checkpoints SET lease_hash=NULL,lease_expires=NULL,updated_at=? WHERE run_id=?', (now(), run_id))


def request_cancellation(c, who, run_id):
    """Persist a request before stopping the worker, never an acknowledgement.

    Completed artifacts remain permission-bound and available. In-flight usage
    remains unknown until a trusted executor settles it; undispatched holds free.
    """
    _lock(c); row = _run(c, who, run_id); doc = _document(row)
    if row['state'] not in {'queued', 'running', 'unknown'}:
        return {'id': run_id, 'status': row['state'], 'changed': False}
    for step in doc['steps']:
        if step['status'] == 'pending': step['status'] = 'cancelled'
        elif step['status'] in {'running', 'unknown'}: step['status'] = 'cancelling'
    doc['status'] = 'cancelling'; doc['updated_at'] = now()
    doc['receipt'] = {'operation_id': run_id, 'status': 'cancelling', 'verification': 'unknown',
        'evidence': ['server cancellation requested'], 'worker_stop_confirmed': False, 'monetary_cost': None}
    c.execute("UPDATE skill_runs SET state='cancelling',document=?,updated_at=? WHERE id=?", (_json(doc), now(), run_id))
    c.execute('UPDATE skill_checkpoints SET lease_hash=NULL,lease_expires=NULL,updated_at=? WHERE run_id=?', (now(), run_id))
    c.execute("UPDATE skill_budget_reservations SET state='released',actual=0,updated_at=? WHERE run_id=? AND state='reserved'", (now(), run_id))
    c.execute("UPDATE skill_budget_reservations SET state='unknown',actual=NULL,updated_at=? WHERE run_id=? AND state='in_flight'", (now(), run_id))
    return {'id': run_id, 'status': 'cancelling', 'changed': True}


def acknowledge_cancellation(c, who, run_id, *, worker_stopped):
    """Trusted monitor calls only after await/join confirms local worker stop.

    This does not claim a remote provider stopped or waived its costs.
    """
    if worker_stopped is not True:
        raise LifecycleError('worker_stop_not_confirmed')
    _lock(c); row = _run(c, who, run_id); doc = _document(row)
    if row['state'] == 'cancelled':
        return {'id': run_id, 'status': 'cancelled', 'changed': False}
    if row['state'] != 'cancelling':
        raise LifecycleError('cancellation_not_requested')
    for step in doc['steps']:
        if step['status'] in {'pending', 'running', 'cancelling', 'unknown'}: step['status'] = 'cancelled'
    unknown = c.execute("SELECT count(*) FROM skill_budget_reservations WHERE run_id=? AND state='unknown'", (run_id,)).fetchone()[0]
    doc['status'] = 'cancelled'; doc['updated_at'] = now()
    doc['receipt'] = {'operation_id': run_id, 'status': 'cancelled', 'verification': 'partial',
        'evidence': ['local worker stopped after cancellation'], 'worker_stop_confirmed': True,
        'external_effects': 'not_confirmed', 'usage_unresolved': bool(unknown), 'monetary_cost': None}
    c.execute("UPDATE skill_runs SET state='cancelled',document=?,updated_at=? WHERE id=?", (_json(doc), now(), run_id))
    return {'id': run_id, 'status': 'cancelled', 'changed': True}


def cancel_state(c, who, run_id):
    """Compatibility alias: cancellation request, not confirmation of stopping."""
    return request_cancellation(c, who, run_id)


def completion_review(document, artifact_checks=None, source_checks=None):
    """Deterministic completeness, not a semantic/human quality verdict."""
    if not isinstance(document, dict):
        raise LifecycleError('invalid_completion_document')
    steps = document.get('steps', []); criteria = document.get('criteria', [])
    if not isinstance(steps, list) or not isinstance(criteria, list) or len(steps) > 8 or len(criteria) > 64:
        raise LifecycleError('invalid_completion_document')
    gaps = []
    for item in steps:
        if not isinstance(item, dict) or item.get('status') != 'completed':
            gaps.append({'kind': 'step', 'id': item.get('id') if isinstance(item, dict) else None,
                'state': item.get('status', 'unknown') if isinstance(item, dict) else 'unknown'})
    for item in criteria:
        if not isinstance(item, dict) or item.get('status') not in {'passed', 'verified'}:
            gaps.append({'kind': 'criterion', 'id': item.get('id', item.get('criterionId')) if isinstance(item, dict) else None,
                'state': item.get('status', 'unknown') if isinstance(item, dict) else 'unknown'})
    result = document.get('result', {})
    if not isinstance(result, dict) or not isinstance(result.get('artifacts', []), list):
        raise LifecycleError('invalid_completion_document')
    artifacts = result.get('artifacts', [])
    if len(artifacts) > 3 or (artifact_checks is not None and
            (not isinstance(artifact_checks, dict) or len(artifact_checks) > 3
             or any(type(value) is not bool for value in artifact_checks.values()))):
        raise LifecycleError('invalid_artifact_verification')
    names = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get('name'), str):
            raise LifecycleError('invalid_completion_document')
        name = artifact['name']
        if name in names or not artifact_checks or artifact_checks.get(name) is not True:
            gaps.append({'kind': 'artifact', 'id': name, 'state': 'unverified_or_duplicate'})
        names.add(name)
    for item in steps:
        if not isinstance(item, dict): continue
        for ref in item.get('output_refs', []):
            if isinstance(ref, str) and re.search(r'\.(?:docx|xlsx|pptx|wav)$', ref, re.I) and ref not in names:
                gaps.append({'kind': 'artifact', 'id': ref, 'state': 'missing_from_result'})
    intent = document.get('intent', {})
    requested = intent.get('requestedOutputs', []) if isinstance(intent, dict) else []
    if not isinstance(requested, list) or len(requested) > 16:
        raise LifecycleError('invalid_completion_document')
    for kind in requested:
        if kind in {'docx', 'xlsx', 'pptx', 'wav'} and not any(name.lower().endswith('.' + kind) for name in names):
            gaps.append({'kind': 'requested_output', 'id': kind, 'state': 'missing'})
    sources, lineage = result.get('source_refs', []), result.get('lineage_refs', [])
    if not isinstance(sources, list) or not isinstance(lineage, list) or len(sources) + len(lineage) > 128:
        raise LifecycleError('invalid_completion_document')
    refs = sources + lineage
    if refs and source_checks is not True:
        gaps.append({'kind': 'sources', 'id': None, 'state': 'not_rechecked'})
    evidence = document.get('receipt', {}).get('verification')
    verified = bool(steps and criteria) and not gaps and evidence == 'verified' and document.get('status') == 'completed'
    return {'status': 'verified_contract_only' if verified else 'needs_review', 'gaps': gaps,
        'semantic_quality': 'not_assessed', 'monetary_cost': None,
        'uncertainty': 'contract_evidence_only' if verified else 'incomplete_or_unverified_evidence'}
