"""Synthetic, offline reconciliation. No current database writes are permitted."""
import hashlib
import json
import os
import sqlite3
from contextlib import closing

import pytest
from backend import db, guard_backup, local_auth, personal_memory, recovery_reconciliation as recovery, learning
from test_learning_core import connect, initialize, activated, feedback, ACTOR


def old_copy(current):
    current.commit(); old=sqlite3.connect(':memory:');current.backup(old);return old


def test_later_personal_document_lesson_deletions_and_project_revocation(monkeypatch):
    from backend import skill_release
    monkeypatch.setattr(skill_release,'current_source_fingerprint',lambda root:'a'*64)
    with closing(connect()) as current:
        initialize(current); personal_memory.init_personal_memory(current)
        current.execute("INSERT INTO memories VALUES('personal','preference',NULL,'OLD_PERSONAL_DELETE','now')")
        current.execute("INSERT INTO memories VALUES('ordinary','project','project','retained project note','now')")
        candidate, release=activated(current,scope='project')
        current.execute("UPDATE message_context_links SET links='[{\"kind\":\"file\",\"file_id\":\"file\"}]' WHERE message_id='answer'")
        current.execute("INSERT INTO messages VALUES('other-answer','otherchat','assistant','PRIVATE_OTHER_PROJECT','complete','test','[]')")
        old=old_copy(current)
        current.execute("DELETE FROM memories WHERE id='personal'")
        current.execute("DELETE FROM knowledge_files WHERE id='file'")
        current.execute("UPDATE guard_projects SET owner_id='new-owner' WHERE project_id='other'")
        f=learning.get_feedback(current,ACTOR,'project',candidate['feedback_id'])
        if f['status']!='deleted':learning.delete_feedback(current,ACTOR,'project',f['id'],f['version'])
        current.commit();before=recovery.manifest(current)
        report=recovery.reconcile(old,before)
        assert report['status']=='reconciled_to_reference_snapshot'
        assert old.execute("SELECT 1 FROM memories WHERE id='personal'").fetchone() is None
        assert old.execute("SELECT 1 FROM knowledge_files WHERE id='file'").fetchone() is None
        assert old.execute("SELECT 1 FROM knowledge_chunks WHERE id='chunk'").fetchone() is None
        assert old.execute("SELECT 1 FROM projects WHERE id='other'").fetchone() is None
        assert old.execute("SELECT 1 FROM messages WHERE id IN ('answer','other-answer')").fetchone() is None
        assert old.execute('SELECT 1 FROM memories WHERE id=?',(release['memory_id'],)).fetchone() is None
        assert old.execute("SELECT content FROM memories WHERE id='ordinary'").fetchone()[0]=='retained project note'
        assert recovery.manifest(current)['fingerprint']==before['fingerprint']
        assert not old.execute('PRAGMA foreign_key_check').fetchall()
        assert 'OLD_PERSONAL_DELETE' not in json.dumps(report)
        old.close()


def test_changed_rows_are_excluded_not_replaced_and_composite_dependents_are_removed():
    with closing(connect()) as current:
        initialize(current)
        current.executescript('CREATE TABLE parent(a TEXT,b TEXT,payload TEXT,PRIMARY KEY(a,b)); CREATE TABLE child(id TEXT PRIMARY KEY,a TEXT,b TEXT,FOREIGN KEY(a,b) REFERENCES parent(a,b));')
        current.execute("INSERT INTO parent VALUES('A','B','old')");current.execute("INSERT INTO child VALUES('c','A','B')")
        old=old_copy(current);current.execute("UPDATE parent SET payload='new' WHERE a='A'")
        report=recovery.reconcile(old,recovery.manifest(current))
        assert old.execute('SELECT * FROM parent').fetchall()==[]
        assert old.execute('SELECT * FROM child').fetchall()==[]
        assert report['exclusions_by_reason']['dependent_row']>=1
        assert current.execute('SELECT payload FROM parent').fetchone()[0]=='new'
        old.close()


def test_file_access_revocation_removes_source_and_all_dependent_chunks():
    with closing(connect()) as current:
        initialize(current);old=old_copy(current)
        current.execute("UPDATE guard_file_policies SET revoked=1,revision=revision+1 WHERE file_id='file'")
        report=recovery.reconcile(old,recovery.manifest(current))
        assert old.execute('SELECT * FROM knowledge_files').fetchall()==[]
        assert old.execute('SELECT * FROM knowledge_chunks').fetchall()==[]
        assert report['exclusions_by_reason']['file_policy_changed']==1
        old.close()


def test_identity_and_budgets_fail_closed(monkeypatch):
    with closing(connect()) as current:
        initialize(current);old=old_copy(current)
        state=recovery.manifest(current);state['owner']='different'
        with pytest.raises(ValueError,match='different installation'):recovery.reconcile(old,state)
        monkeypatch.setattr(recovery,'MAX_ROWS',1)
        with pytest.raises(ValueError,match='budget'):recovery.manifest(current)
        old.close()


def test_unchanged_json_lineage_caches_do_not_survive_source_revocation():
    with closing(connect()) as current:
        initialize(current)
        item=feedback(current)
        # A legacy cache with no authoritative FK/resource entry can still
        # quote a source in a JSON payload. Revoking that source changes neither
        # cache row; exact equality to live storage is not sufficient.
        current.execute('UPDATE learning_feedback SET correction=? WHERE id=?',
            (json.dumps({'kind':'preference','text':'WITHDRAWN_SYNTHETIC_SOURCE'}),item['id']))
        current.executescript('''CREATE TABLE skill_runs(id TEXT PRIMARY KEY,project_id TEXT,payload TEXT,resources TEXT,state TEXT,document TEXT);
          CREATE TABLE skill_artifacts(run_id TEXT,name TEXT,content BLOB,PRIMARY KEY(run_id,name),FOREIGN KEY(run_id) REFERENCES skill_runs(id));''')
        current.execute('INSERT INTO skill_runs VALUES(?,?,?,?,?,?)',('legacy-run','project',json.dumps({'sourceRefs':[{'id':'file'}],'previousRunId':'old-run'}),'[]','completed',json.dumps({'text':'WITHDRAWN_SYNTHETIC_SOURCE'})))
        current.execute('INSERT INTO skill_artifacts VALUES(?,?,?)',('legacy-run','output.txt',b'WITHDRAWN_SYNTHETIC_SOURCE'))
        old=old_copy(current)
        before=current.execute('SELECT correction FROM learning_feedback WHERE id=?',(item['id'],)).fetchone()[0]
        current.execute("UPDATE guard_file_policies SET revoked=1,revision=revision+1 WHERE file_id='file'")
        assert current.execute('SELECT correction FROM learning_feedback WHERE id=?',(item['id'],)).fetchone()[0]==before
        assert current.execute('SELECT COUNT(*) FROM skill_runs').fetchone()[0]==1
        report=recovery.reconcile(old,recovery.manifest(current))
        assert old.execute('SELECT COUNT(*) FROM learning_feedback').fetchone()[0]==0
        assert old.execute('SELECT COUNT(*) FROM skill_runs').fetchone()[0]==0
        assert old.execute('SELECT COUNT(*) FROM skill_artifacts').fetchone()[0]==0
        assert report['exclusions_by_reason']['unverified_json_lineage_requires_regeneration']>=2
        old.close()


def seed_document(c,pid,fid,text):
    raw=text.encode();digest=hashlib.sha256(raw).hexdigest()
    c.execute('INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)',(fid,pid,fid+'.txt',len(raw),digest,1,1,len(text),db.now()))
    c.execute('INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?)',(fid+'chunk',fid,pid,1,1,1,text))
    c.execute('INSERT INTO document_originals VALUES(?,?,?,?,?,?,?)',(fid,raw,'text/plain',1,'{}','{}','complete'))


@pytest.mark.skipif(os.name!='nt',reason='Actual Windows DPAPI required')
def test_dpapi_current_state_reconciliation_source_blobs_and_missing_key(tmp_path,monkeypatch):
    live=tmp_path/'live';monkeypatch.setenv('RIWAQ_DATA_DIR',str(live));monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY',str(tmp_path/'absent.json'))
    db.init_db()
    with db.get_db() as c:
        pid=c.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        seed_document(c,pid,'retained','SYNTHETIC_RETAINED_ORIGINAL')
        seed_document(c,pid,'deleted','SYNTHETIC_WITHDRAWN_ORIGINAL')
        c.execute("INSERT INTO memories VALUES('personal','preference',NULL,'SYNTHETIC_FORGOTTEN_PREFERENCE','now')")
    backup=tmp_path/'original.rwb';sealed=guard_backup.seal(backup);backup_hash=hashlib.sha256(backup.read_bytes()).hexdigest()
    with db.get_db() as c:
        c.execute("DELETE FROM memories WHERE id='personal'");c.execute("DELETE FROM knowledge_files WHERE id='deleted'")
    source_hash=hashlib.sha256((live/'riwaq.sqlite3').read_bytes()).hexdigest()
    target=tmp_path/'reconciled';result=guard_backup.recover(backup,target,reconcile_current=True)
    assert result['reconciliation']['status']=='reconciled_to_reference_snapshot'
    assert result['quarantined'] and not result['production_activation'] and not result['complete_media_restore']
    with sqlite3.connect(target/'riwaq.sqlite3') as c:
        assert c.execute('SELECT original FROM document_originals').fetchall()==[(b'SYNTHETIC_RETAINED_ORIGINAL',)]
        assert c.execute("SELECT * FROM memories WHERE id='personal'").fetchall()==[]
        assert c.execute('SELECT DISTINCT revoked FROM guard_file_policies').fetchall()==[(1,)]
        assert c.execute('SELECT COUNT(*) FROM local_auth_sessions').fetchone()[0]==0
        assert c.execute("SELECT value FROM guard_control WHERE key='restore_quarantine'").fetchone()[0]=='1'
    assert b'SYNTHETIC_FORGOTTEN_PREFERENCE' not in (target/'riwaq.sqlite3').read_bytes()
    assert b'SYNTHETIC_WITHDRAWN_ORIGINAL' not in (target/'riwaq.sqlite3').read_bytes()
    assert hashlib.sha256((live/'riwaq.sqlite3').read_bytes()).hexdigest()==source_hash
    assert hashlib.sha256(backup.read_bytes()).hexdigest()==backup_hash
    assert not (target/'local-auth').exists() # No copied key, fresh key or credentials.
    key=live/'local-auth'/'owner.key';key.rename(key.with_suffix('.held'))
    try:
        with pytest.raises(local_auth.AuthStorageError):guard_backup.recover(backup,tmp_path/'missing-key',reconcile_current=True)
        assert not (tmp_path/'missing-key').exists()
    finally:key.with_suffix('.held').rename(key)
    original_key=key.read_bytes()
    try:
        key.write_bytes(b'RWA1W'+local_auth._dpapi(os.urandom(32),True))
        local_auth._key_cache.clear()
        with pytest.raises(ValueError,match='key does not match'):
            guard_backup.recover(backup,tmp_path/'wrong-key',reconcile_current=True)
        assert (tmp_path/'wrong-key'/'RECOVERY_QUARANTINE').exists()
        assert not (tmp_path/'wrong-key'/'local-auth').exists()
    finally:
        key.write_bytes(original_key);local_auth._key_cache.clear()
    assert result['seconds']>=0 and sealed['seconds']>=0 and result['snapshot_at']


@pytest.mark.skipif(os.name!='nt',reason='Actual Windows DPAPI required')
def test_missing_trusted_reference_is_explicitly_blocked_and_blobs_are_checked(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path/'live'));monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY',str(tmp_path/'absent.json'));db.init_db()
    with db.get_db() as c:
        pid=c.execute('SELECT id FROM projects LIMIT 1').fetchone()[0];seed_document(c,pid,'doc','good blob')
    backup=tmp_path/'backup.rwb';guard_backup.seal(backup)
    report=guard_backup.recover(backup,tmp_path/'unreconciled')
    assert report['reconciliation']['status']=='blocked' and report['quarantined']
    with db.get_db() as c:c.execute("UPDATE document_originals SET original=? WHERE file_id='doc'",(b'bad blob',))
    with pytest.raises(ValueError,match='integrity'):guard_backup.seal(tmp_path/'corrupt.rwb')
    assert not (tmp_path/'corrupt.rwb').exists()
    with db.get_db() as c:c.execute("DELETE FROM document_originals WHERE file_id='doc'")
    missing=guard_backup.seal(tmp_path/'missing-original.rwb')
    assert missing['inspection']['legacy_extracted_files_without_stored_original']==1
