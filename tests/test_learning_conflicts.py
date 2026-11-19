"""SQLite conflict lifecycle; the checker adapter is explicitly simulated here.

API coverage below uses actual authorized sources and the real bounded worker.
No model, production database, global voting or training is involved.
"""
import asyncio
import hashlib
import json

import pytest

from backend import db, guard, learning as core, learning_verification
from test_learning_core import ACTOR, conn, connect, initialize, payload, evaluation
from test_learning_verification import contract_checker
from test_learning_api import client, setup, feedback, checked


def correction(c, key, revised='2+3', *, path=None, scope='conversation'):
    proposal = {'kind': 'input_correction', 'revised_input': revised}
    if path is not None: proposal['value_path'] = path
    return core.add_feedback(c, ACTOR, 'project', payload(c, scope=scope,
        purposes=['current_task', 'project_memory'], signal='correction', correction=proposal), key)


def check(c, row):
    target = core.bind_target(c, ACTOR, 'project', 'message', 'answer')
    proof = asyncio.run(learning_verification.verify_correction(target, row['correction'], checker=contract_checker))
    assert proof['status'] == 'verified'
    return core.record_verification(c, ACTOR, 'project', row['id'], row['version'], proof)


def arithmetic(c):
    c.execute("UPDATE messages SET content='2+2' WHERE id='user'")


def test_different_checked_values_quarantine_both_and_stale_context(conn):
    arithmetic(conn)
    first = check(conn, correction(conn, 'first-key', '2+3'))
    old = core.verified_corrections(conn, ACTOR, 'project', 'chat')
    assert old['corrections'][0]['corrected_output']['value'] == '5'
    assert core.authorize_bindings(conn, ACTOR, 'project', old['bindings'], 'chat')
    second = check(conn, correction(conn, 'second-key', '2+4'))
    first = core.get_feedback(conn, ACTOR, 'project', first['id'])
    assert first['status'] == second['status'] == 'conflicted'
    assert first['conflict']['automatically_selected'] is False
    assert core.verified_corrections(conn, ACTOR, 'project', 'chat') == {'corrections': [], 'bindings': []}
    assert not core.authorize_bindings(conn, ACTOR, 'project', old['bindings'], 'chat')
    assert conn.execute('SELECT count(*) FROM learning_conflicts').fetchone()[0] == 1
    # More reports for one result cannot outvote the conflicting checked result.
    third = check(conn, correction(conn, 'third-key', '2+3'))
    assert third['status'] == 'conflicted'
    assert core.verified_corrections(conn, ACTOR, 'project', 'chat')['corrections'] == []


def test_equivalent_values_do_not_conflict_and_different_fields_remain_independent(conn):
    arithmetic(conn)
    a = check(conn, correction(conn, 'same-key-one', '1+4'))
    b = check(conn, correction(conn, 'same-key-two', '10/2'))
    assert a['status'] == b['status'] == 'verified'
    assert conn.execute('SELECT count(*) FROM learning_conflicts').fetchone()[0] == 0
    text = json.dumps({'operation': 'statistics', 'values': ['1', '2', '3']})
    conn.execute('UPDATE messages SET content=? WHERE id=?', (text, 'user'))
    c = check(conn, correction(conn, 'field-key-one', text, path=['mean']))
    d = check(conn, correction(conn, 'field-key-two', text, path=['sum']))
    assert c['status'] == d['status'] == 'verified'
    assert len(core.verified_corrections(conn, ACTOR, 'project', 'chat')['corrections']) == 2


def test_withdrawal_needs_current_reverification_and_never_restores_old_binding(conn):
    arithmetic(conn)
    a = check(conn, correction(conn, 'first-key', '2+3'))
    binding = core.verified_corrections(conn, ACTOR, 'project', 'chat')['bindings']
    b = check(conn, correction(conn, 'second-key', '2+4'))
    core.delete_feedback(conn, ACTOR, 'project', b['id'], b['version'])
    assert conn.execute('SELECT count(*) FROM learning_conflicts').fetchone()[0] == 0
    assert core.verified_corrections(conn, ACTOR, 'project', 'chat')['corrections'] == []
    with pytest.raises(core.LearningError, match='feedback_version_conflict'):
        check(conn, a)
    current = core.get_feedback(conn, ACTOR, 'project', a['id'])
    restored = check(conn, current)
    assert restored['status'] == 'verified'
    assert core.verified_corrections(conn, ACTOR, 'project', 'chat')['corrections'][0]['id'] == a['id']
    assert not core.authorize_bindings(conn, ACTOR, 'project', binding, 'chat')


def test_conflict_revokes_adopted_lesson_and_evaluation_without_restoring_memory(conn):
    arithmetic(conn)
    a = check(conn, correction(conn, 'first-key', '2+3', scope='project'))
    item = core.propose_candidate(conn, ACTOR, 'project', a['id'], a['version'],
        {'kind':'brevity','value':'short'}, {'task':'any'}, scope='project')
    ev = evaluation(conn, item)
    release = core.activate_candidate(conn, ACTOR, 'project', item['id'], item['version'], ev['id'])
    assert conn.execute('SELECT 1 FROM memories WHERE id=?', (release['memory_id'],)).fetchone()
    check(conn, correction(conn, 'second-key', '2+4'))
    assert core.get_release(conn, ACTOR, 'project', release['id'])['status'] == 'revoked'
    assert not conn.execute('SELECT 1 FROM memories WHERE id=?', (release['memory_id'],)).fetchone()
    assert core.get_candidate(conn, ACTOR, 'project', item['id'])['status'] == 'stale'
    assert conn.execute('SELECT status FROM learning_evaluations WHERE id=?', (ev['id'],)).fetchone()[0] == 'revoked'
    assert core.active_rules(conn, ACTOR, 'project', 'chat', '')['rules'] == []
    with pytest.raises(core.LearningError):
        core.activate_candidate(conn, ACTOR, 'project', item['id'], item['version'], ev['id'])


def test_legacy_duplicate_verified_rows_are_read_blocked_before_recheck(conn):
    arithmetic(conn)
    a = check(conn, correction(conn, 'first-key', '2+3'))
    check(conn, correction(conn, 'second-key', '2+4'))
    # Explicit old-schema simulation, not user-editable authority.
    conn.execute("UPDATE learning_feedback SET status='verified'")
    conn.execute('DELETE FROM learning_conflicts')
    assert core.verified_corrections(conn, ACTOR, 'project', 'chat')['corrections'] == []
    a = core.get_feedback(conn, ACTOR, 'project', a['id'])
    with pytest.raises(core.LearningError, match='feedback_correction_conflict'):
        core.propose_candidate(conn, ACTOR, 'project', a['id'], a['version'], {'kind':'tone','value':'formal'}, {})


def test_preference_and_source_quote_are_not_factual_conflicts(conn):
    arithmetic(conn)
    a = check(conn, correction(conn, 'first-key', '2+3'))
    for i, text in enumerate(('formal', 'friendly')):
        f = core.add_feedback(conn, ACTOR, 'project', payload(conn, correction={'kind':'preference','text':text}), f'preference-key-{i}')
        assert check(conn, f)['status'] == 'verified'
    assert core.get_feedback(conn, ACTOR, 'project', a['id'])['status'] == 'verified'
    assert conn.execute('SELECT count(*) FROM learning_conflicts').fetchone()[0] == 0


def test_conflicts_survive_restart_and_project_access_is_required(tmp_path):
    path = tmp_path/'learning.db'
    c = connect(path); initialize(c); arithmetic(c)
    a = check(c, correction(c, 'first-key', '2+3'))
    check(c, correction(c, 'second-key', '2+4'))
    c.commit(); c.close()
    c = connect(path)
    try:
        core.init_learning(c)
        assert core.get_feedback(c, ACTOR, 'project', a['id'])['status'] == 'conflicted'
        assert core.verified_corrections(c, ACTOR, 'project', 'chat')['corrections'] == []
        with pytest.raises(core.LearningError): core.get_feedback(c, ACTOR, 'other', a['id'])
    finally: c.close()


def test_withdrawing_current_task_consent_prevents_correction_context(conn):
    arithmetic(conn)
    a = check(conn, correction(conn, 'first-key', '2+3'))
    old = core.verified_corrections(conn, ACTOR, 'project', 'chat')['bindings']
    core.withdraw_consent(conn, ACTOR, 'project', a['id'], a['version'], ['current_task'])
    assert core.verified_corrections(conn, ACTOR, 'project', 'chat')['corrections'] == []
    assert not core.authorize_bindings(conn, ACTOR, 'project', old, 'chat')


def test_comparison_limit_refuses_before_marking_new_feedback_verified(conn, monkeypatch):
    arithmetic(conn)
    check(conn, correction(conn, 'first-key', '2+3'))
    check(conn, correction(conn, 'second-key', '1+4'))
    third = correction(conn, 'third-key', '2+4')
    monkeypatch.setattr(core, 'MAX_CORRECTION_GROUP', 1)
    with pytest.raises(core.LearningError, match='correction_comparison_limit'):
        check(conn, third)
    saved = core.get_feedback(conn, ACTOR, 'project', third['id'])
    assert saved['status'] == 'recorded' and saved['verification'] == {} and saved['version'] == 1


def source_ref(client, pid, base, text):
    identifier = db.uid()
    with db.get_db() as c:
        c.execute('INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)',
            (identifier,pid,'number.txt',len(text),hashlib.sha256(text.encode()).hexdigest(),1,1,len(text),db.now()))
        c.execute('INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?)', (db.uid(),identifier,pid,1,1,1,text))
    response = client.get(base+'/targets/file/'+identifier)
    assert response.status_code == 200, response.text
    return {key:response.json()[key] for key in ('kind','id','version')}


def test_api_two_authorized_sources_conflict_real_workers_and_source_revocation(client):
    pid, cid, base, ref = setup(client)
    rows = []
    sources = []
    for value in ('10','20'):
        source = source_ref(client,pid,base,value); sources.append(source)
        proposal = {'kind':'input_correction','revised_input':value+'+2','candidate_value':str(int(value)+2),
            'source_ref':source,'source_quote':value,'source_start':0,'source_end':2,'input_start':0,'input_end':1}
        row,_ = feedback(client,base,cid,ref,signal='correction',correction=proposal)
        rows.append(checked(client,base,row))
    all_rows = client.get(base+'/feedback').json()
    assert {r['status'] for r in all_rows} == {'conflicted'}
    assert {r['verification']['corrected_output']['value'] for r in all_rows} == {'12','22'}
    assert all(r['verification']['worker_calls'] == 2 for r in all_rows)
    current = next(r for r in all_rows if r['id'] == rows[0]['id'])
    response = client.post(base+f"/feedback/{current['id']}/candidates",json={
        'expected_version':current['version'],'rule':{'kind':'brevity','value':'short'},'applicability':{},'scope':'project'})
    assert response.status_code == 409
    # No stale source quote can survive revocation, and the peer is not auto-used.
    with db.get_db() as c:
        c.execute('UPDATE guard_file_policies SET revoked=1,revision=revision+1 WHERE file_id=?', (sources[1]['id'],))
    deleted = client.get(base+f"/feedback/{rows[1]['id']}").json()
    assert deleted['status'] == 'deleted' and deleted['verification'] == {} and deleted['correction'] == {}
    still = client.get(base+f"/feedback/{current['id']}").json()
    assert still['status'] == 'conflicted'
    fresh = checked(client,base,still)
    assert fresh['status'] == 'verified' and fresh['verification']['corrected_output']['value'] == '12'
    # Source access is rechecked after await; this did not endorse source truth.
    assert fresh['verification']['diagnosis']['stage'] == 'requirements'
