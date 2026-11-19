"""Offline synthetic promotion checks; installer/model calls are never executed."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def apply_module():
    spec = importlib.util.spec_from_file_location('apply_security_update_test', ROOT / 'reports/apply_security_update.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.fixture
def wheel(tmp_path, apply_module, monkeypatch):
    monkeypatch.setattr(apply_module, 'STAGE', tmp_path)
    path = tmp_path / 'urllib3-2.8.0-py3-none-any.whl'; path.write_bytes(b'synthetic fixture; never installed')
    return {'path': str(path), 'version': '2.8.0', 'bytes': path.stat().st_size, 'sha256': apply_module.sha(path)}


def test_verified_wheel_binds_size_hash_version(apply_module, wheel):
    assert apply_module.checked_wheel(wheel, 'urllib3', wheel['sha256']) == Path(wheel['path'])


@pytest.mark.parametrize('field,value', [('version', '2.5.0'), ('bytes', 1), ('sha256', '0'*64)])
def test_changed_wheel_rejected(apply_module, wheel, field, value):
    accepted = wheel['sha256']; wheel[field] = value
    with pytest.raises(ValueError): apply_module.checked_wheel(wheel, 'urllib3', accepted)


def test_brotli_120_cannot_be_accepted_even_with_matching_hash(apply_module, tmp_path, monkeypatch):
    monkeypatch.setattr(apply_module, 'STAGE', tmp_path)
    path = tmp_path / 'brotli-1.2.0-cp312-cp312-win_amd64.whl'
    path.write_bytes(b'synthetic old wheel; never installed')
    item = {'version': '1.2.0', 'path': str(path), 'bytes': path.stat().st_size, 'sha256': apply_module.sha(path)}
    with pytest.raises(ValueError, match='version differs'):
        apply_module.checked_wheel(item, 'brotli', item['sha256'])


def test_brotli_121_identity_and_old_candidate_lock_guard(apply_module, tmp_path, monkeypatch):
    monkeypatch.setattr(apply_module, 'STAGE', tmp_path)
    path = tmp_path / 'brotli-1.2.1-cp312-cp312-win_amd64.whl'
    path.write_bytes(b'synthetic current wheel; never installed')
    item = {'version': '1.2.1', 'path': str(path), 'bytes': path.stat().st_size, 'sha256': apply_module.sha(path)}
    assert apply_module.checked_wheel(item, 'brotli', item['sha256']) == path
    old = tmp_path / 'brotli-1.2.0-cp312-cp312-win_amd64.whl'
    lock = tmp_path / 'old-candidate.lock'
    lock.write_text(f"brotli @ {old.as_uri()}#sha256={item['sha256']}\n")
    with pytest.raises(ValueError, match='different wheel'):
        apply_module.changed_requirements(lock, {'brotli'}, {'brotli': {'path': path, 'sha256': item['sha256']}})


def test_changed_requirements_are_hashed_and_do_not_include_unrelated_packages(apply_module, wheel, tmp_path):
    lock = tmp_path / 'candidate.lock'
    lock.write_text(f"urllib3 @ {Path(wheel['path']).as_uri()}#sha256={wheel['sha256']}\nsympy==1.13.3\n")
    result = apply_module.changed_requirements(lock, {'urllib3'}, {'urllib3': {'path': Path(wheel['path']), 'sha256': wheel['sha256']}})
    assert 'sympy' not in result
    assert '--hash=sha256:' + wheel['sha256'] in result
    lock.write_text(lock.read_text().replace(wheel['sha256'], '0'*64))
    with pytest.raises(ValueError, match='hash differs'):
        apply_module.changed_requirements(lock, {'urllib3'}, {'urllib3': {'path': Path(wheel['path']), 'sha256': wheel['sha256']}})


def test_old_acceptance_marker_is_not_enough(apply_module):
    with pytest.raises(ValueError, match='acceptance'):
        apply_module.preflight({'candidate_acceptance': 'passed'})


def test_backup_is_preserved_on_retry(apply_module, tmp_path, monkeypatch):
    monkeypatch.setattr(apply_module, 'save', lambda book: None)
    source = tmp_path / 'tools.json'; source.write_text('original')
    target = tmp_path / 'rollback/tools.json'; book = {}
    apply_module.backup_once(source, target, 'tools', book)
    source.write_text('changed')
    apply_module.backup_once(source, target, 'tools', book)
    assert target.read_text() == 'original'
    with pytest.raises(ValueError, match='Unrecorded'):
        apply_module.backup_once(source, target, 'tools', {})


def test_failed_install_records_partial_failure_without_automatic_restore(apply_module, tmp_path, monkeypatch):
    reports = tmp_path / 'reports'; reports.mkdir()
    bookfile = reports / 'installation.json'; bookfile.write_text('{}')
    lock = reports / 'accepted.lock'; lock.write_text('accepted synthetic lock')
    for key, value in [('ROOT',tmp_path),('OUT',reports),('STAGE',tmp_path/'stage'),('ACTIVE_ROOT',tmp_path/'active'),('BOOK',bookfile)]:
        monkeypatch.setattr(apply_module, key, value)
    envs = {'training-v1': {'lock_name':'requirements-runtime.lock.txt','lock':lock,'lock_sha256':apply_module.sha(lock),
                          'python':tmp_path/'active/python.exe','requirements':'urllib3 @ file:///synthetic.whl --hash=sha256:'+'a'*64+'\n'}}
    monkeypatch.setattr(apply_module, 'preflight', lambda book: (envs, {}, tmp_path/'plan.json', {}, tmp_path/'metadata.json'))
    monkeypatch.setattr(apply_module, 'backup_once', lambda *args: None)
    calls = []
    def fail(command, **kwargs):
        calls.append(command)
        assert '--offline' in command and '--no-index' in command and '--require-hashes' in command
        assert command[command.index('--link-mode')+1] == 'hardlink'
        assert '--no-cache' not in command
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(apply_module.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError): apply_module.main()
    saved = json.loads(bookfile.read_text())
    assert len(calls) == 1
    assert saved['status'] == 'partial_failure' and saved['restore_required'] is True
    assert saved['failure']['automatic_rollback'] is False
    assert saved['active_installs']['training-v1']['status'] == 'started'


def test_updated_recipe_cannot_be_packaged_as_historical_experiment(tmp_path):
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    (runtime / 'download-plan.json').write_text(json.dumps({'security_update':'2026-11-15'}))
    output = tmp_path / 'output'
    # Execute only the packager's early refusal on a synthetic directory; it
    # must stop before reading real run records or creating an archive.
    result = subprocess.run([sys.executable, '-B', str(ROOT/'scripts/package_training.py'), '--runtime', str(runtime), '--output', str(output)],
                            cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0 and 'preserved original recipe' in result.stderr
    assert not output.exists()
