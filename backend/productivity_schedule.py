"""Local calendars and revocable durable in-app reminders in Riwaq SQLite.

Foreground calls require the existing Guard actor. The timer never forges an
actor/session: it consumes a narrowly bound durable grant and checks the current
local owner, project ownership, grant/revision and global control atomically.
No network, OS notification, invitation or model execution is implemented here.
"""
from __future__ import annotations

from datetime import date,datetime,timedelta,timezone
import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import threading
import time

from pydantic import BaseModel,ConfigDict,model_validator
from backend import guard
from backend.db import uid,get_db

VERSION='riwaq-local-schedule-1'
CHANNEL='local:inapp-inbox'
MAX_OCCURRENCES=366
MAX_ACTIVE_REMINDERS=100
MAX_EVENTS=5000
MAX_TICK=50
_WORKER_STATUS={'state':'not_started','last_tick':None,'last_result':None,'error_code':None}

def worker_status():
    """Process-local health only; durable occurrence state remains in SQLite."""
    return dict(_WORKER_STATUS)

class ScheduleError(ValueError):
    def __init__(self,code,status_code=422):self.code=code;self.status_code=status_code;super().__init__(code)

FIELDS={
 'calendar.create':{'name','zone'},'calendar.list':set(),'calendar.get':{'calendarId'},
 'calendar.update':{'calendarId','expectedVersion','name','zone'},'calendar.delete':{'calendarId','expectedVersion'},
 'event.create':{'calendarId','title','description','startLocal','endLocal','startUtc','endUtc','startDate','endDate','zone','fold','endFold','allDay','allowConflict','invitees','fixed','completed'},
 'event.update':{'eventId','expectedVersion','title','description','startLocal','endLocal','startUtc','endUtc','startDate','endDate','zone','fold','endFold','allDay','allowConflict','invitees','fixed','completed'},
 'event.get':{'eventId'},'event.cancel':{'eventId','expectedVersion'},
 'event.list':{'calendarId','fromUtc','toUtc'},'event.freebusy':{'calendarIds','fromUtc','toUtc'},
 'reminder.create':{'text','startLocal','zone','fold','recurrence','missedPolicy','graceSeconds'},
 'reminder.update':{'reminderId','expectedVersion','text','startLocal','zone','fold','recurrence','missedPolicy','graceSeconds'},
 'reminder.pause':{'reminderId','expectedVersion'},'reminder.resume':{'reminderId','expectedVersion'},
 'reminder.cancel':{'reminderId','expectedVersion'},'reminder.delete':{'reminderId','expectedVersion'},
 'reminder.list':set(),'reminder.get':{'reminderId'},'notification.list':{'limit'},'notification.read':{'notificationId'},
 'plan.save':{'planId','expectedVersion','name','body'},'plan.get':{'planId'},'plan.list':set(),'plan.delete':{'planId','expectedVersion'},
 'calendar.book':{'calendarId','expectedVersion','blocks'},'calendar.reschedule':{'changes'},
}
READS={'calendar.list','calendar.get','event.get','event.list','event.freebusy','reminder.list','reminder.get','notification.list','plan.get','plan.list'}

def _json(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def _hash(value):return hashlib.sha256(_json(value).encode()).hexdigest()
def _key(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',value):raise ScheduleError('invalid_schedule_identifier')
    return value
def _text(value,maximum=1000):
    if not isinstance(value,str) or not value.strip() or len(value)>maximum or any(ord(x)<32 and x not in '\n\t' or 0xD800<=ord(x)<=0xDFFF for x in value):raise ScheduleError('invalid_schedule_text')
    return value
def _integer(value,low,high,code='invalid_schedule_integer'):
    if type(value) is not int or not low<=value<=high:raise ScheduleError(code)
    return value
def _bool(value):
    if type(value) is not bool:raise ScheduleError('invalid_schedule_boolean')
    return value
def _utc(value=None):
    if value is None:return datetime.now(timezone.utc)
    try:dt=value if isinstance(value,datetime) else datetime.fromisoformat(value.replace('Z','+00:00'))
    except (ValueError,TypeError,AttributeError):raise ScheduleError('explicit_utc_instant_required') from None
    if dt.tzinfo is None or dt.utcoffset() is None:raise ScheduleError('explicit_utc_instant_required')
    return dt.astimezone(timezone.utc)
def _iso(value):return _utc(value).isoformat(timespec='seconds').replace('+00:00','Z')
def _zone(value):
    from backend.productivity_time import get_zone
    _text(value,100)
    return get_zone(value)
def _lock(c):
    if not c.in_transaction:c.execute('BEGIN IMMEDIATE')

def validate_request(request):
    if not isinstance(request,dict) or set(request)!={'operation','options'} or request['operation'] not in FIELDS:raise ScheduleError('schedule_operation_unsupported')
    options=request['options']
    if not isinstance(options,dict) or set(options)-FIELDS[request['operation']] or len(_json(options).encode())>65536:raise ScheduleError('invalid_schedule_options')
    for field in ('calendarId','eventId','reminderId','notificationId','planId'):
        if field in options:_key(options[field])
    if 'expectedVersion' in options:_integer(options['expectedVersion'],1,10**9)
    return request

class ScheduleRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    operation:str
    options:dict
    @model_validator(mode='after')
    def bounded(self):validate_request(self.model_dump());return self

def init_schedule(c):
    """Additive migration; caller runs after local_auth and Guard initialization."""
    c.executescript('''
    CREATE TABLE IF NOT EXISTS productivity_calendars(
      id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      name TEXT NOT NULL,zone TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS productivity_events(
      id TEXT PRIMARY KEY,calendar_id TEXT NOT NULL REFERENCES productivity_calendars(id) ON DELETE CASCADE,
      owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      title TEXT NOT NULL,description TEXT NOT NULL,start_utc REAL NOT NULL,end_utc REAL NOT NULL CHECK(end_utc>start_utc),
      zone TEXT NOT NULL,start_local TEXT NOT NULL,end_local TEXT NOT NULL,all_day INTEGER NOT NULL,
      start_date TEXT,end_date TEXT,invitees TEXT NOT NULL,fixed INTEGER NOT NULL,completed INTEGER NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('active','cancelled')),version INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS productivity_event_range ON productivity_events(calendar_id,state,start_utc,end_utc);
    CREATE TABLE IF NOT EXISTS productivity_reminders(
      id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      text TEXT NOT NULL,start_local TEXT NOT NULL,zone TEXT NOT NULL,fold INTEGER,rule TEXT,
      missed_policy TEXT NOT NULL,grace_seconds INTEGER NOT NULL,grant_id TEXT,
      state TEXT NOT NULL CHECK(state IN ('active','paused','cancelled','completed','revoked')),
      version INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,resolution TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS productivity_schedule_grants(
      id TEXT PRIMARY KEY,reminder_id TEXT NOT NULL REFERENCES productivity_reminders(id) ON DELETE CASCADE,
      reminder_version INTEGER NOT NULL,owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      created_session TEXT NOT NULL,operation TEXT NOT NULL,destination TEXT NOT NULL,binding_hash TEXT NOT NULL,
      max_occurrences INTEGER NOT NULL,consumed INTEGER NOT NULL DEFAULT 0,expires_at REAL NOT NULL,
      revoked INTEGER NOT NULL DEFAULT 0,policy_version TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS productivity_occurrences(
      id TEXT PRIMARY KEY,reminder_id TEXT NOT NULL REFERENCES productivity_reminders(id) ON DELETE CASCADE,
      reminder_version INTEGER NOT NULL,due_utc REAL NOT NULL,due_local TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('pending','published','skipped','cancelled','revoked')),
      reason TEXT,processed_at TEXT,UNIQUE(reminder_id,reminder_version,due_utc));
    CREATE INDEX IF NOT EXISTS productivity_occurrence_due ON productivity_occurrences(state,due_utc);
    CREATE TABLE IF NOT EXISTS productivity_notifications(
      id TEXT PRIMARY KEY,occurrence_id TEXT UNIQUE NOT NULL REFERENCES productivity_occurrences(id) ON DELETE CASCADE,
      reminder_id TEXT NOT NULL REFERENCES productivity_reminders(id) ON DELETE CASCADE,
      owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      reminder_version INTEGER NOT NULL,text TEXT NOT NULL,due_utc REAL NOT NULL,published_at TEXT NOT NULL,read_at TEXT);
    CREATE TABLE IF NOT EXISTS productivity_operations(
      owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      key TEXT NOT NULL,request_hash TEXT NOT NULL,binding TEXT NOT NULL,created_at TEXT NOT NULL,
      PRIMARY KEY(owner_id,project_id,key));
    CREATE TABLE IF NOT EXISTS productivity_plans(
      id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      name TEXT NOT NULL,body TEXT NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    DROP TRIGGER IF EXISTS productivity_explicit_session_revocation;
    CREATE TRIGGER productivity_explicit_session_revocation AFTER UPDATE OF revoked_at ON local_auth_sessions
      WHEN NEW.revoked_at IS NOT NULL BEGIN
      UPDATE productivity_schedule_grants SET revoked=1 WHERE created_session=NEW.id;
      UPDATE productivity_reminders SET state='revoked' WHERE grant_id IN(SELECT id FROM productivity_schedule_grants WHERE created_session=NEW.id);
      UPDATE productivity_occurrences SET state='revoked',reason='session_revoked' WHERE state='pending' AND reminder_id IN(SELECT r.id FROM productivity_reminders r JOIN productivity_schedule_grants g ON g.id=r.grant_id WHERE g.created_session=NEW.id);
      DELETE FROM productivity_notifications WHERE reminder_id IN(SELECT r.id FROM productivity_reminders r JOIN productivity_schedule_grants g ON g.id=r.grant_id WHERE g.created_session=NEW.id);
    END;
    CREATE TRIGGER IF NOT EXISTS productivity_project_access_deleted AFTER DELETE ON guard_projects BEGIN
      UPDATE productivity_schedule_grants SET revoked=1 WHERE project_id=OLD.project_id;
      UPDATE productivity_reminders SET state='revoked' WHERE project_id=OLD.project_id;
      UPDATE productivity_occurrences SET state='revoked',reason='project_access_revoked' WHERE state='pending' AND reminder_id IN(SELECT id FROM productivity_reminders WHERE project_id=OLD.project_id);
      DELETE FROM productivity_notifications WHERE project_id=OLD.project_id;
    END;
    CREATE TRIGGER IF NOT EXISTS productivity_project_owner_changed AFTER UPDATE OF owner_id ON guard_projects WHEN NEW.owner_id!=OLD.owner_id BEGIN
      UPDATE productivity_schedule_grants SET revoked=1 WHERE project_id=OLD.project_id;
      UPDATE productivity_reminders SET state='revoked' WHERE project_id=OLD.project_id;
      UPDATE productivity_occurrences SET state='revoked',reason='project_owner_changed' WHERE state='pending' AND reminder_id IN(SELECT id FROM productivity_reminders WHERE project_id=OLD.project_id);
      DELETE FROM productivity_notifications WHERE project_id=OLD.project_id;
    END;
    ''')
    if 'resolution' not in {r[1] for r in c.execute('PRAGMA table_info(productivity_reminders)')}:
        c.execute("ALTER TABLE productivity_reminders ADD COLUMN resolution TEXT NOT NULL DEFAULT '{}'")
    c.execute("INSERT OR IGNORE INTO guard_control(key,value) VALUES('local_schedule','enabled')")

def _row(c,who,project,table,identifier):
    guard.check_actor(c,who,project)
    row=c.execute(f'SELECT * FROM {table} WHERE id=? AND owner_id=? AND project_id=?',(_key(identifier),who.owner_id,project)).fetchone()
    if not row:raise ScheduleError('schedule_resource_not_available',404)
    return row
def _cas(row,expected):
    if row['version']!=_integer(expected,1,10**9):raise ScheduleError('schedule_version_conflict',409)
def get_calendar(c,who,project,calendar_id):
    return _calendar(_row(c,who,project,'productivity_calendars',calendar_id))
def _calendar(row):return {k:row[k] for k in ('id','name','zone','version','created_at','updated_at')}|{'adapter':'local','external_account':False}
def _event(row):
    return {k:row[k] for k in ('id','calendar_id','title','description','zone','start_local','end_local','start_date','end_date','state','version','created_at','updated_at')}|{
      'start_utc':_iso(datetime.fromtimestamp(row['start_utc'],timezone.utc)),'end_utc':_iso(datetime.fromtimestamp(row['end_utc'],timezone.utc)),
      'all_day':bool(row['all_day']),'fixed':bool(row['fixed']),'completed':bool(row['completed']),'invitees':json.loads(row['invitees']),
      'invitation_status':'not_sent_local_only','all_day_end':'exclusive' if row['all_day'] else None}
def _reminder(c,row):
    counts={r['state']:r['n'] for r in c.execute('SELECT state,COUNT(*) n FROM productivity_occurrences WHERE reminder_id=? AND reminder_version=? GROUP BY state',(row['id'],row['version']))}
    next_row=c.execute("SELECT due_utc FROM productivity_occurrences WHERE reminder_id=? AND reminder_version=? AND state='pending' ORDER BY due_utc LIMIT 1",(row['id'],row['version'])).fetchone()
    grant=c.execute('SELECT expires_at FROM productivity_schedule_grants WHERE id=?',(row['grant_id'],)).fetchone()
    return {k:row[k] for k in ('id','text','start_local','zone','fold','missed_policy','grace_seconds','state','version','created_at','updated_at')}|{
      'recurrence':json.loads(row['rule']) if row['rule'] else None,'occurrences':counts,'next_utc':_iso(datetime.fromtimestamp(next_row[0],timezone.utc)) if next_row else None,
      'resolution':json.loads(row['resolution']),'grant_expires_utc':_iso(datetime.fromtimestamp(grant[0],timezone.utc)) if grant else None,
      'channel':CHANNEL,'delivery':'persisted_in_app_inbox_only','logout_revokes':True,'requires_running_local_service':True}
def _plan(row):return {k:row[k] for k in ('id','name','version','created_at','updated_at')}|{'body':json.loads(row['body']),'status':'saved_plan_not_execution'}

def _range(options,now):
    start=_utc(options.get('fromUtc',now));end=_utc(options.get('toUtc',start+timedelta(days=30)))
    if not timedelta(0)<end-start<=timedelta(days=366):raise ScheduleError('calendar_range_limit')
    return start.timestamp(),end.timestamp()
def busy_range(c,who,project,calendar_ids,from_utc,to_utc):
    guard.check_actor(c,who,project)
    if not isinstance(calendar_ids,list) or not 1<=len(calendar_ids)<=10 or len(set(calendar_ids))!=len(calendar_ids):raise ScheduleError('calendar_scope_invalid')
    for key in calendar_ids:_row(c,who,project,'productivity_calendars',key)
    start,end=_range({'fromUtc':from_utc,'toUtc':to_utc},_utc());items=[]
    for key in calendar_ids:
        items.extend((max(start,row[0]),min(end,row[1])) for row in c.execute("SELECT start_utc,end_utc FROM productivity_events WHERE calendar_id=? AND state='active' AND start_utc<? AND end_utc>?",(key,end,start)))
    merged=[]
    for left,right in sorted(items):
        if merged and left<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],right)
        else:merged.append([left,right])
    return {'intervals':[{'start_utc':_iso(datetime.fromtimestamp(a,timezone.utc)),'end_utc':_iso(datetime.fromtimestamp(b,timezone.utc))} for a,b in merged],'details_included':False,'scope':'authorized_local_calendars_only'}

def _event_values(options,calendar):
    from backend.productivity_time import resolve_local
    title=_text(options.get('title'),200);description=options.get('description','')
    if description:_text(description,4000)
    elif not isinstance(description,str):raise ScheduleError('invalid_schedule_text')
    zone=options.get('zone',calendar['zone']);tz=_zone(zone);all_day=_bool(options.get('allDay',False))
    if all_day:
        if any(x in options for x in ('startLocal','endLocal','startUtc','endUtc')):raise ScheduleError('ambiguous_event_times')
        try:start_date=date.fromisoformat(options['startDate']);end_date=date.fromisoformat(options['endDate'])
        except (ValueError,KeyError,TypeError):raise ScheduleError('all_day_dates_required') from None
        if not timedelta(0)<end_date-start_date<=timedelta(days=31):raise ScheduleError('event_duration_limit')
        left=start_date.isoformat()+'T00:00:00';right=end_date.isoformat()+'T00:00:00'
        start=resolve_local(left,zone,options.get('fold'));end=resolve_local(right,zone,options.get('endFold'))
    else:
        if any(x in options for x in ('startDate','endDate')):raise ScheduleError('ambiguous_event_times')
        start_date=end_date=None
        if 'startUtc' in options or 'endUtc' in options:
            if any(x in options for x in ('startLocal','endLocal')) or not {'startUtc','endUtc'}<=set(options):raise ScheduleError('ambiguous_event_times')
            start=_utc(options['startUtc']);end=_utc(options['endUtc']);left=start.astimezone(tz).replace(tzinfo=None).isoformat(timespec='seconds');right=end.astimezone(tz).replace(tzinfo=None).isoformat(timespec='seconds')
        else:
            left=options.get('startLocal');right=options.get('endLocal');start=resolve_local(left,zone,options.get('fold'));end=resolve_local(right,zone,options.get('endFold'))
        if not timedelta(0)<end-start<=timedelta(days=31):raise ScheduleError('event_duration_limit')
    invitees=options.get('invitees',[])
    if not isinstance(invitees,list) or len(invitees)>30 or len(set(invitees))!=len(invitees) or any(not isinstance(x,str) or not re.fullmatch(r'[^\s<>@]{1,100}@[^\s<>@]{1,150}\.[^\s<>@]{2,40}',x) for x in invitees):raise ScheduleError('invitees_invalid')
    return {'title':title,'description':description,'start_utc':start.timestamp(),'end_utc':end.timestamp(),'zone':zone,'start_local':left,'end_local':right,'all_day':int(all_day),'start_date':start_date.isoformat() if start_date else None,'end_date':end_date.isoformat() if end_date else None,'invitees':_json(invitees),'fixed':int(_bool(options.get('fixed',False))),'completed':int(_bool(options.get('completed',False)))}

def _conflicts(c,calendar,start,end,exclude=None):
    return [r[0] for r in c.execute("SELECT id FROM productivity_events WHERE calendar_id=? AND state='active' AND start_utc<? AND end_utc>? AND id!=? LIMIT 100",(calendar,end,start,exclude or ''))]
def _insert_event(c,who,project,options,stamp):
    calendar=_row(c,who,project,'productivity_calendars',options.get('calendarId'));values=_event_values(options,calendar)
    conflicts=_conflicts(c,calendar['id'],values['start_utc'],values['end_utc'])
    if conflicts and not _bool(options.get('allowConflict',False)):raise ScheduleError('event_conflict',409)
    if c.execute('SELECT COUNT(*) FROM productivity_events WHERE project_id=?',(project,)).fetchone()[0]>=MAX_EVENTS:raise ScheduleError('event_storage_limit',429)
    identifier=uid();columns=','.join(values);placeholders=','.join('?' for _ in values)
    c.execute(f'INSERT INTO productivity_events(id,calendar_id,owner_id,project_id,{columns},state,version,created_at,updated_at) VALUES(?,?,?,?,{placeholders},?,?,?,?)',(identifier,calendar['id'],who.owner_id,project,*values.values(),'active',1,stamp,stamp))
    c.execute('UPDATE productivity_calendars SET version=version+1,updated_at=? WHERE id=?',(stamp,calendar['id']))
    return _row(c,who,project,'productivity_events',identifier)

def _update_event(c,who,project,options,stamp,*,reschedule=False):
    row=_row(c,who,project,'productivity_events',options.get('eventId'));_cas(row,options.get('expectedVersion'))
    if row['state']!='active':raise ScheduleError('event_not_active',409)
    time_fields={'startLocal','endLocal','startUtc','endUtc','startDate','endDate','zone','allDay','fold','endFold'}
    time_change=bool(time_fields&set(options))
    if time_change and (row['fixed'] or row['completed']):raise ScheduleError('event_immutable_for_rescheduling',409)
    if time_change and json.loads(row['invitees']):raise ScheduleError('external_invitee_reschedule_unsupported',409)
    base={'title':row['title'],'description':row['description'],'zone':row['zone'],'allDay':bool(row['all_day']),'invitees':json.loads(row['invitees']),'fixed':bool(row['fixed']),'completed':bool(row['completed'])}
    if not time_change:base.update({'startDate':row['start_date'],'endDate':row['end_date']} if row['all_day'] else {'startUtc':_iso(datetime.fromtimestamp(row['start_utc'],timezone.utc)),'endUtc':_iso(datetime.fromtimestamp(row['end_utc'],timezone.utc))})
    base.update({k:v for k,v in options.items() if k not in {'eventId','expectedVersion','allowConflict'}})
    calendar=_row(c,who,project,'productivity_calendars',row['calendar_id']);values=_event_values(base,calendar)
    conflicts=_conflicts(c,row['calendar_id'],values['start_utc'],values['end_utc'],row['id'])
    if conflicts and not _bool(options.get('allowConflict',False)):raise ScheduleError('event_conflict',409)
    c.execute('UPDATE productivity_events SET '+','.join(k+'=?' for k in values)+',version=version+1,updated_at=? WHERE id=?',(*values.values(),stamp,row['id']))
    c.execute('UPDATE productivity_calendars SET version=version+1,updated_at=? WHERE id=?',(stamp,row['calendar_id']))
    return _row(c,who,project,'productivity_events',row['id'])

def _reminder_schedule(options,now):
    from backend.productivity_time import resolve_local,local_recurrence,zone_data
    text=_text(options.get('text'),1000);start=_text(options.get('startLocal'),30);zone=_text(options.get('zone'),100);_zone(zone)
    fold=options.get('fold')
    if fold is not None:_integer(fold,0,1)
    rule=options.get('recurrence')
    if rule is None:
        instant=resolve_local(start,zone,fold);occurrences=[{'utc':_iso(instant),'local':start}];skipped=[];normalized_rule=None
    else:
        if not isinstance(rule,dict):raise ScheduleError('invalid_recurrence')
        actual=dict(rule)
        if 'count' not in actual and 'until_local' not in actual:actual['count']=30
        if fold is not None:actual.setdefault('fold',fold)
        result=local_recurrence(start,zone,**actual);occurrences=result['occurrences'];skipped=result['skipped'];normalized_rule=result['rule']
    if not occurrences or len(occurrences)>MAX_OCCURRENCES:raise ScheduleError('recurrence_occurrence_limit')
    if any(_utc(x['utc'])>now+timedelta(days=366) for x in occurrences):raise ScheduleError('schedule_horizon_exceeds_366_days')
    policy=options.get('missedPolicy','latest')
    if policy not in {'latest','skip','catch-up'}:raise ScheduleError('invalid_missed_policy')
    grace=_integer(options.get('graceSeconds',86400),0,7*86400)
    if all(_utc(x['utc'])<now-timedelta(seconds=grace) for x in occurrences):raise ScheduleError('schedule_entirely_expired')
    return {'text':text,'start_local':start,'zone':zone,'fold':fold,'rule':_json(normalized_rule) if normalized_rule else None,'missed_policy':policy,'grace_seconds':grace,
      'resolution':_json({'tzdb':zone_data(zone),'skipped_local_times':skipped,'generated_occurrences':len(occurrences),'horizon_days_max':366})},occurrences,skipped

def _binding(row):
    return _hash({k:row[k] for k in ('id','owner_id','project_id','text','start_local','zone','fold','rule','missed_policy','grace_seconds','version','resolution')})
def _grant_and_occurrences(c,who,row,occurrences,stamp):
    identifier=uid();last=max(_utc(x['utc']).timestamp() for x in occurrences)
    expires=min(last+row['grace_seconds']+60,(_utc(stamp)+timedelta(days=366)).timestamp())
    c.execute('INSERT INTO productivity_schedule_grants VALUES(?,?,?,?,?,?,?,?,?,?,0,?,0,?,?)',(identifier,row['id'],row['version'],row['owner_id'],row['project_id'],who.session_id,'inapp.publish',CHANNEL,_binding(row),len(occurrences),expires,VERSION,stamp))
    c.execute('UPDATE productivity_reminders SET grant_id=? WHERE id=?',(identifier,row['id']))
    for occurrence in occurrences:
        c.execute("INSERT INTO productivity_occurrences VALUES(?,?,?,?,?,'pending',NULL,NULL)",(uid(),row['id'],row['version'],_utc(occurrence['utc']).timestamp(),occurrence['local']))
def _cancel_pending(c,reminder_id,reason):
    c.execute('UPDATE productivity_schedule_grants SET revoked=1 WHERE reminder_id=?',(reminder_id,))
    c.execute("UPDATE productivity_occurrences SET state='cancelled',reason=? WHERE reminder_id=? AND state='pending'",(reason,reminder_id))
    c.execute('DELETE FROM productivity_notifications WHERE reminder_id=?',(reminder_id,))

def _control(c):
    from backend.db import data_dir
    quarantined=c.execute("SELECT value FROM guard_control WHERE key='restore_quarantine'").fetchone()
    if (quarantined and quarantined[0]=='1') or os.path.lexists(data_dir()/'RECOVERY_QUARANTINE'):raise ScheduleError('recovery_quarantine',423)
    row=c.execute("SELECT value FROM guard_control WHERE key='local_schedule'").fetchone()
    if not row or row[0]!='enabled':raise ScheduleError('local_scheduler_disabled',503)

def _view_binding(c,who,project,binding):
    if binding['kind']=='deleted':return {'id':binding['id'],'status':'deleted'}
    if binding['kind']=='batch':return {'events':[_view_binding(c,who,project,x) for x in binding['items']],'status':'saved_local_events','invitations_sent':False}
    table={'calendar':'productivity_calendars','event':'productivity_events','reminder':'productivity_reminders','plan':'productivity_plans','notification':'productivity_notifications'}[binding['kind']]
    row=_row(c,who,project,table,binding['id'])
    if 'version' in binding and row['version']!=binding['version']:raise ScheduleError('idempotency_resource_changed',409)
    if binding['kind']=='calendar':return _calendar(row)
    if binding['kind']=='event':return _event(row)
    if binding['kind']=='reminder':return _reminder(c,row)
    if binding['kind']=='plan':return _plan(row)
    return {'id':row['id'],'read_at':row['read_at'],'status':'read'}

def execute_schedule(c,who,project_id,request,idempotency_key=None,now_utc=None):
    request=request.model_dump() if isinstance(request,ScheduleRequest) else request
    validate_request(request);op=request['operation'];options=request['options'];clock=_utc(now_utc);stamp=_iso(clock)
    guard.check_actor(c,who,project_id)
    if op in READS:return _read(c,who,project_id,op,options,clock)
    _lock(c);guard.check_actor(c,who,project_id);_control(c);key=_key(idempotency_key);hashed=_hash(request)
    previous=c.execute('SELECT * FROM productivity_operations WHERE owner_id=? AND project_id=? AND key=?',(who.owner_id,project_id,key)).fetchone()
    if previous:
        if previous['request_hash']!=hashed:raise ScheduleError('idempotency_parameters_changed',409)
        return _view_binding(c,who,project_id,json.loads(previous['binding']))|{'idempotent_replay':True}
    if c.execute('SELECT COUNT(*) FROM productivity_operations WHERE owner_id=? AND project_id=?',(who.owner_id,project_id)).fetchone()[0]>=10000:raise ScheduleError('schedule_operation_history_limit',429)
    # A savepoint prevents a caller that catches validation errors from committing
    # half of a batch, including its calendar revision increments.
    c.execute('SAVEPOINT productivity_operation')
    try:
        binding=_mutate(c,who,project_id,op,options,clock,stamp)
        c.execute('INSERT INTO productivity_operations VALUES(?,?,?,?,?,?)',(who.owner_id,project_id,key,hashed,_json(binding),stamp))
        guard.audit(c,who,project_id,'schedule:'+key,op,'allow','local_operation_persisted')
        c.execute('RELEASE productivity_operation')
    except BaseException:
        c.execute('ROLLBACK TO productivity_operation');c.execute('RELEASE productivity_operation');raise
    return _view_binding(c,who,project_id,binding)|{'idempotent_replay':False}

def _read(c,who,project,op,o,now):
    if op=='calendar.get':return get_calendar(c,who,project,o.get('calendarId'))
    if op=='calendar.list':return {'calendars':[_calendar(r) for r in c.execute('SELECT * FROM productivity_calendars WHERE owner_id=? AND project_id=? ORDER BY created_at LIMIT 50',(who.owner_id,project))],'adapter':'local','external_accounts':[]}
    if op=='event.get':return _event(_row(c,who,project,'productivity_events',o.get('eventId')))
    if op=='event.list':
        calendar=_row(c,who,project,'productivity_calendars',o.get('calendarId'));start,end=_range(o,now)
        return {'events':[_event(r) for r in c.execute("SELECT * FROM productivity_events WHERE calendar_id=? AND start_utc<? AND end_utc>? ORDER BY start_utc LIMIT 1000",(calendar['id'],end,start))],'calendar_version':calendar['version']}
    if op=='event.freebusy':return busy_range(c,who,project,o.get('calendarIds'),o.get('fromUtc',now),o.get('toUtc',now+timedelta(days=30)))
    if op=='reminder.get':return _reminder(c,_row(c,who,project,'productivity_reminders',o.get('reminderId')))
    if op=='reminder.list':return {'reminders':[_reminder(c,r) for r in c.execute('SELECT * FROM productivity_reminders WHERE owner_id=? AND project_id=? ORDER BY created_at DESC LIMIT 200',(who.owner_id,project))]}
    if op=='notification.list':
        limit=_integer(o.get('limit',50),1,100)
        # Access/grant and revision are checked again when opening the inbox.
        rows=c.execute('''SELECT n.* FROM productivity_notifications n JOIN productivity_reminders r ON r.id=n.reminder_id
          JOIN productivity_schedule_grants g ON g.id=r.grant_id WHERE n.owner_id=? AND n.project_id=? AND n.reminder_version=r.version
          AND g.revoked=0 AND r.state IN('active','completed') ORDER BY n.published_at DESC LIMIT ?''',(who.owner_id,project,limit))
        return {'notifications':[dict(r)|{'delivery':'persisted_in_app_inbox_only'} for r in rows],'channel':CHANNEL}
    if op=='plan.get':return _plan(_row(c,who,project,'productivity_plans',o.get('planId')))
    if op=='plan.list':return {'plans':[_plan(r) for r in c.execute('SELECT * FROM productivity_plans WHERE owner_id=? AND project_id=? ORDER BY updated_at DESC LIMIT 100',(who.owner_id,project))]}
    raise ScheduleError('schedule_operation_unsupported')

def _mutate(c,who,project,op,o,clock,stamp):
    def binding(kind,row):return {'kind':kind,'id':row['id'],'version':row['version']}
    if op=='calendar.create':
        name=_text(o.get('name'),200);zone=_text(o.get('zone'),100);_zone(zone)
        if c.execute('SELECT COUNT(*) FROM productivity_calendars WHERE project_id=?',(project,)).fetchone()[0]>=20:raise ScheduleError('calendar_limit',429)
        identifier=uid();c.execute('INSERT INTO productivity_calendars VALUES(?,?,?,?,?,1,?,?)',(identifier,who.owner_id,project,name,zone,stamp,stamp));return {'kind':'calendar','id':identifier,'version':1}
    if op in {'calendar.update','calendar.delete'}:
        row=_row(c,who,project,'productivity_calendars',o.get('calendarId'));_cas(row,o.get('expectedVersion'))
        if op.endswith('delete'):c.execute('DELETE FROM productivity_calendars WHERE id=?',(row['id'],));return {'kind':'deleted','id':row['id']}
        name=_text(o.get('name',row['name']),200);zone=o.get('zone',row['zone']);_zone(zone);c.execute('UPDATE productivity_calendars SET name=?,zone=?,version=version+1,updated_at=? WHERE id=?',(name,zone,stamp,row['id']));return {'kind':'calendar','id':row['id'],'version':row['version']+1}
    if op=='event.create':return binding('event',_insert_event(c,who,project,o,stamp))
    if op=='event.update':return binding('event',_update_event(c,who,project,o,stamp))
    if op=='event.cancel':
        row=_row(c,who,project,'productivity_events',o.get('eventId'));_cas(row,o.get('expectedVersion'))
        c.execute("UPDATE productivity_events SET state='cancelled',version=version+1,updated_at=? WHERE id=?",(stamp,row['id']));c.execute('UPDATE productivity_calendars SET version=version+1,updated_at=? WHERE id=?',(stamp,row['calendar_id']));return {'kind':'event','id':row['id'],'version':row['version']+1}
    if op=='calendar.book':
        calendar=_row(c,who,project,'productivity_calendars',o.get('calendarId'));_cas(calendar,o.get('expectedVersion'));blocks=o.get('blocks')
        if not isinstance(blocks,list) or not 1<=len(blocks)<=30:raise ScheduleError('booking_batch_limit')
        items=[]
        for block in blocks:
            if not isinstance(block,dict) or set(block)-{'id','taskId','title','start','end'} or not {'title','start','end'}<=set(block):raise ScheduleError('booking_block_invalid')
            row=_insert_event(c,who,project,{'calendarId':calendar['id'],'title':block['title'],'startUtc':block['start'],'endUtc':block['end'],'zone':calendar['zone']},stamp);items.append(binding('event',row))
        return {'kind':'batch','items':items}
    if op=='calendar.reschedule':
        changes=o.get('changes')
        if not isinstance(changes,list) or not 1<=len(changes)<=30 or len({x.get('eventId') for x in changes if isinstance(x,dict)})!=len(changes):raise ScheduleError('reschedule_batch_invalid')
        # Remove all selected events from conflict consideration only after all
        # bindings/flags have passed; this permits an atomic swap of free slots.
        for change in changes:
            validate_request({'operation':'event.update','options':change});row=_row(c,who,project,'productivity_events',change.get('eventId'));_cas(row,change.get('expectedVersion'))
            if row['fixed'] or row['completed'] or json.loads(row['invitees']) or row['state']!='active':raise ScheduleError('event_immutable_for_rescheduling',409)
        for change in changes:c.execute("UPDATE productivity_events SET state='cancelled' WHERE id=?",(change['eventId'],))
        items=[]
        for change in changes:
            c.execute("UPDATE productivity_events SET state='active' WHERE id=?",(change['eventId'],));items.append(binding('event',_update_event(c,who,project,change,stamp,reschedule=True)))
        return {'kind':'batch','items':items}
    if op in {'reminder.create','reminder.update','reminder.resume'}:
        if op=='reminder.create':
            if c.execute("SELECT COUNT(*) FROM productivity_reminders WHERE owner_id=? AND project_id=? AND state IN('active','paused')",(who.owner_id,project)).fetchone()[0]>=MAX_ACTIVE_REMINDERS:raise ScheduleError('reminder_limit',429)
            if c.execute('SELECT COUNT(*) FROM productivity_reminders WHERE owner_id=? AND project_id=?',(who.owner_id,project)).fetchone()[0]>=200:raise ScheduleError('reminder_history_limit',429)
            values,occurrences,skipped=_reminder_schedule(o,clock);identifier=uid();version=1
            c.execute('INSERT INTO productivity_reminders(id,owner_id,project_id,'+','.join(values)+',grant_id,state,version,created_at,updated_at) VALUES(?,?,?,'+','.join('?' for _ in values)+',NULL,?,?,?,?)',(identifier,who.owner_id,project,*values.values(),'active',version,stamp,stamp))
        else:
            row=_row(c,who,project,'productivity_reminders',o.get('reminderId'));_cas(row,o.get('expectedVersion'));identifier=row['id'];version=row['version']+1
            if op=='reminder.resume':
                if row['state'] not in {'paused','revoked'}:raise ScheduleError('reminder_not_paused',409)
                fields={'text':row['text'],'startLocal':row['start_local'],'zone':row['zone'],'fold':row['fold'],'recurrence':json.loads(row['rule']) if row['rule'] else None,'missedPolicy':row['missed_policy'],'graceSeconds':row['grace_seconds']}
            else:fields={k:v for k,v in o.items() if k not in {'reminderId','expectedVersion'}}
            values,occurrences,skipped=_reminder_schedule(fields,clock)
            if op=='reminder.resume':occurrences=[x for x in occurrences if _utc(x['utc'])>clock]
            if not occurrences:raise ScheduleError('no_future_occurrences_to_resume',409)
            _cancel_pending(c,identifier,'reminder_revision_changed')
            c.execute('UPDATE productivity_reminders SET '+','.join(k+'=?' for k in values)+",state='active',version=?,updated_at=? WHERE id=?",(*values.values(),version,stamp,identifier))
        row=_row(c,who,project,'productivity_reminders',identifier);_grant_and_occurrences(c,who,row,occurrences,stamp);return {'kind':'reminder','id':identifier,'version':version}
    if op in {'reminder.pause','reminder.cancel','reminder.delete'}:
        row=_row(c,who,project,'productivity_reminders',o.get('reminderId'));_cas(row,o.get('expectedVersion'));_cancel_pending(c,row['id'],op)
        if op=='reminder.delete':c.execute('DELETE FROM productivity_reminders WHERE id=?',(row['id'],));return {'kind':'deleted','id':row['id']}
        state='paused' if op=='reminder.pause' else 'cancelled';c.execute('UPDATE productivity_reminders SET state=?,version=version+1,updated_at=? WHERE id=?',(state,stamp,row['id']));return {'kind':'reminder','id':row['id'],'version':row['version']+1}
    if op=='notification.read':
        row=_row(c,who,project,'productivity_notifications',o.get('notificationId'));c.execute('UPDATE productivity_notifications SET read_at=COALESCE(read_at,?) WHERE id=?',(stamp,row['id']));return {'kind':'notification','id':row['id']}
    if op=='plan.save':
        name=_text(o.get('name'),200);body=o.get('body')
        if not isinstance(body,dict) or len(_json(body).encode())>60000:raise ScheduleError('plan_body_limit')
        if 'planId' in o:
            row=_row(c,who,project,'productivity_plans',o['planId']);_cas(row,o.get('expectedVersion'));c.execute('UPDATE productivity_plans SET name=?,body=?,version=version+1,updated_at=? WHERE id=?',(name,_json(body),stamp,row['id']));return {'kind':'plan','id':row['id'],'version':row['version']+1}
        if c.execute('SELECT COUNT(*) FROM productivity_plans WHERE owner_id=? AND project_id=?',(who.owner_id,project)).fetchone()[0]>=100:raise ScheduleError('plan_storage_limit',429)
        identifier=uid();c.execute('INSERT INTO productivity_plans VALUES(?,?,?,?,?,1,?,?)',(identifier,who.owner_id,project,name,_json(body),stamp,stamp));return {'kind':'plan','id':identifier,'version':1}
    if op=='plan.delete':
        row=_row(c,who,project,'productivity_plans',o.get('planId'));_cas(row,o.get('expectedVersion'));c.execute('DELETE FROM productivity_plans WHERE id=?',(row['id'],));return {'kind':'deleted','id':row['id']}
    raise ScheduleError('schedule_operation_unsupported')

def _durable_authorized(c,row,grant,stamp):
    owner=c.execute('SELECT owner_id FROM local_auth_owner WHERE slot=1').fetchone()
    project=c.execute('SELECT owner_id FROM guard_projects WHERE project_id=?',(row['project_id'],)).fetchone()
    return bool(owner and project and owner[0]==project[0]==row['owner_id'] and grant and not grant['revoked']
      and grant['owner_id']==row['owner_id'] and grant['project_id']==row['project_id'] and grant['reminder_id']==row['id']
      and grant['reminder_version']==row['version'] and grant['operation']=='inapp.publish' and grant['destination']==CHANNEL
      and grant['policy_version']==VERSION and grant['binding_hash']==_binding(row) and grant['expires_at']>=stamp
      and grant['consumed']<grant['max_occurrences'])

def tick(c,now_utc=None,limit=MAX_TICK):
    """Atomic local publication; safe for concurrent processes and restarts.

    Notification and occurrence state commit together. No external effect occurs
    between them, so there is no network-delivery exactly-once claim.
    """
    clock=_utc(now_utc);stamp=clock.timestamp();iso=_iso(clock);limit=_integer(limit,1,MAX_TICK)
    _lock(c)
    try:_control(c)
    except ScheduleError:return {'published':0,'processed':0,'status':'disabled','channel':CHANNEL}
    due=c.execute("SELECT * FROM productivity_occurrences WHERE state='pending' AND due_utc<=? ORDER BY due_utc,id LIMIT ?",(stamp,limit)).fetchall()
    published=skipped=revoked=0;batch_counts={}
    for occurrence in due:
        row=c.execute('SELECT * FROM productivity_reminders WHERE id=?',(occurrence['reminder_id'],)).fetchone()
        if not row or row['state']!='active' or row['version']!=occurrence['reminder_version']:
            c.execute("UPDATE productivity_occurrences SET state='cancelled',reason='inactive_or_changed',processed_at=? WHERE id=?",(iso,occurrence['id']));skipped+=1;continue
        grant=c.execute('SELECT * FROM productivity_schedule_grants WHERE id=?',(row['grant_id'],)).fetchone()
        if not _durable_authorized(c,row,grant,stamp):
            c.execute("UPDATE productivity_occurrences SET state='revoked',reason='durable_authority_invalid',processed_at=? WHERE id=?",(iso,occurrence['id']));c.execute("UPDATE productivity_reminders SET state='revoked',updated_at=? WHERE id=?",(iso,row['id']));c.execute('DELETE FROM productivity_notifications WHERE reminder_id=?',(row['id'],));revoked+=1;continue
        reason=None;late=stamp-occurrence['due_utc'];policy=row['missed_policy']
        if late>row['grace_seconds']:reason='missed_grace_expired'
        elif policy=='skip' and late>60:reason='missed_skip_policy'
        elif policy=='latest' and c.execute("SELECT 1 FROM productivity_occurrences WHERE reminder_id=? AND reminder_version=? AND state='pending' AND due_utc>? AND due_utc<=? LIMIT 1",(row['id'],row['version'],occurrence['due_utc'],stamp)).fetchone():reason='coalesced_to_latest'
        elif policy=='catch-up' and batch_counts.get(row['id'],0)>=10:reason='catch_up_batch_cap'
        if reason:
            c.execute("UPDATE productivity_occurrences SET state='skipped',reason=?,processed_at=? WHERE id=?",(reason,iso,occurrence['id']));skipped+=1
        else:
            c.execute('INSERT INTO productivity_notifications VALUES(?,?,?,?,?,?,?,?,?,NULL)',(uid(),occurrence['id'],row['id'],row['owner_id'],row['project_id'],row['version'],row['text'],occurrence['due_utc'],iso))
            c.execute("UPDATE productivity_occurrences SET state='published',reason='persisted_in_app_inbox',processed_at=? WHERE id=?",(iso,occurrence['id']));c.execute('UPDATE productivity_schedule_grants SET consumed=consumed+1 WHERE id=?',(grant['id'],));published+=1;batch_counts[row['id']]=batch_counts.get(row['id'],0)+1
        if not c.execute("SELECT 1 FROM productivity_occurrences WHERE reminder_id=? AND reminder_version=? AND state='pending' LIMIT 1",(row['id'],row['version'])).fetchone():c.execute("UPDATE productivity_reminders SET state='completed',updated_at=? WHERE id=?",(iso,row['id']))
    return {'published':published,'processed':len(due),'skipped':skipped,'revoked':revoked,'status':'ticked','channel':CHANNEL,'device_delivered':False,'network_calls':0}

async def scheduler_loop(stop:asyncio.Event,interval_seconds=5):
    """Opt-in app lifespan loop; no worker starts by importing this module."""
    if not isinstance(interval_seconds,(int,float)) or not 1<=interval_seconds<=60:raise ScheduleError('scheduler_interval_invalid')
    def once():
        with get_db() as c:return tick(c)
    failures=0;_WORKER_STATUS.update(state='running',error_code=None)
    try:
        while not stop.is_set():
            try:
                result=await asyncio.to_thread(once);failures=0
                _WORKER_STATUS.update(state='running',last_tick=_iso(_utc()),last_result=result,error_code=None)
            except Exception:
                # No payload/exception text in status. One attempt per tick,
                # with bounded retry backoff rather than a busy failure loop.
                failures+=1;_WORKER_STATUS.update(state='degraded',last_tick=_iso(_utc()),error_code='local_tick_failed')
            retry=min(60,interval_seconds*2**min(failures,4))
            try:await asyncio.wait_for(stop.wait(),timeout=retry)
            except TimeoutError:pass
    finally:_WORKER_STATUS.update(state='stopped')

def standalone_worker(stop,*,interval_seconds=5,max_seconds=3600):
    """Explicit local process, independent of the HTTP/UI lifetime.

    Reuses durable scoped grants; never creates a login, grant, background OS
    service or external notification. A bounded run can be stopped with Ctrl+C.
    """
    from backend import local_auth
    from backend.db import data_dir
    if type(interval_seconds) not in (int,float) or not 1<=interval_seconds<=60:raise ScheduleError('scheduler_interval_invalid')
    if type(max_seconds) not in (int,float) or not 1<=max_seconds<=86400:raise ScheduleError('scheduler_duration_invalid')
    if not (data_dir()/'riwaq.sqlite3').is_file():raise ScheduleError('existing_installation_required',503)
    # Never generate replacement key material or initialize/migrate a database.
    local_auth._master_key(create=False)
    started=time.monotonic();ticks=published=processed=failures=0;last=None;reason='duration_limit'
    while not stop.is_set() and time.monotonic()-started<max_seconds:
        try:
            local_auth._master_key(create=False)
            if local_auth.restore_quarantined():raise ScheduleError('recovery_quarantine',423)
            with get_db() as c:
                remaining=max(0,max_seconds-(time.monotonic()-started))
                c.execute('PRAGMA busy_timeout='+str(max(1,min(1000,int(remaining*1000)))))
                c.set_progress_handler(lambda: int(stop.is_set() or time.monotonic()-started>=max_seconds),1000)
                owner=c.execute('SELECT owner_id FROM local_auth_owner WHERE slot=1').fetchone()
                if not owner:raise ScheduleError('existing_installation_required',503)
                last=tick(c)
            ticks+=1;published+=last.get('published',0);processed+=last.get('processed',0);failures=0
            if last['status']=='disabled':reason='disabled';break
        except (local_auth.AuthStorageError,ScheduleError):
            raise
        except Exception:
            failures+=1
            if failures>=3:reason='repeated_storage_failure';break
        wait=min(60,interval_seconds*2**min(failures,4),max(0,max_seconds-(time.monotonic()-started)))
        if stop.wait(wait):reason='cancelled';break
    if stop.is_set():reason='cancelled'
    return {'status':'stopped','reason':reason,'ticks':ticks,'published':published,'processed':processed,
            'seconds':time.monotonic()-started,'channel':CHANNEL,'device_delivered':False,
            'network_calls':0,'http_service_required':False,'os_wake_installed':False,
            'last_result':last}


def main():
    parser=argparse.ArgumentParser(description='Explicit local reminder worker; existing grants, no network or OS service installation.')
    mode=parser.add_mutually_exclusive_group(required=True);mode.add_argument('--once',action='store_true');mode.add_argument('--run',action='store_true')
    parser.add_argument('--interval-seconds',type=float,default=5);parser.add_argument('--max-seconds',type=float,default=3600)
    args=parser.parse_args();stop=threading.Event()
    try:
        if args.once:
            from backend import local_auth
            local_auth._master_key(create=False)
            if local_auth.restore_quarantined():raise ScheduleError('recovery_quarantine',423)
            with get_db() as c:result=tick(c)
        else:result=standalone_worker(stop,interval_seconds=args.interval_seconds,max_seconds=args.max_seconds)
    except KeyboardInterrupt:
        stop.set();print(_json({'status':'stopped','reason':'cancelled','device_delivered':False}));return 0
    except Exception:
        print(_json({'status':'blocked','reason':'existing_identity_rights_or_storage_unavailable','device_delivered':False}));return 1
    print(_json(result));return 1 if result.get('reason')=='repeated_storage_failure' else 0

if __name__=='__main__':raise SystemExit(main())
