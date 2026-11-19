"""Opt-in real local Docker acceptance. No model, download or host-code fallback."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from backend import app as service, local_auth

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'reports/skills-batch-02-20261116/coding-live.json'
pytestmark = pytest.mark.skipif(os.environ.get('RIWAQ_TEST_BATCH02_LIVE') != '1',
    reason='Explicit opt-in required for existing local Docker integration')

BUGGY = b'def add(a, b):\n    return a - b\n'
FIXED = b'def add(a, b):\n    return a + b\n'
TESTS = b'''import unittest
from calc import add
class Addition(unittest.TestCase):
    def test_positive(self): self.assertEqual(add(2,3),5)
    def test_negative(self): self.assertEqual(add(-2,-3),-5)
    def test_zero(self): self.assertEqual(add(0,0),0)
'''
ISOLATION_TEST = b'''import errno, os, socket, unittest
class Isolation(unittest.TestCase):
    def test_nonroot_and_no_socket(self):
        self.assertNotEqual(os.getuid(),0)
        self.assertFalse(os.path.exists('/var/run/docker.sock'))
    def test_network_is_unroutable(self):
        with socket.socket() as sock:
            sock.settimeout(0.3)
            with self.assertRaises(OSError) as denied:
                sock.connect(('198.51.100.1',9))
            self.assertEqual(denied.exception.errno,errno.ENETUNREACH)
'''


def finish(client, pid, rid):
    deadline=time.monotonic()+100
    while time.monotonic()<deadline:
        response=client.get(f'/api/projects/{pid}/skill-runs/{rid}')
        assert response.status_code==200,response.status_code
        value=response.json()
        if value['status'] not in {'queued','running','cancelling'}:return value
        time.sleep(.05)
    raise AssertionError('Actual isolated check exceeded its acceptance deadline')


def test_real_skill_repository_checks_failure_fix_and_network_denial(tmp_path,monkeypatch):
    data=tmp_path/'data';data.mkdir()
    config=ROOT/'data/isolated-workers.json'
    assert config.is_file(),'Existing reviewed Docker configuration is required; no automatic setup'
    shutil.copyfile(config,data/'isolated-workers.json')
    fixture=tmp_path/'authorized-synthetic-repository';fixture.mkdir()
    dirty=b'Preserve this independent pre-existing synthetic user edit.\n'
    (fixture/'calc.py').write_bytes(BUGGY)
    (fixture/'test_calc.py').write_bytes(TESTS)
    (fixture/'test_isolation.py').write_bytes(ISOLATION_TEST)
    (fixture/'untouched.txt').write_bytes(dirty)
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(data))
    monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY',str(tmp_path/'absent-registry.json'))
    report={'kind':'real FastAPI/SQLite/Guard/Docker with synthetic repository',
        'model_called':False,'network_provider_called':False,'downloaded':False,'host_source_executed':False,
        'configuration_sha256':hashlib.sha256(config.read_bytes()).hexdigest(),'cases':[]}
    started=time.monotonic()
    try:
        with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as client:
            assert client.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
            project=client.post('/api/projects',json={'name':'QA skills batch02 isolated acceptance'}).json()['id']
            response=client.post(f'/api/projects/{project}/coding/workspaces',json={'path':str(fixture),'label':'Synthetic addition fixture'})
            assert response.status_code in {200,201},response.status_code
            workspace=response.json()['id']
            def run(key,slugs,target):
                payload={'request':'Inspect and run only the specified isolated checks. Keep source unchanged.',
                    'requestedSkills':slugs,'inputs':{'workspaceId':workspace,
                        'repositoryPaths':['calc.py','test_calc.py','test_isolation.py'],
                        'testRequest':{'runner':'python_unittest','targets':[target],'timeout_seconds':15,'memory_mb':256}},
                    'idempotencyKey':key}
                response=client.post(f'/api/projects/{project}/skill-runs',json=payload)
                assert response.status_code==202,response.text[:300]
                result=finish(client,project,response.json()['id'])
                assert result['status'] in {'partial','completed','failed'},result.get('error')
                receipt=result['result'].get('structured',{}).get(slugs[-1])
                assert receipt and receipt['executed'],result.get('error','Missing supervisor receipt')
                # This receipt deliberately does not certify an entire sandbox.
                # Verify the actual inspected container policy and cleanup.
                assert receipt['policy_inspected'] and receipt['cleanup_verified'],receipt.get('reason_code')
                assert receipt['isolation_verified'] is False
                assert receipt['network_policy']=='none' and not receipt['host_fallback'] and not receipt['automatic_download']
                assert receipt['tests_passed'] is None and receipt['test_count'] is None
                assert receipt['requested_limits']['seconds']==15 and receipt['requested_limits']['memory_mb']==256
                report['cases'].append({'key':key,'operation_id':result['id'],'status':result['status'],
                    'selected_skills':slugs,'receipt':receipt})
                repeated=client.post(f'/api/projects/{project}/skill-runs',json=payload)
                assert repeated.status_code in {200,202} and repeated.json()['id']==result['id']
                return result,receipt
            before,first=run('batch02-live-before',['repository-understanding','unit-testing'],'test_calc.py')
            assert first['exit_code']!=0 and before['status']=='partial'
            context=before['result']['structured']['repository-understanding']
            assert any(item['path']=='calc.py' for item in context['bindings'])
            assert (fixture/'calc.py').read_bytes()==BUGGY
            # Deliberate, authorized one-line edit to THIS synthetic fixture.
            # It is not attributed to a model or to the repository-reading skill.
            (fixture/'calc.py').write_bytes(FIXED)
            after,fixed=run('batch02-live-after',['repository-understanding','unit-testing'],'test_calc.py')
            assert fixed['exit_code']==0 and after['status']=='completed'
            isolated,isolation=run('batch02-live-network',['sandbox-execution'],'test_isolation.py')
            assert isolation['exit_code']==0 and isolated['status']=='completed'
            assert (fixture/'test_calc.py').read_bytes()==TESTS
            assert (fixture/'untouched.txt').read_bytes()==dirty
            report.update(accepted=True,manual_fixture_fix=True,unrelated_file_sha256=hashlib.sha256(dirty).hexdigest(),
                test_file_sha256=hashlib.sha256(TESTS).hexdigest(),source_before_sha256=hashlib.sha256(BUGGY).hexdigest(),
                source_after_sha256=hashlib.sha256(FIXED).hexdigest(),cost='No paid service; local CPU/time not priced')
    except BaseException as exc:
        report.update(accepted=False,error_type=type(exc).__name__)
        raise
    finally:
        report['seconds']=round(time.monotonic()-started,3)
        REPORT.parent.mkdir(parents=True,exist_ok=True)
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
