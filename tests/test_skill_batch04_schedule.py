"""Real SQLite scheduler acceptance with synthetic owners and controlled time."""
from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
import json,os,sqlite3,subprocess,sys,time
import pytest
from backend import db,guard,productivity_schedule as s

NOW=datetime(2026,11,16,12,tzinfo=timezone.utc)

@pytest.fixture
def environment(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    with db.get_db() as c:
        c.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE projects(id TEXT PRIMARY KEY);
        CREATE TABLE guard_projects(project_id TEXT PRIMARY KEY,owner_id TEXT);
        CREATE TABLE guard_control(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE local_auth_owner(slot INTEGER PRIMARY KEY,owner_id TEXT);
        CREATE TABLE local_auth_sessions(id TEXT PRIMARY KEY,owner_id TEXT,expires_at REAL,revoked_at REAL,last_seen REAL);
        CREATE TABLE guard_audit(id TEXT PRIMARY KEY,owner_id TEXT,project_id TEXT,task_id TEXT,operation TEXT,decision TEXT,reason TEXT,policy_version TEXT,created_at TEXT);
        INSERT INTO projects VALUES('project-a');INSERT INTO projects VALUES('project-b');
        INSERT INTO guard_projects VALUES('project-a','owner-a');INSERT INTO guard_projects VALUES('project-b','owner-b');
        INSERT INTO local_auth_owner VALUES(1,'owner-a');
        ''')
        for key in ('session-a','session-new'):c.execute('INSERT INTO local_auth_sessions VALUES(?,?,?,?,?)',(key,'owner-a',time.time()+3600,None,time.time()))
        s.init_schedule(c)
    return guard.Actor('owner-a','session-a')

def call(who,op,options=None,key=None,project='project-a',clock=NOW):
    with db.get_db() as c:return s.execute_schedule(c,who,project,{'operation':op,'options':options or {}},idempotency_key=key or 'key-'+s.uid(),now_utc=clock)
def calendar(who):return call(who,'calendar.create',{'name':'تقويم المشروع','zone':'Asia/Jerusalem'})
def event(who,cal,start='2026-11-16T13:00:00',end='2026-11-16T14:00:00',**kw):return call(who,'event.create',{'calendarId':cal['id'],'title':'PRIVATE SYNTHETIC','startLocal':start,'endLocal':end,'zone':'UTC',**kw})
def reminder(who,**kw):return call(who,'reminder.create',{'text':'Synthetic reminder','startLocal':'2026-11-16T12:01:00','zone':'UTC',**kw})
def tick(clock=NOW+timedelta(minutes=2),limit=50):
    with db.get_db() as c:return s.tick(c,clock,limit)
def inbox(who):return call(who,'notification.list')['notifications']

def test_calendar_no_phantom_account_and_persistence(environment):
    assert call(environment,'calendar.list')['calendars']==[]
    cal=calendar(environment);assert cal['adapter']=='local';assert cal['external_account'] is False
    assert call(environment,'calendar.get',{'calendarId':cal['id']})['name']=='تقويم المشروع'
    assert call(environment,'calendar.list')['calendars'][0]['id']==cal['id']

def test_event_conflict_atomic_version_and_freebusy_privacy(environment):
    cal=calendar(environment);a=event(environment,cal)
    with pytest.raises(s.ScheduleError,match='event_conflict'):event(environment,cal,start='2026-11-16T13:30:00')
    busy=call(environment,'event.freebusy',{'calendarIds':[cal['id']],'fromUtc':'2026-11-16T12:00:00Z','toUtc':'2026-11-16T15:00:00Z'})
    assert busy['intervals']==[{'start_utc':'2026-11-16T13:00:00Z','end_utc':'2026-11-16T14:00:00Z'}]
    assert 'PRIVATE' not in json.dumps(busy);assert a['id'] not in json.dumps(busy)
    changed=call(environment,'event.update',{'eventId':a['id'],'expectedVersion':1,'title':'changed'})
    assert changed['version']==2
    with pytest.raises(s.ScheduleError,match='version_conflict'):call(environment,'event.update',{'eventId':a['id'],'expectedVersion':1,'title':'stale'})

def test_all_day_end_exclusive_dst_day_is_23_hours(environment):
    cal=calendar(environment)
    a=call(environment,'event.create',{'calendarId':cal['id'],'title':'All day','zone':'America/New_York','allDay':True,'startDate':'2026-03-08','endDate':'2026-03-09'})
    assert a['start_utc']=='2026-03-08T05:00:00Z';assert a['end_utc']=='2026-03-09T04:00:00Z';assert a['all_day_end']=='exclusive'

def test_no_external_invitation_and_immutable_reschedule(environment):
    cal=calendar(environment);a=event(environment,cal,invitees=['person@example.invalid'])
    assert a['invitation_status']=='not_sent_local_only'
    with pytest.raises(s.ScheduleError,match='reschedule_unsupported'):call(environment,'event.update',{'eventId':a['id'],'expectedVersion':1,'startUtc':'2026-11-16T15:00:00Z','endUtc':'2026-11-16T16:00:00Z'})
    b=event(environment,cal,'2026-11-17T13:00:00','2026-11-17T14:00:00',fixed=True)
    with pytest.raises(s.ScheduleError,match='immutable'):call(environment,'calendar.reschedule',{'changes':[{'eventId':b['id'],'expectedVersion':1,'startUtc':'2026-11-17T15:00:00Z','endUtc':'2026-11-17T16:00:00Z'}]})

def test_book_batch_rolls_back_if_second_conflicts_even_when_caller_catches(environment):
    cal=calendar(environment)
    with db.get_db() as c:
        with pytest.raises(s.ScheduleError,match='event_conflict'):
            s.execute_schedule(c,environment,'project-a',{'operation':'calendar.book','options':{'calendarId':cal['id'],'expectedVersion':1,'blocks':[{'id':'a','title':'a','start':'2026-11-16T13:00:00Z','end':'2026-11-16T14:00:00Z'},{'id':'b','title':'b','start':'2026-11-16T13:30:00Z','end':'2026-11-16T14:30:00Z'}]}},idempotency_key='batch',now_utc=NOW)
    assert call(environment,'event.list',{'calendarId':cal['id']})['events']==[]
    assert call(environment,'calendar.get',{'calendarId':cal['id']})['version']==1

def test_atomic_reschedule_swap_preserves_both(environment):
    cal=calendar(environment);a=event(environment,cal);b=event(environment,cal,'2026-11-16T15:00:00','2026-11-16T16:00:00')
    out=call(environment,'calendar.reschedule',{'changes':[{'eventId':a['id'],'expectedVersion':1,'startUtc':b['start_utc'],'endUtc':b['end_utc']},{'eventId':b['id'],'expectedVersion':1,'startUtc':a['start_utc'],'endUtc':a['end_utc']}]})
    assert [x['start_utc'] for x in out['events']]==[b['start_utc'],a['start_utc']]

def test_idempotency_changed_parameters_and_no_deleted_resurrection(environment):
    options={'text':'once','startLocal':'2026-11-16T12:01:00','zone':'UTC'}
    a=call(environment,'reminder.create',options,key='same');b=call(environment,'reminder.create',options,key='same');assert a['id']==b['id'];assert b['idempotent_replay']
    with pytest.raises(s.ScheduleError,match='parameters_changed'):call(environment,'reminder.create',options|{'text':'changed'},key='same')
    call(environment,'reminder.delete',{'reminderId':a['id'],'expectedVersion':1})
    with pytest.raises(s.ScheduleError,match='not_available'):call(environment,'reminder.create',options,key='same')
    assert tick()['published']==0

def test_tick_persists_once_across_connections_and_workers(environment):
    r=reminder(environment)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:tick(),[0,1]))
    assert sum(x['published'] for x in results)==1
    assert tick()['published']==0
    notes=inbox(environment);assert len(notes)==1;assert notes[0]['reminder_id']==r['id'];assert notes[0]['delivery']=='persisted_in_app_inbox_only'

def test_natural_session_expiry_and_cleanup_do_not_cancel_durable_grant(environment):
    reminder(environment)
    with db.get_db() as c:c.execute('UPDATE local_auth_sessions SET expires_at=0 WHERE id=?',(environment.session_id,));c.execute('DELETE FROM local_auth_sessions WHERE expires_at=0')
    assert tick()['published']==1
    assert len(inbox(guard.Actor('owner-a','session-new')))==1

def test_explicit_logout_revokes_and_purges_inbox(environment):
    reminder(environment);assert tick()['published']==1
    with db.get_db() as c:c.execute('UPDATE local_auth_sessions SET revoked_at=? WHERE id=?',(time.time(),environment.session_id))
    assert tick()['published']==0;assert inbox(guard.Actor('owner-a','session-new'))==[]

def test_logout_old_session_does_not_cancel_newly_reauthorized_revision(environment):
    r=reminder(environment)
    fresh=guard.Actor('owner-a','session-new')
    updated=call(fresh,'reminder.update',{'reminderId':r['id'],'expectedVersion':1,'text':'new authorized','startLocal':'2026-11-16T12:01:00','zone':'UTC'})
    assert tick()['published']==1
    with db.get_db() as c:c.execute('UPDATE local_auth_sessions SET revoked_at=? WHERE id=?',(time.time(),environment.session_id))
    assert len(inbox(fresh))==1
    assert call(fresh,'reminder.get',{'reminderId':r['id']})['state']=='completed'

def test_long_horizon_grant_does_not_exceed_366_days(environment):
    r=reminder(environment,startLocal='2027-11-17T11:59:00',graceSeconds=7*86400)
    with db.get_db() as c:g=c.execute('SELECT * FROM productivity_schedule_grants WHERE reminder_id=?',(r['id'],)).fetchone()
    assert g['expires_at']<= (NOW+timedelta(days=366)).timestamp()

def test_project_revocation_and_grant_tampering_fail_closed(environment):
    reminder(environment)
    with db.get_db() as c:c.execute('UPDATE productivity_schedule_grants SET destination=?',('https://evil.invalid',))
    assert tick()['revoked']==1;assert inbox(environment)==[]
    reminder(environment,startLocal='2026-11-16T12:02:00')
    with db.get_db() as c:c.execute("DELETE FROM guard_projects WHERE project_id='project-a'")
    assert tick()['published']==0
    with pytest.raises(guard.Denied):call(environment,'reminder.list')

def test_pause_resume_update_cannot_revive_prior_revision(environment):
    r=reminder(environment,recurrence={'frequency':'daily','count':3})
    p=call(environment,'reminder.pause',{'reminderId':r['id'],'expectedVersion':1});assert p['state']=='paused';assert tick()['published']==0
    resumed=call(environment,'reminder.resume',{'reminderId':r['id'],'expectedVersion':2},clock=NOW+timedelta(minutes=2))
    assert resumed['version']==3;assert resumed['next_utc']=='2026-11-17T12:01:00Z'
    assert tick(NOW+timedelta(days=1,minutes=2))['published']==1
    changed=call(environment,'reminder.update',{'reminderId':r['id'],'expectedVersion':3,'text':'New value','startLocal':'2026-11-18T12:01:00','zone':'UTC'})
    assert inbox(environment)==[];assert changed['version']==4
    assert tick(NOW+timedelta(days=2,minutes=2))['published']==1;assert inbox(environment)[0]['text']=='New value'

@pytest.mark.parametrize('policy,published',[('latest',1),('skip',0),('catch-up',3)])
def test_missed_policy_is_explicit_and_bounded(environment,policy,published):
    reminder(environment,recurrence={'frequency':'daily','count':3},missedPolicy=policy,graceSeconds=7*86400)
    result=tick(NOW+timedelta(days=2,hours=1));assert result['published']==published

def test_dst_recurrence_keeps_wall_clock_not_24_hours(environment):
    r=call(environment,'reminder.create',{'text':'DST','startLocal':'2027-03-13T09:00:00','zone':'America/New_York','recurrence':{'frequency':'daily','count':3}})
    with db.get_db() as c:dates=[x[0] for x in c.execute('SELECT due_utc FROM productivity_occurrences WHERE reminder_id=? ORDER BY due_utc',(r['id'],))]
    assert dates[1]-dates[0]==23*3600;assert dates[2]-dates[1]==24*3600

def test_two_projects_same_name_do_not_leak(environment):
    a=calendar(environment)
    with pytest.raises(guard.Denied):call(environment,'calendar.get',{'calendarId':a['id']},project='project-b')
    with db.get_db() as c:c.execute("INSERT INTO projects VALUES('project-c')");c.execute("INSERT INTO guard_projects VALUES('project-c','owner-a')")
    with pytest.raises(s.ScheduleError,match='not_available'):call(environment,'calendar.get',{'calendarId':a['id']},project='project-c')

def test_plan_is_persisted_proposal_not_task_execution(environment):
    p=call(environment,'plan.save',{'name':'Plan','body':{'taskIds':['task-a'],'status':'proposed','blocks':[]}})
    assert p['status']=='saved_plan_not_execution'
    assert call(environment,'plan.get',{'planId':p['id']})['body']['taskIds']==['task-a']
    with pytest.raises(s.ScheduleError,match='version_conflict'):call(environment,'plan.save',{'planId':p['id'],'expectedVersion':9,'name':'stale','body':{}})

def test_subprocess_restart_consumes_existing_durable_occurrence_once(environment):
    reminder(environment)
    script="from backend import db,productivity_schedule as s; import json\nwith db.get_db() as c: print(json.dumps(s.tick(c,'2026-11-16T12:02:00Z')))"
    results=[]
    for _ in range(2):
        run=subprocess.run([sys.executable,'-X','utf8','-B','-c',script],capture_output=True,text=True,timeout=15,env=os.environ.copy())
        assert run.returncode==0,run.stderr;results.append(json.loads(run.stdout))
    assert [r['published'] for r in results]==[1,0];assert len(inbox(environment))==1

def test_two_processes_racing_publish_only_one_inbox_record(environment):
    reminder(environment)
    script="from backend import db,productivity_schedule as s; import json\nwith db.get_db() as c: print(json.dumps(s.tick(c,'2026-11-16T12:02:00Z')))"
    processes=[subprocess.Popen([sys.executable,'-X','utf8','-B','-c',script],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=os.environ.copy()) for _ in range(2)]
    results=[]
    for proc in processes:
        out,err=proc.communicate(timeout=15);assert proc.returncode==0,err;results.append(json.loads(out))
    assert sum(r['published'] for r in results)==1;assert len(inbox(environment))==1

def test_atomic_event_conflict_recheck_between_two_connections(environment):
    cal=calendar(environment)
    def attempt(_):
        try:return event(environment,cal)['id']
        except s.ScheduleError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[0,1]))
    assert results.count('event_conflict')==1
    assert len(call(environment,'event.list',{'calendarId':cal['id']})['events'])==1

def test_database_verification_failure_does_not_publish(environment):
    reminder(environment)
    with db.get_db() as c:c.execute('ALTER TABLE productivity_schedule_grants RENAME TO inaccessible_grants')
    with pytest.raises(sqlite3.OperationalError):tick()
    with db.get_db() as c:assert c.execute('SELECT COUNT(*) FROM productivity_notifications').fetchone()[0]==0

def test_dst_skipped_local_time_diagnostic_is_persistent(environment):
    r=reminder(environment,startLocal='2027-03-14T02:30:00',zone='America/New_York',recurrence={'frequency':'daily','count':2,'nonexistent':'skip'})
    assert len(r['resolution']['skipped_local_times'])==1
    assert r['resolution']['generated_occurrences']==2
    assert r['next_utc']=='2027-03-15T06:30:00Z'
    assert call(environment,'reminder.get',{'reminderId':r['id']})['resolution']==r['resolution']
