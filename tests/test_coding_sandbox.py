"""Security-boundary tests: untrusted fixtures are DATA and are never executed.

No container, WSL, interpreter, test runner, model or network is launched here.
These tests prove blocked behavior, not a working code-execution sandbox.
"""
import json
import os
from pathlib import Path
import subprocess
import threading
import pytest
from pydantic import ValidationError
from backend import coding_sandbox as sandbox


def request(**changes):return {'runner':'python_unittest','targets':['tests/test_example.py'],**changes}


@pytest.fixture
def no_host_execution(monkeypatch,tmp_path):
    from backend import visual_runner
    # These cases specify the unconfigured boundary. Never inherit the user's
    # active Docker pins or read runtime state from the default data directory.
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path/'unconfigured-runtime'))
    def forbidden(*args,**kwargs):raise AssertionError('Untrusted-code boundary attempted host execution')
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    monkeypatch.setattr(subprocess,'run',forbidden)
    monkeypatch.setattr(os,'system',forbidden)
    monkeypatch.setattr(visual_runner,'execute',forbidden)


def test_unconfigured_recorded_status_is_preview_only_without_starting_runtime(no_host_execution):
    value=sandbox.status()
    assert value['mode']=='preview_only' and not value['execution']['available'] and not value['tested']
    assert value['execution']['adapter'] is None and value['isolation_verified'] is False
    assert value['capability_evidence_sha256'] and value['reason_code']=='sandbox_unavailable'
    assert all(not row['available'] and not row['tested'] for row in value['runners'])


@pytest.mark.parametrize('change',[
    {'runner':'shell'}, {'runner':'powershell'}, {'command':'python arbitrary.py'},
    {'network':True}, {'install_dependencies':True}, {'host_fallback':True},
    {'privileged':True}, {'environment':{'API_KEY':'must-not-be-forwarded'}},
    {'timeout_seconds':121}, {'timeout_seconds':True}, {'memory_mb':2049},
    {'targets':[]}, {'targets':['test'+str(i)+'.py' for i in range(21)]},
    {'targets':['same.py','SAME.py']}, {'targets':['test.js']},
])
def test_invalid_or_policy_override_request_is_rejected_without_spawn(change,tmp_path,no_host_execution):
    with pytest.raises(ValidationError):sandbox.run_tests(tmp_path,request(**change))


@pytest.mark.parametrize('target',[
    '../test.py','tests/../test.py','/test.py','C:/test.py','tests\\test.py',
    'https://example.invalid/test.py','tests//test.py','tests/*test.py',
    'tests/test.py;whoami.py','tests/test.py\x00','tests /test.py',
    '.git/test.py','.aws/test.py','.ssh/test.py','.gnupg/test.py','.azure/test.py',
    '.config/test.py','.codex/test.py','.agents/test.py','node_modules/test.py',
    '.env.private/test.py','tests/credentials.py','tests/secrets_test.py',
])
def test_paths_and_secret_targets_are_refused(target,tmp_path,no_host_execution):
    with pytest.raises(ValidationError):sandbox.run_tests(tmp_path,request(targets=[target]))


@pytest.mark.parametrize('runner,target',[('python_unittest','tests/test_example.py'),('node_test','test/example.test.mjs')])
def test_valid_intent_still_cannot_execute_user_code(runner,target,tmp_path,no_host_execution):
    # Deliberately hostile-looking fixture remains inert text; it is not imported.
    selected=tmp_path/target;selected.parent.mkdir(parents=True)
    selected.write_text("raise RuntimeError('must never execute on host')\n",encoding='utf-8')
    before=selected.read_bytes();events=[]
    value=sandbox.run_tests(tmp_path,{'runner':runner,'targets':[target]},progress=events.append)
    assert value['status']=='blocked' and value['reason_code']=='sandbox_unavailable'
    assert value['executed'] is False and value['tests_passed'] is None
    assert value['test_count'] is None and value['exit_code'] is None
    assert value['stdout']=='' and value['stderr']=='' and value['seconds']==0
    assert not value['snapshot_inspected'] and not value['limits_enforced'] and not value['isolation_verified']
    assert not value['host_fallback'] and not value['automatic_download']
    assert selected.read_bytes()==before
    assert events==[{'stage':'blocked','executed':False,'reason_code':'sandbox_unavailable'}]


def test_nonexistent_snapshot_is_not_opened_or_created(tmp_path,no_host_execution):
    absent=tmp_path/'not-materialized'
    result=sandbox.run_tests(absent,request())
    assert result['status']=='blocked' and not result['snapshot_inspected']
    assert not absent.exists()


def test_cancelled_before_execution_has_no_false_test_result(tmp_path,no_host_execution):
    stop=threading.Event();stop.set()
    value=sandbox.run_tests(tmp_path,request(),stop)
    assert value['status']=='cancelled' and value['reason_code']=='cancelled'
    assert not value['executed'] and value['tests_passed'] is None and value['exit_code'] is None


@pytest.mark.parametrize('mutate',[
    lambda data:data.update(execution_enabled=True),
    lambda data:data.update(mode='docker'),
    lambda data:data.update(host_fallback=True),
    lambda data:data['limits'].update(max_seconds=999),
])
def test_metadata_tampering_cannot_enable_execution(tmp_path,no_host_execution,mutate):
    data=json.loads(sandbox.CONFIG.read_text(encoding='utf-8-sig'));mutate(data)
    config=tmp_path/'capabilities.json';config.write_text(json.dumps(data),encoding='utf-8')
    value=sandbox.status(config)
    assert value['reason_code']=='sandbox_configuration_invalid'
    assert not value['execution']['available'] and not value['isolation_verified']


def test_environment_flags_cannot_enable_host_execution(tmp_path,monkeypatch,no_host_execution):
    monkeypatch.setenv('RIWAQ_CODING_ALLOW_HOST_EXECUTION','1')
    monkeypatch.setenv('RIWAQ_CODING_SANDBOX','docker')
    monkeypatch.setenv('DOCKER_HOST','tcp://example.invalid:2375')
    result=sandbox.run_tests(tmp_path,request())
    assert result['status']=='blocked' and not result['executed'] and not result['host_fallback']


def test_missing_capability_evidence_fails_closed(tmp_path,no_host_execution):
    value=sandbox.status(tmp_path/'missing.json')
    assert value['reason_code']=='sandbox_configuration_invalid'
    assert not value['execution']['available'] and value['runtime_observations']=={}
