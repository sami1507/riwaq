"""Actual local skill API/SQLite with ordered synthetic files; model use labelled."""
import json
from io import BytesIO
from zipfile import ZipFile
import pytest
from tests.test_skills import client,pid,wait
from tests.test_skill_batch02_content import upload
from backend import skills,skill_content,providers
from backend.db import get_db


def files(client):
    p=pid(client)
    a=upload(client,p,'First project budget is 111 units. First project is cancelled.','A.txt')
    b=upload(client,p,'Second project budget is 222 units. Second project is approved.','B.txt')
    return p,a,b


def body(a,b,key='binding-second-001',request='Summarize the second file',**inputs):
    return {'request':request,'idempotencyKey':key,'inputs':{'documentIds':[a,b],**inputs}}


def run(client,p,payload):
    response=client.post(f'/api/projects/{p}/skill-runs',json=payload)
    assert response.status_code==202,response.text
    return wait(client,p,response.json()['id'])


def test_second_file_summary_consumes_only_bound_file_and_preserves_original_request(client):
    p,a,b=files(client);payload=body(a,b);result=run(client,p,payload)
    assert '222 units' in result['result']['text'] and '111 units' not in result['result']['text']
    assert {r['file_id'] for r in result['result']['source_refs']}=={b}
    assert result['result']['request_record']['resolved_resource']['file_id']==b
    with get_db() as c:
        row=c.execute('SELECT payload,resources FROM skill_runs WHERE id=?',(result['id'],)).fetchone()
        assert json.loads(row['payload'])['inputs']['documentIds']==[a,b]
        assert [r['file_id'] for r in json.loads(row['resources'])]==[b]
    replay=client.post(f'/api/projects/{p}/skill-runs',json=payload)
    assert replay.status_code==202 and replay.json()['id']==result['id']
    assert len(client.get(f'/api/projects/{p}/skill-runs').json())==1


def test_second_file_summary_export_contains_only_selected_numbers(client):
    p,a,b=files(client)
    result=run(client,p,body(a,b,'binding-export-001','Summarize the second file and create Word'))
    artifact=result['result']['artifacts'][0]
    response=client.get(f"/api/projects/{p}/skill-runs/{result['id']}/artifacts/{artifact['name']}")
    assert response.status_code==200
    with ZipFile(BytesIO(response.content)) as package:xml=package.read('word/document.xml').decode()
    assert '222 units' in xml and '111 units' not in xml


def test_selected_file_binding_does_not_depend_on_unselected_file_revision_or_acl(client):
    p,a,b=files(client);payload=body(a,b,'binding-unselected-001');result=run(client,p,payload)
    with get_db() as c:
        c.execute('UPDATE knowledge_files SET sha256=? WHERE id=?',('f'*64,a))
        c.execute('UPDATE guard_file_policies SET revoked=1 WHERE file_id=?',(a,))
    assert client.get(f"/api/projects/{p}/skill-runs/{result['id']}").status_code==200
    replay=client.post(f'/api/projects/{p}/skill-runs',json=payload)
    assert replay.status_code==202 and replay.json()['id']==result['id']


@pytest.mark.parametrize('change',['revision','revoke'])
def test_selected_file_change_or_revocation_withholds_readback_and_replay(client,change):
    p,a,b=files(client);payload=body(a,b,'binding-selected-001');result=run(client,p,payload)
    with get_db() as c:
        if change=='revision':c.execute('UPDATE knowledge_files SET sha256=? WHERE id=?',('e'*64,b))
        else:c.execute('UPDATE guard_file_policies SET revoked=1 WHERE file_id=?',(b,))
    assert client.get(f"/api/projects/{p}/skill-runs/{result['id']}").status_code in {403,409}
    assert client.post(f'/api/projects/{p}/skill-runs',json=payload).status_code in {403,409}


def test_referential_single_target_cannot_silently_narrow_multisource_operation(client):
    p,a,b=files(client);payload=body(a,b,'binding-multi-001','Synthesize documents from the second file')
    payload['requestedSkills']=['cross-document-synthesis']
    response=client.post(f'/api/projects/{p}/skill-runs',json=payload)
    assert response.status_code==422 and response.json()['detail']['code']=='request_clarification_required'
    assert client.get(f'/api/projects/{p}/skill-runs').json()==[]


def test_explicit_artifact_payload_cannot_override_resolved_source(client):
    p,a,b=files(client)
    payload=body(a,b,'binding-payload-001','Create Word from the second file',artifact={'kind':'docx','payload':{'title':'Injected unrelated output','paragraphs':[{'text':'First project budget is 111 units.'}]}})
    response=client.post(f'/api/projects/{p}/skill-runs',json=payload)
    assert response.status_code==422 and response.json()['detail']['code']=='request_clarification_required'
    assert client.get(f'/api/projects/{p}/skill-runs').json()==[]


def test_translation_export_passes_only_second_file_to_mock_provider(client,monkeypatch):
    p,a,b=files(client);seen=[]
    async def validate(*args,**kwargs):pass
    async def model(provider,model,messages,cfg):
        data=json.loads(messages[-1]['content']);seen.append(data['text'])
        yield {'content':json.dumps({'translation':data['text']})}
        yield {'finish_reason':'stop'}
    monkeypatch.setattr(providers,'validate_provider',validate)
    monkeypatch.setattr(providers,'stream',model)
    result=run(client,p,body(a,b,'binding-translation-001','Translate the second file and create Word',targetLanguage='English'))
    assert seen and all('First project' not in x and 'Second project' in x for x in seen)
    assert '222 units' in result['result']['text'] and '111 units' not in result['result']['text']
    assert result['result']['artifacts']


def test_second_file_scope_survives_explicit_checkpoint_resume(client,monkeypatch):
    from tests.test_skill_batch05_execution import expire
    p,a,b=files(client);original=skill_content.extractive_summary;progress=skills.progress;seen=[];expired=[]
    def capture(bundle,**kwargs):
        seen.append(bundle['text'])
        return original(bundle,**kwargs)
    def checkpoint_then_expire(rid,doc,*args,**kwargs):
        result=progress(rid,doc,*args,**kwargs)
        # Expire after a committed read, before dispatching the next step. An
        # already running summary is intentionally not an automatic replay.
        if not expired and any(step['skill']=='pdf-reading' and step['status']=='completed' for step in doc['steps']):
            expired.append(rid);expire(rid)
        return result
    monkeypatch.setattr(skill_content,'extractive_summary',capture)
    monkeypatch.setattr(skills,'progress',checkpoint_then_expire)
    payload=body(a,b,'binding-resume-001')
    first=run(client,p,payload)
    assert first['status']=='unknown' and first['result']['checkpoint']['resumable']
    assert not seen
    response=client.post(f"/api/projects/{p}/skill-runs/{first['id']}/resume",json={'expected_revision':first['result']['checkpoint']['revision']})
    assert response.status_code==202,response.text
    resumed=wait(client,p,first['id'])
    assert resumed['status']=='completed'
    assert len(seen)==1 and '222 units' in seen[0] and '111 units' not in seen[0]
    assert {r['file_id'] for r in resumed['result']['source_refs']}=={b}
    assert '222 units' in resumed['result']['text'] and '111 units' not in resumed['result']['text']


@pytest.mark.parametrize('change',['revision','revoke'])
def test_selected_file_mutation_during_summary_blocks_publication(client,monkeypatch,change):
    p,a,b=files(client);original=skill_content.extractive_summary
    def changed(bundle,**kwargs):
        result=original(bundle,**kwargs)
        with get_db() as c:
            if change=='revision':c.execute('UPDATE knowledge_files SET sha256=? WHERE id=?',('d'*64,b))
            else:c.execute('UPDATE guard_file_policies SET revoked=1 WHERE file_id=?',(b,))
        return result
    monkeypatch.setattr(skill_content,'extractive_summary',changed)
    response=client.post(f'/api/projects/{p}/skill-runs',json=body(a,b,'binding-race-001'))
    assert response.status_code==202,response.text
    rid=response.json()['id']
    import time
    for _ in range(100):
        with get_db() as c:row=c.execute('SELECT state,document FROM skill_runs WHERE id=?',(rid,)).fetchone()
        if row['state'] not in ('queued','running'):break
        time.sleep(.02)
    assert row['state']=='failed'
    assert not json.loads(row['document'])['result'].get('summary')
    assert client.get(f'/api/projects/{p}/skill-runs/{rid}').status_code in {403,409}


def test_preview_and_execution_share_all_requested_output_metadata(client):
    p=pid(client)
    payload={'request':'حلل هذا الجدول، واكتب ملخصًا، وأنشئ رسمًا وملف نتائج','idempotencyKey':'binding-output-metadata-001'}
    preview=client.post(f'/api/projects/{p}/skill-request-preview',json=payload)
    assert preview.status_code==200
    result=run(client,p,payload)
    assert set(preview.json()['intent']['requestedOutputs'])=={'analysis','summary','chart','file'}
    assert result['intent']['requestedOutputs']==preview.json()['intent']['requestedOutputs']
    assert result['intent']['requestedOutputs']==result['result']['request_record']['deliverables']
