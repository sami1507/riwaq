"""Durability/recovery simulations; no engine or user code is executed."""
import json
from pathlib import Path
import pytest
from backend import isolated_journal as j
from backend.isolated_worker import IsolatedConfig, IsolatedError


@pytest.fixture
def config(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    return IsolatedConfig(docker_executable='C:/trusted/docker.exe',docker_sha256='a'*64,
        endpoint='npipe:////./pipe/dockerDesktopLinuxEngine',daemon_id='owned-daemon',images={'python_unittest':'sha256:'+'b'*64})


def create(config):
    journal=j.Journal(config,'python_unittest')
    journal.event({'stage':'container_created','container_name':'riwaq-isolated-'+'c'*32,'container_id':'d'*64})
    return journal


def test_journal_exists_before_progress_callback_and_contains_no_input(config):
    seen=[];journal=j.Journal(config,'python_unittest',lambda event:seen.append(journal.path.is_file()))
    journal.event({'stage':'container_created','container_name':'riwaq-isolated-'+'c'*32,'container_id':'d'*64})
    assert seen==[True]
    value=json.loads(journal.path.read_bytes())
    assert set(value)==j.KEYS and value['state']=='pending'
    assert not {'files','body','payload','stdout','stderr','content','project_id'} & value.keys()
    with pytest.raises(IsolatedError,match='pending_container_recovery'):j.assert_ready()


def test_verified_cleanup_removes_obligation(config):
    journal=create(config);journal.finish({'cleanup_verified':True})
    assert not journal.path.exists();j.assert_ready()


def test_cleanup_failure_retained_for_explicit_recovery_no_replay(config,monkeypatch):
    journal=create(config);journal.finish({'cleanup_verified':False});calls=[]
    def cleanup(config_arg,name,**expected):
        calls.append((name,expected));return {'cleanup_verified':True}
    monkeypatch.setattr(j,'cleanup_owned',cleanup)
    result=j.recover_pending(config)
    assert result['cleaned']==1 and not result['blocked'] and not result['execution_replayed']
    assert calls==[('riwaq-isolated-'+'c'*32,{'expected_id':'d'*64,'expected_image':'sha256:'+'b'*64})]
    assert not journal.path.exists()


def test_recovery_rejects_changed_configuration_without_engine_call(config,monkeypatch):
    journal=create(config);data=json.loads(journal.path.read_bytes());data['daemon_id']='other-daemon'
    journal.path.write_text(json.dumps(data));calls=[]
    monkeypatch.setattr(j,'cleanup_owned',lambda *a,**k:calls.append(1))
    result=j.recover_pending(config)
    assert result['blocked'][0]['reason_code']=='journal_configuration_mismatch' and not calls
    assert journal.path.exists()


def test_corrupt_partial_record_blocks_new_execution(config,monkeypatch):
    folder=j._directory();path=folder/('riwaq-isolated-'+'c'*32+'.tmp');path.write_text('{partial')
    with pytest.raises(IsolatedError):j.assert_ready()
    result=j.recover_pending(config)
    assert result['blocked'] and path.exists()


def test_recovery_failure_does_not_delete_evidence(config,monkeypatch):
    journal=create(config)
    monkeypatch.setattr(j,'cleanup_owned',lambda *a,**k:{'cleanup_verified':False})
    result=j.recover_pending(config)
    assert result['cleaned']==0 and result['blocked'][0]['reason_code']=='cleanup_unverified' and journal.path.exists()


def test_no_pending_records_never_loads_runtime_or_calls_engine(config,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('Unexpected runtime call')
    monkeypatch.setattr(j,'load_config',forbidden);monkeypatch.setattr(j,'cleanup_owned',forbidden)
    assert j.recover_pending()['checked']==0
