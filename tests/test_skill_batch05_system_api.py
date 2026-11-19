"""The product API exposes current scoped state; no mock is called live."""
import json
import hashlib
from pathlib import Path
import pytest
from backend import providers
from backend.skill_system import SKILLS
from tests.test_skills import client,pid,wait

@pytest.fixture(autouse=True)
def no_external_provider(monkeypatch):
    async def fake(model,config,probe=False):
        return {'provider':'ollama','status':'unavailable','checked_at':'2026-11-16T00:00:00+00:00','reason':'synthetic_missing_model','measured':{'text':'unverified'},'write_operations':False,'probe_requested':probe}
    monkeypatch.setattr(providers,'runtime_status',fake)

@pytest.mark.parametrize('slug',sorted(SKILLS))
def test_system_inspection_has_real_task_entry_and_evidence(client,slug):
    p=pid(client);operation={'operation':slug}
    inputs={'systemOperation':operation,'text':'{"value":0}'}
    if slug=='structured-output-validation':operation['schemaDefinition']={'type':'object','properties':{'value':{'type':'integer'}},'required':['value'],'additionalProperties':False}
    response=client.post(f'/api/projects/{p}/skill-runs',json={'request':slug,'inputs':inputs,'idempotencyKey':'system-'+slug})
    assert response.status_code==202,response.text
    result=wait(client,p,response.json()['id'])
    assert result['status'] in {'completed','partial'},result
    assert slug in result['result']['structured']
    assert result['result']['package_versions']['loaded'][0]['slug']==slug
    assert 'package_pin' not in result

def test_discovery_is_same_catalog_preserves_first_hundred_and_reports_blockers(client):
    response=client.get('/api/skills/catalog');assert response.status_code==200,response.text
    body=response.json();assert len(body['skills'])==125
    memory=next(s for s in body['skills'] if s['slug']=='persistent-memory')
    assert memory['installed'] and not memory['enabled'] and not memory['ready'] and memory['blockers']
    planner=next(s for s in body['skills'] if s['slug']=='daily-planning')
    assert planner['installed'] and planner['enabled'] and planner['ready'] and planner['tested'] is False
    current=json.loads(Path('config/skills-batch-01.json').read_bytes())
    # Recorded from the untouched 100-entry catalog before this batch; the CI
    # test must not depend on an operator's ignored reports directory.
    digest=hashlib.sha256(json.dumps(current['skills'][:100],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    assert digest=='942b369b0383036bf4f1d59b3d5492c3b2b4e8ad66797ca4eef5d9efaa0f9094'

def test_monitoring_audit_and_probe_do_not_claim_external_health(client):
    p=pid(client)
    response=client.get(f'/api/projects/{p}/skill-runtime');assert response.status_code==200,response.text
    assert response.json()['project_id']==p and response.json()['model']['status']=='unavailable'
    assert response.json()['monitoring']['alerts_sent'] is False
    probe=client.post(f'/api/projects/{p}/skill-runtime/probe').json()
    assert probe['synthetic_only'] and probe['external_calls'] is False
    response=client.post(f'/api/projects/{p}/skill-runs',json={'request':'intent-routing','requestedSkills':['intent-routing'],'idempotencyKey':'audit-route-001'})
    value=wait(client,p,response.json()['id'])
    trace=client.get(f'/api/projects/{p}/skill-runs/{value["id"]}/audit').json()
    assert any(e['operation']=='skill.completed' for e in trace['events'])
    events=[e['operation'] for e in trace['events']]
    assert events.index('skill.started')<events.index('skill.tool_started')<events.index('skill.tool_finished')<events.index('skill.verifying')<events.index('skill.completed')
    assert all('request' not in e and 'body' not in e for e in trace['events'])
    assert client.get('/api/projects/not-authorized/skill-runtime').status_code in {403,404}

def test_system_inspection_rejects_hidden_write_and_malformed_contract(client):
    p=pid(client)
    response=client.post(f'/api/projects/{p}/skill-runs',json={'request':'Inspect only','idempotencyKey':'hidden-write-001','inputs':{'systemOperation':{'operation':'skill-catalog'},'scheduleOperation':{'operation':'calendar.create','options':{'name':'Hidden','zone':'UTC'}}}})
    assert response.status_code==422
    response=client.post(f'/api/projects/{p}/skill-runs',json={'request':'Inspect only','idempotencyKey':'bad-contract-001','inputs':{'systemOperation':{'operation':'structured-output-validation','schemaDefinition':{'type':'object','properties':{},'additionalProperties':True}}}})
    assert response.status_code==422
