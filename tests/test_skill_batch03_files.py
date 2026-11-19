"""Real local HTTP/SQLite versioning; no provider or document parser mocks."""
import hashlib
import json
import pytest
from fastapi import HTTPException
from backend import guard,skill_files
from backend.db import get_db
from test_app import client
from test_skills import pid,submit,wait


def test_restore_through_task_creates_new_version_and_keeps_history(client):
    p=pid(client);create(client,p);f=client.get(f'/api/projects/{p}/skill-files').json()[0]
    original=client.get(f['url']).content
    create(client,p,'Updated 150',key='restore-update',target={'artifactId':f['id'],'expectedVersion':1,'reason':'Edit'})
    restored=submit(client,p,'artifact-versioning',{'versionOperation':{'action':'restore','artifactId':f['id'],'version':1,'expectedVersion':2}},request='Restore file',key='restore-original')
    assert restored['status']=='completed',restored
    assert client.get(f['latest_url']).content==original
    history=client.get(f'/api/projects/{p}/skill-files/{f["id"]}/versions').json()
    assert [x['version'] for x in history['versions']]==[3,2,1]
    assert restored['result']['artifacts'][0]['version']==3


def test_folder_preview_and_apply_through_task(client):
    p=pid(client);create(client,p);f=client.get(f'/api/projects/{p}/skill-files').json()[0]
    moves=[{'artifactId':f['id'],'expectedRevision':1,'name':'ملف שלום.docx','folder':'تقارير'}]
    preview=submit(client,p,'folder-organization',{'folderOperation':{'moves':moves}},request='Organize files',key='folder-preview')
    assert preview['status']=='completed',preview
    assert client.get(f'/api/projects/{p}/skill-files').json()[0]['folder']==''
    plan=preview['result']['structured']['folder-organization']
    applied=submit(client,p,'folder-organization',{'folderOperation':{'action':'apply','moves':moves,'expectedPlanSha256':plan['plan_sha256']}},request='Apply reviewed organization',key='folder-apply')
    assert applied['status']=='completed',applied
    latest=client.get(f'/api/projects/{p}/skill-files').json()[0]
    assert latest['folder']=='تقارير' and latest['sha256']==f['sha256']


def test_failed_update_before_final_publication_keeps_original(client,monkeypatch):
    # Explicit failure injection after real artifact persistence; no parser/provider mock.
    from backend import skills
    p=pid(client);create(client,p);f=client.get(f'/api/projects/{p}/skill-files').json()[0]
    original=client.get(f['url']).content;progress=skills.progress
    def interrupt(rid,doc,artifacts=(),final=False,lease=None):
        progress(rid,doc,artifacts,final,lease)
        if artifacts and not final:
            with get_db() as c:assert c.execute('SELECT latest_version FROM skill_file_heads WHERE id=?',(f['id'],)).fetchone()[0]==1
            raise skills.SkillFailure('Injected interruption before final publication')
    monkeypatch.setattr(skills,'progress',interrupt)
    result=create(client,p,'Not published',key='interrupted-update',target={'artifactId':f['id'],'expectedVersion':1,'reason':'Interrupted'})
    assert result['status']=='failed'
    assert client.get(f['latest_url']).content==original


def test_mismatched_operation_rejected_before_worker(client,monkeypatch):
    from backend import skill_transform
    calls=[];monkeypatch.setattr(skill_transform,'execute_transform',lambda *a,**k:calls.append(1))
    p=pid(client)
    response=client.post(f'/api/projects/{p}/skill-runs',json={'request':'Profile data','requestedSkills':['data-profiling'],'inputs':{'dataOperation':{'operation':'stats','table':{'columns':['x'],'rows':[[0],[None],[10]]}}},'idempotencyKey':'mismatched-data'})
    assert response.status_code==422 and calls==[]


def create(client,p,text='Original 42',key='version-original',target=None):
    inputs={'text':text}
    if target:inputs['outputVersion']=target
    return submit(client,p,'docx-authoring',inputs,request='Create Word',key=key)


def principal(run):
    with get_db() as c:
        r=c.execute('SELECT owner_id,session_id FROM skill_runs WHERE id=?',(run['id'],)).fetchone()
        return guard.Actor(r['owner_id'],r['session_id'])


def test_version_identity_retains_old_bytes_and_refuses_stale_write(client):
    p=pid(client);first=create(client,p);assert first['status']=='completed',first
    files=client.get(f'/api/projects/{p}/skill-files').json();assert len(files)==1
    f=files[0];original=client.get(f['url']).content
    target={'artifactId':f['id'],'expectedVersion':1,'reason':'Explicit revised text'}
    second=create(client,p,'Revised 150',key='version-second',target=target)
    assert second['status']=='completed',second
    history=client.get(f'/api/projects/{p}/skill-files/{f["id"]}/versions').json()
    assert [x['version'] for x in history['versions']]==[2,1]
    assert client.get(f['url']).content==original
    newest=client.get(f['latest_url']);assert newest.status_code==200 and newest.content!=original
    assert newest.headers['x-riwaq-file-version']=='2'
    refused=client.post(f'/api/projects/{p}/skill-runs',json={'request':'Create Word','requestedSkills':['docx-authoring'],'inputs':{'text':'stale','outputVersion':target},'idempotencyKey':'version-stale'})
    assert refused.status_code==409


def test_file_versions_are_not_name_based_and_are_project_scoped(client):
    p=pid(client);first=create(client,p);create(client,p,'Other same filename',key='same-name-new')
    items=client.get(f'/api/projects/{p}/skill-files').json()
    assert len(items)==2 and len({x['id'] for x in items})==2 and len({x['name'] for x in items})==1
    other=client.post('/api/projects',json={'name':'Different project'}).json()['id']
    assert client.get(f'/api/projects/{other}/skill-files').json()==[]
    assert client.get(f'/api/projects/{other}/skill-files/{items[0]["id"]}/download').status_code==404


def test_organization_preview_collision_and_undo_preserve_bytes(client):
    p=pid(client);run=create(client,p);create(client,p,'Second',key='organization-second')
    who=principal(run);items=client.get(f'/api/projects/{p}/skill-files').json();a,b=items
    moves=[{'artifactId':a['id'],'expectedRevision':a['revision'],'name':'تقرير.docx','folder':'مشروع/تقارير'}]
    with get_db() as c:
        preview=skill_files.organize(c,who,p,{'moves':moves},'preview')
    assert preview['status']=='preview'
    assert client.get(f'/api/projects/{p}/skill-files').json()[0]['folder']==''
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE');done=skill_files.organize(c,who,p,{'action':'apply','moves':moves,'expectedPlanSha256':preview['plan_sha256']},'apply-one')
    assert done['physical_files_moved'] is False and done['sharing_changed'] is False
    moved=next(x for x in client.get(f'/api/projects/{p}/skill-files').json() if x['id']==a['id'])
    assert moved['folder']=='مشروع/تقارير' and moved['sha256']==a['sha256']
    with get_db() as c:
        with pytest.raises(HTTPException) as e:
            skill_files.organize(c,who,p,{'moves':[{'artifactId':b['id'],'expectedRevision':1,'name':'تقرير.docx','folder':'مشروع/تقارير'}]},'collision')
        assert e.value.status_code==409
    # Restoring into an already occupied name must refuse instead of overwriting.
    with get_db() as c:
        with pytest.raises(HTTPException):skill_files.organize(c,who,p,{'action':'undo','operationId':done['operation_id']},'undo-collision')
    with get_db() as c:
        preview2=skill_files.organize(c,who,p,{'moves':[{'artifactId':b['id'],'expectedRevision':1,'name':'other.docx'}]},'preview-b')
        skill_files.organize(c,who,p,{'action':'apply','moves':[{'artifactId':b['id'],'expectedRevision':1,'name':'other.docx'}],'expectedPlanSha256':preview2['plan_sha256']},'move-b')
        undo=skill_files.organize(c,who,p,{'action':'undo','operationId':done['operation_id']},'undo-one')
    assert undo['status']=='completed'
    assert hashlib.sha256(client.get(a['url']).content).hexdigest()==a['sha256']


@pytest.mark.parametrize('name,folder',[('../escape.docx',''),('x.docx','../outside'),('CON.docx',''),('x.docx','C:/private'),('x.docx','a\\b')])
def test_organization_rejects_path_like_escape_before_effect(name,folder):
    with pytest.raises(ValueError):skill_files.FolderMove(artifactId='file',expectedRevision=1,name=name,folder=folder)


def test_source_change_blocks_active_task_but_explicit_fixed_version_reads(client):
    p=pid(client);run=create(client,p);who=principal(run)
    f=client.get(f'/api/projects/{p}/skill-files').json()[0]
    ref={'kind':'artifact','id':f['id'],'version':1,'sha256':f['sha256']}
    create(client,p,'Different 64',key='source-new',target={'artifactId':f['id'],'expectedVersion':1,'reason':'new version'})
    with get_db() as c:
        with pytest.raises(HTTPException) as e:skill_files.resolve(c,who,p,ref,active=True)
        assert e.value.status_code==409
        assert skill_files.resolve(c,who,p,ref|{'pinned':True},active=True)['sha256']==f['sha256']


def test_deleted_output_identity_does_not_resurrect_previous_version(client):
    p=pid(client);first=create(client,p);f=client.get(f'/api/projects/{p}/skill-files').json()[0]
    second=create(client,p,'second',key='delete-current',target={'artifactId':f['id'],'expectedVersion':1,'reason':'new'})
    assert client.delete(f'/api/projects/{p}/skill-runs/{second["id"]}').status_code in (200,204)
    assert client.get(f['latest_url']).status_code==404
    assert client.get(f['url']).status_code==404
    assert client.get(f'/api/projects/{p}/skill-files').json()==[]
