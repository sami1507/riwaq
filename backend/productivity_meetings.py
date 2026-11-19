"""Source-bound local meeting records and tasks. Source text never grants actions.

This deterministic reader accepts explicit line labels, retains unclassified text,
and makes no claim to infer decisions from unrestricted conversation.
"""
from __future__ import annotations
import hashlib
import json
import re
from datetime import date, timedelta
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend import guard
from backend.db import now, uid


class MeetingInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    operation: Literal['triage','minutes','actions']
    action: Literal['review','save']= 'review'
    sourceId: str|None=Field(default=None,max_length=80)
    expectedVersion: int|None=Field(default=None,ge=1)
    title: str=Field(default='Meeting notes',min_length=1,max_length=160)
    text: str=Field(default='',max_length=16000)
    referenceDate: str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    zone: str=Field(min_length=1,max_length=80)
    completeSource: bool=True
    weekStartsOn: int|None=Field(default=None,ge=0,le=6)

    @model_validator(mode='after')
    def content(self):
        date.fromisoformat(self.referenceDate)
        # ZoneInfo validates against the installed IANA data; no server-zone fallback.
        from zoneinfo import ZoneInfo
        try:ZoneInfo(self.zone)
        except (KeyError,ValueError) as exc:raise ValueError('Unsupported time zone') from exc
        if bool(self.sourceId)!=(self.expectedVersion is not None):raise ValueError('Saved source requires its expected version')
        return self


def packed(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(v):return hashlib.sha256(packed(v).encode()).hexdigest()


def init(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS productivity_meeting_sources(
      id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      version INTEGER NOT NULL,title TEXT NOT NULL,text TEXT NOT NULL,reference_date TEXT NOT NULL,zone TEXT NOT NULL,
      complete INTEGER NOT NULL,source_hash TEXT NOT NULL,bindings TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS meeting_source_scope ON productivity_meeting_sources(owner_id,project_id,source_hash);
    CREATE TABLE IF NOT EXISTS productivity_tasks(
      id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      source_id TEXT NOT NULL REFERENCES productivity_meeting_sources(id) ON DELETE CASCADE,
      source_version INTEGER NOT NULL,record_id TEXT NOT NULL,revision INTEGER NOT NULL,
      title TEXT NOT NULL,assignee TEXT,due TEXT,state TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
      UNIQUE(owner_id,project_id,source_id,record_id));
    CREATE TABLE IF NOT EXISTS productivity_meeting_effects(
      operation_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      source_id TEXT NOT NULL,source_version INTEGER NOT NULL,task_ids TEXT NOT NULL,created_at TEXT NOT NULL);
    ''')


def source(c,who,pid,sid,version=None):
    guard.check_actor(c,who,pid)
    row=c.execute('SELECT * FROM productivity_meeting_sources WHERE id=? AND owner_id=? AND project_id=? AND deleted=0',(sid,who.owner_id,pid)).fetchone()
    if not row:raise HTTPException(404,'Source is unavailable')
    if version is not None and row['version']!=version:raise HTTPException(409,'Meeting source changed; review the current notes')
    bindings=json.loads(row['bindings'])
    if bindings:guard.validate_resources(c,who,pid,bindings,guard.LOCAL_DESTINATION)
    return dict(row)


LABELS={
 'decision':r'decision|approved decision|قرار|قرار معتمد|החלטה',
 'proposal':r'proposal|suggestion|اقتراح|مقترح|הצעה',
 'rejected':r'rejected|cancelled|canceled|مرفوض|ألغي|ملغي|נדחה|בוטל',
 'objection':r'objection|اعتراض|התנגדות',
 'action':r'action|task|إجراء|اجراء|مهمة|פעולה|משימה',
 'question':r'question|open question|سؤال|سؤال مفتوح|שאלה',
 'done':r'done|completed|منجز|مكتمل|הושלם',
 'reference':r'reference|note|معلومة|مرجع|ملاحظة|הערה',
}
PREFIX=re.compile(r'^\s*(?:\[(?P<id>[A-Za-z0-9_-]{1,40})\]\s*)?(?P<label>[^:：]{1,40})[:：]\s*(?P<text>.+)$')


def due_value(raw,request):
    if not raw:return None,None
    raw=raw.strip();base=date.fromisoformat(request.referenceDate)
    try:return {'kind':'date','value':date.fromisoformat(raw).isoformat(),'zone':request.zone,'basis':request.referenceDate},None
    except ValueError:pass
    offsets={'today':0,'اليوم':0,'היום':0,'tomorrow':1,'غدًا':1,'غدا':1,'מחר':1}
    if raw.casefold() in offsets:return {'kind':'date','value':(base+timedelta(days=offsets[raw.casefold()])).isoformat(),'zone':request.zone,'basis':request.referenceDate},None
    if raw.casefold() in {'next week','الأسبوع القادم','الاسبوع القادم','שבוע הבא'}:
        if request.weekStartsOn is None:return None,'week_start_required'
        start=base+timedelta(days=(request.weekStartsOn-base.weekday())%7 or 7)
        return {'kind':'date_range','start':start.isoformat(),'end':(start+timedelta(days=6)).isoformat(),'zone':request.zone,'basis':request.referenceDate},'specific_due_day_unconfirmed'
    return None,'due_expression_unresolved'


def parse(request):
    records=[];issues=[];seen=set()
    for n,line in enumerate(request.text.splitlines(),1):
        if not line.strip():continue
        if len(records)>=200:raise HTTPException(422,'Notes exceed 200 nonempty lines; split the source')
        match=PREFIX.match(line);kind='unclassified';body=line.strip();record_id='line-'+str(n)
        if match:
            label=match['label'].strip()
            kind=next((k for k,p in LABELS.items() if re.fullmatch(p,label,re.I)),'unclassified')
            if kind!='unclassified':body=match['text'];record_id=match['id'] or record_id
        if record_id in seen:raise HTTPException(422,'Duplicate record marker in notes')
        seen.add(record_id)
        fields=body.split('|');text=fields[0].strip();assignee=None;due=None
        for field in fields[1:]:
            pair=field.split(':',1)
            if len(pair)==2 and pair[0].strip().casefold() in {'owner','مسؤول','المسؤول','אחראי'}:assignee=pair[1].strip() or None
            elif len(pair)==2 and pair[0].strip().casefold() in {'due','موعد','الموعد','מועד'}:due=pair[1].strip() or None
            else:issues.append({'record_id':record_id,'reason':'unrecognized_field_preserved'})
        deadline,problem=due_value(due,request)
        item={'id':record_id,'kind':kind,'text':text,'raw':line,'line':n,'assignee':assignee,'due':deadline,'due_literal':due,'source_trust':'untrusted_content'}
        records.append(item)
        if kind=='unclassified':issues.append({'record_id':record_id,'reason':'classification_unconfirmed'})
        if kind=='action' and not assignee:issues.append({'record_id':record_id,'reason':'assignee_not_stated'})
        if problem:issues.append({'record_id':record_id,'reason':problem})
        if re.search(r'\[\?\]|غير واضح|غير مفهوم|unclear|unintelligible|לא ברור',line,re.I):issues.append({'record_id':record_id,'reason':'uncertain_source_preserved'})
    return records,issues


def run(c,who,pid,request,*,bindings=(),operation_id=None):
    request=MeetingInput.model_validate(request);guard.check_actor(c,who,pid)
    existing=source(c,who,pid,request.sourceId,request.expectedVersion) if request.sourceId else None
    if existing and not request.text:
        request=request.model_copy(update={'text':existing['text'],'title':existing['title'],'referenceDate':existing['reference_date'],'zone':existing['zone'],'completeSource':bool(existing['complete'])})
    if not request.text.strip():raise HTTPException(422,'Meeting notes are required')
    prior_bindings=json.loads(existing['bindings']) if existing else []
    effective_bindings=list({x['file_id']:x for x in prior_bindings+list(bindings)}.values())
    if len(effective_bindings)>8:raise HTTPException(422,'Meeting source lineage exceeds eight documents')
    if effective_bindings:guard.validate_resources(c,who,pid,effective_bindings,guard.LOCAL_DESTINATION)
    signature=digest({'title':request.title,'text':request.text,'referenceDate':request.referenceDate,'zone':request.zone,'completeSource':request.completeSource,'bindings':effective_bindings})
    records,issues=parse(request)
    sid=existing['id'] if existing else None;version=existing['version'] if existing else None
    if request.action=='save':
        if existing:
            if existing['source_hash']!=signature:
                # Without explicit stable record markers a changed line number is not task identity.
                old_tasks=c.execute('SELECT record_id FROM productivity_tasks WHERE source_id=?',(sid,)).fetchall()
                if any(x[0].startswith('line-') for x in old_tasks):raise HTTPException(409,'Updating a source with saved tasks requires stable [record-id] markers; preserve the original and review a new source')
                version+=1
                c.execute('UPDATE productivity_meeting_sources SET version=?,title=?,text=?,reference_date=?,zone=?,complete=?,source_hash=?,bindings=?,updated_at=? WHERE id=? AND version=?',(version,request.title,request.text,request.referenceDate,request.zone,int(request.completeSource),signature,packed(effective_bindings),now(),sid,existing['version']))
        else:
            if c.execute('SELECT 1 FROM productivity_meeting_sources WHERE owner_id=? AND project_id=? AND source_hash=? AND deleted=1',(who.owner_id,pid,signature)).fetchone():
                raise HTTPException(409,'This source was deleted; an old operation cannot recreate it')
            duplicate=c.execute('SELECT id,version FROM productivity_meeting_sources WHERE owner_id=? AND project_id=? AND source_hash=? AND deleted=0',(who.owner_id,pid,signature)).fetchone()
            if duplicate:sid,version=duplicate['id'],duplicate['version'];source(c,who,pid,sid)
            else:
                if c.execute('SELECT COUNT(*) FROM productivity_meeting_sources WHERE owner_id=? AND project_id=?',(who.owner_id,pid)).fetchone()[0]>=500:raise HTTPException(429,'Meeting source limit reached')
                sid=uid();version=1
                c.execute('INSERT INTO productivity_meeting_sources(id,owner_id,project_id,version,title,text,reference_date,zone,complete,source_hash,bindings,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(sid,who.owner_id,pid,version,request.title,request.text,request.referenceDate,request.zone,int(request.completeSource),signature,packed(effective_bindings),now(),now()))
    tasks=[]
    if request.operation in {'actions','triage'} and request.action=='save':
        for r in records:
            if r['kind']!='action':continue
            old=c.execute('SELECT * FROM productivity_tasks WHERE source_id=? AND record_id=?',(sid,r['id'])).fetchone()
            fields=(r['text'],r['assignee'],packed(r['due']) if r['due'] else None)
            if old:
                changed=fields!=(old['title'],old['assignee'],old['due'])
                if old['state'] in {'completed','cancelled'} and changed:
                    issues.append({'record_id':r['id'],'reason':'completed_or_cancelled_task_preserved'});tasks.append(dict(old));continue
                if old['assignee']!=r['assignee'] and old['source_version']!=version:
                    issues.append({'record_id':r['id'],'reason':'reassignment_needs_task_review'});tasks.append(dict(old));continue
                if changed or old['source_version']!=version:c.execute('UPDATE productivity_tasks SET title=?,assignee=?,due=?,source_version=?,revision=revision+1,updated_at=? WHERE id=?',(*fields,version,now(),old['id']))
                tid=old['id']
            else:
                tid=uid();c.execute('INSERT INTO productivity_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(tid,who.owner_id,pid,sid,version,r['id'],1,*fields,'open',now(),now()))
            tasks.append(dict(c.execute('SELECT * FROM productivity_tasks WHERE id=?',(tid,)).fetchone()))
        present={r['id'] for r in records if r['kind']=='action'}
        stale=c.execute('SELECT id,record_id FROM productivity_tasks WHERE source_id=? AND state="open"',(sid,)).fetchall()
        issues.extend({'task_id':r['id'],'record_id':r['record_id'],'reason':'prior_action_missing_or_rejected_review_required'} for r in stale if r['record_id'] not in present)
    for t in tasks:
        for k in ('owner_id','project_id'):t.pop(k,None)
        if isinstance(t.get('due'),str):t['due']=json.loads(t['due'])
    if request.action=='save' and operation_id:
        c.execute('INSERT OR REPLACE INTO productivity_meeting_effects VALUES(?,?,?,?,?,?,?)',(operation_id,who.owner_id,pid,sid,version,packed([t['id'] for t in tasks]),now()))
    return {'operation':request.operation,'status':'partial' if issues or not request.completeSource else ('saved' if request.action=='save' else 'proposed'),
            'saved':request.action=='save','source':{'id':sid,'version':version,'sha256':signature,'date':request.referenceDate,'zone':request.zone,'complete':request.completeSource},
            'records':records,'tasks':tasks,'issues':issues,'external_assignments_sent':0,'method':'explicit-labeled-lines; unclassified content retained; no semantic decision inference',
            'meeting_binding':{'id':sid,'version':version} if sid else None}


def list_sources(c,who,pid):
    guard.check_actor(c,who,pid);items=[]
    for row in c.execute('SELECT id FROM productivity_meeting_sources WHERE owner_id=? AND project_id=? AND deleted=0 ORDER BY updated_at DESC LIMIT 100',(who.owner_id,pid)):
        try:v=source(c,who,pid,row['id'])
        except (HTTPException,guard.Denied):continue
        items.append({k:v[k] for k in ('id','title','version','reference_date','zone','complete','updated_at')})
    return items


def list_tasks(c,who,pid):
    guard.check_actor(c,who,pid);items=[]
    for row in c.execute('SELECT * FROM productivity_tasks WHERE owner_id=? AND project_id=? ORDER BY updated_at DESC LIMIT 300',(who.owner_id,pid)):
        try:s=source(c,who,pid,row['source_id'])
        except (HTTPException,guard.Denied):continue
        item=dict(row);item.pop('owner_id');item.pop('project_id');item['due']=json.loads(item['due']) if item['due'] else None
        item['source_changed']=s['version']!=item['source_version'];items.append(item)
    return items


class TaskUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    expectedRevision:int=Field(ge=1)
    state:Literal['open','completed','cancelled']


def update_task(c,who,pid,tid,request):
    guard.check_actor(c,who,pid)
    row=c.execute('SELECT * FROM productivity_tasks WHERE id=? AND owner_id=? AND project_id=?',(tid,who.owner_id,pid)).fetchone()
    if not row:raise HTTPException(404,'Task unavailable')
    source(c,who,pid,row['source_id'])
    if row['revision']!=request.expectedRevision:raise HTTPException(409,'Task changed; refresh before updating')
    c.execute('UPDATE productivity_tasks SET state=?,revision=revision+1,updated_at=? WHERE id=?',(request.state,now(),tid))
    return {'id':tid,'revision':row['revision']+1,'state':request.state,'external_notifications_sent':0}
