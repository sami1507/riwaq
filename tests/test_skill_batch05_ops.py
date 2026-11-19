"""Synthetic diagnostics/recovery cases. No production state or external service."""
import hashlib,json,os,sqlite3
from datetime import datetime,timedelta,timezone
import pytest
from backend import guard_backup,guard,skill_observability,environment_audit
from backend.db import get_db,uid
from test_app import client
from test_skills import pid,submit
from test_skill_batch04_integration import scheduling

def actor(c):
    row=c.execute('SELECT owner_id,id FROM local_auth_sessions WHERE revoked_at IS NULL ORDER BY created_at DESC').fetchone()
    return guard.Actor(row['owner_id'],row['id'])

@pytest.mark.skipif(os.name!='nt',reason='Actual DPAPI current-user recovery requires Windows')
def test_recovery_revokes_durable_reminders_and_clears_pending_send_authority(client,tmp_path):
    p=pid(client)
    run=scheduling(client,p,'reminder.create',{'text':'Synthetic recovery reminder','startLocal':'2026-12-01T12:00:00','zone':'UTC'},'recovery-reminder','reminder-creation')
    assert run['status']=='completed'
    with get_db() as c:
        who=actor(c);aid=uid();did=uid();sid=uid()
        c.execute('INSERT INTO mail_send_grants VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',('a'*64,who.owner_id,who.session_id,p,aid,did,1,'b'*64,'c'*64,sid,0,1999999999,'claimed'))
        c.execute('INSERT INTO mail_submissions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(sid,who.owner_id,p,aid,did,1,'b'*64,'c'*64,'<synthetic@example.test>','dispatching','{}','2026-11-16','2026-11-16'))
    backup=tmp_path/'safe.rwbackup';guard_backup.seal(backup)
    target=tmp_path/'quarantine';report=guard_backup.recover(backup,target)
    with sqlite3.connect(target/'riwaq.sqlite3') as c:
        assert c.execute('SELECT count(*) FROM productivity_schedule_grants WHERE revoked=0').fetchone()[0]==0
        assert c.execute("SELECT count(*) FROM productivity_occurrences WHERE state='pending'").fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM local_auth_sessions').fetchone()[0]==0
        assert c.execute('SELECT state FROM mail_send_grants').fetchone()[0]=='revoked'
        assert c.execute('SELECT state FROM mail_submissions').fetchone()[0]=='unknown'
    assert report['quarantined'] and not report['old_rights_reactivated']
    with get_db() as c:assert c.execute('SELECT count(*) FROM productivity_schedule_grants WHERE revoked=0').fetchone()[0]==1

@pytest.mark.skipif(os.name!='nt',reason='DPAPI unavailable')
def test_backup_refuses_corrupt_persisted_blob_before_encryption(client,tmp_path):
    p=pid(client);run=scheduling(client,p,'calendar.create',{'name':'Synthetic','zone':'UTC'},'backup-corrupt')
    with get_db() as c:c.execute('INSERT INTO skill_artifacts VALUES(?,?,?,?,?)',(run['id'],'result.txt','text/plain','0'*64,b'actual data'))
    with pytest.raises(ValueError,match='integrity'):guard_backup.seal(tmp_path/'must-not-exist.rwbackup')
    assert not (tmp_path/'must-not-exist.rwbackup').exists()

def test_diagnostics_marks_stopped_stale_and_missing_worker_honestly(client):
    p=pid(client);at=datetime(2026,11,16,12,tzinfo=timezone.utc)
    with get_db() as c:
        who=actor(c)
        unknown=skill_observability.snapshot(c,who,p,at=at,worker={'state':'running','last_tick':None})
        stale=skill_observability.snapshot(c,who,p,at=at,worker={'state':'running','last_tick':(at-timedelta(seconds=31)).isoformat()})
        stopped=skill_observability.snapshot(c,who,p,at=at,worker={'state':'stopped','last_tick':at.isoformat()})
    assert unknown['worker']['state']=='unknown' and stale['worker']['state']=='stale' and stopped['worker']['state']=='unavailable'
    assert unknown['timing']['median'] is None and not unknown['alerts_sent']

def test_audit_allowlist_rejects_secret_payload_and_scope_mismatch(client):
    p=pid(client);run=scheduling(client,p,'calendar.create',{'name':'Synthetic','zone':'UTC'},'audit-test')
    other=client.post('/api/projects',json={'name':'Other'}).json()['id']
    secret='SYNTHETIC_SECRET_7823'
    with get_db() as c:
        who=actor(c)
        with pytest.raises(ValueError):skill_observability.record_event(c,who,p,run['id'],'failed',{'exception':secret})
        skill_observability.record_event(c,who,p,run['id'],'completed',{'state':'completed','elapsed_ms':42})
        with pytest.raises(guard.Denied):skill_observability.audit_trace(c,who,other,run['id'])
        result=skill_observability.audit_trace(c,who,p,run['id'])
    assert secret not in json.dumps(result) and any(e['details'].get('elapsed_ms')==42 for e in result['events'])

def test_environment_audit_does_not_echo_values_and_catches_client_secret(tmp_path):
    (tmp_path/'backend').mkdir();(tmp_path/'frontend/src').mkdir(parents=True)
    (tmp_path/'backend/example.py').write_text("import os\nx=os.environ.get('RIWAQ_DATA_DIR','./data')\n",encoding='utf-8')
    (tmp_path/'frontend/src/example.ts').write_text('const key=import.meta.env.VITE_API_KEY;',encoding='utf-8')
    secret='SYNTHETIC_SECRET_2489'
    result=environment_audit.audit_environment(tmp_path,{'VITE_API_KEY':secret,'RIWAQ_DATA_DIR':'\n'+secret})
    assert result['state']=='blocked' and result['client_secret_references'][0]['name']=='VITE_API_KEY'
    assert secret not in json.dumps(result) and not result['validation']['valid']
    assert environment_audit.validate_settings({})['valid']

def test_environment_audit_rejects_remote_development_origin_without_value():
    result=environment_audit.validate_settings({'RIWAQ_DEV_ORIGIN':'https://secret.example/path?token=private'})
    assert not result['valid'] and 'secret.example' not in json.dumps(result) and 'private' not in json.dumps(result)

def test_storage_failure_is_unknown_without_raw_exception(client):
    p=pid(client)
    with get_db() as c:
        who=actor(c)
        class BrokenRead:
            def execute(self,sql,args=()):
                if sql.startswith('PRAGMA quick_check'):raise sqlite3.OperationalError('SYNTHETIC_PRIVATE_PATH_AND_TOKEN')
                return c.execute(sql,args)
        report=skill_observability.snapshot(BrokenRead(),who,p)
    assert report['database']['state']=='unavailable' and report['queue']['state']=='unknown'
    assert 'SYNTHETIC_PRIVATE' not in json.dumps(report)

def test_audit_collector_failure_propagates_before_commit(client):
    p=pid(client);run=scheduling(client,p,'calendar.create',{'name':'Audit failure','zone':'UTC'},'audit-failure')
    with get_db() as c:
        who=actor(c)
        class BrokenWrite:
            def execute(self,sql,args=()):
                if sql.startswith('INSERT INTO guard_audit'):raise sqlite3.OperationalError('collector_unavailable')
                return c.execute(sql,args)
        with pytest.raises(sqlite3.OperationalError):skill_observability.record_event(BrokenWrite(),who,p,run['id'],'completed',{'state':'completed'})

@pytest.mark.skipif(os.name!='nt',reason='Actual Windows DPAPI recovery')
def test_restore_reopens_persisted_output_but_cannot_reactivate_access(client,tmp_path,monkeypatch):
    from backend import local_auth
    p=pid(client);run=scheduling(client,p,'calendar.create',{'name':'Recovery data','zone':'UTC'},'restore-file')
    raw='id,value\nO1,10\nO2,0\n'.encode();sha=hashlib.sha256(raw).hexdigest()
    with get_db() as c:c.execute('INSERT INTO skill_artifacts VALUES(?,?,?,?,?)',(run['id'],'orders.csv','text/csv',sha,raw))
    backup=tmp_path/'file.rwbackup';sealed=guard_backup.seal(backup)
    assert sealed['inspection']['verified_blob_rows']['skill_artifacts']==1
    restored=tmp_path/'recovered';receipt=guard_backup.recover(backup,restored)
    with sqlite3.connect(restored/'riwaq.sqlite3') as c:
        actual=c.execute('SELECT content,sha256 FROM skill_artifacts').fetchone()
        assert actual==(raw,sha) and len(actual[0].decode().splitlines())==3
    with monkeypatch.context() as m:
        m.setenv('RIWAQ_DATA_DIR',str(restored))
        assert local_auth.restore_quarantined() is True
    assert receipt['inspection']['references']=='ok' and receipt['production_activation'] is False

@pytest.mark.skipif(os.name!='nt',reason='Windows DPAPI availability')
def test_restore_missing_os_credential_and_linked_backup_fail_closed(client,tmp_path,monkeypatch):
    backup=tmp_path/'source.rwbackup';guard_backup.seal(backup)
    with monkeypatch.context() as m:
        m.setattr(guard_backup,'_dpapi',lambda *_: (_ for _ in ()).throw(ValueError('unavailable_identity')))
        with pytest.raises(ValueError):guard_backup.recover(backup,tmp_path/'must-not-exist')
        assert not (tmp_path/'must-not-exist').exists()
    hardlink=tmp_path/'linked.rwbackup';os.link(backup,hardlink)
    with pytest.raises(ValueError,match='regular file'):guard_backup.recover(hardlink,tmp_path/'must-not-link')

@pytest.mark.skipif(os.name!='nt',reason='Windows DPAPI backup')
def test_backup_refuses_successful_task_with_missing_output(client,tmp_path):
    p=pid(client)
    run=submit(client,p,'docx-authoring',{'artifact':{'kind':'docx','payload':{'title':'Synthetic','paragraphs':[{'text':'42 שלום'}]}}},key='missing-backup-output')
    assert run['status']=='completed' and run['result']['artifacts']
    with get_db() as c:c.execute('DELETE FROM skill_artifacts WHERE run_id=?',(run['id'],))
    with pytest.raises(ValueError,match='Referenced output integrity'):guard_backup.seal(tmp_path/'missing-output.rwbackup')
    assert not (tmp_path/'missing-output.rwbackup').exists()
