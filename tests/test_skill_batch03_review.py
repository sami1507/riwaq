"""Independent synthetic HTTP regressions for Batch03 derived-source lifetimes."""
from test_app import client
from test_skills import pid, submit, wait
import pytest


@pytest.mark.parametrize('slug',['artifact-versioning','folder-organization'])
def test_deleted_file_withholds_saved_history_or_organization_metadata(client,slug):
    p=pid(client)
    source=submit(client,p,'docx-authoring',{'text':'Synthetic private source'},key='metadata-original',request='Create Word')
    f=client.get(f'/api/projects/{p}/skill-files').json()[0]
    inputs={'versionOperation':{'action':'list','artifactId':f['id']}} if slug=='artifact-versioning' else {'folderOperation':{'moves':[{'artifactId':f['id'],'expectedRevision':1,'name':'private.docx','folder':'Drafts'}]}}
    result=submit(client,p,slug,inputs,key='metadata-operation',request='Inspect file metadata')
    assert result['status']=='completed',result
    assert client.delete(f'/api/projects/{p}/skill-runs/{source["id"]}').status_code in (200,204)
    assert client.get(f'/api/projects/{p}/skill-runs/{result["id"]}').status_code in (403,404,409)


def test_deleted_restore_source_revokes_followup_delivery_metadata(client):
    p = pid(client)
    source = submit(client, p, 'docx-authoring', {'text': 'Synthetic private source 42'},
                    key='review-source', request='Create Word')
    assert source['status'] == 'completed'
    f = client.get(f'/api/projects/{p}/skill-files').json()[0]
    restored = submit(client, p, 'artifact-versioning', {'versionOperation': {
        'action': 'restore', 'artifactId': f['id'], 'version': 1, 'expectedVersion': 1}},
        key='review-restore', request='Restore file')
    assert restored['status'] == 'completed'
    response = client.post(f'/api/projects/{p}/skill-runs', json={
        'request': 'Review delivery', 'requestedSkills': ['artifact-delivery'],
        'previousRunId': restored['id'], 'inputs': {}, 'idempotencyKey': 'review-followup'})
    assert response.status_code == 202, response.text
    followup = wait(client, p, response.json()['id'])
    assert followup['result']['structured']['artifact-delivery']['files']
    assert client.delete(f'/api/projects/{p}/skill-runs/{source["id"]}').status_code in (200, 204)
    assert client.get(f'/api/projects/{p}/skill-runs/{restored["id"]}').status_code in (403, 404, 409)
    # A follow-up must recheck ancestor source bindings, not merely ancestor existence.
    after = client.get(f'/api/projects/{p}/skill-runs/{followup["id"]}')
    assert after.status_code in (403, 404, 409), 'Derived delivery still exposes withdrawn source metadata'


def test_file_routes_reject_unavailable_project_with_defined_http_status(client):
    # A missing / foreign project is an authorization denial, not an uncaught exception.
    p = 'not-an-authorized-project'
    for suffix in ('skill-files', 'skill-files/missing/versions', 'skill-files/missing/download'):
        response = client.get(f'/api/projects/{p}/{suffix}')
        assert response.status_code in (403, 404)
