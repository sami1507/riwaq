"""Productivity adapters over the existing trusted task executor and Guard."""
from __future__ import annotations
from contextvars import ContextVar
import json
from fastapi import HTTPException
from backend import guard,skill_operations
from backend.db import get_db

PLANNING={'daily-planning':'daily','weekly-planning':'weekly','shift-schedule-planning':'shift','calendar-conflict-check':'conflicts',
          'timezone-scheduling':'timezone','time-blocking':'time-blocking','meeting-time-search':'meeting-search','contingency-scheduling':'replan','meeting-agenda':'agenda'}
SCHEDULE={'calendar-event-creation':{'calendar.create','calendar.list','calendar.get','calendar.update','calendar.delete','event.create','event.update','event.cancel','event.get','event.list'},
          'calendar-conflict-check':{'event.freebusy'},'reminder-creation':{'reminder.create','reminder.update','reminder.pause','reminder.resume','reminder.cancel','reminder.delete','reminder.get','reminder.list','notification.list','notification.read'},
          'recurring-task-setup':{'reminder.create','reminder.update','reminder.pause','reminder.resume','reminder.cancel','reminder.get','reminder.list'},
          'time-blocking':{'calendar.book'},'contingency-scheduling':{'calendar.reschedule'},'daily-planning':{'plan.save','plan.get','plan.list','plan.delete'},'weekly-planning':{'plan.save','plan.get','plan.list','plan.delete'}}
MAIL={'email-search','email-thread-summary','email-action-extraction','email-drafting','email-reply-context','recipient-resolution','attachment-verification','authorized-email-send','email-followup-tracking','bounce-analysis'}
MEETINGS={'task-inbox-triage':'triage','meeting-minutes':'minutes','meeting-actions':'actions'}
SKILLS=set(PLANNING)|set(SCHEDULE)|MAIL|set(MEETINGS)
SEND_CONTEXT=ContextVar('riwaq_explicit_mail_send',default=None)
SEND_GRANTS={}


def route(goal):
    import re
    from backend.skill_core import _normalized,_negated
    text=_normalized(re.sub(r'`[^`]*`|"[^"\n]*"|“[^”\n]*”',' ',goal))
    if re.search(r'explain|اشرح|הסבר',text):return []
    rules=[('daily-planning',r'خطط يوم|خطة يوم|plan (?:my |the )?day|תכנן יום'),('weekly-planning',r'خطط الاسبوع|خطة اسبوع|plan (?:my |the )?week|תכנן שבוע'),
      ('shift-schedule-planning',r'خطط.*وردي|plan.*shift|תכנן.*משמר'),('calendar-event-creation',r'انش[ئء] (?:حدث|موعد)|create (?:calendar )?event|צור אירוע'),
      ('calendar-conflict-check',r'تعارض المواعيد|calendar conflicts?|התנגשות.*יומן'),('timezone-scheduling',r'مناطق زمنية|time ?zones?|אזורי זמן'),
      ('recurring-task-setup',r'تذكير متكرر|recurring (?:task|reminder)|תזכורת חוזרת'),('reminder-creation',r'ذكرني|remind me|הזכר לי'),
      ('task-inbox-triage',r'فرز المهام|triage tasks|מיין משימות'),('time-blocking',r'فترات التركيز|time blocking|זמן מיקוד'),
      ('meeting-time-search',r'جد وقت اجتماع|meeting time|זמן לפגישה'),('contingency-scheduling',r'اعد الجدولة|reschedule|תזמן מחדש'),
      ('email-search',r'ابحث.*البريد|search (?:my )?email|חפש.*דואר'),('email-thread-summary',r'لخص.*(?:البريد|سلسلة)|summari[sz]e.*(?:email|thread)|סכם.*דואר'),
      ('email-action-extraction',r'استخرج اجراءات البريد|email actions|פעולות.*דואר'),('email-drafting',r'اكتب.*(?:بريد|مسودة)|draft.*email|כתוב.*טיוט'),
      ('email-reply-context',r'اكتب رد|draft.*reply|כתוב תשובה'),('recipient-resolution',r'تحقق.*المستلمين|resolve recipients|בדוק נמענים'),
      ('attachment-verification',r'تحقق.*المرفقات|verify attachments|בדוק קבצים מצורפים'),('authorized-email-send',r'ارسل.*(?:البريد|الرسالة)|send.*email|שלח.*דואר'),
      ('email-followup-tracking',r'متابعة الردود|track.*repl|עקוב.*תשוב'),('bounce-analysis',r'فشل التسليم|bounce|כשל.*מסירה'),
      ('meeting-agenda',r'جدول الاجتماع|اجندة|meeting agenda|סדר יום'),('meeting-minutes',r'محضر الاجتماع|meeting minutes|פרוטוקול'),('meeting-actions',r'مهام.*الاجتماع|meeting actions|משימות.*פגישה')]
    selected=[slug for slug,pattern in rules if any(not _negated(text,m.start()) for m in re.finditer(pattern,text))]
    if 'recurring-task-setup' in selected and 'reminder-creation' in selected:selected.remove('reminder-creation')
    if 'email-reply-context' in selected and 'email-drafting' in selected:selected.remove('email-drafting')
    return selected


def requested_operations(inputs):
    out=[]
    for field,mapping in [('planningOperation',PLANNING),('meetingOperation',MEETINGS)]:
        value=getattr(inputs,field,None)
        if value:out.extend(s for s,op in mapping.items() if op==value['operation'])
    if inputs.mailOperation:out.append(inputs.mailOperation['operation'])
    if inputs.scheduleOperation:
        op=inputs.scheduleOperation['operation'];options=inputs.scheduleOperation['options']
        targets=[s for s,ops in SCHEDULE.items() if op in ops]
        if op.startswith('reminder.'):
            out.append('recurring-task-setup' if options.get('recurrence') else 'reminder-creation')
        else:out.append(targets[0])
    return list(dict.fromkeys(out))


def validate_plan(selected,inputs):
    targets=set(selected)&SKILLS;covered=set()
    for field,mapping in [('planningOperation',PLANNING),('meetingOperation',MEETINGS)]:
        value=getattr(inputs,field,None)
        if value:
            match={s for s,op in mapping.items() if op==value['operation']}&targets
            if len(match)!=1:raise ValueError('Productivity input does not match the selected skill')
            covered|=match
    if inputs.scheduleOperation:
        match={s for s,ops in SCHEDULE.items() if inputs.scheduleOperation['operation'] in ops}&targets
        if len(match)!=1:raise ValueError('Schedule input must match one skill')
        if covered&match:raise ValueError('Separate a planning proposal from a calendar write')
        if 'recurring-task-setup' in match and inputs.scheduleOperation['operation'] in {'reminder.create','reminder.update'} and not inputs.scheduleOperation['options'].get('recurrence'):
            raise ValueError('A recurring reminder requires an explicit recurrence rule')
        covered|=match
    if inputs.mailOperation:
        if inputs.mailOperation['operation'] not in targets:raise ValueError('Mail input and selected skill differ')
        covered.add(inputs.mailOperation['operation'])
    if targets!=covered:raise ValueError('Confirmed productivity inputs are required before execution')
    if inputs.scheduleOperation and inputs.scheduleOperation['operation'] not in __import__('backend.productivity_schedule',fromlist=['READS']).READS:
        if inputs.documentIds or inputs.sourceRefs:raise ValueError('Persistent schedule writes currently require direct reviewed input, not document-derived text')
    if inputs.mailOperation and (inputs.documentIds or inputs.sourceRefs):raise ValueError('Use the mail draft attachment field for exact reviewed file references')


def prepare_send(who,pid,rid,inputs):
    request=inputs.mailOperation
    if not request or request['operation']!='authorized-email-send' or request.get('submissionId'):return
    context=SEND_CONTEXT.get()
    if not context or context['project_id']!=pid:raise HTTPException(403,'Sending requires the reviewed draft send action')
    from backend import productivity_mail
    SEND_GRANTS[rid]=productivity_mail.approve_send(who,pid,request['accountId'],request['draftId'],request['expectedRevision'],request['expectedPayloadSha256'],operation_id=rid,attachments_authorized=context['attachments_authorized'])


def recheck(c,who,pid,result):
    from backend import productivity_mail,productivity_meetings,productivity_schedule
    try:
        for binding in result.get('productivity_bindings',[]):
            if binding['kind']=='mail':productivity_mail.recheck_binding(c,who,pid,binding['value'])
            elif binding['kind']=='meeting':productivity_meetings.source(c,who,pid,binding['value']['id'],binding['value']['version'])
            elif binding['kind']=='schedule':productivity_schedule._view_binding(c,who,pid,binding['value'])
        for effect in result.get('effects',[]):
            if effect.get('kind') in {'mail_submission','local_mail_change'}:
                table='mail_submissions' if effect['kind']=='mail_submission' else 'mail_local_effects'
                key='id' if effect['kind']=='mail_submission' else 'operation_id'
                row=c.execute('SELECT account_id FROM '+table+' WHERE '+key+'=? AND owner_id=? AND project_id=?',(effect['operation_id'],who.owner_id,pid)).fetchone()
                if not row:raise HTTPException(404,'Prior mail effect is unavailable')
                productivity_mail.account(c,who,pid,row['account_id'])
            elif effect.get('kind')=='saved_meeting_records':productivity_meetings.source(c,who,pid,effect['source_id'])
    except productivity_mail.MailError as exc:raise HTTPException(403,'Mail source or current authority changed') from exc
    except productivity_schedule.ScheduleError as exc:raise HTTPException(409,'Calendar or reminder changed; historical result is withheld') from exc
    except guard.Denied as exc:raise HTTPException(403,'Source permission is no longer available') from exc


def read_schedule_binding(c,who,pid,request,output):
    """Persisted search excerpts inherit current resource existence and revision."""
    op=request['operation'];options=request['options'];items=[]
    def add(kind,row):
        if row and row.get('id'):
            value={'kind':kind,'id':row['id']}
            if 'version' in row:value['version']=row['version']
            items.append(value)
    for key,kind in [('calendars','calendar'),('events','event'),('reminders','reminder'),('plans','plan')]:
        for row in output.get(key,[]):add(kind,row)
    for row in output.get('notifications',[]):add('reminder',{'id':row['reminder_id'],'version':row['reminder_version']})
    if op.endswith('.get'):add(op.split('.')[0],output)
    from backend import productivity_schedule as schedule
    for cid in options.get('calendarIds',[])+([options['calendarId']] if options.get('calendarId') else []):
        add('calendar',schedule.get_calendar(c,who,pid,cid))
    return {'kind':'batch','items':list({(v['kind'],v['id']):v for v in items}.values())}


def effects(c,rid):
    output=[]
    for row in c.execute('SELECT key,binding FROM productivity_operations WHERE key LIKE ?',(rid+'-%',)):
        output.append({'kind':'local_calendar_or_reminder_change','operation_id':row['key'],'effect':json.loads(row['binding'])})
    for row in c.execute('SELECT id,state,message_id FROM mail_submissions WHERE id=?',(rid,)):
        output.append({'kind':'mail_submission','operation_id':row['id'],'state':row['state'],'message_id':row['message_id'],'automatic_replay':False,'delivered':False})
    from backend import productivity_mail
    if hasattr(productivity_mail,'local_effects'):output.extend(productivity_mail.local_effects(c,rid))
    for row in c.execute('SELECT source_id,source_version,task_ids FROM productivity_meeting_effects WHERE operation_id=?',(rid,)):
        output.append({'kind':'saved_meeting_records','operation_id':rid,'source_id':row['source_id'],'source_version':row['source_version'],'task_ids':json.loads(row['task_ids']),'state':'saved_before_cancellation_or_failure','external_notifications_sent':0})
    return output


async def run(slug,inputs,who,pid,rid,check):
    check();output=None
    from backend import productivity_planning as planning,productivity_schedule as schedule,productivity_mail as mail,productivity_meetings as meetings
    try:
        if slug in PLANNING and inputs.planningOperation:output=planning.execute_planning(inputs.planningOperation)
        elif slug in SCHEDULE and inputs.scheduleOperation:
            with get_db() as c:
                c.execute('BEGIN IMMEDIATE');check()
                output=schedule.execute_schedule(c,who,pid,inputs.scheduleOperation,idempotency_key=rid+'-'+slug)
                committed=c.execute('SELECT binding FROM productivity_operations WHERE owner_id=? AND project_id=? AND key=?',(who.owner_id,pid,rid+'-'+slug)).fetchone()
                if committed:output['schedule_binding']=json.loads(committed['binding'])
                elif inputs.scheduleOperation['operation'] in schedule.READS:output['schedule_binding']=read_schedule_binding(c,who,pid,inputs.scheduleOperation,output)
        elif slug in MAIL:
            authority=SEND_GRANTS.pop(rid,None)
            output=await skill_operations.stopped_thread(lambda stop:mail.run(slug,who,pid,inputs.mailOperation,operation_id=rid,check=check,stop=stop,authority=authority),check)
        elif slug in MEETINGS:
            with get_db() as c:
                c.execute('BEGIN IMMEDIATE');check()
                bindings=[];request=dict(inputs.meetingOperation)
                if inputs.documentIds:
                    from backend import skill_content
                    bundle=skill_content.read_files(c,who,pid,inputs.documentIds)
                    if request.get('text'):raise HTTPException(422,'Choose document notes or direct text, not both')
                    request['text']=bundle['text'];request['completeSource']=bundle['coverage']['complete_extracted_scope']
                    bindings=[guard.resource(c,who,pid,fid)[0] for fid in inputs.documentIds]
                output=meetings.run(c,who,pid,request,bindings=bindings,operation_id=rid)
        else:raise HTTPException(422,'Productivity executor inputs are missing')
    except (schedule.ScheduleError,mail.MailError) as exc:raise HTTPException(getattr(exc,'status_code',422),str(exc)) from exc
    check()
    output['skill']=slug
    partial=output.get('status') in {'partial','needs_input','unknown','blocked'} or output.get('state') in {'unknown','partial','failed'}
    if slug=='authorized-email-send' and output.get('state')!='smtp_accepted':partial=True
    if slug=='recipient-resolution' and any(r.get('status')!='resolved' for r in output.get('resolutions',[])):partial=True
    if slug=='email-followup-tracking' and output.get('status')!='cancelled':partial=True
    invitation_pending=bool(output.get('invitees') and output.get('invitation_status')=='not_sent_local_only')
    if invitation_pending:
        partial=True
        output.setdefault('limitations',[]).append('Invitees are local metadata only; no invitation was sent or accepted.')
    if output.get('submission',{}).get('state') in {'unknown','partial','failed'}:partial=True
    message=output.get('text') if isinstance(output.get('text'),str) else None
    if not message:
        if invitation_pending:message='حُفظ الحدث محليًا. لم تُرسل دعوات للمشاركين؛ يلزم موصل تقويم خارجي لهذا الجزء.'
        elif inputs.planningOperation:message='أُعد اقتراح محسوب؛ لم تُحجز مواعيد. راجع الفترات المؤجلة أو البيانات الناقصة.'
        elif slug in MAIL:message='نتيجة البريد محفوظة للمراجعة بالحالة المبينة؛ قبول الإرسال لا يثبت الوصول أو القراءة.'
        elif slug in MEETINGS:message='نُظمت الملاحظات والقرارات والإجراءات مع مواضعها؛ العناصر غير المحسومة ظاهرة للمراجعة.'
        else:message='سُجلت نتيجة العملية المحلية. إنشاء التذكير يختلف عن نشر الإشعار وقراءته.'
    return output,[],partial,message
