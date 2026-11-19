"""Synthetic local reminders with the HTTP app stopped; no device notification."""
from datetime import datetime,timedelta,timezone
import json
import os
import subprocess
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient
from backend import app as service,db,local_auth,productivity_schedule as schedule
from test_skill_batch04_integration import scheduling


def setup_reminder(path,monkeypatch,*,late_seconds=-2,policy='latest'):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(path));monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY',str(path/'absent.json'))
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as client:
        assert client.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
        pid=client.get('/api/projects').json()[0]['id']
        # Freeze nothing: use an actual future due instant so app shutdown
        # occurs before publication. A separate process subsequently consumes it.
        due=datetime.now(timezone.utc)+timedelta(seconds=-late_seconds)
        result=scheduling(client,pid,'reminder.create',{'text':'SYNTHETIC_STANDALONE_REMINDER',
            'startLocal':due.replace(tzinfo=None).isoformat(timespec='seconds'),'zone':'UTC',
            'missedPolicy':policy,'graceSeconds':600},'standalone-fixture','reminder-creation')
        assert result['status']=='completed',result
    assert schedule.worker_status()['state']=='stopped'
    return pid


def process(*args):
    return subprocess.Popen([sys.executable,'-B','-m','backend.productivity_schedule',*args],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=os.environ.copy(),
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def collect(proc):
    try:out,err=proc.communicate(timeout=12)
    except BaseException:
        proc.kill();proc.communicate(timeout=3);raise
    assert proc.returncode==0,err+out
    result=json.loads(out)
    assert 'SYNTHETIC_STANDALONE_REMINDER' not in out
    return result


def test_two_independent_workers_publish_once_while_http_app_is_closed(tmp_path,monkeypatch):
    setup_reminder(tmp_path,monkeypatch)
    procs=[process('--run','--interval-seconds','1','--max-seconds','4') for _ in range(2)]
    results=[collect(proc) for proc in procs]
    assert sum(result['published'] for result in results)==1
    assert all(result['http_service_required'] is False and result['device_delivered'] is False for result in results)
    with db.get_db() as c:assert c.execute('SELECT COUNT(*) FROM productivity_notifications').fetchone()[0]==1
    restarted=collect(process('--once'))
    assert restarted['published']==0
    with db.get_db() as c:assert c.execute('SELECT COUNT(*) FROM productivity_notifications').fetchone()[0]==1


def test_missing_key_and_recovery_quarantine_cannot_run_the_standalone_worker(tmp_path,monkeypatch):
    setup_reminder(tmp_path,monkeypatch,late_seconds=-60)
    key=tmp_path/'local-auth'/'owner.key';held=key.with_suffix('.held');key.rename(held)
    try:
        with pytest.raises(local_auth.AuthStorageError):schedule.standalone_worker(threading.Event(),max_seconds=1)
    finally:held.rename(key)
    (tmp_path/'RECOVERY_QUARANTINE').write_text('Synthetic recovery fixture')
    with pytest.raises(schedule.ScheduleError,match='recovery_quarantine'):
        schedule.standalone_worker(threading.Event(),max_seconds=1)
    with db.get_db() as c:
        assert schedule.tick(c,datetime.now(timezone.utc)+timedelta(minutes=2))['status']=='disabled'
        assert c.execute('SELECT COUNT(*) FROM productivity_notifications').fetchone()[0]==0


def test_explicit_logout_blocks_existing_durable_grant_in_separate_process(tmp_path,monkeypatch):
    setup_reminder(tmp_path,monkeypatch,late_seconds=-1)
    with db.get_db() as c:c.execute('UPDATE local_auth_sessions SET revoked_at=?',(time.time(),))
    result=collect(process('--run','--interval-seconds','1','--max-seconds','2'))
    assert result['published']==0
    with db.get_db() as c:assert c.execute('SELECT COUNT(*) FROM productivity_notifications').fetchone()[0]==0


def test_stop_and_limits_are_explicit_and_do_not_create_installation(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    for kwargs in ({'interval_seconds':True},{'max_seconds':float('nan')},{'max_seconds':86401}):
        with pytest.raises(schedule.ScheduleError):schedule.standalone_worker(threading.Event(),**kwargs)
    with pytest.raises(schedule.ScheduleError,match='existing_installation_required'):
        schedule.standalone_worker(threading.Event(),max_seconds=1)
    assert not (tmp_path/'riwaq.sqlite3').exists()


@pytest.mark.parametrize('policy,published',[('skip',0),('latest',1)])
def test_real_missed_occurrence_policy_after_app_shutdown(tmp_path,monkeypatch,policy,published):
    setup_reminder(tmp_path,monkeypatch,late_seconds=120,policy=policy)
    result=collect(process('--once'))
    assert result['published']==published
    assert result['skipped']==1-published
    with db.get_db() as c:assert c.execute('SELECT COUNT(*) FROM productivity_notifications').fetchone()[0]==published


def test_stop_interrupts_worker_wait_without_publishing_future_item(tmp_path,monkeypatch):
    setup_reminder(tmp_path,monkeypatch,late_seconds=-60)
    stop=threading.Event();timer=threading.Timer(.2,stop.set);started=time.monotonic();timer.start()
    try:result=schedule.standalone_worker(stop,interval_seconds=5,max_seconds=20)
    finally:timer.cancel();timer.join(timeout=1)
    assert result['reason']=='cancelled' and result['published']==0
    assert time.monotonic()-started<3
