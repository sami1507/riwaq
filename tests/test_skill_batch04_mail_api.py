"""Authenticated API and task engine, synthetic mail, actual owned SMTP.

The SMTP connection/DNS/keyring seam targets only the loopback fixture defined
in test_skill_batch04_mail. This is not a live provider/TLS integration claim.
"""
import base64
import json
import threading
import time
import pytest
from backend import guard,productivity_mail as mail,productivity_mail_transport as transport
from backend.db import get_db,uid
from test_app import client
from test_skill_batch04_mail import smtp

@pytest.fixture
def ctx(client):
    pid=client.get('/api/projects').json()[0]['id']
    result=client.post(f'/api/projects/{pid}/productivity/mail/accounts',json={'label':'Owned API fixture','address':'owner@example.test'})
    assert result.status_code==200,result.text
    with get_db() as c:
        row=c.execute('SELECT owner_id,id FROM local_auth_sessions WHERE revoked_at IS NULL').fetchone()
    return guard.Actor(row['owner_id'],row['id']),pid,result.json()['id']

def root(ctx):return f'/api/projects/{ctx[1]}'

def wait(client,ctx,rid,timeout=15):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        response=client.get(root(ctx)+'/skill-runs/'+rid)
        assert response.status_code==200,response.text
        value=response.json()
        if value['status'] not in {'queued','running','cancelling'}:return value
        time.sleep(.02)
    raise AssertionError('Owned API task did not settle')

def task(client,ctx,operation,**values):
    body={'request':'Execute the explicit synthetic mail operation','requestedSkills':[operation],
      'inputs':{'mailOperation':{'operation':operation,'accountId':ctx[2],**values}},'idempotencyKey':uid()}
    response=client.post(root(ctx)+'/skill-runs',json=body);assert response.status_code==202,response.text
    return wait(client,ctx,response.json()['id'])

def draft(client,ctx,**values):
    result=task(client,ctx,'email-drafting',action='create',subject='API synthetic',body='Original 42.50 USD. رواق שלום',to=[{'email':'sender@example.test'}],**values)
    assert result['status']=='completed',result
    return result['result']['structured']['email-drafting']

def send_input(ctx,draft,key=None,attachments=False):
    return {'accountId':ctx[2],'draftId':draft['id'],'expectedRevision':draft['revision'],
      'expectedPayloadSha256':draft['payload_sha256'],'idempotencyKey':key or uid(),'attachmentsAuthorized':attachments}

def test_owned_smtp_api_lost_reply_idempotency_and_no_grant_in_records(ctx,client,smtp,monkeypatch):
    smtp[0]['drop_data']=True;value=draft(client,ctx);tokens=[];original=mail.approve_send
    def observed(*a,**k):
        token=original(*a,**k);tokens.append(token);return token
    monkeypatch.setattr(mail,'approve_send',observed)
    body=send_input(ctx,value);first=client.post(root(ctx)+'/productivity/mail/send',json=body)
    assert first.status_code==202,first.text
    final=wait(client,ctx,first.json()['id']);assert final['status']=='unknown'
    repeated=client.post(root(ctx)+'/productivity/mail/send',json=body)
    assert repeated.status_code==202 and repeated.json()['id']==first.json()['id']
    assert len(smtp[0]['messages'])==1 and len(tokens)==1
    with get_db() as c:
        stored=[dict(row) for row in c.execute('SELECT * FROM skill_runs WHERE id=?',(final['id'],))]
        checkpoint=[dict(row) for row in c.execute('SELECT * FROM skill_checkpoints WHERE run_id=?',(final['id'],))]
    visible=json.dumps([stored,checkpoint,first.json(),final,repeated.json()],default=str)
    if tokens[0] in visible:raise AssertionError('A private server authorization grant leaked into task data')
    generic=client.post(root(ctx)+'/skill-runs',json={'request':'Send email','requestedSkills':['authorized-email-send'],'inputs':{'mailOperation':{'operation':'authorized-email-send',**{k:v for k,v in body.items() if k not in {'idempotencyKey','attachmentsAuthorized'}}}},'idempotencyKey':uid()})
    assert generic.status_code==403 and len(smtp[0]['messages'])==1

def paused_mail(monkeypatch):
    entered=threading.Event();release=threading.Event();original=mail.run
    def paused(*args,**kwargs):
        if args[0]=='authorized-email-send':entered.set();assert release.wait(5),'Test pause exceeded bound'
        return original(*args,**kwargs)
    monkeypatch.setattr(mail,'run',paused)
    return entered,release

def test_contact_change_after_grant_before_dispatch_is_denied(ctx,client,smtp,monkeypatch):
    contact=client.post(root(ctx)+f'/productivity/mail/accounts/{ctx[2]}/contacts',json={'displayName':'Known contact','email':'sender@example.test'}).json()
    created=task(client,ctx,'email-drafting',action='create',body='Literal body',to=[{'contactId':contact['id']}]);value=created['result']['structured']['email-drafting']
    entered,release=paused_mail(monkeypatch)
    response=client.post(root(ctx)+'/productivity/mail/send',json=send_input(ctx,value));assert response.status_code==202,response.text
    try:
        assert entered.wait(5)
        with get_db() as c:c.execute('UPDATE mail_contacts SET address=?,revision=revision+1 WHERE id=?',('hidden@example.test',contact['id']))
    finally:release.set()
    final=wait(client,ctx,response.json()['id']);assert final['status']=='failed',final
    assert smtp[0]['messages']==[] and smtp[0]['recipients']==[]

def test_attachment_deleted_after_grant_cannot_be_dispatched(ctx,client,smtp,monkeypatch):
    from test_skill_batch03_files import create
    produced=create(client,ctx[1]);file=client.get(root(ctx)+'/skill-files').json()[0]
    value=draft(client,ctx,attachments=[{k:file[k] for k in ('kind','id','version','sha256')}]);entered,release=paused_mail(monkeypatch)
    response=client.post(root(ctx)+'/productivity/mail/send',json=send_input(ctx,value,attachments=True));assert response.status_code==202,response.text
    try:
        assert entered.wait(5)
        removed=client.delete(root(ctx)+'/skill-runs/'+produced['id']);assert removed.status_code==200,removed.text
    finally:release.set()
    final=wait(client,ctx,response.json()['id']);assert final['status']=='failed',final
    assert smtp[0]['messages']==[]

def test_cancel_after_actual_smtp_acceptance_preserves_known_effect(ctx,client,smtp,monkeypatch):
    value=draft(client,ctx);accepted=threading.Event();original=transport.submit
    def paused(*args,**kwargs):
        receipt=original(*args,**kwargs);accepted.set();args[-1].wait(5);return receipt
    monkeypatch.setattr(transport,'submit',paused)
    response=client.post(root(ctx)+'/productivity/mail/send',json=send_input(ctx,value));assert response.status_code==202,response.text
    rid=response.json()['id'];assert accepted.wait(5)
    cancel=client.post(root(ctx)+f'/skill-runs/{rid}/cancel');assert cancel.status_code==200,cancel.text
    final=wait(client,ctx,rid);assert final['status']=='cancelled'
    effects=final['result']['effects'];assert any(x.get('kind')=='mail_submission' and x.get('state')=='smtp_accepted' for x in effects),effects
    assert all(not x.get('delivered') for x in effects) and len(smtp[0]['messages'])==1

def imported(client,ctx):
    raw=b'From: sender@example.test\r\nTo: owner@example.test\r\nMessage-ID: <source@example.test>\r\nSubject: Evidence\r\n\r\nACTION [A1]: Check exact 42.50 USD.'
    response=client.post(root(ctx)+'/productivity/mail/import',json={'accountId':ctx[2],'rawBase64':base64.b64encode(raw).decode()})
    assert response.status_code==200,response.text;return response.json()

def test_message_revocation_withholds_summary_derived_file_and_ancestry(ctx,client):
    source=imported(client,ctx);summary=task(client,ctx,'email-thread-summary',messageIds=[source['id']]);assert summary['status']=='partial',summary
    response=client.post(root(ctx)+'/skill-runs',json={'request':'Create Word from approved imported-mail evidence','requestedSkills':['docx-authoring'],
        'inputs':{'text':'Explicit reviewed synthetic mail note.'},'previousRunId':summary['id'],'idempotencyKey':uid()})
    assert response.status_code==202,response.text
    derived=wait(client,ctx,response.json()['id']);assert derived['status'] in {'completed','partial'},derived
    artifact=derived['result']['artifacts'][0]
    download=root(ctx)+f'/skill-runs/{derived["id"]}/artifacts/{artifact["name"]}'
    assert client.get(download).status_code==200
    mail.delete_message(*ctx,source['id'],1)
    for url in [root(ctx)+'/skill-runs/'+summary['id'],root(ctx)+'/skill-runs/'+derived['id'],download]:assert client.get(url).status_code in {403,404,409},url

def test_account_revocation_and_cross_project_access(ctx,client):
    source=imported(client,ctx);summary=task(client,ctx,'email-thread-summary',messageIds=[source['id']])
    other=client.post('/api/projects',json={'name':'Other API project'}).json()['id']
    assert client.get(f'/api/projects/{other}/productivity/mail/accounts/{ctx[2]}/drafts').status_code in {403,404,422}
    with get_db() as c:c.execute('UPDATE mail_accounts SET active=0,revision=revision+1 WHERE id=?',(ctx[2],))
    assert client.get(root(ctx)+'/skill-runs/'+summary['id']).status_code in {403,404,409}
