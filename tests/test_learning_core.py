"""Actual SQLite lifecycle; server evaluator and preference verifier are exercised.

No model training, provider calls, DPAPI or production database is involved.
The fixed source hash isolates concurrent source edits in this component suite.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import time

import pytest

from backend import guard, learning as core, learning_evaluation, learning_verification, skill_release


ACTOR = guard.Actor('owner', 'session')


def connect(path=':memory:'):
    c = sqlite3.connect(path, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c


def initialize(c):
    c.executescript('''
    CREATE TABLE projects(id TEXT PRIMARY KEY);
    CREATE TABLE conversations(id TEXT PRIMARY KEY,project_id TEXT REFERENCES projects(id));
    CREATE TABLE messages(id TEXT PRIMARY KEY,conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
      role TEXT,content TEXT,status TEXT,model TEXT,citations TEXT);
    CREATE TABLE reasoning_runs(message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,receipt TEXT);
    CREATE TABLE message_context_links(message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,links TEXT);
    CREATE TABLE memories(id TEXT PRIMARY KEY,scope TEXT,project_id TEXT REFERENCES projects(id),content TEXT,created_at TEXT);
    CREATE TABLE memory_revisions(memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,version INTEGER,updated_at TEXT);
    CREATE TRIGGER memory_revision_insert AFTER INSERT ON memories BEGIN INSERT INTO memory_revisions VALUES(NEW.id,1,NEW.created_at); END;
    CREATE TRIGGER memory_revision_update AFTER UPDATE OF content,scope,project_id ON memories BEGIN UPDATE memory_revisions SET version=version+1 WHERE memory_id=NEW.id; END;
    CREATE TABLE local_auth_owner(slot INTEGER PRIMARY KEY,owner_id TEXT);
    CREATE TABLE local_auth_sessions(id TEXT PRIMARY KEY,owner_id TEXT,revoked_at REAL,expires_at REAL,last_seen REAL);
    CREATE TABLE guard_projects(project_id TEXT PRIMARY KEY,owner_id TEXT);
    CREATE TABLE guard_audit(id TEXT,owner_id TEXT,project_id TEXT,task_id TEXT,operation TEXT,decision TEXT,reason TEXT,policy TEXT,created_at TEXT);
    CREATE TABLE knowledge_files(id TEXT PRIMARY KEY,project_id TEXT,sha256 TEXT);
    CREATE TABLE knowledge_chunks(id TEXT PRIMARY KEY,file_id TEXT REFERENCES knowledge_files(id) ON DELETE CASCADE,project_id TEXT,page INTEGER,line_start INTEGER,line_end INTEGER,text TEXT);
    CREATE TABLE guard_file_policies(file_id TEXT PRIMARY KEY,project_id TEXT,owner_id TEXT,revoked INTEGER,revision INTEGER,sensitivity TEXT,trust_origin TEXT,destinations TEXT,retention TEXT);
    ''')
    c.executemany('INSERT INTO projects VALUES(?)', [('project',), ('other',)])
    c.executemany('INSERT INTO conversations VALUES(?,?)', [('chat','project'), ('chat2','project'), ('otherchat','other')])
    c.execute('INSERT INTO local_auth_owner VALUES(1,?)', ('owner',))
    c.execute('INSERT INTO local_auth_sessions VALUES(?,?,NULL,?,?)', ('session','owner',time.time()+3600,time.time()))
    c.executemany('INSERT INTO guard_projects VALUES(?,?)', [('project','owner'),('other','other-owner')])
    c.execute("INSERT INTO messages VALUES('user','chat','user','Summarize this','complete','test','[]')")
    c.execute("INSERT INTO messages VALUES('answer','chat','assistant','A long answer','complete','test','[]')")
    c.execute("INSERT INTO message_context_links VALUES('answer','[]')")
    c.execute("INSERT INTO knowledge_files VALUES('file','project','source-hash')")
    c.execute("INSERT INTO knowledge_chunks VALUES('chunk','file','project',1,1,1,'The answer is forty.')")
    c.execute('INSERT INTO guard_file_policies VALUES(?,?,?,0,1,?,?,?,?)', ('file','project','owner','private','user_file','["local:ollama"]','until_user_deletion'))
    core.init_learning(c)
    c.commit()


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(skill_release, 'current_source_fingerprint', lambda root: 'a'*64)
    c = connect(); initialize(c)
    yield c
    c.close()


def payload(c, scope='conversation', purposes=None, **updates):
    target = core.target_snapshot(c, ACTOR, 'project', 'message', 'answer')
    out = {'target': {k:target[k] for k in ('kind','id','version')}, 'signal':'preference',
           'correction': {'kind':'preference','text':'Use concise responses'}, 'scope':scope,
           'purposes': purposes or ['current_task']}
    return out | updates


def feedback(c, **kwargs):
    return core.add_feedback(c, ACTOR, 'project', payload(c, **kwargs), 'feedback-key')


def verified(c, scope='conversation', purposes=None):
    f = feedback(c, scope=scope, purposes=purposes)
    target = core.bind_target(c, ACTOR, 'project', **{'kind':'message','identifier':'answer'})
    receipt = asyncio.run(learning_verification.verify_correction(target, f['correction']))
    assert receipt['status'] == 'verified' and receipt['objective_verified'] is False
    return core.record_verification(c, ACTOR, 'project', f['id'], f['version'], receipt)


def candidate(c, scope='conversation', rule=None, purposes=None):
    f = verified(c, scope=scope, purposes=purposes)
    return core.propose_candidate(c, ACTOR, 'project', f['id'], f['version'],
        rule or {'kind':'brevity','value':'short'}, {'task':'any'}, scope)


def evaluation(c, item):
    base = core.active_rules(c, ACTOR, 'project', item['conversation_id'], '')['rules']
    report = learning_evaluation.evaluate_candidate(item, base)
    assert report['status'] == 'passed'
    return core.record_evaluation(c, ACTOR, 'project', item['id'], item['version'], report)


def activated(c, scope='conversation'):
    item = candidate(c, scope=scope, purposes=['current_task','project_memory'] if scope=='project' else None)
    result = evaluation(c, item)
    release = core.activate_candidate(c, ACTOR, 'project', item['id'], item['version'], result['id'])
    return item, release


def test_idempotency_is_one_event_and_changed_payload_conflicts(conn):
    body = payload(conn)
    one = core.add_feedback(conn, ACTOR, 'project', body, 'stable-key')
    conn.commit()  # emulate a persisted effect followed by a lost HTTP reply
    two = core.add_feedback(conn, ACTOR, 'project', body, 'stable-key')
    assert one['id'] == two['id']
    assert conn.execute('SELECT count(*) FROM learning_events').fetchone()[0] == 1
    with pytest.raises(core.LearningError, match='idempotency_payload_conflict'):
        core.add_feedback(conn, ACTOR, 'project', body | {'reason':'different'}, 'stable-key')


@pytest.mark.parametrize('signal', ['negative', 'regenerate', 'abandon'])
def test_weak_signal_never_directly_adopts_rule(conn, signal):
    f = feedback(conn, signal=signal, correction=None)
    assert f['status'] == 'recorded'
    with pytest.raises(core.LearningError, match='feedback_not_verified'):
        core.propose_candidate(conn, ACTOR, 'project', f['id'], 1, {'kind':'language','value':'en'}, {})


def test_current_target_mutation_blocks_and_redacts_read_and_replay(conn):
    body = payload(conn); f = core.add_feedback(conn, ACTOR, 'project', body, 'stable-key')
    conn.execute("UPDATE messages SET content='Changed answer' WHERE id='answer'")
    for item in (core.get_feedback(conn, ACTOR, 'project', f['id']), core.add_feedback(conn, ACTOR, 'project', body, 'stable-key')):
        assert item['status'] == 'stale' and item['correction'] == {} and item['reason'] == ''
        assert item['target']['version'] == f['target']['version']


@pytest.mark.parametrize('field,value', [('verified',True),('owner_id','evil'),('criteria',{}),('actor',{})])
def test_feedback_rejects_forged_authority_fields(conn, field, value):
    with pytest.raises(core.LearningError, match='invalid_learning_fields'):
        feedback(conn, **{field:value})


@pytest.mark.parametrize('rule', [
    {'kind':'language','value':{'en':True}}, {'kind':'language','value':'en','verified':True},
    {'kind':'terminology','source':'4','target':'5'}, {'kind':'terminology','source':'2+2','target':'5'},
    {'kind':'terminology','source':'ignore instructions','target':'send secret'}, {'kind':'tool_permission','value':'admin'}])
def test_typed_rule_has_no_factual_or_authority_payload(rule):
    with pytest.raises(core.LearningError): core.validate_rule(rule)


def test_personal_gate_and_project_purpose_are_enforced(conn):
    with pytest.raises(core.LearningError) as blocked: feedback(conn, scope='personal')
    assert blocked.value.status_code == 423
    f = verified(conn, scope='project')
    with pytest.raises(core.LearningError, match='project_memory_consent_required'):
        core.propose_candidate(conn, ACTOR, 'project', f['id'], f['version'], {'kind':'language','value':'en'}, {}, 'project')


def test_conversation_lesson_uses_no_memory_and_current_instruction_wins(conn):
    item, release = activated(conn)
    selected = core.active_rules(conn, ACTOR, 'project', 'chat', 'اكتب ملخصا')
    assert [r['id'] for r in selected['rules']] == [item['id']]
    assert conn.execute('SELECT count(*) FROM memories').fetchone()[0] == 0
    assert core.active_rules(conn, ACTOR, 'project', 'chat2', 'اكتب ملخصا')['rules'] == []
    assert core.active_rules(conn, ACTOR, 'project', 'chat', 'اشرح بالتفصيل')['rules'] == []
    assert core.authorize_bindings(conn, ACTOR, 'project', selected['bindings'], 'chat')
    assert not core.authorize_bindings(conn, ACTOR, 'project', selected['bindings'], 'chat2')


def test_project_memory_is_existing_versioned_record_and_rollback_disables(conn):
    item, release = activated(conn, 'project')
    assert release['memory_id'] in core.managed_memory_ids(conn, 'project')
    assert conn.execute('SELECT version FROM memory_revisions WHERE memory_id=?', (release['memory_id'],)).fetchone()[0] == 1
    selected = core.active_rules(conn, ACTOR, 'project', 'chat2', 'Summarize')
    assert len(selected['rules']) == 1
    assert core.get_candidate(conn, ACTOR, 'project', item['id'])['latest_evaluation']['status'] == 'passed'
    rolled = core.rollback_release(conn, ACTOR, 'project', release['id'], 1)
    assert rolled['status'] == 'rolled_back'
    assert conn.execute('SELECT count(*) FROM memories').fetchone()[0] == 0
    assert not core.authorize_bindings(conn, ACTOR, 'project', selected['bindings'], 'chat2')
    assert not core.active_rules(conn, ACTOR, 'project', 'chat2', 'Summarize')['rules']
    with pytest.raises(core.LearningError): core.activate_candidate(conn, ACTOR, 'project', item['id'], item['version'], release['evaluation_id'])


@pytest.mark.parametrize('mutation', ['edit','delete'])
def test_manual_memory_change_invalidates_lesson_without_overwriting_user_edit(conn, mutation):
    item, release = activated(conn, 'project')
    if mutation == 'edit': conn.execute('UPDATE memories SET content=? WHERE id=?', ('User replacement',release['memory_id']))
    else: conn.execute('DELETE FROM memories WHERE id=?', (release['memory_id'],))
    assert core.active_rules(conn, ACTOR, 'project', 'chat', '')['rules'] == []
    assert release['memory_id'] not in core.managed_memory_ids(conn, 'project')
    if mutation == 'edit': assert conn.execute('SELECT content FROM memories').fetchone()[0] == 'User replacement'
    assert core.get_candidate(conn, ACTOR, 'project', item['id'])['status'] == 'stale'


def test_deleted_feedback_tombstone_prevents_stale_activation(conn):
    item = candidate(conn); ev = evaluation(conn, item)
    f = core.get_feedback(conn, ACTOR, 'project', item['feedback_id'])
    core.delete_feedback(conn, ACTOR, 'project', f['id'], f['version'])
    assert conn.execute('SELECT count(*) FROM learning_tombstones').fetchone()[0] == 1
    with pytest.raises(core.LearningError): core.activate_candidate(conn, ACTOR, 'project', item['id'], item['version'], ev['id'])
    assert conn.execute('SELECT evidence FROM learning_evaluations').fetchone()[0] == '{}'


def test_conversation_delete_cascades_project_lesson_and_retains_no_correction(conn):
    item, release = activated(conn, 'project')
    conn.execute("DELETE FROM conversations WHERE id='chat'")
    assert conn.execute('SELECT count(*) FROM memories').fetchone()[0] == 0
    assert conn.execute('SELECT correction FROM learning_feedback').fetchone()[0] == '{}'
    assert core.get_release(conn, ACTOR, 'project', release['id'])['status'] == 'revoked'


def test_evaluation_rejects_candidate_change_criteria_and_source_drift(conn, monkeypatch):
    item = candidate(conn)
    report = learning_evaluation.evaluate_candidate(item, [])
    for key in ('candidate_sha256','criteria_sha256','source_fingerprint'):
        with pytest.raises(core.LearningError): core.record_evaluation(conn, ACTOR, 'project', item['id'], 1, report | {key:'b'*64})
    ev = core.record_evaluation(conn, ACTOR, 'project', item['id'], 1, report)
    monkeypatch.setattr(skill_release, 'current_source_fingerprint', lambda root: 'b'*64)
    with pytest.raises(core.LearningError, match='evaluation_source_changed'):
        core.activate_candidate(conn, ACTOR, 'project', item['id'], 1, ev['id'])


def test_failed_required_case_is_not_an_average_pass(conn):
    item = candidate(conn); report = learning_evaluation.evaluate_candidate(item, [])
    report['cases'][0]['status'] = 'failed'
    ev = core.record_evaluation(conn, ACTOR, 'project', item['id'], 1, report)
    assert ev['status'] == 'failed'
    with pytest.raises(core.LearningError): core.activate_candidate(conn, ACTOR, 'project', item['id'], 1, ev['id'])


def test_default_consent_does_not_export_and_withdrawal_marks_weights_unchanged(conn):
    f = feedback(conn)
    assert core.consented_feedback(conn, ACTOR, 'project', 'training') == []
    core.delete_feedback(conn, ACTOR, 'project', f['id'], f['version'])
    f = core.add_feedback(conn, ACTOR, 'project', payload(conn, purposes=['current_task','training']), 'training-key')
    assert len(core.consented_feedback(conn, ACTOR, 'project', 'training')) == 1
    model = core.register_descendant(conn, ACTOR, 'project', f['id'], 'model', 'existing-test-model')
    assert model['created_training_job'] is False
    core.withdraw_consent(conn, ACTOR, 'project', f['id'], f['version'], ['training'])
    assert core.consented_feedback(conn, ACTOR, 'project', 'training') == []
    assert conn.execute('SELECT status FROM learning_descendants').fetchone()[0] == 'weights_unchanged_review_required'


def test_unauthorized_project_and_revoked_session_cannot_read(conn):
    f = feedback(conn)
    with pytest.raises(core.LearningError) as missing: core.get_feedback(conn, ACTOR, 'other', f['id'])
    assert missing.value.status_code == 404
    conn.execute("UPDATE local_auth_sessions SET revoked_at=1 WHERE id='session'")
    with pytest.raises(core.LearningError): core.get_feedback(conn, ACTOR, 'project', f['id'])


def test_parallel_same_key_and_cas_have_one_winner(tmp_path):
    path = tmp_path/'concurrent.sqlite'
    c = connect(path); initialize(c); body = payload(c); c.close()
    def record():
        with connect(path) as db:
            return core.add_feedback(db, ACTOR, 'project', body, 'parallel-key')['id']
    with ThreadPoolExecutor(2) as pool: ids = list(pool.map(lambda _:record(), range(2)))
    assert ids[0] == ids[1]
    def delete():
        with connect(path) as db:
            try: core.delete_feedback(db, ACTOR, 'project', ids[0], 1); return 'deleted'
            except core.LearningError as exc: return exc.status_code
    with ThreadPoolExecutor(2) as pool: outcomes = list(pool.map(lambda _:delete(), range(2)))
    assert sorted(map(str, outcomes)) == ['409','deleted']
    with connect(path) as db: assert db.execute('SELECT count(*) FROM learning_feedback').fetchone()[0] == 1


def test_objective_correction_only_with_current_version_and_conversation(conn):
    f = feedback(conn)
    target = core.bind_target(conn, ACTOR, 'project', 'message', 'answer')
    # Simulated server proof tests storage/lineage only. The verifier suite
    # separately proves arithmetic against its actual fixed worker.
    proof = {'status':'verified','objective_verified':True,'target':{'kind':'message','id':'answer','version':target.version},
             'corrected_output':{'kind':'exact_math','value':'4','operation':'evaluate'}, 'verification_scope':'exact_math_value'}
    f = core.record_verification(conn, ACTOR, 'project', f['id'], f['version'], proof)
    result = core.verified_corrections(conn, ACTOR, 'project', 'chat')
    assert result['corrections'][0]['corrected_output']['value'] == '4'
    assert 'reason' not in core.pack(result) and 'Use concise responses' not in core.pack(result)
    assert not core.verified_corrections(conn, ACTOR, 'project', 'chat2')['corrections']
    assert core.authorize_bindings(conn, ACTOR, 'project', result['bindings'], 'chat')
    assert not core.authorize_bindings(conn, ACTOR, 'project', result['bindings'], 'chat2')
    core.delete_feedback(conn, ACTOR, 'project', f['id'], f['version'])
    assert not core.authorize_bindings(conn, ACTOR, 'project', result['bindings'], 'chat')
    assert not core.verified_corrections(conn, ACTOR, 'project', 'chat')['corrections']


def test_preference_is_not_replayed_as_objective_correction(conn):
    verified(conn)
    assert core.verified_corrections(conn, ACTOR, 'project', 'chat') == {'corrections':[], 'bindings':[]}


def test_superseding_one_rule_preserves_independent_rule_and_source(conn):
    first, old = activated(conn, 'project')
    f = core.add_feedback(conn, ACTOR, 'project', payload(conn, scope='project', purposes=['current_task','project_memory']), 'second-key')
    target = core.bind_target(conn, ACTOR, 'project', 'message', 'answer')
    proof = asyncio.run(learning_verification.verify_correction(target, f['correction']))
    f = core.record_verification(conn, ACTOR, 'project', f['id'], 1, proof)
    new = core.propose_candidate(conn, ACTOR, 'project', f['id'], f['version'], {'kind':'brevity','value':'detailed'}, {}, 'project')
    ev = evaluation(conn, new)
    with pytest.raises(core.LearningError, match='active_lesson_changed'):
        core.activate_candidate(conn, ACTOR, 'project', new['id'], 1, ev['id'])
    release = core.activate_candidate(conn, ACTOR, 'project', new['id'], 1, ev['id'], expected_active_id=old['id'])
    assert core.get_release(conn, ACTOR, 'project', old['id'])['status'] == 'superseded'
    assert [r['id'] for r in core.active_rules(conn, ACTOR, 'project', 'chat2', '')['rules']] == [new['id']]
    assert len(conn.execute('SELECT * FROM memories').fetchall()) == 1
    core.rollback_release(conn, ACTOR, 'project', release['id'], 1)
    assert not core.active_rules(conn, ACTOR, 'project', 'chat2', '')['rules']
    assert core.get_feedback(conn, ACTOR, 'project', f['id'])['target_status'] == 'current'


def test_stale_managed_memory_never_becomes_unfiltered_generic_memory(conn):
    item, release = activated(conn, 'project')
    conn.execute("UPDATE messages SET content='New target version' WHERE id='answer'")
    assert not core.active_rules(conn, ACTOR, 'project', 'chat2', '')['rules']
    assert release['memory_id'] in core.managed_memory_ids(conn, 'project')


def test_source_policy_change_invalidates_quote_and_candidate(conn):
    source = core.target_snapshot(conn, ACTOR, 'project', 'file', 'file')
    f = feedback(conn, correction={'kind':'source_correction','source_ref':{k:source[k] for k in ('kind','id','version')},
        'source_quote':'The answer is forty.','source_start':0,'source_end':20})
    conn.execute("UPDATE guard_file_policies SET revision=2 WHERE file_id='file'")
    result = core.get_feedback(conn, ACTOR, 'project', f['id'])
    assert result['status'] == 'stale' and result['correction'] == {}


def test_user_message_is_not_an_answer_target(conn):
    with pytest.raises(core.LearningError): core.bind_target(conn, ACTOR, 'project', 'message', 'user')


def test_identical_term_has_independent_project_meanings(conn):
    conn.execute("UPDATE guard_projects SET owner_id='owner' WHERE project_id='other'")
    conn.execute("INSERT INTO messages VALUES('otheruser','otherchat','user','Explain the schedule','complete','test','[]')")
    conn.execute("INSERT INTO messages VALUES('otheranswer','otherchat','assistant','Schedule','complete','test','[]')")
    conn.execute("INSERT INTO message_context_links VALUES('otheranswer','[]')")
    for pid, cid, target_id, term in [('project','chat','answer','جدول زمني'), ('other','otherchat','otheranswer','برنامج العمل')]:
        target = core.bind_target(conn, ACTOR, pid, 'message', target_id)
        correction = {'kind':'preference','text':'Use the stated project terminology'}
        f = core.add_feedback(conn, ACTOR, pid, {'target':{'kind':'message','id':target_id,'version':target.version},
            'signal':'remember','scope':'project','purposes':['current_task','project_memory'],'correction':correction}, 'same-idempotency-key')
        proof = asyncio.run(learning_verification.verify_correction(target, correction))
        f = core.record_verification(conn, ACTOR, pid, f['id'], 1, proof)
        item = core.propose_candidate(conn, ACTOR, pid, f['id'], f['version'], {'kind':'terminology','source':'schedule','target':term}, {}, 'project')
        report = learning_evaluation.evaluate_candidate(item, [])
        assert report['status'] == 'passed'
        ev = core.record_evaluation(conn, ACTOR, pid, item['id'], item['version'], report)
        core.activate_candidate(conn, ACTOR, pid, item['id'], item['version'], ev['id'])
        selected = core.active_rules(conn, ACTOR, pid, cid, 'Translate schedule')
        assert [r['rule']['target'] for r in selected['rules']] == [term]
    assert core.active_rules(conn, ACTOR, 'project', 'chat', '')['rules'][0]['rule']['target'] == 'جدول زمني'
    assert core.active_rules(conn, ACTOR, 'other', 'otherchat', '')['rules'][0]['rule']['target'] == 'برنامج العمل'


def test_rollback_preserves_a_different_active_rule(conn):
    item, release = activated(conn, 'project')
    second = core.propose_candidate(conn, ACTOR, 'project', item['feedback_id'], item['feedback_version'], {'kind':'language','value':'en'}, {}, 'project')
    ev = evaluation(conn, second)
    core.activate_candidate(conn, ACTOR, 'project', second['id'], second['version'], ev['id'])
    assert len(core.active_rules(conn, ACTOR, 'project', 'chat', '')['rules']) == 2
    core.rollback_release(conn, ACTOR, 'project', release['id'], 1)
    assert [r['rule']['kind'] for r in core.active_rules(conn, ACTOR, 'project', 'chat', '')['rules']] == ['language']


def test_rule_budget_and_new_candidate_cannot_hide_lost_behavior():
    rules = [{'id':f'rule-{i}', 'version':1, 'scope':'project', 'conversation_id':None,
              'rule':{'kind':'terminology','source':f'term{i}','target':f'name{i}'}, 'applicability':{'task':'any','expires_at':None}} for i in range(25)]
    resolved = core.resolve_rules(rules, '', 'chat')
    assert len(resolved['rules']) == 20 and len(resolved['suppressed']) == 5
    assert {v['reason'] for v in resolved['suppressed']} == {'context_budget'}
    report = learning_evaluation.evaluate_candidate(rules[20], rules[:20])
    assert next(v for v in report['cases'] if v['id']=='original_behavior_preserved')['status'] == 'failed'


def test_history_branch_cannot_reuse_conversation_lesson(conn):
    from backend import local_auth
    activated(conn)
    bindings = core.active_rules(conn, ACTOR, 'project', 'chat', '')['bindings']
    token = local_auth._principal.set({'owner_id':'owner','session_id':'session'})
    try:
        assert core.authorize_context_links(conn, 'project', bindings, 'chat')
        assert not core.authorize_context_links(conn, 'project', bindings, 'chat2')
    finally: local_auth._principal.reset(token)


def test_recovery_quarantine_cannot_reactivate_old_lesson(conn):
    from backend.guard_backup import _quarantine_effects
    item, release = activated(conn, 'project')
    bindings = core.active_rules(conn, ACTOR, 'project', 'chat', '')['bindings']
    changes = _quarantine_effects(conn)
    assert changes['learning_releases'] == 1
    assert changes['learning_candidates'] == 1
    assert changes['learning_evaluations'] == 1
    assert core.get_release(conn, ACTOR, 'project', release['id'])['status'] == 'quarantined'
    assert not core.active_rules(conn, ACTOR, 'project', 'chat', '')['rules']
    assert not core.authorize_bindings(conn, ACTOR, 'project', bindings, 'chat')
    assert release['memory_id'] in core.managed_memory_ids(conn, 'project')
    with pytest.raises(core.LearningError): core.activate_candidate(conn, ACTOR, 'project', item['id'], item['version'], release['evaluation_id'])


def test_recovery_quarantine_accepts_older_database_without_learning_tables():
    from backend.guard_backup import _quarantine_effects
    with connect() as c: assert _quarantine_effects(c) == {}


def test_activation_rejects_changed_baseline_even_for_different_rule(conn):
    item = candidate(conn)
    ev = evaluation(conn, item)
    second = core.propose_candidate(conn, ACTOR, 'project', item['feedback_id'], item['feedback_version'], {'kind':'language','value':'en'}, {}, 'conversation')
    ev2 = evaluation(conn, second)
    core.activate_candidate(conn, ACTOR, 'project', second['id'], second['version'], ev2['id'])
    with pytest.raises(core.LearningError, match='evaluation_baseline_changed'):
        core.activate_candidate(conn, ACTOR, 'project', item['id'], item['version'], ev['id'])
