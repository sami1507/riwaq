"""Scoped diagnostics over existing task/Guard stores; no payload logging."""
from datetime import datetime, timezone
import json, math, re, sqlite3, statistics
from backend import guard

EVENTS=frozenset({'queued','started','tool_started','tool_finished','verifying','completed','partial','failed','cancel_requested','cancelled','resumed','timed_out','checkpointed','unknown','interrupted'})
STATES=frozenset({'queued','running','verifying','completed','partial','failed','cancelling','cancelled','unknown','blocked','interrupted','pending','waiting'})
SAFE_ID=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')
SAFE_CODE=re.compile(r'^[a-z][a-z0-9_]{0,79}$')

def _metadata(metadata):
    if not isinstance(metadata,dict):raise ValueError('invalid_audit_metadata')
    data=dict(metadata)
    if set(data)-{'state','phase','attempt','elapsed_ms','error_code','resource_id','revision'}:raise ValueError('audit_field_not_allowed')
    for key,value in data.items():
        if key=='state' and value not in STATES:raise ValueError('invalid_audit_state')
        if key in {'phase','error_code'} and (not isinstance(value,str) or not SAFE_CODE.fullmatch(value)):raise ValueError('invalid_audit_code')
        if key=='resource_id' and (not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{32}',value)):raise ValueError('invalid_audit_resource')
        if key in {'attempt','elapsed_ms','revision'} and (type(value) is not int or not 0<=value<=10**9):raise ValueError('invalid_audit_number')
    return data

def record_event(c, who, project_id, run_id, event, metadata=None, *, terminal_cleanup=False):
    """Fail closed: append in caller's transaction before critical state commit.

    Allowed metadata is constructed by execution code. Exceptions/payloads and
    unknown fields are rejected rather than logged and subsequently redacted.
    """
    if terminal_cleanup:
        if event not in {'failed','cancelled','timed_out','unknown','interrupted'}:raise ValueError('audit_cleanup_must_be_terminal')
    else:guard.check_actor(c,who,project_id)
    if event not in EVENTS or not SAFE_ID.fullmatch(run_id):raise ValueError('invalid_audit_event')
    row=c.execute('SELECT owner_id,project_id FROM skill_runs WHERE id=?',(run_id,)).fetchone()
    if not row or row['owner_id']!=who.owner_id or row['project_id']!=project_id:raise guard.Denied('resource_not_authorized')
    data=_metadata(metadata or {})
    return guard.audit(c,who,project_id,run_id,'skill.'+event,'observe',json.dumps(data,sort_keys=True,separators=(',',':')))

def audit_trace(c,who,project_id,run_id,limit=100):
    guard.check_actor(c,who,project_id)
    if not isinstance(run_id,str) or not SAFE_ID.fullmatch(run_id):raise ValueError('invalid_audit_identifier')
    if type(limit) is not int or not 1<=limit<=200:raise ValueError('invalid_audit_limit')
    run=c.execute('SELECT owner_id,project_id FROM skill_runs WHERE id=?',(run_id,)).fetchone()
    if not run or run['owner_id']!=who.owner_id or run['project_id']!=project_id:raise guard.Denied('resource_not_authorized')
    # Legacy Guard entries are not payload-bearing, but only fixed event fields
    # are shown here. No request/body/output or arbitrary exception text joins.
    rows=c.execute('SELECT id,operation,decision,reason,policy_version,created_at FROM guard_audit WHERE owner_id=? AND project_id=? AND task_id=? ORDER BY created_at,id LIMIT ?', (who.owner_id,project_id,run_id,limit+1)).fetchall()
    result=[]
    for row in rows[:limit]:
        item={k:row[k] for k in ('id','decision','policy_version','created_at')}
        operation=row['operation'];item['operation']=operation if re.fullmatch(r'[a-z0-9_.:-]{1,80}',operation) else 'legacy_operation'
        if operation.startswith('skill.'):
            try:
                item['details']=_metadata(json.loads(row['reason']))
            except (ValueError,TypeError):item['details']={'error_code':'invalid_stored_event'}
        else:item['details']={'reason_code':row['reason'] if SAFE_CODE.fullmatch(row['reason']) else 'legacy_reason_omitted'}
        result.append(item)
    return {'events':result,'truncated':len(rows)>limit,'retention':'linked to local database; no mail or model payload captured','collector':'transactional SQLite; write failures propagate'}

def _instant(value):
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError,AttributeError,TypeError):return None

def snapshot(c,who,project_id,*,at=None,worker=None):
    guard.check_actor(c,who,project_id)
    at=at or datetime.now(timezone.utc)
    if at.tzinfo is None:raise ValueError('explicit_monitor_time_required')
    from backend.productivity_schedule import worker_status
    state=worker_status() if worker is None else dict(worker)
    last=_instant(state.get('last_tick'));age=max(0,(at-last).total_seconds()) if last else None
    # The existing worker ticks every five seconds. Three missed ticks is a
    # diagnostic stale signal, not a delivery SLA or fabricated alert service.
    health='unknown' if last is None else 'stale' if age>15 else 'ready' if state.get('state')=='running' else 'degraded'
    if state.get('state') in {'stopped','not_started'}:health='unavailable'
    result={'observed_at':at.isoformat(),'scope':'current_project','database':{'state':'unknown'},'worker':{'state':health,'last_tick':last.isoformat() if last else None,'age_seconds':age,'stale_after_seconds':15,'basis':'three nominal 5-second worker ticks','device_delivery_verified':False},'queue':{'state':'unknown'},'outcomes':{},'timing':{'state':'unknown','unit':'seconds','metric':'persisted_task_total_elapsed_including_queue'},'alerts_sent':False}
    try:
        ok=c.execute('PRAGMA quick_check(1)').fetchone()[0]=='ok'
        result['database']={'state':'ready' if ok else 'unavailable','check':'sqlite_quick_check'}
        if not ok:return result
        rows=c.execute('SELECT state,created_at,updated_at FROM skill_runs WHERE owner_id=? AND project_id=? ORDER BY created_at DESC LIMIT 200',(who.owner_id,project_id)).fetchall()
        pending=[r for r in rows if r['state']=='queued'];ages=[(at-x).total_seconds() for r in pending if (x:=_instant(r['created_at']))]
        result['queue']={'state':'observed','queued':len(pending),'oldest_queued_age_seconds':max(ages,default=0),'sample_limit':200,'complete':len(rows)<200}
        times=[]
        for row in rows:
            status=row['state'];result['outcomes'][status]=result['outcomes'].get(status,0)+1
            start=_instant(row['created_at']);end=_instant(row['updated_at'])
            if status in {'completed','partial','failed','cancelled'} and start and end and end>=start:times.append((end-start).total_seconds())
        result['timing'].update(state='observed' if times else 'unknown',samples=len(times),median=statistics.median(times) if times else None,range=[min(times),max(times)] if times else None)
        if len(times)>=100:result['timing']['p95']=sorted(times)[math.ceil(.95*len(times))-1]
        result['reminders']={'pending':c.execute("SELECT COUNT(*) FROM productivity_occurrences o JOIN productivity_reminders r ON r.id=o.reminder_id WHERE r.owner_id=? AND r.project_id=? AND o.state='pending'",(who.owner_id,project_id)).fetchone()[0]}
    except sqlite3.DatabaseError:
        # Do not propagate SQLite text (paths/values) into diagnostics.
        result.update(database={'state':'unavailable','reason_code':'storage_check_failed'},queue={'state':'unknown'},outcomes={},timing={'state':'unknown'})
    return result
