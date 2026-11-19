"""Authenticated local productivity entry points; no implicit live account access."""
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from backend import skills,guard,productivity_schedule as schedule,productivity_mail as mail,productivity_meetings as meetings
from backend.db import get_db

router=APIRouter(prefix='/api/projects/{pid}/productivity')

def call(function,*args,**kwargs):
    try:return function(*args,**kwargs)
    except guard.Denied as exc:raise HTTPException(403,'Current project authority is unavailable') from exc
    except (schedule.ScheduleError,mail.MailError) as exc:raise HTTPException(getattr(exc,'status_code',422),str(exc)) from exc


@router.get('/status')
def status(pid:str):
    from backend.productivity_time import zone_data
    with get_db() as c:
        who=skills.actor();skills.authorize(c,who,pid)
        state=c.execute("SELECT value FROM guard_control WHERE key='local_schedule'").fetchone()
    worker=schedule.worker_status()
    return {'calendar':'local','external_calendar':'not_configured','scheduler':{k:worker[k] for k in ('state','last_tick','error_code')}|{'enabled':bool(state and state[0]=='enabled')},
            'notification_channel':'in-app inbox; execution while Riwaq service runs; missed-run policy on restart','device_notifications':False,
            'mail':'local imported RFC822 and drafts; SMTP requires operator configuration and explicit reviewed send','mail_inbox_sync':False,'time':zone_data('UTC')}


@router.post('/schedule')
def scheduling(pid:str,body:schedule.ScheduleRequest):
    # Read-only lookup endpoint. Mutations run through the existing skill budget,
    # operation identity, cancellation and execution receipt instead.
    if body.operation not in schedule.READS:raise HTTPException(422,'Use the task executor for a schedule change')
    with get_db() as c:return call(schedule.execute_schedule,c,skills.actor(),pid,body)


@router.get('/mail/accounts')
def accounts(pid:str):return call(mail.list_accounts,skills.actor(),pid)

@router.post('/mail/accounts')
def create_account(pid:str,body:mail.AccountInput):return call(mail.create_account,skills.actor(),pid,body)

@router.get('/mail/accounts/{aid}/contacts')
def contacts(pid:str,aid:str):return call(mail.list_contacts,skills.actor(),pid,aid)

@router.post('/mail/accounts/{aid}/contacts')
def save_contact(pid:str,aid:str,body:mail.ContactInput):return call(mail.save_contact,skills.actor(),pid,aid,body)

@router.post('/mail/import')
def import_message(pid:str,body:mail.ImportInput):return call(mail.import_message,skills.actor(),pid,body)

@router.get('/mail/accounts/{aid}/messages')
def messages(pid:str,aid:str):return call(mail.list_messages,skills.actor(),pid,aid)

@router.get('/mail/accounts/{aid}/messages/{mid}')
def message(pid:str,aid:str,mid:str):return call(mail.get_message,skills.actor(),pid,aid,mid)

@router.delete('/mail/accounts/{aid}/messages/{mid}')
def delete_message(pid:str,aid:str,mid:str,expectedRevision:int):return call(mail.delete_message,skills.actor(),pid,aid,mid,expected_revision=expectedRevision)

@router.get('/mail/accounts/{aid}/drafts')
def drafts(pid:str,aid:str):return call(mail.list_drafts,skills.actor(),pid,aid)

@router.get('/mail/accounts/{aid}/drafts/{did}')
def draft(pid:str,aid:str,did:str):return call(mail.get_draft,skills.actor(),pid,aid,did)


class SendInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    accountId:str=Field(pattern=r'^[a-f0-9]{32}$')
    draftId:str=Field(pattern=r'^[a-f0-9]{32}$')
    expectedRevision:int=Field(ge=1)
    expectedPayloadSha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    idempotencyKey:str=Field(min_length=8,max_length=80,pattern=r'^[a-zA-Z0-9_-]+$')
    attachmentsAuthorized:bool=False


@router.post('/mail/send',status_code=202)
async def send(pid:str,body:SendInput):
    from backend import skill_batch04
    from backend.skill_core import SkillRequest
    request=SkillRequest.model_validate({'request':'Send the reviewed email draft','requestedSkills':['authorized-email-send'],
       'inputs':{'mailOperation':{'operation':'authorized-email-send',**body.model_dump(exclude={'idempotencyKey','attachmentsAuthorized'})}},'idempotencyKey':body.idempotencyKey})
    token=skill_batch04.SEND_CONTEXT.set({'project_id':pid,'attachments_authorized':body.attachmentsAuthorized})
    try:return await skills.create_run(pid,request)
    except (mail.MailError,schedule.ScheduleError) as exc:raise HTTPException(422,str(exc)) from exc
    finally:skill_batch04.SEND_CONTEXT.reset(token)


@router.get('/meeting-sources')
def sources(pid:str):
    with get_db() as c:return call(meetings.list_sources,c,skills.actor(),pid)

@router.get('/meeting-sources/{sid}')
def get_source(pid:str,sid:str):
    with get_db() as c:v=call(meetings.source,c,skills.actor(),pid,sid)
    return {k:v[k] for k in ('id','version','title','text','reference_date','zone','complete','source_hash','updated_at')}

@router.delete('/meeting-sources/{sid}')
def delete_source(pid:str,sid:str,expectedVersion:int):
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');who=skills.actor();call(meetings.source,c,who,pid,sid,expectedVersion)
        c.execute("UPDATE productivity_meeting_sources SET deleted=1,text='',title='',version=version+1 WHERE id=?",(sid,))
        c.execute('DELETE FROM productivity_tasks WHERE source_id=?',(sid,))
    return {'id':sid,'deleted':True,'tasks_removed':True}

@router.get('/tasks')
def tasks(pid:str):
    with get_db() as c:return call(meetings.list_tasks,c,skills.actor(),pid)

@router.patch('/tasks/{tid}')
def update_task(pid:str,tid:str,body:meetings.TaskUpdate):
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');return call(meetings.update_task,c,skills.actor(),pid,tid,body)
