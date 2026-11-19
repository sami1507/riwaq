"""Existing authenticated skill API and real SQLite; no provider or release."""
import json
from copy import deepcopy
import pytest
from fastapi import HTTPException
from backend import skills,skill_core,guard
from backend.db import get_db
from test_app import client
from test_skills import pid,submit

def test_actual_run_pins_packages_and_public_receipt_hides_instruction_bodies(client):
    p=pid(client);run=submit(client,p,'intent-routing',request='Explain the intended plan, read-only',key='registry-pinning-api')
    assert run['status']=='completed',run
    receipt=run['result']['package_versions']
    assert receipt['loaded'][0]['slug']=='intent-routing' and receipt['loaded'][0]['version']
    assert 'package_pin' not in run and all('content' not in row for row in receipt['loaded'])
    with get_db() as c:
        row=c.execute('SELECT * FROM skill_runs WHERE id=?',(run['id'],)).fetchone()
        pin=json.loads(row['document'])['package_pin']
    assert pin['resources'][0]['content'].startswith('---\nname: intent-routing')
    assert pin['composition']['effective']['read_only'] is True

def test_actual_pending_resource_check_rejects_revoked_pin(client,monkeypatch):
    p=pid(client);run=submit(client,p,'intent-routing',request='Review the request only',key='registry-revoke-api')
    current=deepcopy(skill_core.catalog());next(s for s in current['skills'] if s['slug']=='intent-routing')['revoked']=True
    monkeypatch.setattr(skills,'catalog',lambda:current)
    with get_db() as c:
        c.execute("UPDATE skill_runs SET state='unknown' WHERE id=?",(run['id'],))
        row=c.execute('SELECT * FROM skill_runs WHERE id=?',(run['id'],)).fetchone()
        with pytest.raises(HTTPException) as denied:skills.recheck(c,row)
        assert denied.value.status_code==409

def test_actual_pending_check_keeps_compatible_old_package_after_upgrade(client,monkeypatch):
    p=pid(client);run=submit(client,p,'intent-routing',request='Review the request only',key='registry-upgrade-api')
    current=deepcopy(skill_core.catalog());entry=next(s for s in current['skills'] if s['slug']=='intent-routing');entry.update(version='9.0.0',instructions_sha256='a'*64)
    monkeypatch.setattr(skills,'catalog',lambda:current)
    with get_db() as c:
        c.execute("UPDATE skill_runs SET state='unknown' WHERE id=?",(run['id'],))
        row=c.execute('SELECT * FROM skill_runs WHERE id=?',(run['id'],)).fetchone();skills.recheck(c,row)
        old=json.loads(row['document'])['package_pin']
    assert old['resources'][0]['version']!='9.0.0'

@pytest.mark.parametrize('state',['missing','unsigned','stale','critical-fail','valid'])
def test_actual_activation_release_gate_checks_server_evidence(client,monkeypatch,tmp_path,state):
    from backend import skill_system,skill_release,local_auth
    from backend.db import data_dir
    from test_skill_batch05_registry import report
    p=pid(client);current=deepcopy(skill_core.catalog())
    next(s for s in current['skills'] if s['slug']=='intent-routing')['release_profile']='batch05-fast'
    monkeypatch.setattr(skills,'catalog',lambda:current);monkeypatch.setattr(skill_core,'catalog',lambda:current)
    # Synthetic source/report issuer fixture; verifies gate wiring, not a real
    # passing test run. The operator runner is tested independently.
    source=tmp_path/'synthetic-source';(source/'backend').mkdir(parents=True);file=source/'backend/fixture.py';file.write_bytes(b'value=1\n')
    monkeypatch.setattr(skill_system,'ROOT',source)
    if state!='missing':
        value=report(source)
        if state=='critical-fail':
            value['cases'][0]['status']='failed';value['totals'].update(passed=1,failed=1)
        evidence=skill_release.seal_runner_report(value,source,local_auth._master_key(create=False))
        if state=='unsigned':evidence['signature']='0'*64
        folder=data_dir()/'skill-release';folder.mkdir();(folder/'batch05-fast.json').write_text(json.dumps(evidence),encoding='utf-8')
        if state=='stale':file.write_bytes(b'value=2\n')
    response=client.post(f'/api/projects/{p}/skill-runs',json={'request':'Review only','requestedSkills':['intent-routing'],'idempotencyKey':'gated-'+state})
    if state=='valid':
        from test_skills import wait
        assert response.status_code==202,response.text
        assert wait(client,p,response.json()['id'])['status']=='completed'
    else:
        assert response.status_code==422,response.text
        with get_db() as c:assert c.execute('SELECT COUNT(*) FROM skill_runs').fetchone()[0]==0
