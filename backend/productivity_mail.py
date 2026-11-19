"""Project-scoped local mail records and explicit, receipt-based dispatch.

Imported mail is evidence, never authority. No automatic inbox access, replies,
remote drafts or background sends. SMTP acceptance is not recipient delivery.
"""
from __future__ import annotations
import base64
from datetime import datetime, timezone,timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
import hashlib
import json
import re
import secrets
import threading
import time
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from fastapi import HTTPException
from backend import guard,skill_files
from backend.db import get_db,now,uid,settings

VERSION='riwaq-mail-1'
SLUGS={'email-search','email-thread-summary','email-action-extraction','email-drafting','email-reply-context','recipient-resolution','attachment-verification','authorized-email-send','email-followup-tracking','bounce-analysis'}
MAX_RAW=1024*1024
MAX_BODY=64000

class MailError(ValueError):
    def __init__(self,code):self.code=code;super().__init__(code)

def require(ok,code):
    if not ok:raise MailError(code)

def packed(value):return guard.packed(value)
def digest(value):return guard.digest(value)

def address(value):
    require(isinstance(value,str) and len(value)<=254 and value==value.strip() and value.isascii(),'invalid_email_address')
    require(re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?",value) is not None,'invalid_email_address')
    local,domain=value.rsplit('@',1)
    require(len(local)<=64 and not local.startswith('.') and not local.endswith('.') and '..' not in local and '.' in domain and '..' not in domain,'invalid_email_address')
    return local+'@'+domain.lower()

def header_text(value):
    require(not any(ord(c)<32 or ord(c)==127 for c in value),'header_control_character')
    return value

class Strict(BaseModel):model_config=ConfigDict(extra='forbid',strict=True)

class AccountInput(Strict):
    label:str=Field(min_length=1,max_length=100)
    address:str=Field(min_length=3,max_length=254)
    @field_validator('label')
    @classmethod
    def label_ok(cls,v):return header_text(v)
    @field_validator('address')
    @classmethod
    def address_ok(cls,v):return address(v)

class ContactInput(Strict):
    displayName:str=Field(min_length=1,max_length=100)
    email:str=Field(min_length=3,max_length=254)
    @field_validator('displayName')
    @classmethod
    def name_ok(cls,v):return header_text(v)
    @field_validator('email')
    @classmethod
    def email_ok(cls,v):return address(v)

class ImportInput(Strict):
    accountId:str=Field(pattern=r'^[a-f0-9]{32}$')
    rawBase64:str=Field(min_length=4,max_length=1400000)

class Recipient(Strict):
    contactId:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    email:str|None=Field(default=None,max_length=254)
    @model_validator(mode='after')
    def one(self):
        require(bool(self.contactId)!=bool(self.email),'recipient_requires_contact_id_or_exact_address')
        if self.email:self.email=address(self.email)
        return self

class DraftComposition(Strict):
    language:Literal['ar','he','en']
    goal:str=Field(min_length=1,max_length=1000)
    confirmedFacts:list[str]=Field(default_factory=list,max_length=20)
    request:str=Field(min_length=1,max_length=2000)
    greeting:str|None=Field(default=None,max_length=300)
    closing:str|None=Field(default=None,max_length=300)
    @field_validator('confirmedFacts')
    @classmethod
    def facts_ok(cls,values):
        require(all(isinstance(v,str) and v.strip() and len(v)<=1000 for v in values),'invalid_draft_fact')
        return values
    @field_validator('goal','request')
    @classmethod
    def content_ok(cls,value):
        require(bool(value.strip()),'empty_draft_composition');return value

def compose_draft(value):
    """Local editable template. Supplied facts remain claims of the user."""
    labels={
      'ar':('مرحبًا،','بخصوص:','المعلومات التي أوردتها:','الطلب:','مع التحية،','مسودة للمراجعة'),
      'he':('שלום,','בנוגע ל:','המידע שסיפקת:','הבקשה:','בברכה,','טיוטה לבדיקה'),
      'en':('Hello,','Regarding:','Information you provided:','Request:','Best regards,','Draft for review'),
    }[value.language]
    greeting,goal,facts,request,closing,subject=labels
    parts=[greeting if value.greeting is None else value.greeting,goal+'\n'+value.goal]
    if value.confirmedFacts:parts.append(facts+'\n'+'\n'.join('- '+fact for fact in value.confirmedFacts))
    parts.extend([request+'\n'+value.request,closing if value.closing is None else value.closing])
    body='\n\n'.join(part for part in parts if part)
    require(len(body)<=16000,'composed_draft_size_limit')
    return body,subject,{'method':'literal-local-template','template_version':1,'language':value.language,
      'fact_count':len(value.confirmedFacts),'facts_independently_verified':False,'requires_review':True}

class MailInput(Strict):
    operation:str=Field(pattern='^('+'|'.join(sorted(SLUGS))+')$')
    accountId:str=Field(pattern=r'^[a-f0-9]{32}$')
    action:Literal['get','create','update','delete','list','track']='get'
    messageIds:list[str]=Field(default_factory=list,max_length=20)
    query:str=Field(default='',max_length=500)
    names:list[str]=Field(default_factory=list,max_length=8)
    draftId:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    expectedRevision:int|None=Field(default=None,ge=1)
    expectedPayloadSha256:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    subject:str=Field(default='',max_length=200)
    body:str=Field(default='',max_length=16000)
    compose:DraftComposition|None=None
    to:list[Recipient]=Field(default_factory=list,max_length=20)
    cc:list[Recipient]=Field(default_factory=list,max_length=20)
    bcc:list[Recipient]=Field(default_factory=list,max_length=20)
    attachments:list[skill_files.SourceRef]=Field(default_factory=list,max_length=8)
    replyToMessageId:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    replyAll:bool=False
    followupId:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    dueAt:str|None=Field(default=None,max_length=40)
    submissionId:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    dsnMessageId:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    @model_validator(mode='after')
    def composition_scope(self):
        if self.compose:
            require(self.operation=='email-drafting' and self.action in {'create','update'} and not self.body,'composition_requires_draft_write_and_no_body')
        return self
    @field_validator('subject')
    @classmethod
    def subject_ok(cls,v):return header_text(v)
    @field_validator('messageIds','names')
    @classmethod
    def list_ok(cls,v):
        require(len(v)==len(set(v)) and all(isinstance(x,str) and 0<len(x)<=200 for x in v),'invalid_mail_selection')
        return v
    @field_validator('dueAt')
    @classmethod
    def date_ok(cls,v):
        if v is not None:
            try:d=datetime.fromisoformat(v.replace('Z','+00:00'))
            except ValueError:raise MailError('followup_requires_explicit_offset') from None
            require(d.tzinfo is not None,'followup_requires_explicit_offset')
            return d.astimezone(timezone.utc).isoformat()
        return v

    @model_validator(mode='after')
    def supported_action(self):
        allowed={'email-drafting':{'get','create','update','delete','list'},
                 'email-followup-tracking':{'get','track','delete','list'}}.get(self.operation,{'get'})
        require(self.action in allowed,'mail_action_not_supported_for_operation')
        if self.compose is not None:require(self.operation=='email-drafting' and self.action in {'create','update'},'compose_requires_draft_write')
        return self

def init(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS mail_accounts(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,label TEXT NOT NULL,address TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS mail_contacts(id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES mail_accounts(id) ON DELETE CASCADE,display_name TEXT NOT NULL,address TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS mail_messages(id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES mail_accounts(id) ON DELETE CASCADE,thread_id TEXT NOT NULL,message_id TEXT NOT NULL,sha256 TEXT NOT NULL,document TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,deleted INTEGER NOT NULL DEFAULT 0,imported_at TEXT NOT NULL,UNIQUE(account_id,message_id));
    CREATE TABLE IF NOT EXISTS mail_drafts(id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES mail_accounts(id) ON DELETE CASCADE,revision INTEGER NOT NULL,document TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS mail_send_grants(token_hash TEXT PRIMARY KEY,owner_id TEXT NOT NULL,session_id TEXT NOT NULL,project_id TEXT NOT NULL,account_id TEXT NOT NULL,draft_id TEXT NOT NULL,revision INTEGER NOT NULL,payload_hash TEXT NOT NULL,transport_hash TEXT NOT NULL,operation_id TEXT NOT NULL,attachments_authorized INTEGER NOT NULL,expires REAL NOT NULL,state TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS mail_submissions(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,project_id TEXT NOT NULL,account_id TEXT NOT NULL,draft_id TEXT NOT NULL,revision INTEGER NOT NULL,payload_hash TEXT NOT NULL,transport_hash TEXT NOT NULL,message_id TEXT NOT NULL,state TEXT NOT NULL,receipt TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(draft_id,revision));
    CREATE TABLE IF NOT EXISTS mail_followups(id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES mail_accounts(id) ON DELETE CASCADE,submission_id TEXT NOT NULL,due_at TEXT NOT NULL,state TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS mail_local_effects(operation_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,account_id TEXT NOT NULL REFERENCES mail_accounts(id) ON DELETE CASCADE,resource_kind TEXT NOT NULL,resource_id TEXT NOT NULL,revision INTEGER NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TRIGGER IF NOT EXISTS mail_account_cleanup AFTER DELETE ON mail_accounts BEGIN
      DELETE FROM mail_send_grants WHERE account_id=OLD.id;
      DELETE FROM mail_submissions WHERE account_id=OLD.id;
    END;
    ''')

def account(c,who,pid,aid):
    guard.check_actor(c,who,pid)
    row=c.execute('SELECT * FROM mail_accounts WHERE id=? AND owner_id=? AND project_id=? AND active=1',(aid,who.owner_id,pid)).fetchone()
    require(row is not None,'mail_account_unavailable');return row

def create_account(who,pid,request):
    request=request if isinstance(request,AccountInput) else AccountInput.model_validate(request)
    with get_db() as c:
        guard.check_actor(c,who,pid);init(c)
        require(c.execute('SELECT count(*) FROM mail_accounts WHERE project_id=?',(pid,)).fetchone()[0]<10,'mail_account_limit')
        aid=uid();c.execute('INSERT INTO mail_accounts VALUES(?,?,?,?,?,1,1,?)',(aid,who.owner_id,pid,request.label,request.address,now()))
        return {'id':aid,'label':request.label,'address':request.address,'mode':'local-import','send_configured':False,'provider_drafts_supported':False}

def list_accounts(who,pid):
    with get_db() as c:
        guard.check_actor(c,who,pid);init(c)
        return [{'id':r['id'],'label':r['label'],'address':r['address'],'revision':r['revision'],'mode':'local-import','provider_drafts_supported':False} for r in c.execute('SELECT * FROM mail_accounts WHERE owner_id=? AND project_id=? AND active=1',(who.owner_id,pid))]

def save_contact(who,pid,aid,request):
    request=request if isinstance(request,ContactInput) else ContactInput.model_validate(request)
    with get_db() as c:
        account(c,who,pid,aid)
        require(c.execute('SELECT count(*) FROM mail_contacts WHERE account_id=?',(aid,)).fetchone()[0]<500,'contact_limit')
        cid=uid();c.execute('INSERT INTO mail_contacts VALUES(?,?,?,?,1)',(cid,aid,request.displayName,request.email))
        return {'id':cid,'displayName':request.displayName,'email':request.email,'revision':1}

def list_contacts(who,pid,aid):
    with get_db() as c:
        account(c,who,pid,aid)
        return [{'id':r['id'],'displayName':r['display_name'],'email':r['address'],'revision':r['revision']} for r in c.execute('SELECT * FROM mail_contacts WHERE account_id=? ORDER BY display_name,id',(aid,))]

def _addresses(values):
    pairs=getaddresses(values,strict=True)
    require(len(pairs)<=40,'mail_address_limit')
    return [{'name':header_text(name)[:200],'email':address(email)} for name,email in pairs if email]

def parse_message(raw):
    require(isinstance(raw,bytes) and 0<len(raw)<=MAX_RAW,'mail_size_limit')
    require(len(re.findall(br'(?im)^content-type\s*:',raw))<=24 and len(re.findall(br'(?im)^message-id\s*:',raw))<=8,'mail_structure_limit')
    head=re.split(br'\r?\n\r?\n',raw,maxsplit=1)[0]
    require(len(head)<=32768 and head.count(b'\n')<=200,'mail_header_limit')
    try:message=BytesParser(policy=policy.default).parsebytes(raw)
    except (ValueError,RecursionError):raise MailError('mail_parse_failed') from None
    for key in ('from','reply-to','to','cc','subject','message-id','in-reply-to','references','date'):
        require(len(message.get_all(key,[]))<=1,'duplicate_mail_header')
    require(not message.defects,'malformed_mail')
    mid=str(message.get('Message-ID','')).strip()
    require(not mid or re.fullmatch(r'<[^\s<>\x00-\x1f]{1,190}@[^\s<>\x00-\x1f]{1,190}>',mid),'invalid_message_id')
    raw_hash=hashlib.sha256(raw).hexdigest();mid=mid or '<import-'+raw_hash+'@riwaq.invalid>'
    parts=list(message.walk());require(len(parts)<=32,'mail_parts_limit')
    texts=[];attachments=[];warnings=[];dsn=[];reported_ids=[]
    for part in parts:
        require(not part.defects,'malformed_mail_part')
        if part is not message and part.get('Message-ID'):reported_ids.append(str(part.get('Message-ID'))[:390])
        if part.get_content_type()=='message/delivery-status':
            for block in part.get_payload() if isinstance(part.get_payload(),list) else []:
                dsn.append({k.lower():str(block.get(k,''))[:300] for k in ('Original-Envelope-Id','Final-Recipient','Original-Recipient','Action','Status','Original-Message-ID')})
            continue
        if part.is_multipart():continue
        data=part.get_payload(decode=True) or b'';require(len(data)<=MAX_RAW,'mail_part_limit')
        require(not part.defects,'malformed_encoded_mail_part')
        filename=part.get_filename()
        if filename or part.get_content_disposition()=='attachment':
            attachments.append({'filename':str(filename or 'unnamed')[:180],'mime':part.get_content_type(),'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'download_available':False,'scanned':False})
        elif part.get_content_type()=='text/plain':
            try:texts.append(data.decode(part.get_content_charset() or 'ascii',errors='strict'))
            except (LookupError,UnicodeDecodeError):warnings.append('undecodable_text_part_omitted')
        elif part.get_content_type()=='text/html':warnings.append('html_not_rendered_or_fetched')
        elif part.get_content_type()=='text/rfc822-headers':
            header=BytesParser(policy=policy.default).parsebytes(data,headersonly=True)
            if header.get('Message-ID'):reported_ids.append(str(header.get('Message-ID'))[:390])
    text='\n\n'.join(texts);require(len(text)<=MAX_BODY,'mail_text_limit')
    authored=[];quoted=[];signature=[];section='authored'
    for n,line in enumerate(text.splitlines(),1):
        if line.strip() in {'--','-- '} or line.strip().startswith(('Sent from my ','أُرسلت من ','נשלח מ')):section='signature'
        if re.match(r'(?i)^(On .+wrote:|-----Original Message-----|في .+كتب:|בתאריך .+כתב)',line):section='quoted'
        destination=quoted if section=='quoted' or line.lstrip().startswith('>') else signature if section=='signature' else authored
        destination.append({'line':n,'text':line})
    date=None;date_local=None
    try:
        parsed=parsedate_to_datetime(str(message.get('Date','')))
        if parsed.tzinfo is not None:date=parsed.astimezone(timezone.utc).isoformat();date_local=parsed.isoformat()
    except (TypeError,ValueError,OverflowError):pass
    refs=re.findall(r'<[^\s<>]{1,390}>',str(message.get('References',''))+' '+str(message.get('In-Reply-To','')))[:20]
    return {'message_id':mid,'subject':header_text(str(message.get('Subject','')))[:200],
      'from':_addresses(message.get_all('From',[])),'reply_to':_addresses(message.get_all('Reply-To',[])),
      'to':_addresses(message.get_all('To',[])),'cc':_addresses(message.get_all('Cc',[])),
      'date':date,'date_local':date_local,'date_header':str(message.get('Date',''))[:100],'references':list(dict.fromkeys(refs)),
      'text':text,'authored':authored,'quoted':quoted,'signature':signature,'attachments':attachments,'dsn':dsn,'reported_original_message_ids':reported_ids,
      'auto_submitted':str(message.get('Auto-Submitted','no')).lower()!='no',
      'warnings':warnings,'sha256':raw_hash,'trust':'untrusted_import','remote_resources_loaded':False,
      'section_detection':'literal quote/signature markers only; ambiguous attribution remains unverified'}

def import_message(who,pid,request):
    request=request if isinstance(request,ImportInput) else ImportInput.model_validate(request)
    with get_db() as c:account(c,who,pid,request.accountId)
    try:raw=base64.b64decode(request.rawBase64,validate=True)
    except ValueError:raise MailError('invalid_mail_base64') from None
    value=parse_message(raw)
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');account(c,who,pid,request.accountId)
        prior=c.execute('SELECT * FROM mail_messages WHERE account_id=? AND message_id=?',(request.accountId,value['message_id'])).fetchone()
        if prior:
            require(prior['sha256']==value['sha256'] and not prior['deleted'],'message_id_conflict_or_deleted')
            return {'id':prior['id'],'thread_id':prior['thread_id'],'revision':prior['revision'],'duplicate':True}
        require(c.execute('SELECT count(*) FROM mail_messages WHERE account_id=?',(request.accountId,)).fetchone()[0]<500,'mail_import_limit')
        thread=uid();related_threads=[]
        for reference in reversed(value['references']):
            known=c.execute('SELECT thread_id FROM mail_messages WHERE account_id=? AND message_id=? AND deleted=0',(request.accountId,reference)).fetchone()
            if known:related_threads.append(known['thread_id'])
        for known in c.execute('SELECT * FROM mail_messages WHERE account_id=? AND deleted=0',(request.accountId,)):
            if value['message_id'] in json.loads(known['document'])['references']:related_threads.append(known['thread_id'])
        if related_threads:
            thread=related_threads[0]
            for prior_thread in set(related_threads)-{thread}:
                c.execute('UPDATE mail_messages SET thread_id=?,revision=revision+1 WHERE account_id=? AND thread_id=?',(thread,request.accountId,prior_thread))
        identifier=uid();c.execute('INSERT INTO mail_messages VALUES(?,?,?,?,?,?,1,0,?)',(identifier,request.accountId,thread,value['message_id'],value['sha256'],packed(value),now()))
        return {'id':identifier,'thread_id':thread,'revision':1,'duplicate':False,'subject':value['subject'],'warnings':value['warnings']}

def delete_message(who,pid,aid,mid,expected_revision):
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');row,_=message(c,who,pid,aid,mid)
        require(row['revision']==expected_revision,'mail_revision_conflict')
        c.execute("UPDATE mail_messages SET document='{}',deleted=1,revision=revision+1 WHERE id=?",(mid,))
        return {'id':mid,'deleted':True,'revision':expected_revision+1,'tombstone_prevents_same_message_reimport':True}

def message(c,who,pid,aid,mid):
    account(c,who,pid,aid)
    row=c.execute('SELECT * FROM mail_messages WHERE id=? AND account_id=? AND deleted=0',(mid,aid)).fetchone()
    require(row is not None,'mail_message_unavailable')
    return row,json.loads(row['document'])

def get_message(who,pid,aid,mid):
    with get_db() as c:
        row,value=message(c,who,pid,aid,mid)
        return {'id':row['id'],'thread_id':row['thread_id'],'revision':row['revision'],'imported_at':row['imported_at'],**value}

def list_messages(who,pid,aid):
    with get_db() as c:
        account(c,who,pid,aid);result=[]
        for row in c.execute('SELECT * FROM mail_messages WHERE account_id=? AND deleted=0 ORDER BY imported_at DESC LIMIT 100',(aid,)):
            value=json.loads(row['document']);result.append({'id':row['id'],'thread_id':row['thread_id'],'revision':row['revision'],'subject':value['subject'],'from':value['from'],'date':value['date'],'imported_at':row['imported_at']})
        return result

def _ordered_thread(pairs):
    """Reply references establish causality; source Date orders independent nodes."""
    remaining={value['message_id']:(row,value) for row,value in pairs};output=[];warnings=[]
    known=set(remaining);emitted=set()
    while remaining:
        ready=[pair for pair in remaining.values() if not (set(pair[1]['references'])&known)-emitted]
        if not ready:
            warnings.append('cyclic_reply_headers_order_unverified');ready=list(remaining.values())
        ready.sort(key=lambda pair:(pair[1]['date'] is None,pair[1]['date'] or pair[0]['imported_at'],pair[0]['id']))
        row,value=ready[0]
        if value['date'] is None:warnings.append('unknown_message_date')
        parents=[v for _,v in output if v['message_id'] in value['references']]
        if value['date'] and any(p['date'] and p['date']>value['date'] for p in parents):warnings.append('reply_date_precedes_parent')
        output.append((row,value));emitted.add(value['message_id']);remaining.pop(value['message_id'])
    return output,list(dict.fromkeys(warnings))

ACTION_PATTERN=re.compile(r'^\s*(ACTION|TODO|UPDATE|UPDATED|CANCEL|CANCELLED|ANSWERED|REJECTED|مطلوب|مهمة|تحديث|إلغاء|ملغاة|أُجيب|تمت الإجابة|مرفوض|משימה|פעולה|עדכון|בוטל|נענה|נדחה)\s*(?:\[([A-Za-z0-9_-]{1,40})\])?\s*:\s*(.*)$',re.IGNORECASE)
ACTION_KIND={'update':'updated','updated':'updated','تحديث':'updated','עדכון':'updated','cancel':'cancelled','cancelled':'cancelled','إلغاء':'cancelled','ملغاة':'cancelled','בוטל':'cancelled','answered':'answered','أُجيب':'answered','تمت الإجابة':'answered','נענה':'answered','rejected':'rejected','مرفوض':'rejected','נדחה':'rejected'}

def _deadline(text,date_local):
    match=re.search(r'(?i)(?:\bdue|الموعد|מועד)\s*:\s*(\d{4}-\d{2}-\d{2}|tomorrow|today|غدًا|غدا|اليوم|מחר|היום)(?=$|[;,.\s])',text)
    if not match:return None
    literal=match[1]
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',literal):
        try:datetime.fromisoformat(literal)
        except ValueError:return {'literal':literal,'date':None,'reason':'invalid_literal_date'}
        return {'literal':literal,'date':literal,'basis':'explicit_date','time':None}
    if not date_local:return {'literal':literal,'date':None,'reason':'message_date_missing_not_resolved_against_now'}
    delta=1 if literal.casefold() in {'tomorrow','غدًا','غدا','מחר'} else 0
    return {'literal':literal,'date':(datetime.fromisoformat(date_local).date()+timedelta(days=delta)).isoformat(),'basis':'message_date_local_offset','time':None}

def _action_evidence(pairs):
    pairs,warnings=_ordered_thread(pairs);events=[];states={};unstructured=0
    for row,value in pairs:
        for line in value['authored']:
            if not line['text'].strip():continue
            match=ACTION_PATTERN.match(line['text'])
            if not match:unstructured+=1;continue
            kind=ACTION_KIND.get(match[1].casefold(),'proposed');key=match[2]
            event={'message_id':row['id'],'line':line['line'],'quote':line['text'],'action_id':key,'state':kind,
                   'deadline':_deadline(match[3],value.get('date_local')),'assignee':None}
            if len(events)>=80:warnings.append('action_event_limit');continue
            events.append(event)
            if key:
                prior=states.get(key)
                if prior and prior['state'] in {'cancelled','rejected'} and kind in {'proposed','updated'}:
                    warnings.append('cancelled_action_requires_explicit_reopening');event=event|{'state':'unresolved_change_after_cancellation'}
                states[key]=event
    return {'candidates':events,'latest_explicit_states':list(states.values()),'status':'partial','warnings':list(dict.fromkeys(warnings)),
      'unstructured_authored_lines':unstructured,'method':'literal-stable-action-labels-and-reply-causality','tasks_created':0,
      'notice':'Only explicit labels with stable IDs update prior states. Other language remains quoted evidence; no implied commitment, assignment or semantic resolution is invented.'}

def _recipients(c,aid,items):
    out=[]
    for item in items:
        if item.contactId:
            row=c.execute('SELECT * FROM mail_contacts WHERE id=? AND account_id=?',(item.contactId,aid)).fetchone()
            require(row is not None,'contact_unavailable')
            out.append({'email':row['address'],'name':row['display_name'],'contact_id':row['id'],'contact_revision':row['revision']})
        else:out.append({'email':item.email,'name':'','contact_id':None,'contact_revision':None})
    return out

def _draft(c,who,pid,aid,did):
    account(c,who,pid,aid)
    row=c.execute('SELECT * FROM mail_drafts WHERE id=? AND account_id=? AND state!=?',(did,aid,'deleted')).fetchone()
    require(row is not None,'draft_unavailable')
    value=json.loads(row['document']);return row,value

def draft_payload(row,value):
    return {'id':row['id'],'account_id':row['account_id'],'revision':row['revision'],'state':row['state'],
      'provider_draft':False,'payload_sha256':digest(value),'draft':value,'updated_at':row['updated_at']}

def get_draft(who,pid,aid,did):
    with get_db() as c:
        row,value=_draft(c,who,pid,aid,did);recheck_draft(c,who,pid,aid,value)
        return draft_payload(row,value)

def list_drafts(who,pid,aid):
    with get_db() as c:
        account(c,who,pid,aid);result=[]
        for row in c.execute("SELECT * FROM mail_drafts WHERE account_id=? AND state!='deleted' ORDER BY updated_at DESC LIMIT 200",(aid,)):
            value=json.loads(row['document'])
            try:recheck_draft(c,who,pid,aid,value)
            except (MailError,HTTPException,guard.Denied):continue
            result.append({'id':row['id'],'revision':row['revision'],'payload_sha256':digest(value),'subject':value['subject'],'state':row['state']})
        account(c,who,pid,aid)
        return result[:100]

def recheck_draft(c,who,pid,aid,value):
    account(c,who,pid,aid)
    for person in value['to']+value['cc']+value['bcc']:
        if person['contact_id']:
            row=c.execute('SELECT * FROM mail_contacts WHERE id=? AND account_id=?',(person['contact_id'],aid)).fetchone()
            require(row is not None and row['revision']==person['contact_revision'] and row['address']==person['email'],'recipient_contact_changed')
    for ref in value['attachments']:skill_files.resolve(c,who,pid,ref,active=True)
    for ref in value.get('message_refs',[]):
        row,_=message(c,who,pid,aid,ref['id'])
        require(row['revision']==ref['revision'] and row['sha256']==ref['sha256'],'mail_source_changed')

def save_draft(who,pid,req,*,operation_id=None):
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');a=account(c,who,pid,req.accountId)
        recipients={key:_recipients(c,req.accountId,getattr(req,key)) for key in ('to','cc','bcc')}
        addresses=[x['email'] for value in recipients.values() for x in value]
        require(len(addresses)<=20 and len(addresses)==len(set(addresses)),'duplicate_or_excess_recipients')
        refs=[];in_reply_to=None;references=[]
        if req.replyToMessageId:
            row,original=message(c,who,pid,req.accountId,req.replyToMessageId)
            refs=[{'id':row['id'],'revision':row['revision'],'sha256':row['sha256']}]
            in_reply_to=original['message_id'];references=(original['references']+[in_reply_to])[-20:]
        body,subject,composition=compose_draft(req.compose) if req.compose else (req.body,req.subject,None)
        value={'from':a['address'],**recipients,'subject':req.subject or subject,'body':body,
            'attachments':[ref.model_dump() for ref in req.attachments],'in_reply_to':in_reply_to,'references':references,'message_refs':refs,
            'body_origin':'confirmed_literal_template' if composition else 'explicit_user_draft','account_revision':a['revision']}
        if composition:value['composition']=composition
        recheck_draft(c,who,pid,req.accountId,value)
        if req.draftId:
            row,_=_draft(c,who,pid,req.accountId,req.draftId)
            require(req.expectedRevision==row['revision'],'draft_revision_conflict')
            require(row['state']=='draft','sent_or_dispatching_draft_immutable')
            n=row['revision']+1;did=row['id']
            c.execute('UPDATE mail_drafts SET revision=?,document=?,updated_at=? WHERE id=?',(n,packed(value),now(),did))
            c.execute("UPDATE mail_send_grants SET state='revoked' WHERE draft_id=? AND state='pending'",(did,))
        else:
            if operation_id:
                prior=c.execute('SELECT * FROM mail_drafts WHERE id=?',(operation_id,)).fetchone()
                if prior:
                    require(prior['account_id']==req.accountId and prior['revision']==1 and prior['document']==packed(value) and prior['state']=='draft','draft_operation_scope_changed')
                    return draft_payload(prior,value)|{'reused_local_operation':True}
            require(c.execute('SELECT count(*) FROM mail_drafts WHERE account_id=?',(req.accountId,)).fetchone()[0]<200,'draft_limit')
            did=operation_id or uid();c.execute('INSERT INTO mail_drafts VALUES(?,?,1,?,?,?,?)',(did,req.accountId,packed(value),'draft',now(),now()))
        row=c.execute('SELECT * FROM mail_drafts WHERE id=?',(did,)).fetchone()
        if operation_id:_record_local_effect(c,who,pid,req.accountId,operation_id,'draft',did,row['revision'],'draft')
        return draft_payload(row,value)

def _record_local_effect(c,who,pid,aid,operation_id,kind,identifier,revision,state):
    """Called inside the mutation transaction; no content or recipient metadata."""
    c.execute('INSERT INTO mail_local_effects VALUES(?,?,?,?,?,?,?,?,?)',
      (operation_id,who.owner_id,pid,aid,kind,identifier,revision,state,now()))

def local_effects(c,operation_id):
    return [{'kind':'local_mail_change','operation_id':row['operation_id'],'resource_kind':row['resource_kind'],
      'resource_id':row['resource_id'],'revision':row['revision'],'state':row['state'],'automatic_replay':False}
      for row in c.execute('SELECT * FROM mail_local_effects WHERE operation_id=?',(operation_id,))]

def _attachment_permissions(c,who,pid,aid,recipients,refs):
    """No implicit local-to-mail upgrade. Traverse authored artifact ancestry."""
    seen=set();checked=set()
    def private_file(fid):
        row=c.execute('SELECT * FROM guard_file_policies WHERE file_id=? AND owner_id=? AND project_id=? AND revoked=0',(fid,who.owner_id,pid)).fetchone()
        require(row is not None,'attachment_policy_unavailable')
        allowed=json.loads(row['destinations'])
        require(all('mail:'+aid+':'+email in allowed for email in recipients),'attachment_egress_not_authorized')
    def source(ref):
        skill_files.resolve(c,who,pid,ref,active=True)
        if ref['kind']=='document':private_file(ref['id']);return
        _,version=skill_files.version(c,who,pid,ref['id'],ref['version']);origin(version['run_id'])
    def origin(rid):
        require(rid not in seen,'attachment_lineage_cycle_or_limit')
        if rid in checked:return
        require(len(checked)+len(seen)<32,'attachment_lineage_cycle_or_limit');seen.add(rid)
        row=c.execute('SELECT * FROM skill_runs WHERE id=? AND owner_id=? AND project_id=?',(rid,who.owner_id,pid)).fetchone()
        require(row is not None,'attachment_origin_unavailable')
        from backend import skills
        skills.recheck(c,row,who)
        for resource in json.loads(row['resources']):private_file(resource['file_id'])
        result=json.loads(row['document']).get('result',{})
        require(not result.get('memory_lineage') and not result.get('repository_binding') and not result.get('mail_binding') and not result.get('productivity_bindings'),'attachment_lineage_has_no_external_sharing_policy')
        request=json.loads(row['payload'])
        for ref in request.get('inputs',{}).get('sourceRefs',[]):source(ref)
        if request.get('previousRunId'):origin(request['previousRunId'])
        seen.remove(rid);checked.add(rid)
    for ref in refs:source(ref)

def verify_attachments(c,who,pid,refs):
    output=[];total=0
    allowed={'application/pdf','text/plain','text/csv','image/png','image/jpeg','image/webp',
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'application/vnd.openxmlformats-officedocument.presentationml.presentation'}
    for ref in refs:
        ref=ref.model_dump() if isinstance(ref,skill_files.SourceRef) else ref
        item=skill_files.resolve(c,who,pid,ref,active=True);size=len(item['content']);total+=size
        require(size<=2*1024*1024 and total<=8*1024*1024,'mail_attachment_size_limit')
        require(item['mime'].split(';')[0] in allowed,'mail_attachment_type_unsupported')
        mime=item['mime'].split(';')[0];content=item['content']
        signatures={'application/pdf':b'%PDF-','image/png':b'\x89PNG\r\n\x1a\n','image/jpeg':b'\xff\xd8'}
        if mime in signatures:require(content.startswith(signatures[mime]),'mail_attachment_signature_mismatch')
        if mime.startswith('application/vnd.openxmlformats-officedocument.'):require(content.startswith(b'PK\x03\x04'),'mail_attachment_signature_mismatch')
        if mime=='image/webp':require(content.startswith(b'RIFF') and content[8:12]==b'WEBP','mail_attachment_signature_mismatch')
        require('/' not in item['name'] and '\\' not in item['name'] and not any(ord(x)<32 for x in item['name']),'attachment_filename_invalid')
        output.append({'source':ref,'name':item['name'],'mime':item['mime'],'size':size,'sha256':item['sha256'],
            'current_authorized_bytes':True,'malware_free_verified':False,'external_sharing_authorized':False})
    return {'attachments':output,'total_bytes':total,'notice':'Current bytes and versions verified; this is not a malware scan or external-sharing permission.'}

def _transport_configuration(aid):
    from backend import productivity_mail_transport as transport
    try:return transport.configuration(aid)
    except transport.TransportError as exc:raise MailError(str(exc) if str(exc) in {'smtp_not_configured','smtp_configuration_unavailable'} else 'smtp_configuration_unavailable') from None

def approve_send(who,pid,aid,did,revision,payload_hash,*,operation_id,attachments_authorized=False):
    """Trusted direct-user endpoint only. Never expose this as a model tool."""
    from backend import productivity_mail_transport as transport
    require(re.fullmatch(r'[a-f0-9]{32}',operation_id) is not None,'invalid_mail_operation')
    require(settings().get('mode')=='hybrid','mail_network_blocked_in_local_mode')
    config=_transport_configuration(aid)
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');a=account(c,who,pid,aid);row,value=_draft(c,who,pid,aid,did)
        require(row['state']=='draft' and row['revision']==revision and digest(value)==payload_hash,'draft_changed_or_not_sendable')
        recheck_draft(c,who,pid,aid,value)
        people=[p['email'] for name in ('to','cc','bcc') for p in value[name]]
        require(people and config.fromAddress==a['address'] and set(people)<=set(config.allowedRecipients),'smtp_account_or_recipient_not_authorized')
        require(not value['attachments'] or attachments_authorized is True,'attachment_sharing_review_required')
        verify_attachments(c,who,pid,value['attachments']);_attachment_permissions(c,who,pid,aid,people,value['attachments'])
        token=secrets.token_urlsafe(32)
        c.execute('INSERT INTO mail_send_grants VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
          (hashlib.sha256(token.encode()).hexdigest(),who.owner_id,who.session_id,pid,aid,did,revision,payload_hash,digest(config.model_dump()),operation_id,int(attachments_authorized),time.time()+120,'pending'))
        guard.audit(c,who,pid,operation_id,'mail.send','allow','explicit_exact_draft_review')
        return token

def _wire(c,who,pid,aid,value,message_id):
    verify_attachments(c,who,pid,value['attachments'])
    email=EmailMessage(policy=policy.SMTP)
    email['From']=value['from'];email['Subject']=value['subject'];email['Message-ID']=message_id
    email['Date']=datetime.now(timezone.utc)
    for name in ('to','cc'):
        if value[name]:email[name.title()]=', '.join(x['email'] for x in value[name])
    if value['in_reply_to']:email['In-Reply-To']=value['in_reply_to']
    if value['references']:email['References']=' '.join(value['references'])
    email.set_content(value['body'])
    for ref in value['attachments']:
        item=skill_files.resolve(c,who,pid,ref,active=True);main,sub=item['mime'].split(';')[0].split('/',1)
        email.add_attachment(item['content'],maintype=main,subtype=sub,filename=item['name'])
    return email.as_bytes()

def send(who,pid,req,*,operation_id,authority,check,stop):
    from backend import productivity_mail_transport as transport
    require(req.draftId and req.expectedRevision and req.expectedPayloadSha256,'send_requires_reviewed_draft')
    require(isinstance(authority,str) and len(authority)==43,'trusted_send_authorization_required')
    require(settings().get('mode')=='hybrid','mail_network_blocked_in_local_mode')
    config=_transport_configuration(req.accountId);config_hash=digest(config.model_dump())
    def authorize():
        require(check() is not False,'mail_authority_revoked');require(not stop.is_set(),'mail_cancelled')
        require(settings().get('mode')=='hybrid','mail_network_blocked_in_local_mode')
        require(digest(_transport_configuration(req.accountId).model_dump())==config_hash,'smtp_policy_changed')
        with get_db() as c:
            a=account(c,who,pid,req.accountId);row,value=_draft(c,who,pid,req.accountId,req.draftId)
            require(row['revision']==req.expectedRevision and digest(value)==req.expectedPayloadSha256 and a['revision']==value['account_revision'],'draft_changed')
            recheck_draft(c,who,pid,req.accountId,value)
            recipients=[p['email'] for name in ('to','cc','bcc') for p in value[name]]
            require(set(recipients)<=set(config.allowedRecipients) and value['from']==config.fromAddress,'smtp_destination_changed')
            _attachment_permissions(c,who,pid,req.accountId,recipients,value['attachments'])
    authorize()
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');account(c,who,pid,req.accountId)
        grant=c.execute('SELECT * FROM mail_send_grants WHERE token_hash=?',(hashlib.sha256(authority.encode()).hexdigest(),)).fetchone()
        expected=(who.owner_id,who.session_id,pid,req.accountId,req.draftId,req.expectedRevision,req.expectedPayloadSha256,config_hash,operation_id)
        require(grant is not None and tuple(grant[x] for x in ('owner_id','session_id','project_id','account_id','draft_id','revision','payload_hash','transport_hash','operation_id'))==expected and grant['expires']>time.time() and grant['state'] in {'pending','claimed'},'send_authorization_invalid')
        prior=c.execute('SELECT * FROM mail_submissions WHERE draft_id=? AND revision=?',(req.draftId,req.expectedRevision)).fetchone()
        if prior:
            require(prior['id']==operation_id and prior['payload_hash']==req.expectedPayloadSha256,'draft_dispatch_already_claimed')
            result=json.loads(prior['receipt']) if prior['receipt']!='{}' else {'state':'unknown','accepted_by_smtp':None,'delivered':False,'automatic_retry':False}
            return result|{'operation_id':operation_id,'reused_receipt':True,'message_id':prior['message_id']}
        row,value=_draft(c,who,pid,req.accountId,req.draftId)
        require(row['state']=='draft','draft_not_sendable')
        recheck_draft(c,who,pid,req.accountId,value)
        mid='<'+operation_id+'@'+value['from'].split('@')[1]+'>';wire=_wire(c,who,pid,req.accountId,value,mid)
        people=[p['email'] for name in ('to','cc','bcc') for p in value[name]]
        c.execute('INSERT INTO mail_submissions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(operation_id,who.owner_id,pid,req.accountId,req.draftId,req.expectedRevision,req.expectedPayloadSha256,config_hash,mid,'dispatching','{}',now(),now()))
        c.execute("UPDATE mail_send_grants SET state='claimed' WHERE token_hash=?",(grant['token_hash'],))
        c.execute("UPDATE mail_drafts SET state='dispatching' WHERE id=?",(req.draftId,))
    try:receipt=transport.submit(config,req.accountId,value['from'],people,wire,authorize,stop)
    except Exception:receipt={'state':'unknown','error':'mail_outcome_unknown','accepted_by_smtp':None,'delivered':False,'automatic_retry':False}
    receipt=receipt|{'operation_id':operation_id,'message_id':mid,'reused_receipt':False,'wire_sha256':hashlib.sha256(wire).hexdigest(),
      'notice':'SMTP acceptance only; recipient delivery/read status is not proved. Unknown outcomes are never resent automatically.'}
    with get_db() as c:
        c.execute('UPDATE mail_submissions SET state=?,receipt=?,updated_at=? WHERE id=?',(receipt['state'],packed(receipt),now(),operation_id))
        c.execute('UPDATE mail_drafts SET state=?,updated_at=? WHERE id=?',(receipt['state'],now(),req.draftId))
        guard.audit(c,who,pid,operation_id,'mail.send','result',receipt['state'])
    return receipt

def recheck_binding(c,who,pid,binding):
    a=account(c,who,pid,binding['account_id'])
    require(a['revision']==binding['account_revision'],'mail_account_changed')
    for expected in binding.get('messages',[]):
        row,_=message(c,who,pid,a['id'],expected['id'])
        require(row['revision']==expected['revision'] and row['sha256']==expected['sha256'],'mail_source_changed')
    for expected in binding.get('contacts',[]):
        row=c.execute('SELECT revision FROM mail_contacts WHERE id=? AND account_id=?',(expected['id'],a['id'])).fetchone()
        require(row is not None and row['revision']==expected['revision'],'recipient_contact_changed')
    for expected in binding.get('followups',[]):
        row=c.execute('SELECT revision FROM mail_followups WHERE id=? AND account_id=?',(expected['id'],a['id'])).fetchone()
        require(row is not None and row['revision']==expected['revision'],'followup_changed')
    drafts=list(binding.get('drafts',[]))
    if binding.get('draft'):drafts.append(binding['draft'])
    for ref in drafts:
        row,value=_draft(c,who,pid,a['id'],ref['id'])
        require(row['revision']==ref['revision'] and digest(value)==ref['payload_sha256'],'draft_changed')
        recheck_draft(c,who,pid,a['id'],value)
    for ref in binding.get('attachments',[]):skill_files.resolve(c,who,pid,ref,active=True)
    return True

def run(slug,who,pid,request,*,operation_id,check,stop=None,authority=None):
    request=request if isinstance(request,MailInput) else MailInput.model_validate(request)
    require(slug==request.operation and slug in SLUGS,'mail_operation_mismatch')
    require(isinstance(operation_id,str) and re.fullmatch(r'[a-f0-9]{32}',operation_id),'invalid_mail_operation')
    require(callable(check),'mail_authority_callback_required');stop=stop or threading.Event()
    def permitted():require(check() is not False,'mail_authority_revoked');require(not stop.is_set(),'mail_cancelled')
    permitted()
    with get_db() as c:
        a=account(c,who,pid,request.accountId)
        binding={'account_id':a['id'],'account_revision':a['revision'],'messages':[],
                 'attachments':[ref.model_dump() for ref in request.attachments]}
    result={}
    if slug=='email-drafting':
        if request.action in {'create','update'}:
            require(request.action=='create' and not request.draftId or request.action=='update' and request.draftId and request.expectedRevision,'draft_action_requires_correct_identity')
            result=save_draft(who,pid,request,operation_id=operation_id)
        elif request.action=='list':
            result={'drafts':list_drafts(who,pid,request.accountId)}
            binding['drafts']=[{key:row[key] for key in ('id','revision','payload_sha256')} for row in result['drafts']]
        elif request.action=='delete':
            with get_db() as c:
                c.execute('BEGIN IMMEDIATE');row,_=_draft(c,who,pid,request.accountId,request.draftId)
                require(row['revision']==request.expectedRevision and row['state']!='dispatching','draft_revision_conflict_or_dispatching')
                c.execute("UPDATE mail_drafts SET state='deleted',document='{}',revision=revision+1,updated_at=? WHERE id=?",(now(),row['id']))
                c.execute("UPDATE mail_send_grants SET state='revoked' WHERE draft_id=?",(row['id'],))
                _record_local_effect(c,who,pid,request.accountId,operation_id,'draft',row['id'],row['revision']+1,'deleted')
            result={'deleted':True,'draft_id':request.draftId,'provider_draft':False}
        else:result=get_draft(who,pid,request.accountId,request.draftId)
        if 'draft' in result:binding['draft']={k:result[k] for k in ('id','revision','payload_sha256')}
    elif slug=='authorized-email-send':
        if request.submissionId and not request.draftId:
            with get_db() as c:
                submission=_submission(c,who,pid,request.accountId,request.submissionId)
                result=json.loads(submission['receipt']) if submission['receipt']!='{}' else {'state':'unknown','accepted_by_smtp':None,'delivered':False,'automatic_retry':False}
                binding['draft']={'id':submission['draft_id'],'revision':submission['revision'],'payload_sha256':submission['payload_hash']}
        else:
            result=send(who,pid,request,operation_id=operation_id,authority=authority,check=check,stop=stop)
            binding['draft']={'id':request.draftId,'revision':request.expectedRevision,'payload_sha256':request.expectedPayloadSha256}
    else:
        with get_db() as c:
            a=account(c,who,pid,request.accountId)
            if slug=='email-search':
                require(request.query.strip(),'mail_search_requires_query')
                rows=c.execute('SELECT * FROM mail_messages WHERE account_id=? AND deleted=0 ORDER BY imported_at DESC LIMIT 500',(a['id'],)).fetchall();found=[]
                for row in rows:
                    value=json.loads(row['document']);query=request.query.casefold()
                    if query in (value['subject']+'\n'+value['text']+'\n'+packed(value['from'])).casefold():found.append((row,value))
                result={'matches':[{'id':r['id'],'thread_id':r['thread_id'],'subject':v['subject'],'from':v['from'],'date':v['date'],'excerpt':v['text'][:500],'revision':r['revision']} for r,v in found[:20]],'has_more':len(found)>20,'method':'literal-case-insensitive-local-import','provider_inbox_searched':False}
                binding['messages']=[{'id':r['id'],'revision':r['revision'],'sha256':r['sha256']} for r,v in found[:20]]
            elif slug in {'email-thread-summary','email-action-extraction','email-reply-context'}:
                require(request.messageIds,'choose_mail_messages');pairs=[message(c,who,pid,a['id'],mid) for mid in request.messageIds]
                if slug=='email-thread-summary' and len(pairs)==1:
                    rows=c.execute('SELECT * FROM mail_messages WHERE account_id=? AND thread_id=? AND deleted=0 ORDER BY imported_at,id LIMIT 21',(a['id'],pairs[0][0]['thread_id'])).fetchall()
                    require(len(rows)<=20,'thread_scope_limit');pairs=[(r,json.loads(r['document'])) for r in rows]
                binding['messages']=[{'id':r['id'],'revision':r['revision'],'sha256':r['sha256']} for r,v in pairs]
                if slug=='email-thread-summary':
                    ordered,warnings=_ordered_thread(pairs);messages=[];remaining=12000;returned=0;total=sum(len(v['authored']) for r,v in ordered)
                    for r,v in ordered:
                        quotes=[]
                        for line in v['authored']:
                            if len(line['text'])>remaining:continue
                            quotes.append(line);remaining-=len(line['text']);returned+=1
                        messages.append({'id':r['id'],'subject':v['subject'],'from':v['from'],'date':v['date'],'date_uncertain':v['date'] is None,'quotes':quotes,'quoted_lines_excluded':len(v['quoted']),'signature_lines_excluded':len(v['signature'])})
                    result={'kind':'extractive-thread-excerpts','messages':messages,'status':'partial','latest_message_id':ordered[-1][0]['id'],
                      'explicit_action_changes':_action_evidence(ordered),'warnings':warnings,'coverage':{'authored_lines':total,'included_lines':returned,'complete_authored_scope':total==returned,'character_budget':12000},
                      'notice':'Reply references order changes; source dates order independent messages. Literal authored excerpts and explicit labeled action changes are shown; unstructured agreements, refusals and partial answers require review.'}
                elif slug=='email-action-extraction':
                    result=_action_evidence(pairs)
                else:
                    require(len(pairs)==1,'reply_requires_one_original');r,v=pairs[0];suggested=v['reply_to'] or v['from']
                    if request.replyAll:suggested=suggested+v['to']+v['cc']
                    people={p['email']:p for p in suggested if p['email']!=a['address']}
                    result={'source_message_id':r['id'],'in_reply_to':v['message_id'],'subject':v['subject'],'body_excerpt':v['text'][:4000],
                      'suggested_recipients':list(people.values()),'recipients_trust':'unverified imported headers; review exact addresses','reply_all':request.replyAll,'bcc_recovered':False,'draft_created':False}
            elif slug=='recipient-resolution':
                results=[]
                for name in request.names:
                    matches=[{'id':r['id'],'email':r['address'],'displayName':r['display_name'],'revision':r['revision']} for r in c.execute('SELECT * FROM mail_contacts WHERE account_id=?',(a['id'],)) if r['display_name'].casefold()==name.casefold() or r['address'].casefold()==name.casefold()]
                    results.append({'query':name,'status':'resolved' if len(matches)==1 else 'ambiguous' if matches else 'not_found','candidates':matches})
                result={'resolutions':results,'exact_recipients':{key:_recipients(c,a['id'],getattr(request,key)) for key in ('to','cc','bcc')},'authorization_granted':False}
                contacts={person['id']:person['revision'] for item in results for person in item['candidates']}
                contacts.update({person['contact_id']:person['contact_revision'] for people in result['exact_recipients'].values() for person in people if person['contact_id']})
                binding['contacts']=[{'id':key,'revision':value} for key,value in contacts.items()]
            elif slug=='attachment-verification':result=verify_attachments(c,who,pid,request.attachments)
            elif slug=='email-followup-tracking':
                result=_followup(c,who,pid,request,operation_id)
                binding['followups']=[{key:item[key] for key in ('id','revision')} for item in result.get('followups',[])]
                if result.get('id'):binding['followups'].append({key:result[key] for key in ('id','revision')})
            elif slug=='bounce-analysis':result=_bounce(c,who,pid,request)
    if result.get('_draft_binding'):binding['draft']=result.pop('_draft_binding')
    for mid in result.pop('_mail_source_ids',[]):
        with get_db() as c:
            row,_=message(c,who,pid,request.accountId,mid)
            binding['messages'].append({'id':row['id'],'revision':row['revision'],'sha256':row['sha256']})
    permitted()
    with get_db() as c:recheck_binding(c,who,pid,binding)
    return result|{'mail_binding':binding,'version':VERSION}

def _submission(c,who,pid,aid,sid):
    account(c,who,pid,aid)
    row=c.execute('SELECT * FROM mail_submissions WHERE id=? AND owner_id=? AND project_id=? AND account_id=?',(sid,who.owner_id,pid,aid)).fetchone()
    require(row is not None,'mail_submission_unavailable');return row

def operation_effect(c,who,pid,operation_id):
    """Safe operational receipt survives cancellation; no message body or grant."""
    guard.check_actor(c,who,pid)
    row=c.execute('SELECT * FROM mail_submissions WHERE id=? AND owner_id=? AND project_id=?',(operation_id,who.owner_id,pid)).fetchone()
    if not row:return None
    account(c,who,pid,row['account_id'])
    return json.loads(row['receipt']) if row['receipt']!='{}' else {'operation_id':operation_id,'state':'unknown','accepted_by_smtp':None,'delivered':False,'automatic_retry':False}

def _followup(c,who,pid,req,operation_id):
    if req.action=='list':
        return {'followups':[dict(row) for row in c.execute('SELECT id,due_at,state,revision FROM mail_followups WHERE account_id=? ORDER BY due_at LIMIT 100',(req.accountId,))],'continuous_monitoring':False}
    if req.action=='track':
        original=_submission(c,who,pid,req.accountId,req.submissionId)
        require(original['state']=='smtp_accepted','followup_requires_smtp_acceptance')
        require(req.dueAt and datetime.fromisoformat(req.dueAt)>datetime.now(timezone.utc),'followup_requires_future_due_time')
        identifier=operation_id
        existing=c.execute('SELECT * FROM mail_followups WHERE id=?',(identifier,)).fetchone()
        if existing:require(existing['account_id']==req.accountId and existing['submission_id']==original['id'] and existing['due_at']==req.dueAt,'followup_operation_scope_changed')
        else:
            c.execute('INSERT INTO mail_followups VALUES(?,?,?,?,?,1,?)',(identifier,req.accountId,original['id'],req.dueAt,'watching',now()))
            _record_local_effect(c,who,pid,req.accountId,operation_id,'followup',identifier,1,'watching')
    else:identifier=req.followupId
    row=c.execute('SELECT * FROM mail_followups WHERE id=? AND account_id=?',(identifier,req.accountId)).fetchone()
    require(row is not None,'followup_unavailable')
    if req.action=='delete':
        require(row['revision']==req.expectedRevision,'followup_revision_conflict')
        c.execute("UPDATE mail_followups SET state='cancelled',revision=revision+1 WHERE id=? AND revision=?",(row['id'],row['revision']))
        _record_local_effect(c,who,pid,req.accountId,operation_id,'followup',row['id'],row['revision']+1,'cancelled')
        return {'id':row['id'],'status':'cancelled','revision':row['revision']+1,'automatic_followup_sent':False}
    if row['state']=='cancelled':return {'id':row['id'],'status':'cancelled','revision':row['revision'],'automatic_followup_sent':False}
    original=_submission(c,who,pid,req.accountId,row['submission_id']);draft_row,draft=_draft(c,who,pid,req.accountId,original['draft_id'])
    recipients={p['email'] for key in ('to','cc','bcc') for p in draft[key]};observed=[];excluded=[];answered=set()
    expected_ids={m[2] for line in draft['body'].splitlines() if (m:=ACTION_PATTERN.match(line)) and m[2] and ACTION_KIND.get(m[1].casefold(),'proposed')=='proposed'}
    for item in c.execute('SELECT * FROM mail_messages WHERE account_id=? AND deleted=0',(req.accountId,)):
        value=json.loads(item['document'])
        if original['message_id'] in value['references'] and recipients.intersection(x['email'] for x in value['from']):
            if value.get('auto_submitted') or value['dsn'] or re.search(r'(?i)(out of office|automatic reply|خارج المكتب|رد آلي|מחוץ למשרד|תשובה אוטומטית)',value['subject']):excluded.append(item['id']);continue
            observed.append(item['id'])
            for line in value['authored']:
                match=ACTION_PATTERN.match(line['text'])
                if match and match[2] and ACTION_KIND.get(match[1].casefold())=='answered':answered.add(match[2])
    pending=sorted(expected_ids-answered)
    return {'id':row['id'],'revision':row['revision'],'due_at':row['due_at'],'status':'reply_received_partial' if observed and pending else 'local_reply_evidence' if observed else 'due' if datetime.fromisoformat(row['due_at'])<=datetime.now(timezone.utc) else 'watching',
      '_draft_binding':{'id':draft_row['id'],'revision':draft_row['revision'],'payload_sha256':digest(draft)},
      'unanswered_explicit_action_ids':pending,'all_commitments_resolved':False,'excluded_auto_reply_ids':excluded,
      'reply_message_ids':observed,'_mail_source_ids':observed+excluded,'provider_checked':False,'automatic_followup_sent':False,'continuous_monitoring':False,'notice':'One-shot check of imported evidence; automatic replies and DSNs excluded. Explicit answered IDs do not prove every commitment is complete. No inbox polling or automatic reply.'}

def _bounce(c,who,pid,req):
    original=_submission(c,who,pid,req.accountId,req.submissionId);draft_row,draft=_draft(c,who,pid,req.accountId,original['draft_id'])
    row,value=message(c,who,pid,req.accountId,req.dsnMessageId)
    recipients={p['email'] for key in ('to','cc','bcc') for p in draft[key]};reports=[]
    mid_match=original['message_id'] in value['references'] or original['message_id'] in value.get('reported_original_message_ids',[]) or any(x.get('original-message-id')==original['message_id'] for x in value['dsn'])
    for part in value['dsn']:
        target=part.get('final-recipient','').split(';',1)[-1].strip()
        action=part.get('action');status=part.get('status','');consistent=action=='failed' and status.startswith('5.') or action=='delayed' and status.startswith('4.') or action in {'delivered','relayed','expanded'} and status.startswith('2.')
        if target in recipients and mid_match and re.fullmatch(r'[245]\.\d{1,3}\.\d{1,3}',status) and consistent:
            reports.append({'recipient':target,'action':part['action'],'status':part['status'],'source_message_id':row['id'],'trust':'untrusted_import_not_authoritative_delivery_receipt'})
    return {'matched_reports':reports,'_mail_source_ids':[row['id']],
      '_draft_binding':{'id':draft_row['id'],'revision':draft_row['revision'],'payload_sha256':digest(draft)},
      'correlation':'exact outbound message ID and recipient required','delivery_status_changed':False,'automatic_resend':False,'notice':'Imported DSNs can be forged; matched reports are evidence to review, not verified provider events.'}
