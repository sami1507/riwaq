"""Offline installer regressions. Synthetic files only; no pip/model/download calls."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from packaging.tags import Tag
from training import runtime_setup as dl


@pytest.fixture
def plan(monkeypatch):
    monkeypatch.setattr(dl, 'sys_tags', lambda: iter([Tag('cp312', 'cp312', 'win_amd64'), Tag('py3', 'none', 'any')]))
    items = [{'package': 'torch', 'version': dl.TORCH_VERSION, 'filename': dl.TORCH_FILENAME,
              'size': dl.TORCH_SIZE, 'sha256': dl.TORCH_SHA256}]
    for name, version in dl.PINS.items():
        items.append({'package': name, 'version': version, 'filename': f"{name.replace('-', '_')}-{version}-py3-none-any.whl",
                      'size': 10, 'sha256': 'a' * 64})
    return {'items': items, 'wheel_bytes': sum(x['size'] for x in items)}


def test_current_exact_plan_accepted(plan):
    assert dl.validate_plan(plan) is plan
    assert dl.PINS['urllib3'] == '2.8.0'


@pytest.mark.parametrize('change', ['torch_old', 'urllib_old', 'duplicate', 'missing', 'unknown', 'total', 'torch_hash', 'path'])
def test_invalid_or_downgrade_plan_rejected(plan, change):
    value = copy.deepcopy(plan)
    if change == 'torch_old': value['items'][0]['version'] = '2.7.1+cu128'
    elif change == 'urllib_old': next(x for x in value['items'] if x['package'] == 'urllib3')['version'] = '2.5.0'
    elif change == 'duplicate': value['items'].append(copy.deepcopy(value['items'][0]))
    elif change == 'missing': value['items'].pop()
    elif change == 'unknown': value['items'][-1]['package'] = 'unknown'
    elif change == 'total': value['wheel_bytes'] -= 1
    elif change == 'torch_hash': value['items'][0]['sha256'] = '0' * 64
    elif change == 'path': value['items'][0]['filename'] = '../' + value['items'][0]['filename']
    with pytest.raises(ValueError): dl.validate_plan(value)


def test_stale_cached_plan_rejected_before_network(tmp_path, monkeypatch, plan):
    monkeypatch.setattr(dl, 'RUNTIME', tmp_path)
    monkeypatch.setattr(dl, 'WHEELS', tmp_path / 'wheels')
    monkeypatch.setattr(dl, 'fetch', lambda *a, **k: pytest.fail('Stale plan caused network'))
    plan['items'][0]['version'] = '2.7.1+cu128'
    (tmp_path / 'download-plan.json').write_text(json.dumps(plan))
    with pytest.raises(ValueError, match='Stale'): dl.plan()


@pytest.fixture
def local_item(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    monkeypatch.setattr(dl, 'RUNTIME_ROOT', runtime)
    monkeypatch.setattr(dl, 'fetch', lambda *a, **k: pytest.fail('Local reference used network fallback'))
    body = b'synthetic fixture, never installed'
    target = runtime / 'urllib3-2.8.0-py3-none-any.whl'; target.write_bytes(body)
    return {'filename': target.name, 'local_file': str(target), 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()}


def test_explicit_local_wheel_is_verified(local_item):
    assert dl.resolve_wheel(local_item, allow_download=True) == Path(local_item['local_file'])


@pytest.mark.parametrize('change', ['missing', 'outside', 'relative', 'wrong_hash', 'wrong_size'])
def test_bad_local_reference_fails_closed(local_item, tmp_path, change):
    if change == 'missing': Path(local_item['local_file']).unlink()
    elif change == 'outside': local_item['local_file'] = str(tmp_path / local_item['filename'])
    elif change == 'relative': local_item['local_file'] = local_item['filename']
    elif change == 'wrong_hash': local_item['sha256'] = '0' * 64
    elif change == 'wrong_size': local_item['size'] += 1
    with pytest.raises(ValueError): dl.resolve_wheel(local_item, allow_download=True)


def test_legacy_fetch_cannot_ignore_new_aggregate_ledger(tmp_path, monkeypatch):
    (tmp_path / 'reports').mkdir()
    (tmp_path / 'reports/security-update-downloads.json').write_text('{}')
    monkeypatch.setattr(dl, 'ROOT', tmp_path)
    monkeypatch.setattr(dl.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('Legacy downloader sent a request'))
    monkeypatch.setattr(dl, 'ledger', lambda: pytest.fail('Legacy ledger was used'))
    with pytest.raises(ValueError, match='10 GiB'): dl.fetch('https://example.invalid/no-request')


def load_visual(monkeypatch, tmp_path, value):
    source = Path(__file__).resolve().parents[1] / 'scripts/visual_setup.py'
    spec = importlib.util.spec_from_file_location('visual_setup_security_test', source)
    visual = importlib.util.module_from_spec(spec); spec.loader.exec_module(visual)
    monkeypatch.setattr(visual, 'dl', dl)
    file = tmp_path / 'download-plan.json'; file.write_text(json.dumps(value))
    monkeypatch.setattr(visual, 'Path', lambda name: file if str(name) == 'D:/RiwaqRuntime/training-v1/download-plan.json' else Path(name))
    monkeypatch.setattr(visual, 'fetch_small', lambda *a, **k: pytest.fail('Unexpected metadata/download'))
    monkeypatch.setattr(visual.subprocess, 'run', lambda *a, **k: pytest.fail('No installation allowed'))
    return visual


def test_visual_rejects_old_inherited_plan_before_any_install(tmp_path, monkeypatch, plan):
    plan['items'][0]['version'] = '2.7.1+cu128'
    visual = load_visual(monkeypatch, tmp_path, plan)
    assert visual.PINS['brotli'] == '1.2.1'
    with pytest.raises(ValueError, match='Stale'): visual.tools_install()


def test_visual_uses_shared_local_resolver(tmp_path, monkeypatch, plan):
    plan['items'][0]['local_file'] = 'D:/RiwaqRuntime/security-updates-20261115/' + dl.TORCH_FILENAME
    visual = load_visual(monkeypatch, tmp_path, plan)
    class ObservedResolver(Exception): pass
    def resolve(item, **kwargs):
        assert item['version'] == '2.10.0+cu128'
        assert item['local_file'] == plan['items'][0]['local_file']
        assert kwargs['cache_dir'] == Path('D:/RiwaqRuntime/training-v1/wheels')
        raise ObservedResolver()
    monkeypatch.setattr(dl, 'resolve_wheel', resolve)
    with pytest.raises(ObservedResolver): visual.tools_install()
