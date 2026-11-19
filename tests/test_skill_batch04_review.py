"""Independent holdout API cases: synthetic SQLite data, no external mail/calendar.

Assertions were written before changing the reviewed implementation. A seeded
legacy text extraction is fixture setup, not a claimed parser/provider test.
"""
import base64
import hashlib
import json
import threading
import time

import pytest

from backend.db import get_db, now, uid
from test_app import client
from test_skills import pid, submit
from test_skill_batch04_integration import notes, scheduling


def _draft(client, p, action, key, **fields):
    return submit(client, p, 'email-drafting', {'mailOperation': {
        'operation': 'email-drafting', 'action': action, **fields,
    }}, request='Execute the reviewed local draft operation', key=key)


def _account(client, p):
    response = client.post(f'/api/projects/{p}/productivity/mail/accounts', json={
        'label': 'Holdout synthetic mailbox', 'address': 'author@example.test'})
    assert response.status_code == 200, response.text
    return response.json()['id']


def _seed_text(p, text):
    fid = uid()
    with get_db() as c:
        c.execute('INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)', (
            fid, p, 'holdout-notes.txt', len(text.encode()), hashlib.sha256(text.encode()).hexdigest(),
            1, 0, len(text), now()))
        c.execute('INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?)', (
            uid(), fid, p, None, 1, len(text.splitlines()), text))
    return fid


def test_deleted_draft_is_not_exposed_by_prior_draft_listing_run(client):
    p = pid(client); aid = _account(client, p)
    created = _draft(client, p, 'create', 'holdout-draft-create', accountId=aid,
        subject='SYNTHETIC PRIVATE SUBJECT 729', body='تفصيل שלום', to=[{'email':'reader@example.test'}])
    assert created['status'] == 'completed', created
    draft = created['result']['structured']['email-drafting']
    listing = _draft(client, p, 'list', 'holdout-draft-list', accountId=aid)
    assert listing['status'] == 'completed', listing
    assert listing['result']['structured']['email-drafting']['drafts'][0]['subject'] == 'SYNTHETIC PRIVATE SUBJECT 729'
    deletion = _draft(client, p, 'delete', 'holdout-draft-delete', accountId=aid,
        draftId=draft['id'], expectedRevision=draft['revision'])
    assert deletion['status'] == 'completed', deletion
    response = client.get(f'/api/projects/{p}/skill-runs/{listing["id"]}')
    assert response.status_code in {403, 404, 409}, 'A deleted subject remains in a stored draft-list result'


def test_meeting_update_inherits_new_document_policy_before_source_delivery(client):
    p = pid(client)
    saved = submit(client, p, 'meeting-actions', {'meetingOperation': notes('actions', 'save',
        text='[a1] Action: ordinary original | owner: Sam')}, key='holdout-source-original', request='Save reviewed notes')
    sid = saved['result']['structured']['meeting-actions']['source']['id']
    private = '[a1] Action: SYNTHETIC PRIVATE UPDATED 817 | owner: Sam'
    fid = _seed_text(p, private)
    updated = submit(client, p, 'meeting-actions', {'documentIds':[fid], 'meetingOperation': notes('actions', 'save',
        text='', sourceId=sid, expectedVersion=1)}, key='holdout-source-update', request='Save reviewed notes from the selected file')
    assert updated['status'] in {'completed', 'partial'}, updated
    before = client.get(f'/api/projects/{p}/productivity/meeting-sources/{sid}')
    assert before.status_code == 200 and private in before.text
    with get_db() as c:
        c.execute('UPDATE guard_file_policies SET revoked=1,revision=revision+1 WHERE file_id=?', (fid,))
    response = client.get(f'/api/projects/{p}/productivity/meeting-sources/{sid}')
    assert response.status_code in {403, 404, 409}, 'Updated meeting content lost its new source policy'
    tasks = client.get(f'/api/projects/{p}/productivity/tasks')
    assert 'SYNTHETIC PRIVATE UPDATED 817' not in tasks.text


def test_cross_project_meeting_task_and_draft_cannot_be_read_or_mutated(client):
    p = pid(client); other = client.post('/api/projects', json={'name':'Holdout other project'}).json()['id']
    saved = submit(client, p, 'meeting-actions', {'meetingOperation': notes('actions','save',
        text='[t1] Action: scoped task | owner: Alex')}, key='holdout-scoped-task', request='Save reviewed notes')
    result = saved['result']['structured']['meeting-actions']; sid = result['source']['id']; task = result['tasks'][0]
    assert client.get(f'/api/projects/{other}/productivity/meeting-sources/{sid}').status_code in {403,404}
    assert client.delete(f'/api/projects/{other}/productivity/meeting-sources/{sid}?expectedVersion=1').status_code in {403,404}
    assert client.patch(f'/api/projects/{other}/productivity/tasks/{task["id"]}', json={'expectedRevision':1,'state':'completed'}).status_code in {403,404}
    actual = client.get(f'/api/projects/{p}/productivity/tasks').json()
    assert actual[0]['state'] == 'open' and actual[0]['revision'] == 1
    aid = _account(client,p)
    draft = _draft(client,p,'create','holdout-scoped-draft',accountId=aid,subject='Scoped',body='Private',to=[{'email':'reader@example.test'}])['result']['structured']['email-drafting']
    assert client.get(f'/api/projects/{other}/productivity/mail/accounts/{aid}/drafts/{draft["id"]}').status_code in {403,404,422}


def test_source_edit_preserves_completed_task_and_marks_reassignment_for_review(client):
    p=pid(client)
    original='[a1] Action: done work | owner: Sam\n[a2] Action: pending work | owner: Lee'
    saved=submit(client,p,'meeting-actions',{'meetingOperation':notes('actions','save',text=original)},key='holdout-done-start',request='Save reviewed notes')
    result=saved['result']['structured']['meeting-actions']; sid=result['source']['id']
    first=next(t for t in result['tasks'] if t['record_id']=='a1')
    assert client.patch(f'/api/projects/{p}/productivity/tasks/{first["id"]}',json={'expectedRevision':1,'state':'completed'}).status_code==200
    updated=submit(client,p,'meeting-actions',{'meetingOperation':notes('actions','save',sourceId=sid,expectedVersion=1,
        text='[a1] Action: changed completed work | owner: Sam\n[a2] Action: pending work | owner: New')},key='holdout-done-update',request='Save reviewed source update')
    out=updated['result']['structured']['meeting-actions']
    assert {'completed_or_cancelled_task_preserved','reassignment_needs_task_review'} <= {x['reason'] for x in out['issues']}
    tasks=client.get(f'/api/projects/{p}/productivity/tasks').json()
    completed=next(t for t in tasks if t['record_id']=='a1');pending=next(t for t in tasks if t['record_id']=='a2')
    assert completed['state']=='completed' and completed['title']=='done work' and completed['source_changed']
    assert pending['assignee']=='Lee' and pending['source_changed']
    assert client.delete(f'/api/projects/{p}/productivity/meeting-sources/{sid}?expectedVersion=1').status_code==409
    assert client.delete(f'/api/projects/{p}/productivity/meeting-sources/{sid}?expectedVersion=2').status_code==200
    assert client.get(f'/api/projects/{p}/productivity/tasks').json()==[]


def test_deleted_plan_invalidates_saved_read_result_and_cross_project_get(client):
    p=pid(client)
    saved=scheduling(client,p,'plan.save',{'name':'Synthetic private plan','body':{'details':'SYNTHETIC PLAN 123'}},'holdout-plan-save','daily-planning')
    assert saved['status']=='completed',saved
    plan=saved['result']['structured']['daily-planning']
    read=scheduling(client,p,'plan.get',{'planId':plan['id']},'holdout-plan-get','daily-planning')
    assert read['status']=='completed',read
    other=client.post('/api/projects',json={'name':'Other plan'}).json()['id']
    assert client.post(f'/api/projects/{other}/productivity/schedule',json={'operation':'plan.get','options':{'planId':plan['id']}}).status_code in {403,404}
    deleted=scheduling(client,p,'plan.delete',{'planId':plan['id'],'expectedVersion':plan['version']},'holdout-plan-delete','daily-planning')
    assert deleted['status']=='completed',deleted
    assert client.get(f'/api/projects/{p}/skill-runs/{read["id"]}').status_code in {403,404,409}


@pytest.mark.parametrize('action',['create','update'])
def test_cancellation_discloses_already_committed_draft_effect(client,monkeypatch,action):
    """Inject only a post-commit pause; the mutation and cancellation are real APIs."""
    from backend import productivity_mail
    p=pid(client);aid=_account(client,p)
    fields={'accountId':aid,'subject':'Changed synthetic draft','body':'PRIVATE BODY NOT FOR RECEIPT',
            'to':[{'email':'reader@example.test'}]}
    if action=='update':
        initial=_draft(client,p,'create','holdout-before-cancel',accountId=aid,subject='Original',body='Original',to=[{'email':'reader@example.test'}])
        prior=initial['result']['structured']['email-drafting']
        fields.update(draftId=prior['id'],expectedRevision=prior['revision'])
    committed=threading.Event();release=threading.Event();result={}
    original=productivity_mail.save_draft
    def paused_save(*args,**kwargs):
        value=original(*args,**kwargs);result.update(value);committed.set()
        assert release.wait(5),'Fixture pause was not released'
        return value
    monkeypatch.setattr(productivity_mail,'save_draft',paused_save)
    queued=client.post(f'/api/projects/{p}/skill-runs',json={'request':'Apply the reviewed local draft change',
        'requestedSkills':['email-drafting'],'inputs':{'mailOperation':{'operation':'email-drafting','action':action,**fields}},
        'idempotencyKey':'holdout-cancel-'+action})
    assert queued.status_code==202,queued.text
    rid=queued.json()['id']
    try:
        assert committed.wait(3),'The real local commit did not occur'
        response=client.post(f'/api/projects/{p}/skill-runs/{rid}/cancel')
        assert response.status_code==200,response.text
    finally:release.set()
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        response=client.get(f'/api/projects/{p}/skill-runs/{rid}')
        assert response.status_code==200,response.text
        final=response.json()
        if final['status']=='cancelled':break
        time.sleep(.02)
    assert final['status']=='cancelled',final
    current=client.get(f'/api/projects/{p}/productivity/mail/accounts/{aid}/drafts/{result["id"]}')
    assert current.status_code==200 and current.json()['revision']==result['revision']
    effects=final['result'].get('effects',[])
    assert effects,'Cancellation concealed a committed draft mutation; disclose the surviving local effect'
    assert 'PRIVATE BODY NOT FOR RECEIPT' not in json.dumps(effects)


def test_direct_draft_list_withholds_reply_metadata_after_source_message_deletion(client):
    """All setup/import/draft/deletion/read actions use the public local APIs."""
    p=pid(client);aid=_account(client,p)
    raw=('From: reader@example.test\r\nTo: author@example.test\r\nSubject: Source 927\r\n'
         'Message-ID: <holdout-927@example.test>\r\nMIME-Version: 1.0\r\n'
         'Content-Type: text/plain; charset=utf-8\r\n\r\nSYNTHETIC SOURCE BODY').encode()
    imported=client.post(f'/api/projects/{p}/productivity/mail/import',json={'accountId':aid,'rawBase64':base64.b64encode(raw).decode()})
    assert imported.status_code==200,imported.text
    mid=imported.json()['id']
    created=_draft(client,p,'create','holdout-reply-source',accountId=aid,replyToMessageId=mid,
        subject='SYNTHETIC REPLY METADATA 927',body='Reply content',to=[{'email':'reader@example.test'}])
    assert created['status']=='completed',created
    draft=created['result']['structured']['email-drafting']
    assert client.get(f'/api/projects/{p}/productivity/mail/accounts/{aid}/drafts').status_code==200
    deletion=client.delete(f'/api/projects/{p}/productivity/mail/accounts/{aid}/messages/{mid}?expectedRevision=1')
    assert deletion.status_code==200,deletion.text
    direct=client.get(f'/api/projects/{p}/productivity/mail/accounts/{aid}/drafts/{draft["id"]}')
    listing=client.get(f'/api/projects/{p}/productivity/mail/accounts/{aid}/drafts')
    assert direct.status_code in {403,404,409,422}
    assert listing.status_code in {403,404,409,422} or listing.json()==[]
    assert 'SYNTHETIC REPLY METADATA 927' not in listing.text


def test_revoked_draft_does_not_hide_an_independent_valid_draft(client):
    p=pid(client);aid=_account(client,p)
    raw=('From: reader@example.test\r\nTo: author@example.test\r\nSubject: Scoped source\r\nMessage-ID: <independent-list@example.test>\r\n\r\nSynthetic source').encode()
    imported=client.post(f'/api/projects/{p}/productivity/mail/import',json={'accountId':aid,'rawBase64':base64.b64encode(raw).decode()}).json()
    _draft(client,p,'create','independent-stale',accountId=aid,replyToMessageId=imported['id'],subject='Hidden obsolete source',body='Source-derived',to=[{'email':'reader@example.test'}])
    valid=_draft(client,p,'create','independent-valid',accountId=aid,subject='Available independent draft',body='Confirmed direct input',to=[{'email':'reader@example.test'}])['result']['structured']['email-drafting']
    assert client.delete(f'/api/projects/{p}/productivity/mail/accounts/{aid}/messages/{imported["id"]}?expectedRevision=1').status_code==200
    listing=client.get(f'/api/projects/{p}/productivity/mail/accounts/{aid}/drafts')
    assert listing.status_code==200,listing.text
    assert [item['id'] for item in listing.json()]==[valid['id']]
    assert 'Hidden obsolete source' not in listing.text
