"""Independent source/CAS/scope review against actual SQLite and guard code.

Synthetic local owner/session and source contents only; no model/network calls.
"""
import asyncio
import hashlib
import json
import time

import pytest

from backend import db, guard, learning, learning_verification, local_auth


@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    db.init_db()
    with db.get_db() as c:
        learning.init_learning(c)
        owner=local_auth.owner_id(c);stamp=time.time()
        c.execute('INSERT INTO local_auth_sessions VALUES(?,?,?,?,?,NULL,?)',('review-session','review-token-hash',owner,stamp,stamp+3600,stamp))
        pid=c.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        for cid in ('first','second'):
            c.execute('INSERT INTO conversations VALUES(?,?,?,?,?,NULL)',(cid,pid,cid,db.now(),db.now()))
        c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',('question','first','user','2+2','complete',None,db.now(),'[]'))
        c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',('answer','first','assistant','4','complete','local-fixture',db.now(),'[]'))
        content='Synthetic source assertion'
        c.execute('INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)',('source',pid,'source.txt',len(content),hashlib.sha256(content.encode()).hexdigest(),1,1,len(content),db.now()))
        c.execute('INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?)',('chunk','source',pid,1,1,1,content))
    return guard.Actor(owner,'review-session'),pid


def source_feedback(c,who,pid):
    target=learning.target_snapshot(c,who,pid,'message','answer')
    source=learning.target_snapshot(c,who,pid,'file','source')
    quote='Synthetic source assertion'
    payload={'target':{k:target[k] for k in ('kind','id','version')},'signal':'correction',
             'correction':{'kind':'source_correction','source_ref':{k:source[k] for k in ('kind','id','version')},
                           'source_quote':quote,'source_start':0,'source_end':len(quote)}}
    row=learning.add_feedback(c,who,pid,payload,'review-source-key')
    return payload,row


def assert_no_quote(operation):
    try:value=operation()
    except learning.LearningError as exc:
        assert exc.status_code in (404,409,410)
    else:
        assert 'Synthetic source assertion' not in json.dumps(value)


def test_source_correction_revocation_redacts_get_list_and_idempotent_replay(store):
    who,pid=store
    with db.get_db() as c:
        payload,row=source_feedback(c,who,pid)
        c.execute("UPDATE guard_file_policies SET revoked=1,revision=revision+1 WHERE file_id='source'")
        assert_no_quote(lambda:learning.get_feedback(c,who,pid,row['id']))
        assert_no_quote(lambda:learning.list_feedback(c,who,pid))
        assert_no_quote(lambda:learning.add_feedback(c,who,pid,payload,'review-source-key'))


def test_new_correction_source_deletion_scrubs_derived_feedback(store):
    who,pid=store
    with db.get_db() as c:
        _,row=source_feedback(c,who,pid)
        c.execute("DELETE FROM knowledge_files WHERE id='source'")
        assert_no_quote(lambda:learning.get_feedback(c,who,pid,row['id']))


def verified_preference(c,who,pid):
    target=learning.bind_target(c,who,pid,'message','answer')
    proposal={'kind':'preference','text':'formal'}
    row=learning.add_feedback(c,who,pid,{'target':{'kind':'message','id':'answer','version':target.version},
                        'signal':'preference','correction':proposal},'review-preference-key')
    result=asyncio.run(learning_verification.verify_correction(target,proposal))
    return learning.record_verification(c,who,pid,row['id'],row['version'],result)


def test_conversation_only_feedback_cannot_migrate_to_another_conversation(store):
    who,pid=store
    with db.get_db() as c:
        row=verified_preference(c,who,pid)
        with pytest.raises(learning.LearningError) as raised:
            learning.propose_candidate(c,who,pid,row['id'],row['version'],{'kind':'tone','value':'formal'},
                                       {'task':'any','expires_at':None},conversation_id='second')
        assert raised.value.status_code in (403,409)


def test_stored_verified_status_cannot_replace_nested_target_binding(store):
    who,pid=store
    with db.get_db() as c:
        target=learning.bind_target(c,who,pid,'message','answer')
        proposal={'kind':'preference','text':'formal'}
        row=learning.add_feedback(c,who,pid,{'target':{'kind':'message','id':'answer','version':target.version},
                            'signal':'preference','correction':proposal},'review-forged-binding')
        result=asyncio.run(learning_verification.verify_correction(target,proposal))
        result['target']['id']='foreign-answer'
        with pytest.raises(learning.LearningError,match='verification_target_mismatch'):
            learning.record_verification(c,who,pid,row['id'],row['version'],result)


def test_source_change_after_verification_before_commit_is_rejected(store):
    who,pid=store
    with db.get_db() as c:
        payload,row=source_feedback(c,who,pid)
        target=learning.bind_target(c,who,pid,'message','answer')
        source=learning.bind_target(c,who,pid,'file','source')
        result=asyncio.run(learning_verification.verify_correction(target,payload['correction'],source=source))
        assert result['status']=='verified'
        c.execute("UPDATE knowledge_chunks SET text='updated' WHERE id='chunk'")
        with pytest.raises(learning.LearningError) as raised:
            learning.record_verification(c,who,pid,row['id'],row['version'],result)
        assert raised.value.status_code==409


def test_negative_vote_is_not_permission_to_propose_a_rule(store):
    who,pid=store
    with db.get_db() as c:
        target=learning.bind_target(c,who,pid,'message','answer')
        row=learning.add_feedback(c,who,pid,{'target':{'kind':'message','id':'answer','version':target.version},'signal':'negative'},'review-negative-key')
        with pytest.raises(learning.LearningError):
            learning.propose_candidate(c,who,pid,row['id'],row['version'],{'kind':'tone','value':'formal'},{'task':'any','expires_at':None})


def test_concurrent_verification_version_cannot_overwrite_newer_decision(store):
    who,pid=store
    with db.get_db() as c:
        target=learning.bind_target(c,who,pid,'message','answer')
        proposal={'kind':'preference','text':'formal'}
        row=learning.add_feedback(c,who,pid,{'target':{'kind':'message','id':'answer','version':target.version},'signal':'preference','correction':proposal},'review-version-key')
        result=asyncio.run(learning_verification.verify_correction(target,proposal))
        saved=learning.record_verification(c,who,pid,row['id'],row['version'],result)
        with pytest.raises(learning.LearningError,match='feedback_version_conflict'):
            learning.record_verification(c,who,pid,row['id'],row['version'],result)
        assert learning.get_feedback(c,who,pid,row['id'])['version']==saved['version']
