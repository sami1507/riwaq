"""Static approved-source navigation and isolated extraction; no live DOM claims.

Only server-registered exact public URLs are read. Form values stay local in an
explicit draft; submission, authentication and dynamic browser control fail closed.
"""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Literal
from urllib.parse import urljoin,urlsplit,urlencode

from pydantic import BaseModel,ConfigDict,Field,StrictBool,field_validator
from backend import guard,knowledge_fetch as fetch,public_knowledge as public
from backend.db import get_db,ROOT

MAX_BYTES=512*1024
MAX_TOTAL=1024*1024
MAX_REQUESTS=8
VERSION='riwaq-static-web-v1'


def policy_fingerprint(source):
    material={'source':public.source_fingerprint(source),'form_targets':source.get('web_form_targets',[])}
    return hashlib.sha256(json.dumps(material,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def recheck_binding(conn,who,project_id,binding):
    guard.check_actor(conn,who,project_id)
    if not isinstance(binding,dict) or set(binding)!={'source_id','source_fingerprint','url'}:
        raise guard.Denied('web_source_binding_invalid')
    source=public.source_dict(public.source_row(conn,binding['source_id']))
    if not public.source_allows(source,binding['url']) or policy_fingerprint(source)!=binding['source_fingerprint']:
        raise guard.Denied('web_source_revoked_or_changed')
    registered={fetch.normalize_url(item if isinstance(item,str) else item['url']) for item in source.get('urls',[])}
    if binding['url'] not in registered:raise guard.Denied('web_source_revoked_or_changed')
    return True


class WebError(ValueError):
    def __init__(self,code,message):self.code=code;super().__init__(message)


class _Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)


class _Option(_Strict):
    value:str=Field(max_length=2000)
    text:str=Field(max_length=200)
    disabled:bool


class _ControlField(_Strict):
    name:str=Field(max_length=200)
    type:str=Field(max_length=32)
    required:bool
    disabled:bool
    readonly:bool
    label:str=Field(max_length=200)
    maxlength:str=Field(max_length=16)
    options:list[_Option]=Field(max_length=20)


class _Form(_Strict):
    index:int=Field(ge=0,le=7)
    action:str=Field(max_length=4096)
    method:str=Field(max_length=30)
    enctype:str=Field(max_length=100)
    fields:list[_ControlField]=Field(max_length=24)
    issues:list[str]=Field(max_length=16)


class _Link(_Strict):
    href:str=Field(max_length=4096)
    text:str=Field(max_length=200)


class _Page(_Strict):
    version:Literal['riwaq-static-web-v1']
    input_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    title:str=Field(max_length=300)
    text:str=Field(max_length=12000)
    links:list[_Link]=Field(max_length=32)
    forms:list[_Form]=Field(max_length=8)
    script_present:bool
    password_control_present:bool
    truncated:bool
    issues:list[str]=Field(max_length=16)


class WebInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    sourceId:str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')
    url:str=Field(min_length=1,max_length=4096)
    allowNetwork:StrictBool=False
    formIndex:int=Field(default=0,ge=0,le=7)
    expectedPageSha256:str|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')
    expectedDestination:str|None=Field(default=None,max_length=4096)
    fields:dict[str,str]=Field(default_factory=dict,max_length=24)
    submit:StrictBool=False

    @field_validator('url')
    @classmethod
    def canonical(cls,value):return fetch.normalize_url(value)

    @field_validator('expectedDestination')
    @classmethod
    def destination(cls,value):return fetch.normalize_url(value) if value is not None else None

    @field_validator('fields')
    @classmethod
    def bounded_fields(cls,value):
        for key,item in value.items():
            if not key or len(key)>200 or len(item)>2000 or any(ord(c)<32 and c not in '\n\t' for c in key+item):
                raise ValueError('Invalid bounded form field')
            if re.search(r'password|passwd|secret|token|api.?key|credit|card.?number|cvv|otp',key,re.I):
                raise ValueError('Credential and payment fields are not accepted')
        if sum(len(k)+len(v) for k,v in value.items())>8000:raise ValueError('Form draft exceeds text limit')
        return value


def authority(who,project_id,request,expected=None):
    """Identity comes from the caller's authenticated session, never WebInput."""
    with get_db() as conn:
        guard.check_actor(conn,who,project_id)
        source=public.source_dict(public.source_row(conn,request.sourceId))
    if request.allowNetwork is not True or public.settings()['mode']!='hybrid':
        raise WebError('network_not_authorized','Public page reads require hybrid mode and explicit network permission.')
    if not public.source_allows(source,request.url):
        raise WebError('source_not_authorized','The source is not approved for this URL or its rights were withdrawn.')
    registered={fetch.normalize_url(item if isinstance(item,str) else item['url']) for item in source.get('urls',[])}
    if request.url not in registered:
        raise WebError('url_not_registered','Only exact server-registered source URLs may be read; query strings cannot be invented by the model.')
    fingerprint=policy_fingerprint(source)
    if expected and fingerprint!=expected:raise WebError('source_changed','Source permissions or configuration changed during the operation.')
    return source,fingerprint


class Control(threading.Event):
    def __init__(self,original,authorize):
        super().__init__();self.original=original;self.authorize=authorize
        self.deadline=min(time.monotonic()+60,getattr(original,'deadline',float('inf')))
        self.requests=0;self.bytes=0;self.pacer=public.PUBLIC_PACER
    def is_set(self):
        if super().is_set() or (self.original is not None and self.original.is_set()) or time.monotonic()>=self.deadline:return True
        self.authorize();return False
    def count_request(self):
        if self.is_set():raise fetch.FetchCancelled('Page read cancelled')
        if self.requests>=MAX_REQUESTS:raise WebError('request_limit','Page request budget exhausted')
        self.requests+=1
    def charge(self,size):
        self.bytes+=size
        if self.bytes>MAX_TOTAL:raise WebError('byte_limit','Page transfer budget exhausted')


def inspect_isolated(body,stop,authorize):
    """Use the existing pinned Python image; never parse page HTML on the host."""
    from backend.isolated_worker import execute,load_config,IsolatedError
    from backend.isolated_journal import Journal,assert_ready
    from backend.isolated_locks import worker_lease,LeaseError
    if not isinstance(body,bytes) or not 1<=len(body)<=MAX_BYTES:raise WebError('page_limit','HTML page must contain 1 byte to 512 KiB')
    config_path=Path(os.environ.get('RIWAQ_DATA_DIR',str(ROOT/'data')))/'isolated-workers.json'
    try:
        config=load_config(config_path)
        helper=Path(__file__).with_name('skill_web_parser.py').read_bytes()
        # The existing runner invokes unittest discovery. One trusted entrypoint
        # performs inspection; its test count is never product acceptance evidence.
        bootstrap=b'import unittest\nfrom skill_web_parser import emit\nclass PageInspection(unittest.TestCase):\n    def test_inspect(self):\n        emit()\n'
        with worker_lease():
            assert_ready();journal=Journal(config,'python_unittest')
            receipt=execute({'skill_web_parser.py':helper,'inspect_page.py':bootstrap,'page.bin':body},
                'python_unittest',['inspect_page.py'],config=config,stop=stop,authorize=authorize,
                timeout_seconds=30,memory_mb=256,progress=journal.event)
            journal.finish(receipt)
    except (IsolatedError,LeaseError,OSError,RuntimeError):
        raise WebError('isolated_parser_unavailable','The isolated static-page inspector is unavailable; no host parser fallback was used.') from None
    if stop.is_set():raise fetch.FetchCancelled('Page inspection cancelled')
    if receipt.get('status')!='completed' or not receipt.get('policy_inspected') or not receipt.get('cleanup_verified') or receipt.get('exit_code')!=0:
        raise WebError('page_inspection_failed','The isolated page inspector did not complete with a verified clean result.')
    try:
        raw=receipt['stdout']
        if len(raw.encode())>48000:raise ValueError()
        parsed=_Page.model_validate_json(raw).model_dump()
        if parsed['version']!=VERSION or parsed['input_sha256']!=hashlib.sha256(body).hexdigest():raise ValueError()
        if not isinstance(parsed['text'],str) or len(parsed['text'])>12000 or not isinstance(parsed['forms'],list) or len(parsed['forms'])>8:raise ValueError()
        if not isinstance(parsed['links'],list) or len(parsed['links'])>32:raise ValueError()
    except (KeyError,TypeError,ValueError,UnicodeError):
        raise WebError('page_result_invalid','The page inspector returned invalid or mismatched data.') from None
    resources={k:v for k,v in receipt.items() if k not in {'stdout','stderr'}}
    resources['isolation']='linux_container_shared_kernel_no_host_mounts_network_none'
    return parsed,resources


def _draft(page,request,source):
    if not request.expectedPageSha256 or request.expectedPageSha256!=page['sha256']:
        raise WebError('page_changed','Refresh the page and bind the draft to its current content fingerprint before filling.')
    forms=page['forms']
    if request.formIndex>=len(forms):raise WebError('form_missing','The selected form was not observed on the page.')
    form=forms[request.formIndex]
    if form.get('issues'):raise WebError('form_not_supported','This static form uses unsupported or ambiguous controls; use a verified browser session.')
    if form.get('method') not in {'get','post'} or form.get('enctype')!='application/x-www-form-urlencoded':
        raise WebError('form_not_supported','Only simple text/select form drafts are supported.')
    try:destination=fetch.validate_scope(urljoin(page['url'],form['action']),source['allowed_hosts'],source['allowed_prefixes'])
    except fetch.FetchError:raise WebError('form_destination_forbidden','The form destination is outside the approved source scope.') from None
    if urlsplit(destination).netloc!=urlsplit(page['url']).netloc:
        raise WebError('form_destination_forbidden','Cross-origin form destinations are not supported.')
    controls={row['name']:row for row in form['fields']}
    if set(request.fields)-set(controls):raise WebError('field_not_observed','A requested field was not observed in this form.')
    if not request.fields:raise WebError('empty_form_draft','Provide explicit values for the observed form controls.')
    values=[]
    for name,value in request.fields.items():
        item=controls[name]
        if item['disabled'] or item['readonly']:raise WebError('field_not_editable','A selected form field is disabled or read-only.')
        if item['type'] not in {'text','search','textarea','select'}:raise WebError('field_not_supported','This control type is not supported.')
        if item.get('maxlength'):
            if not re.fullmatch(r'[0-9]{1,5}',item['maxlength']):raise WebError('field_constraint_unknown','This field has an unsupported length constraint.')
            if len(value.encode('utf-16-le'))//2>int(item['maxlength']):raise WebError('field_too_long','A value exceeds the observed field length limit.')
        if item['required'] and not value.strip():raise WebError('required_field_missing','An observed required field is empty.')
        if item['type']=='select' and value not in {o['value'] for o in item['options'] if not o['disabled']}:
            raise WebError('option_not_observed','A select value does not match an observed enabled option.')
        values.append({'name':name,'type':item['type'],'value':value})
    missing=[name for name,item in controls.items() if item['required'] and not item['disabled'] and name not in request.fields]
    return {'status':'draft_only','submitted':False,'site_modified':False,'form_index':request.formIndex,
            'page_sha256':page['sha256'],'destination':destination,'method':form['method'],
            'fields':values,'missing_required_fields':missing,'complete_observed_fields':not missing,
            'notice':'Local draft of observed static controls only. No site values were changed, no request was submitted, and browser validation has not run.'}


def init(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS skill_web_submissions(
      owner_id TEXT NOT NULL,project_id TEXT NOT NULL,operation_id TEXT NOT NULL,
      session_id TEXT NOT NULL,request_sha256 TEXT NOT NULL,state TEXT NOT NULL,
      result TEXT NOT NULL,created_at TEXT NOT NULL,
      PRIMARY KEY(owner_id,project_id,operation_id))''')


def _submit(page,draft,request,source,who,project_id,operation_id,control):
    if not isinstance(operation_id,str) or not re.fullmatch(r'[0-9a-f]{32}',operation_id):
        raise WebError('submission_identity_required','Form submission requires a server-created task identifier.')
    if draft['method']!='post' or draft['missing_required_fields']:
        raise WebError('form_incomplete','Submission requires a complete supported POST form.')
    if request.expectedDestination is None or request.expectedDestination!=draft['destination']:
        raise WebError('form_destination_changed','Explicitly confirm the observed resolved form destination before submission.')
    targets=source.get('web_form_targets',[])
    if not isinstance(targets,list) or len(targets)>20:raise WebError('submission_policy_invalid','The server form-target policy is invalid.')
    approved=False
    for target in targets:
        if not isinstance(target,dict) or set(target)!={'url','method'}:continue
        try:canonical=fetch.normalize_url(target['url'])
        except fetch.FetchError:continue
        if target['method']=='POST' and canonical==draft['destination']:approved=True
    if not approved:raise WebError('submission_destination_not_authorized','The form action has no separate server-approved POST policy. No values were sent.')
    encoded=urlencode([(item['name'],item['value']) for item in draft['fields']],encoding='utf-8').encode('ascii')
    if len(encoded)>32768:raise WebError('submission_body_limit','Encoded form values exceed the outbound limit.')
    scope={'owner':who.owner_id,'project':project_id,'session':who.session_id,'operation':operation_id,
           'page_sha256':page['sha256'],'policy':page['source_fingerprint'],'destination':draft['destination'],
           'method':'POST','body_sha256':hashlib.sha256(encoded).hexdigest()}
    fingerprint=hashlib.sha256(json.dumps(scope,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    control.authorize()
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE');init(conn);guard.check_actor(conn,who,project_id)
        prior=conn.execute('SELECT request_sha256,state,result FROM skill_web_submissions WHERE owner_id=? AND project_id=? AND operation_id=?',
            (who.owner_id,project_id,operation_id)).fetchone()
        if prior:
            if prior['request_sha256']!=fingerprint:raise WebError('submission_scope_changed','The task identifier is already bound to a different form operation.')
            if prior['state']=='response_received':return json.loads(prior['result'])|{'reused_receipt':True}
            raise WebError('submission_outcome_unknown','A previous dispatch may have reached the site. It will not be retried automatically; verify the site separately.')
        conn.execute('INSERT INTO skill_web_submissions VALUES(?,?,?,?,?,?,?,?)',
            (who.owner_id,project_id,operation_id,who.session_id,fingerprint,'dispatching','{}',datetime.now(timezone.utc).isoformat()))
    # From the durable claim onward, any interruption is conservatively unknown.
    # The model, an HTTP redirect, or a retry cannot authorize a second POST.
    try:
        response=fetch.fetch_https(draft['destination'],allowed_hosts=source['allowed_hosts'],allowed_prefixes=source['allowed_prefixes'],
            max_bytes=MAX_BYTES,charge=control.charge,cancelled=control,remaining_budget=lambda:MAX_TOTAL-control.bytes,
            pacer=control.pacer,method='POST',body=encoded,follow_redirects=False)
        result={'status':'response_received','http_status':response.status,'request_dispatched':True,
            'operation_id':operation_id,'request_sha256':fingerprint,'response_sha256':hashlib.sha256(response.body).hexdigest(),
            'response_bytes':len(response.body),'redirect_followed':False,'verified_effect':False,'reused_receipt':False,
            'notice':'The server returned an HTTP response. This does not prove the intended change was accepted or persisted. No redirect or automatic retry was performed.'}
        control.authorize()
        with get_db() as conn:
            guard.check_actor(conn,who,project_id)
            conn.execute("UPDATE skill_web_submissions SET state='response_received',result=? WHERE owner_id=? AND project_id=? AND operation_id=? AND request_sha256=?",
                (json.dumps(result,sort_keys=True,separators=(',',':')),who.owner_id,project_id,operation_id,fingerprint))
        return result
    except BaseException:
        with get_db() as conn:
            conn.execute("UPDATE skill_web_submissions SET state='unknown' WHERE owner_id=? AND project_id=? AND operation_id=? AND request_sha256=?",
                (who.owner_id,project_id,operation_id,fingerprint))
        raise WebError('submission_outcome_unknown','The form outcome is unknown after a possible dispatch. Do not resubmit; inspect the site state separately.') from None


def run(operation:Literal['browser-navigation','page-content-extraction','page-extraction','web-form-filling'],who,project_id,request,stop=None,check=None,operation_id=None):
    if operation not in {'browser-navigation','page-content-extraction','page-extraction','web-form-filling'}:raise WebError('operation_not_supported','Unsupported static web operation')
    request=WebInput.model_validate(request) if not isinstance(request,WebInput) else request
    if request.submit and operation!='web-form-filling':raise WebError('submission_not_supported','Submission is only allowed in the explicit form workflow.')
    source,fingerprint=authority(who,project_id,request)
    def recheck():
        if check:check()
        authority(who,project_id,request,fingerprint)
        return True
    control=Control(stop,recheck)
    if control.is_set():raise fetch.FetchCancelled('Page read cancelled before network access')
    robots=fetch.robots_policy(request.url,allowed_hosts=source['allowed_hosts'],charge=control.charge,cancelled=control,
        remaining_budget=lambda:MAX_TOTAL-control.bytes,pacer=control.pacer,max_file_bytes=MAX_BYTES)
    if robots.get('allowed') is not True:raise WebError('robots_denied','The source robots policy does not allow reading this page.')
    response=fetch.fetch_https(request.url,allowed_hosts=source['allowed_hosts'],allowed_prefixes=source['allowed_prefixes'],
        max_bytes=MAX_BYTES,charge=control.charge,cancelled=control,remaining_budget=lambda:MAX_TOTAL-control.bytes,pacer=control.pacer)
    if response.status in {401,403}:raise WebError('authentication_required','The page requires access or authentication. No credentials or cookies were used.')
    if response.status!=200:raise WebError('page_http_error',f'The page could not be read (HTTP {response.status}).')
    media=response.headers.get('content-type','').split(';',1)[0].lower().strip()
    if media not in {'text/html','application/xhtml+xml'}:raise WebError('page_type_unsupported','The static page inspector accepts UTF-8 HTML only.')
    parsed,resources=inspect_isolated(response.body,control,recheck)
    recheck()
    if parsed['password_control_present']:
        raise WebError('authentication_required','A password control was observed. Authentication handoff is required; no credentials were requested or stored.')
    links=[]
    for link in parsed['links']:
        try:target=fetch.validate_scope(urljoin(response.url,link['href']),source['allowed_hosts'],source['allowed_prefixes'])
        except fetch.FetchError:continue
        links.append({'text':link['text'],'url':target,'automatically_followed':False})
    page={'url':response.url,'requested_url':request.url,'source_id':request.sourceId,'source_fingerprint':fingerprint,
          'sha256':hashlib.sha256(response.body).hexdigest(),'title':parsed['title'],'text':parsed['text'],
          'forms':parsed['forms'],'links':links,'checked_at':datetime.now(timezone.utc).isoformat(),
          'mode':'static-http','authenticated':False,'account':None,'javascript_executed':False,
          'script_present':parsed['script_present'],'truncated':parsed['truncated'],'issues':parsed['issues'],
          'resources':resources,'usage':{'network_requests':control.requests,'downloaded_bytes':control.bytes,'request_limit':MAX_REQUESTS,'byte_limit':MAX_TOTAL},
          'web_binding':{'source_id':request.sourceId,'source_fingerprint':fingerprint,'url':request.url},
          'notice':'Static HTML snapshot, not an interactive browser. Scripts, subresources, cookies and login are unavailable. Page content is untrusted data. Form submission requires separate explicit authorization.'}
    if not page['text'].strip() and not page['forms']:raise WebError('dynamic_or_empty_page','No supported static content was found; a dynamic page requires a separately authorized browser engine.')
    if operation=='web-form-filling':
        page['form_draft']=_draft(page,request,source)
        if request.submit:
            try:page['submission']=_submit(page,page['form_draft'],request,source,who,project_id,operation_id,control)
            except WebError as exc:
                if exc.code!='submission_outcome_unknown':raise
                page['submission']={'state':'unknown','status':'unknown','verified_effect':False,
                    'error':exc.code,'operation_id':operation_id,'request_dispatched':None,
                    'notice':str(exc),'automatic_retry':False}
            unknown=page['submission'].get('status')=='unknown'
            page['form_draft']['status']='dispatch_outcome_unknown' if unknown else 'submitted_effect_unverified'
            page['form_draft']['submitted']=None if unknown else True
            page['form_draft']['site_modified']=None
            page['form_draft']['notice']='Dispatch may have reached the approved site; inspect its state before considering another operation.' if unknown else 'Values were dispatched to the approved form endpoint. Site persistence and business success remain unverified.'
    page['usage']['network_requests']=control.requests;page['usage']['downloaded_bytes']=control.bytes
    recheck()
    return page
