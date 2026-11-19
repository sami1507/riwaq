"""Trusted transitions and total deadlines for the existing skill_runs worker.

No independent queue or dispatcher. SQLite's transaction is the admission and
write boundary; a model response is never a state-transition authorization.
"""
from datetime import datetime
import json
import math
import time
from backend import guard
from backend.db import now

TOTAL_SECONDS=180
ACTIVE_LIMIT=2
TRANSITIONS={
    'queued':{'running','cancelling','failed','unknown','cancelled'},
    'running':{'running','completed','partial','failed','cancelling','unknown','cancelled'},
    'cancelling':{'cancelled','failed','unknown'},
    'unknown':{'queued'},
    'completed':set(),'partial':set(),'failed':set(),'cancelled':set(),
}

def init(c):
    c.executescript('''CREATE TABLE IF NOT EXISTS skill_run_controls(
      run_id TEXT PRIMARY KEY REFERENCES skill_runs(id) ON DELETE CASCADE,
      deadline_epoch REAL NOT NULL, admitted_epoch REAL NOT NULL,
      attempt INTEGER NOT NULL CHECK(attempt>=1),
      state_revision INTEGER NOT NULL CHECK(state_revision>=1));''')

def _stamp(value):
    try:
        d=datetime.fromisoformat(value)
        if d.tzinfo is None:raise ValueError()
        result=d.timestamp()
        if not math.isfinite(result):raise ValueError()
        return result
    except (ValueError,TypeError):raise ValueError('Task admission timestamp is unavailable') from None

def admit(c,row,*,at=None):
    at=time.time() if at is None else at
    if not math.isfinite(at):raise ValueError('Invalid admission time')
    c.execute('INSERT INTO skill_run_controls VALUES(?,?,?,?,?)',
              (row['id'],at+TOTAL_SECONDS,at,1,1))

def remaining(c,row,*,at=None):
    """Persisted deadline includes admission/queue time; no new grant on retry."""
    at=time.time() if at is None else at
    ctl=c.execute('SELECT * FROM skill_run_controls WHERE run_id=?',(row['id'],)).fetchone()
    deadline=ctl['deadline_epoch'] if ctl else _stamp(row['created_at'])+TOTAL_SECONDS
    if ctl and ctl['attempt']==1:deadline=min(deadline,_stamp(row['created_at'])+TOTAL_SECONDS)
    left=deadline-at
    if not math.isfinite(left) or left<=0:raise TimeoutError('Task total deadline expired')
    return min(left,TOTAL_SECONDS)

def admission_available(c):
    # A cancellation request is not proof that the underlying work has stopped.
    return c.execute("SELECT COUNT(*) FROM skill_runs WHERE state IN ('queued','running','cancelling')").fetchone()[0]<ACTIVE_LIMIT

def resume_deadline(c,row):
    """Called only after explicit checkpoint/CAS/lease/permission acceptance."""
    stamp=time.time()
    ctl=c.execute('SELECT * FROM skill_run_controls WHERE run_id=?',(row['id'],)).fetchone()
    if ctl:c.execute('UPDATE skill_run_controls SET deadline_epoch=?,admitted_epoch=?,attempt=attempt+1,state_revision=state_revision+1 WHERE run_id=?',(stamp+TOTAL_SECONDS,stamp,row['id']))
    else:admit(c,row,at=stamp)

def transition(c,who,row,target,document,*,expected_revision=None,reason=None,cleanup=False):
    if cleanup and target not in {'failed','cancelled','unknown'}:raise ValueError('Cleanup cannot start or complete work')
    if not cleanup:guard.check_actor(c,who,row['project_id'])
    current=c.execute('SELECT * FROM skill_runs WHERE id=? AND owner_id=?',(row['id'],who.owner_id)).fetchone()
    if not current:raise ValueError('Task unavailable')
    if target not in TRANSITIONS.get(current['state'],set()):raise ValueError('Task state transition rejected')
    ctl=c.execute('SELECT * FROM skill_run_controls WHERE run_id=?',(row['id'],)).fetchone()
    if expected_revision is not None and (not ctl or ctl['state_revision']!=expected_revision):raise ValueError('Task state version conflict')
    if target=='completed':
        if not document.get('steps') or any(s['status']!='completed' for s in document['steps']):raise ValueError('Incomplete steps cannot complete a task')
        if document.get('receipt',{}).get('operation_id')!=row['id'] or not document['receipt'].get('evidence'):raise ValueError('Completion receipt is required')
    document['status']=target
    c.execute('UPDATE skill_runs SET state=?,document=?,updated_at=? WHERE id=? AND state=?',
              (target,json.dumps(document,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False),now(),row['id'],current['state']))
    if ctl:c.execute('UPDATE skill_run_controls SET state_revision=state_revision+1 WHERE run_id=?',(row['id'],))
    from backend.skill_observability import record_event
    event={'running':'started' if current['state']=='queued' else 'checkpointed','cancelling':'cancel_requested','unknown':'interrupted'}.get(target,target)
    if reason=='deadline':event='timed_out'
    if target=='queued':event='resumed'
    record_event(c,who,row['project_id'],row['id'],event,{'state':target,'revision':(ctl['state_revision']+1 if ctl else 1)},terminal_cleanup=cleanup)

def public_control(c,row):
    ctl=c.execute('SELECT * FROM skill_run_controls WHERE run_id=?',(row['id'],)).fetchone()
    return {'total_seconds':TOTAL_SECONDS,'includes_queue':True,'active_limit':ACTIVE_LIMIT,
        'state_revision':ctl['state_revision'] if ctl else None,
        'attempt':ctl['attempt'] if ctl else None,
        'deadline_epoch':ctl['deadline_epoch'] if ctl else None,
        'cancel_requested':row['state']=='cancelling',
        'execution_outcome_unknown':row['state']=='unknown'}
