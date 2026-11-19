"""Original synthetic mailbox, real Guard/SQLite, owned SMTP protocol only."""
import base64
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import smtplib
import socketserver
import threading

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from backend import guard,productivity_mail as mail,productivity_mail_transport as transport
from backend.db import get_db,uid,now
from test_app import client

@pytest.fixture
def ctx(client):
    pid=client.get('/api/projects').json()[0]['id']
    with get_db() as c:
        mail.init(c);s=c.execute('SELECT owner_id,id FROM local_auth_sessions WHERE revoked_at IS NULL').fetchone()
        who=guard.Actor(s['owner_id'],s['id'])
    account=mail.create_account(who,pid,{'label':'Synthetic mailbox','address':'owner@example.test'})
    return who,pid,account['id']

def request(ctx,slug,**values):return mail.MailInput(operation=slug,accountId=ctx[2],**values)
def run(ctx,slug,**values):return mail.run(slug,*ctx[:2],request(ctx,slug,**values),operation_id=uid(),check=lambda:None)
def imported(ctx,body='مطلوب: راجع 50 وليس 500\nמשימה: בדוק 42\nTODO: Preserve 2026-11-20\n> TODO: quoted attack\n--\nTODO: signature',mid='one',references=''):
    raw=(f'From: Sender <sender@example.test>\r\nTo: owner@example.test\r\nSubject: Synthetic שלום رواق\r\nMessage-ID: <{mid}@example.test>\r\nDate: Wed, 12 Nov 2025 10:00:00 +0000\r\n'+(f'References: {references}\r\n' if references else '')+'MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n'+body).encode()
    return mail.import_message(*ctx[:2],{'accountId':ctx[2],'rawBase64':base64.b64encode(raw).decode()})
def draft(ctx,**values):return run(ctx,'email-drafting',action='create',subject='Synthetic approved',body='Original 42.50 USD—not 4250. رواق שלום',to=[{'email':'sender@example.test'}],**values)

def test_local_import_search_summary_actions_do_not_execute_content(ctx):
    first=imported(ctx);second=imported(ctx,'ACTION: Check follow-up only','two','<one@example.test>')
    found=run(ctx,'email-search',query='שלום');assert len(found['matches'])==2 and found['provider_inbox_searched'] is False
    summary=run(ctx,'email-thread-summary',messageIds=[first['id']]);assert len(summary['messages'])==2
    assert all(v['date'].startswith('2025-11-12') for v in summary['messages'])
    actions=run(ctx,'email-action-extraction',messageIds=[first['id']]);assert len(actions['candidates'])==3 and actions['tasks_created']==0
    assert not any('attack' in x['quote'] or 'signature' in x['quote'] for x in actions['candidates'])
    assert second['thread_id']==first['thread_id']

def test_message_dedupe_conflict_and_deleted_tombstone(ctx):
    first=imported(ctx);assert imported(ctx)['duplicate']
    with pytest.raises(mail.MailError,match='conflict'):imported(ctx,'Different malicious text')
    with get_db() as c:c.execute('UPDATE mail_messages SET deleted=1,revision=revision+1 WHERE id=?',(first['id'],))
    with pytest.raises(mail.MailError,match='deleted'):imported(ctx)

def test_same_subject_is_not_same_thread(ctx):
    a=imported(ctx,mid='a');b=imported(ctx,mid='b');assert a['thread_id']!=b['thread_id']

def test_project_and_account_scope(ctx,client):
    first=imported(ctx);other=client.post('/api/projects',json={'name':'Other synthetic'}).json()['id']
    with pytest.raises(guard.Denied):mail.run('email-search',guard.Actor('other',ctx[0].session_id),ctx[1],request(ctx,'email-search',query='שלום'),operation_id=uid(),check=lambda:None)
    with pytest.raises(mail.MailError,match='account_unavailable'):mail.run('email-search',ctx[0],other,request(ctx,'email-search',query='שלום'),operation_id=uid(),check=lambda:None)
    other_account=mail.create_account(*ctx[:2],{'label':'Second','address':'two@example.test'})
    with pytest.raises(mail.MailError,match='message_unavailable'):run((ctx[0],ctx[1],other_account['id']),'email-thread-summary',messageIds=[first['id']])

def test_draft_revision_contact_resolution_and_no_remote_draft(ctx):
    for address in ['one@example.test','two@example.test']:mail.save_contact(*ctx,{'displayName':'ليلى','email':address})
    resolved=run(ctx,'recipient-resolution',names=['ليلى','Missing']);assert resolved['resolutions'][0]['status']=='ambiguous'
    assert resolved['resolutions'][1]['status']=='not_found' and not resolved['authorization_granted']
    first=draft(ctx);assert first['state']=='draft' and first['provider_draft'] is False
    updated=run(ctx,'email-drafting',action='update',draftId=first['id'],expectedRevision=1,subject='Updated',body='No value changed',to=[{'email':'sender@example.test'}])
    assert updated['revision']==2
    with pytest.raises(mail.MailError,match='revision_conflict'):run(ctx,'email-drafting',action='update',draftId=first['id'],expectedRevision=1,body='Stale')
    with get_db() as c:
        with pytest.raises(mail.MailError,match='draft_changed'):mail.recheck_binding(c,*ctx[:2],first['mail_binding'])

@pytest.mark.parametrize('language,greeting',[('ar','مرحبًا،'),('he','שלום,'),('en','Hello,')])
def test_compose_creates_editable_literal_draft_without_inventing_facts(ctx,language,greeting):
    facts=['المبلغ 42.50 USD وليس 4250.','התאריך 2026-11-20.','Endpoint /api/items_v2 stays unchanged.']
    result=run(ctx,'email-drafting',action='create',to=[{'email':'sender@example.test'}],
        compose={'language':language,'goal':'Discuss the confirmed delivery','confirmedFacts':facts,'request':'Please confirm the exact date.'})
    value=result['draft'];assert value['body'].startswith(greeting)
    assert all(value['body'].count(fact)==1 for fact in facts)
    assert value['composition']['method']=='literal-local-template'
    assert value['composition']['facts_independently_verified'] is False
    assert value['composition']['requires_review'] is True and result['provider_draft'] is False
    corrected=run(ctx,'email-drafting',action='update',draftId=result['id'],expectedRevision=1,
        subject='Reviewed draft',body='Exact user replacement.',to=[{'email':'sender@example.test'}])
    assert corrected['draft']['body']=='Exact user replacement.' and corrected['revision']==2
    assert corrected['draft']['body_origin']=='explicit_user_draft'
    with get_db() as c:assert c.execute('SELECT count(*) FROM mail_submissions').fetchone()[0]==0

def test_compose_conflicting_body_wrong_scope_and_oversize_rejected(ctx):
    composed={'language':'en','goal':'A goal','confirmedFacts':[],'request':'Exact request'}
    with pytest.raises((mail.MailError,ValidationError),match='composition_requires'):
        request(ctx,'email-drafting',action='create',body='Other body',compose=composed)
    with pytest.raises((mail.MailError,ValidationError),match='composition_requires'):
        request(ctx,'authorized-email-send',compose=composed)
    with pytest.raises(mail.MailError,match='size_limit'):
        run(ctx,'email-drafting',action='create',compose={**composed,'confirmedFacts':['X'*1000]*20})

def test_list_draft_binding_rechecks_nested_attachment_and_current_revision(ctx,client):
    from test_skill_batch03_files import create
    created=create(client,ctx[1]);descriptor=client.get(f'/api/projects/{ctx[1]}/skill-files').json()[0]
    ref={k:descriptor[k] for k in ('kind','id','version','sha256')}
    item=draft(ctx,attachments=[ref]);listing=run(ctx,'email-drafting',action='list')
    assert listing['mail_binding']['drafts']==[{'id':item['id'],'revision':1,'payload_sha256':item['payload_sha256']}]
    assert client.delete(f'/api/projects/{ctx[1]}/skill-runs/{created["id"]}').status_code==200
    with get_db() as c:
        with pytest.raises((HTTPException,guard.Denied,mail.MailError)):mail.recheck_binding(c,*ctx[:2],listing['mail_binding'])
    # Current listings filter inaccessible rows; cached prior results must still
    # fail revalidation above. Other independent drafts remain usable.
    assert mail.list_drafts(*ctx)==[]

def test_local_draft_effect_metadata_is_atomic_and_content_free(ctx):
    identifier=uid();req=request(ctx,'email-drafting',action='create',subject='PRIVATE SYNTHETIC SUBJECT',body='PRIVATE SYNTHETIC BODY',to=[{'email':'sender@example.test'}])
    result=mail.run('email-drafting',*ctx[:2],req,operation_id=identifier,check=lambda:None)
    with get_db() as c:
        effects=mail.local_effects(c,identifier)
        assert effects==[{'kind':'local_mail_change','operation_id':identifier,'resource_kind':'draft','resource_id':result['id'],'revision':1,'state':'draft','automatic_replay':False}]
        assert 'PRIVATE' not in json.dumps(effects) and 'sender@example.test' not in json.dumps(effects)
    repeated=mail.run('email-drafting',*ctx[:2],req,operation_id=identifier,check=lambda:None)
    assert repeated['id']==result['id']
    deleted=uid();mail.run('email-drafting',*ctx[:2],request(ctx,'email-drafting',action='delete',draftId=result['id'],expectedRevision=1),operation_id=deleted,check=lambda:None)
    with get_db() as c:assert mail.local_effects(c,deleted)[0]['state']=='deleted'

def test_recipient_resolution_snapshot_stops_after_contact_change(ctx):
    person=mail.save_contact(*ctx,{'displayName':'ليلى','email':'sender@example.test'})
    result=run(ctx,'recipient-resolution',names=['ليلى'],to=[{'contactId':person['id']}])
    assert result['mail_binding']['contacts']==[{'id':person['id'],'revision':1}]
    with get_db() as c:
        c.execute('UPDATE mail_contacts SET address=?,revision=revision+1 WHERE id=?',('hidden@example.test',person['id']))
        with pytest.raises(mail.MailError,match='contact_changed'):mail.recheck_binding(c,*ctx[:2],result['mail_binding'])

def test_reply_context_does_not_send_or_assume_header_identity(ctx):
    original=imported(ctx);reply=run(ctx,'email-reply-context',messageIds=[original['id']],replyAll=True)
    assert reply['suggested_recipients']==[{'email':'sender@example.test','name':'Sender'}]
    assert reply['draft_created'] is False and reply['bcc_recovered'] is False

def test_import_html_never_fetches_and_malformed_headers_rejected(ctx):
    raw=b'From: sender@example.test\r\nContent-Type: text/html\r\n\r\n<img src="https://evil.invalid/private">'
    value=mail.parse_message(raw);assert not value['text'] and not value['remote_resources_loaded'] and value['warnings']==['html_not_rendered_or_fetched']
    with pytest.raises(mail.MailError,match='duplicate'):mail.parse_message(b'Subject: one\r\nSubject: two\r\n\r\nBody')
    with pytest.raises(mail.MailError,match='header'):mail.parse_message(b'X-Test: '+b'a'*33000+b'\r\n\r\nBody')

@pytest.mark.parametrize('email',['test@example.test\r\nBcc: bad@example.test','Name <user@example.test>','a..b@example.test','שלום@example.test'])
def test_ambiguous_or_smtputf8_address_not_silently_changed(email):
    with pytest.raises((ValidationError,mail.MailError)):mail.Recipient(email=email)

@pytest.fixture
def smtp(ctx,monkeypatch):
    state={'messages':[],'recipients':[],'drop_data':False,'refuse':False}
    class Handler(socketserver.StreamRequestHandler):
        def reply(self,line):self.wfile.write(line+b'\r\n');self.wfile.flush()
        def handle(self):
            self.reply(b'220 owned.test ESMTP')
            while True:
                line=self.rfile.readline(2000000)
                if not line:break
                command=line.split(b' ',1)[0].strip().upper()
                if command in {b'EHLO',b'HELO'}:self.reply(b'250-owned.test');self.reply(b'250 AUTH PLAIN')
                elif command==b'AUTH':self.reply(b'235 Authenticated fixture')
                elif command==b'MAIL':self.reply(b'250 Sender accepted')
                elif command==b'RCPT':
                    state['recipients'].append(line.decode().strip());self.reply(b'550 Refused' if state['refuse'] else b'250 Recipient accepted')
                elif command==b'DATA':
                    self.reply(b'354 End with dot');data=[]
                    while True:
                        piece=self.rfile.readline(2000000)
                        if piece==b'.\r\n' or not piece:break
                        data.append(piece[1:] if piece.startswith(b'..') else piece)
                    state['messages'].append(b''.join(data))
                    if state['drop_data']:break
                    self.reply(b'250 Queued by owned fixture')
                elif command==b'RSET':self.reply(b'250 Reset')
                elif command==b'QUIT':self.reply(b'221 Bye');break
                else:self.reply(b'502 Unsupported')
    class Server(socketserver.ThreadingTCPServer):allow_reuse_address=True;daemon_threads=True
    server=Server(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    config=transport.SMTPConfig(enabled=True,host='smtp.example.test',username='owner@example.test',fromAddress='owner@example.test',allowedRecipients=['sender@example.test','hidden@example.test'])
    monkeypatch.setattr(transport,'configuration',lambda aid:config)
    monkeypatch.setattr(transport.knowledge_fetch,'resolve_public',lambda *a,**k:['8.8.8.8'])
    monkeypatch.setattr(transport.providers,'secret',lambda name:'SYNTHETIC-NOT-A-SECRET')
    monkeypatch.setattr(transport,'_connect',lambda config,ip:smtplib.SMTP('127.0.0.1',server.server_address[1],local_hostname='riwaq.test',timeout=3))
    monkeypatch.setattr(mail,'settings',lambda:{'mode':'hybrid'})
    try:yield state,config
    finally:server.shutdown();server.server_close();thread.join(2)

def approved(ctx,first,rid=None):
    rid=rid or uid();token=mail.approve_send(*ctx,first['id'],first['revision'],first['payload_sha256'],operation_id=rid)
    req=request(ctx,'authorized-email-send',draftId=first['id'],expectedRevision=first['revision'],expectedPayloadSha256=first['payload_sha256'])
    return rid,token,req
def dispatch(ctx,scope):
    rid,token,req=scope
    return mail.run('authorized-email-send',*ctx[:2],req,operation_id=rid,authority=token,check=lambda:None)

def test_actual_owned_smtp_send_bcc_and_idempotent_receipt(ctx,smtp):
    first=draft(ctx,bcc=[{'email':'hidden@example.test'}]);scope=approved(ctx,first)
    sent=dispatch(ctx,scope);again=dispatch(ctx,scope);state,_=smtp
    assert sent['accepted_by_smtp'] and sent['delivered'] is False and again['reused_receipt'] and len(state['messages'])==1
    message=BytesParser(policy=policy.default).parsebytes(state['messages'][0])
    assert message['Bcc'] is None and 'hidden@example.test' not in str(message)
    assert 'hidden@example.test' in state['recipients'][1]
    assert '42.50' in message.get_content() and 'שלום' in message.get_content()
    if os.environ.get('RIWAQ_RECORD_MAIL_EVIDENCE')=='1':
        target=Path(__file__).resolve().parents[1]/'reports/skills-batch-04-20261116/mail/owned-smtp.json'
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps({'environment':'Windows Python, owned loopback SMTP fixture','external_recipient_contacted':False,
          'tls_fixture':False,'transport_dns_tls_seam_mocked':True,'actual_smtp_messages_received':len(state['messages']),
          'envelope_recipient_count':len(state['recipients']),'bcc_header_absent':message['Bcc'] is None,
          'arabic_hebrew_english_body_checked':True,'content_sha256':hashlib.sha256(state['messages'][0]).hexdigest(),
          'first_receipt':{k:v for k,v in sent.items() if k not in {'mail_binding'}},
          'second_call_reused_receipt':again['reused_receipt'],'second_call_additional_messages':0},ensure_ascii=False,indent=2),encoding='utf-8')

def test_unknown_reply_no_automatic_or_explicit_same_draft_resend(ctx,smtp):
    state,_=smtp;state['drop_data']=True;first=draft(ctx);scope=approved(ctx,first)
    assert dispatch(ctx,scope)['state']=='unknown'
    assert dispatch(ctx,scope)['state']=='unknown' and len(state['messages'])==1
    with pytest.raises(mail.MailError,match='not_sendable'):approved(ctx,first)

def test_parallel_dispatch_claim_is_shared(ctx,smtp):
    first=draft(ctx);scope=approved(ctx,first)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:dispatch(ctx,scope),range(2)))
    assert len(smtp[0]['messages'])==1
    assert any(x['state']=='smtp_accepted' for x in results)
    assert all(x['state'] in {'smtp_accepted','unknown'} for x in results)

def test_send_requires_separate_server_authority(ctx,smtp):
    first=draft(ctx)
    with pytest.raises(mail.MailError,match='authorization_required'):run(ctx,'authorized-email-send',draftId=first['id'],expectedRevision=1,expectedPayloadSha256=first['payload_sha256'])
    assert smtp[0]['messages']==[]

def test_changed_draft_or_recipient_policy_invalidates_approval(ctx,smtp):
    first=draft(ctx);scope=approved(ctx,first)
    run(ctx,'email-drafting',action='update',draftId=first['id'],expectedRevision=1,subject='Changed',body='Changed',to=[{'email':'sender@example.test'}])
    with pytest.raises(mail.MailError,match='draft_changed'):dispatch(ctx,scope)
    assert smtp[0]['messages']==[]

def test_smtp_recipient_rejection_is_not_delivery(ctx,smtp):
    smtp[0]['refuse']=True;value=dispatch(ctx,approved(ctx,draft(ctx)))
    assert value['state']=='rejected' and value['stage']=='recipients' and not value['accepted_by_smtp']
    assert smtp[0]['messages']==[]

def test_local_mode_blocks_before_config_or_credentials(ctx,monkeypatch):
    first=draft(ctx);monkeypatch.setattr(transport,'configuration',lambda *a:pytest.fail('No cloud settings inspected'))
    with pytest.raises(mail.MailError,match='local_mode'):approved(ctx,first)

def test_stop_and_revocation_fail_closed(ctx,smtp):
    first=draft(ctx);scope=approved(ctx,first);stop=threading.Event();stop.set()
    with pytest.raises(mail.MailError,match='cancelled'):mail.run('authorized-email-send',*ctx[:2],scope[2],operation_id=scope[0],authority=scope[1],check=lambda:None,stop=stop)
    with get_db() as c:c.execute('UPDATE local_auth_sessions SET revoked_at=? WHERE id=?',('2026-11-16',ctx[0].session_id))
    with pytest.raises(guard.Denied):dispatch(ctx,scope)
    assert smtp[0]['messages']==[]

def import_raw(ctx,raw):return mail.import_message(*ctx[:2],{'accountId':ctx[2],'rawBase64':base64.b64encode(raw).decode()})

def test_thread_changes_dates_refusal_and_partial_answer(ctx):
    first=imported(ctx,'ACTION [A1]: Review due: tomorrow\nمهمة [A2]: افحص القيمة 50\nמשימה [A3]: בדוק 42',mid='plan')
    update=imported(ctx,'UPDATE [A1]: New requirement due: 2025-11-20\nإلغاء [A2]: ألغي الطلب\nנענה [A3]: נבדק\nREJECTED [A4]: Not agreed\nThis freeform sentence is not assumed complete.',mid='change',references='<plan@example.test>')
    result=run(ctx,'email-thread-summary',messageIds=[first['id']]);assert result['latest_message_id']==update['id'] and result['coverage']['complete_authored_scope']
    states={x['action_id']:x for x in result['explicit_action_changes']['latest_explicit_states']}
    assert states['A1']['state']=='updated' and states['A2']['state']=='cancelled' and states['A3']['state']=='answered' and states['A4']['state']=='rejected'
    assert result['status']=='partial' and result['explicit_action_changes']['unstructured_authored_lines']==1
    original=run(ctx,'email-action-extraction',messageIds=[first['id']]);assert original['candidates'][0]['deadline']['date']=='2025-11-13'
    assert original['candidates'][0]['deadline']['basis']=='message_date_local_offset'

def test_import_reply_before_parent_still_connects_explicit_thread(ctx):
    reply=imported(ctx,'ANSWERED [A1]: Done',mid='reply',references='<parent@example.test>')
    parent=imported(ctx,'ACTION [A1]: Check',mid='parent')
    assert reply['thread_id']==parent['thread_id']
    result=run(ctx,'email-thread-summary',messageIds=[parent['id']]);assert result['latest_message_id']==reply['id']

def test_thread_budget_is_visible_not_silent_first_three_lines(ctx):
    first=imported(ctx,'\n'.join('Original line '+str(n) for n in range(100)),mid='long')
    summary=run(ctx,'email-thread-summary',messageIds=[first['id']]);assert len(summary['messages'][0]['quotes'])==100
    assert summary['coverage']['complete_authored_scope']
    second=imported(ctx,'X'*13000,mid='oversized',references='<long@example.test>')
    summary=run(ctx,'email-thread-summary',messageIds=[second['id']]);assert not summary['coverage']['complete_authored_scope']
    assert summary['coverage']['included_lines']<summary['coverage']['authored_lines']

def test_deleted_message_invalidates_cached_evidence(ctx):
    first=imported(ctx);summary=run(ctx,'email-thread-summary',messageIds=[first['id']])
    mail.delete_message(*ctx,first['id'],1)
    with get_db() as c:
        with pytest.raises(mail.MailError,match='unavailable'):mail.recheck_binding(c,*ctx[:2],summary['mail_binding'])
        assert c.execute('SELECT document FROM mail_messages WHERE id=?',(first['id'],)).fetchone()[0]=='{}'

def test_false_authority_callback_never_runs_operation(ctx):
    with pytest.raises(mail.MailError,match='authority_revoked'):mail.run('email-drafting',*ctx[:2],request(ctx,'email-drafting',action='create',body='No'),operation_id=uid(),check=lambda:False)
    assert mail.list_drafts(*ctx)==[]

def test_followup_ignores_auto_reply_and_preserves_unanswered_ids(ctx,smtp):
    first=run(ctx,'email-drafting',action='create',body='ACTION [A1]: Check invoice\nACTION [A2]: Check dates',to=[{'email':'sender@example.test'}])
    sent=dispatch(ctx,approved(ctx,first));tracked=run(ctx,'email-followup-tracking',action='track',submissionId=sent['operation_id'],dueAt='2090-01-01T12:00:00+02:00')
    raw=(f'From: sender@example.test\r\nSubject: Out of office\r\nAuto-Submitted: auto-replied\r\nMessage-ID: <auto@example.test>\r\nIn-Reply-To: {sent["message_id"]}\r\n\r\nANSWERED [A1]: Automated').encode()
    automated=import_raw(ctx,raw)
    result=run(ctx,'email-followup-tracking',followupId=tracked['id']);assert result['status']=='watching' and result['excluded_auto_reply_ids']==[automated['id']]
    actual=imported(ctx,'ANSWERED [A1]: Invoice checked only',mid='realreply',references=sent['message_id'])
    result=run(ctx,'email-followup-tracking',followupId=tracked['id']);assert result['status']=='reply_received_partial' and result['unanswered_explicit_action_ids']==['A2']
    assert result['reply_message_ids']==[actual['id']] and not result['all_commitments_resolved'] and not result['continuous_monitoring']
    cancelled=run(ctx,'email-followup-tracking',action='delete',followupId=tracked['id'],expectedRevision=1);assert cancelled['status']=='cancelled'
    assert run(ctx,'email-followup-tracking',followupId=tracked['id'])['status']=='cancelled'
    with get_db() as c:
        with pytest.raises(mail.MailError,match='followup_changed'):mail.recheck_binding(c,*ctx[:2],result['mail_binding'])
        effects=[dict(row) for row in c.execute("SELECT resource_kind,resource_id,revision,state FROM mail_local_effects WHERE resource_kind='followup' ORDER BY revision")]
        assert effects==[{'resource_kind':'followup','resource_id':tracked['id'],'revision':1,'state':'watching'},{'resource_kind':'followup','resource_id':tracked['id'],'revision':2,'state':'cancelled'}]

def test_followup_snapshot_inherits_outbound_draft_revocation(ctx,smtp):
    first=draft(ctx);sent=dispatch(ctx,approved(ctx,first))
    tracked=run(ctx,'email-followup-tracking',action='track',submissionId=sent['operation_id'],dueAt='2090-01-01T12:00:00+02:00')
    assert tracked['mail_binding']['draft']['id']==first['id']
    run(ctx,'email-drafting',action='delete',draftId=first['id'],expectedRevision=1)
    with get_db() as c:
        with pytest.raises(mail.MailError,match='draft_unavailable'):mail.recheck_binding(c,*ctx[:2],tracked['mail_binding'])

def test_dsn_requires_exact_message_and_recipient_and_is_not_authority(ctx,smtp):
    sent=dispatch(ctx,approved(ctx,draft(ctx)))
    def dsn(recipient,original):
        return (f'From: mailer-daemon@example.test\r\nMessage-ID: <dsn-{uid()}@example.test>\r\nMIME-Version: 1.0\r\nContent-Type: multipart/report; report-type=delivery-status; boundary="bound"\r\n\r\n--bound\r\nContent-Type: text/plain\r\n\r\nDelivery failed\r\n--bound\r\nContent-Type: message/delivery-status\r\n\r\nReporting-MTA: dns; example.test\r\n\r\nFinal-Recipient: rfc822; {recipient}\r\nAction: failed\r\nStatus: 5.1.1\r\n\r\n--bound\r\nContent-Type: text/rfc822-headers\r\n\r\nMessage-ID: {original}\r\n\r\n--bound--\r\n').encode()
    good=import_raw(ctx,dsn('sender@example.test',sent['message_id']))
    result=run(ctx,'bounce-analysis',submissionId=sent['operation_id'],dsnMessageId=good['id']);assert len(result['matched_reports'])==1
    assert not result['delivery_status_changed'] and not result['automatic_resend']
    wrong=import_raw(ctx,dsn('other@example.test',sent['message_id']))
    assert run(ctx,'bounce-analysis',submissionId=sent['operation_id'],dsnMessageId=wrong['id'])['matched_reports']==[]
    wrong=import_raw(ctx,dsn('sender@example.test','<other@example.test>'))
    assert run(ctx,'bounce-analysis',submissionId=sent['operation_id'],dsnMessageId=wrong['id'])['matched_reports']==[]
    run(ctx,'email-drafting',action='delete',draftId=result['mail_binding']['draft']['id'],expectedRevision=1)
    with get_db() as c:
        with pytest.raises(mail.MailError,match='draft_unavailable'):mail.recheck_binding(c,*ctx[:2],result['mail_binding'])

def test_status_lookup_does_not_dispatch_again(ctx,smtp):
    sent=dispatch(ctx,approved(ctx,draft(ctx)))
    current=run(ctx,'authorized-email-send',submissionId=sent['operation_id'])
    assert current['state']=='smtp_accepted' and len(smtp[0]['messages'])==1

def test_expired_grant_and_changed_transport_policy(ctx,smtp):
    first=draft(ctx);scope=approved(ctx,first)
    with get_db() as c:c.execute('UPDATE mail_send_grants SET expires=0')
    with pytest.raises(mail.MailError,match='authorization_invalid'):dispatch(ctx,scope)
    scope=approved(ctx,first);smtp[1].allowedRecipients=['hidden@example.test']
    with pytest.raises(mail.MailError,match='destination_changed'):dispatch(ctx,scope)
    assert smtp[0]['messages']==[]

def test_authored_artifact_sharing_needs_explicit_grant(ctx,smtp,client):
    from test_skill_batch03_files import create
    create(client,ctx[1]);file=client.get(f'/api/projects/{ctx[1]}/skill-files').json()[0]
    ref={k:file[k] for k in ('kind','id','version','sha256')}
    first=draft(ctx,attachments=[ref])
    with pytest.raises(mail.MailError,match='sharing_review'):approved(ctx,first)
    rid=uid();token=mail.approve_send(*ctx,first['id'],1,first['payload_sha256'],operation_id=rid,attachments_authorized=True)
    result=dispatch(ctx,(rid,token,request(ctx,'authorized-email-send',draftId=first['id'],expectedRevision=1,expectedPayloadSha256=first['payload_sha256'])))
    assert result['state']=='smtp_accepted'
    message=BytesParser(policy=policy.default).parsebytes(smtp[0]['messages'][0]);assert len(list(message.iter_attachments()))==1

def test_private_file_attachment_does_not_gain_mail_permission(ctx,smtp,client):
    # Explicit synthetic stored-original fixture. This is a policy test, not an extraction-engine claim.
    fid=uid();raw=b'PRIVATE_SYNTHETIC_MARKER';sha=hashlib.sha256(raw).hexdigest()
    with get_db() as c:
        c.execute('INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)',(fid,ctx[1],'private.txt',len(raw),sha,0,1,len(raw),now()))
        c.execute('INSERT INTO document_originals VALUES(?,?,?,?,?,?,?)',(fid,raw,'text/plain',1,'{}','{}','complete'))
        c.execute('UPDATE guard_file_policies SET destinations=? WHERE file_id=?',(json.dumps(['local','local:ollama']),fid))
    ref={'kind':'document','id':fid,'version':1,'sha256':sha}
    verified=run(ctx,'attachment-verification',attachments=[ref]);assert verified['attachments'][0]['current_authorized_bytes'] and not verified['attachments'][0]['external_sharing_authorized']
    first=draft(ctx,attachments=[ref])
    with pytest.raises(mail.MailError,match='egress_not_authorized'):mail.approve_send(*ctx,first['id'],1,first['payload_sha256'],operation_id=uid(),attachments_authorized=True)
    assert smtp[0]['messages']==[] and smtp[0]['recipients']==[]
    from test_skill_batch03_files import create
    produced=create(client,ctx[1]);file=client.get(f'/api/projects/{ctx[1]}/skill-files').json()[0]
    # Synthetic lineage binding over a real writer output; does not claim a model transformed the private text.
    with get_db() as c:
        row=c.execute('SELECT payload FROM skill_runs WHERE id=?',(produced['id'],)).fetchone();payload=json.loads(row['payload']);payload['inputs']['sourceRefs']=[ref]
        c.execute('UPDATE skill_runs SET payload=? WHERE id=?',(json.dumps(payload),produced['id']))
    derived=draft(ctx,attachments=[{k:file[k] for k in ('kind','id','version','sha256')}])
    with pytest.raises(mail.MailError,match='egress_not_authorized'):mail.approve_send(*ctx,derived['id'],1,derived['payload_sha256'],operation_id=uid(),attachments_authorized=True)
    assert smtp[0]['messages']==[]

def test_same_local_draft_operation_is_not_duplicated(ctx):
    rid=uid();req=request(ctx,'email-drafting',action='create',subject='Exact',body='Original')
    first=mail.run(req.operation,*ctx[:2],req,operation_id=rid,check=lambda:None)
    second=mail.run(req.operation,*ctx[:2],req,operation_id=rid,check=lambda:None)
    assert first['id']==second['id'] and second['reused_local_operation'] and len(mail.list_drafts(*ctx))==1
    with pytest.raises(mail.MailError,match='scope_changed'):mail.run(req.operation,*ctx[:2],req.model_copy(update={'body':'Changed'}),operation_id=rid,check=lambda:None)

def test_late_cancellation_does_not_erase_known_smtp_effect(ctx,smtp,monkeypatch):
    first=draft(ctx);rid,token,req=approved(ctx,first);stop=threading.Event();original=transport.submit
    def stop_after_acceptance(*args,**kwargs):
        receipt=original(*args,**kwargs);stop.set();return receipt
    monkeypatch.setattr(transport,'submit',stop_after_acceptance)
    with pytest.raises(mail.MailError,match='cancelled'):mail.run(req.operation,*ctx[:2],req,operation_id=rid,authority=token,check=lambda:None,stop=stop)
    with get_db() as c:
        effect=mail.operation_effect(c,*ctx[:2],rid);assert effect['state']=='smtp_accepted' and not effect['delivered']
    assert len(smtp[0]['messages'])==1

def test_actual_connection_socket_is_pinned_and_tls_uses_hostname(monkeypatch):
    calls=[];sock=object()
    monkeypatch.setattr(transport.socket,'create_connection',lambda target,timeout:(calls.append((target,timeout)) or sock))
    class Context:
        def wrap_socket(self,actual,server_hostname):
            assert actual is sock;calls.append(server_hostname);return 'wrapped'
    connection=object.__new__(transport.PinnedSMTPSSL);connection._pinned_ip='8.8.8.8';connection.context=Context()
    assert connection._get_socket('smtp.example.test',465,15)=='wrapped'
    assert calls==[(('8.8.8.8',465),15),'smtp.example.test']

def test_starttls_uses_verified_context_before_authentication(monkeypatch):
    calls=[]
    class Connection:
        def ehlo_or_helo_if_needed(self):calls.append('ehlo')
        def starttls(self,context):assert context.check_hostname and context.verify_mode==transport.ssl.CERT_REQUIRED;calls.append('starttls')
        def ehlo(self):calls.append('ehlo-after-tls')
    monkeypatch.setattr(transport,'PinnedSMTP',lambda *a:Connection())
    config=transport.SMTPConfig(enabled=True,host='smtp.example.test',port=587,tls='starttls',username='sender@example.test',fromAddress='sender@example.test')
    transport._connect(config,'8.8.8.8');assert calls==['ehlo','starttls','ehlo-after-tls']

def test_private_dns_is_blocked_before_secret_or_connection(monkeypatch):
    monkeypatch.setattr(transport.knowledge_fetch.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
    monkeypatch.setattr(transport.providers,'secret',lambda *a:pytest.fail('Secret must not be read'))
    monkeypatch.setattr(transport,'_connect',lambda *a:pytest.fail('Connection must not be made'))
    config=transport.SMTPConfig(enabled=True,host='smtp.example.test',username='owner@example.test',fromAddress='owner@example.test')
    result=transport.submit(config,'a'*32,'owner@example.test',['sender@example.test'],b'Original',lambda:None,threading.Event())
    assert result['state']=='failed' and not result['accepted_by_smtp']

def test_operator_configuration_requires_reviewed_endpoint_and_exact_addresses(ctx,monkeypatch,tmp_path):
    monkeypatch.setattr(transport,'data_dir',lambda:tmp_path)
    config={'version':1,'accounts':{ctx[2]:{'enabled':True,'host':'smtp.example.test','port':465,'tls':'implicit','username':'owner@example.test','fromAddress':'owner@example.test','allowedRecipients':['sender@example.test']}}}
    (tmp_path/'mail-accounts.json').write_text(json.dumps(config))
    assert transport.configuration(ctx[2]).host=='smtp.example.test'
    config['accounts'][ctx[2]]['password']='SYNTHETIC_FORBIDDEN_INLINE'
    (tmp_path/'mail-accounts.json').write_text(json.dumps(config))
    with pytest.raises(transport.TransportError) as error:transport.configuration(ctx[2])
    assert 'SYNTHETIC_FORBIDDEN_INLINE' not in str(error.value)

def test_no_hidden_authority_field_or_invented_followup_timezone(ctx):
    with pytest.raises(ValidationError):request(ctx,'authorized-email-send',authority='fake')
    with pytest.raises((ValidationError,mail.MailError)):request(ctx,'email-followup-tracking',dueAt='2030-01-01T12:00:00')

def test_productivity_derived_attachment_has_no_implicit_sharing_policy(ctx,smtp,client,monkeypatch):
    from test_skill_batch03_files import create
    from backend import skills
    output=create(client,ctx[1]);file=client.get(f'/api/projects/{ctx[1]}/skill-files').json()[0]
    with get_db() as c:
        row=c.execute('SELECT document FROM skill_runs WHERE id=?',(output['id'],)).fetchone();value=json.loads(row['document']);value['result']['productivity_bindings']=[{'kind':'mail','value':{'account_id':ctx[2],'account_revision':1,'messages':[]}}]
        c.execute('UPDATE skill_runs SET document=? WHERE id=?',(json.dumps(value),output['id']))
    first=draft(ctx,attachments=[{k:file[k] for k in ('kind','id','version','sha256')}])
    with pytest.raises(mail.MailError,match='no_external_sharing_policy'):mail.approve_send(*ctx,first['id'],1,first['payload_sha256'],operation_id=uid(),attachments_authorized=True)
    assert not smtp[0]['messages']

def test_missing_transport_has_safe_mail_error(ctx,monkeypatch):
    first=draft(ctx);monkeypatch.setattr(mail,'settings',lambda:{'mode':'hybrid'})
    with pytest.raises(mail.MailError,match='smtp_not_configured'):approved(ctx,first)
