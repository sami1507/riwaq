"""Host/container data-engine compatibility; local contracts vs opt-in real image."""
from contextlib import nullcontext
from dataclasses import asdict
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend import skill_data, skill_transform
from backend import skill_transform_protocol as protocol


def parent_with_worker_result(monkeypatch, domain, result):
    monkeypatch.setattr(skill_transform, 'worker_lease', lambda: nullcontext())
    monkeypatch.setattr(skill_transform, 'load_config', lambda path: SimpleNamespace(images={'skill_transform': 'sha256:'+'a'*64}))
    monkeypatch.setattr(skill_transform, 'assert_ready', lambda: None)
    monkeypatch.setattr(skill_transform, 'Journal', lambda *args: SimpleNamespace(event=lambda *args: None, finish=lambda *args: None))
    def fake_execute(files, *args, **kwargs):
        artifact = protocol.output_artifact(b'amount\r\n1\r\n', 'synthetic.csv')
        raw = protocol.encode_result(files['transform.json'], result, [artifact])
        return {'status': 'completed', 'cleanup_verified': True, 'policy_inspected': True,
                'exit_code': 0, 'stdout': raw.decode(), 'stderr': ''}
    monkeypatch.setattr(skill_transform, 'execute', fake_execute)
    return skill_transform.execute_transform(domain, {}, [], check=lambda: True)


def test_data_result_wire_declares_actual_engine_version():
    value = skill_data.execute_data({'operation': 'stats', 'table': {'columns': ['amount'], 'rows': [[10], [0], [20]]}, 'columns': ['amount']})
    assert asdict(value)['engine_version'] == skill_data.VERSION


@pytest.mark.parametrize('version', [None, 'data-v1', 'future-data-v999', 2])
def test_old_missing_or_unrecognized_data_engine_cannot_publish_artifacts(monkeypatch, version):
    result = {'summary': {'version': skill_data.VERSION}, 'table': None}
    if version is not None:
        result['engine_version'] = version
    with pytest.raises(protocol.TransformError, match='^data_engine_version_mismatch$'):
        parent_with_worker_result(monkeypatch, 'data', result)


def test_current_data_engine_result_is_returned_after_wire_validation(monkeypatch):
    result = parent_with_worker_result(monkeypatch, 'data', {'engine_version': skill_data.VERSION, 'summary': {'rows': 3}})
    assert result.result['engine_version'] == skill_data.VERSION
    assert result.artifacts[0].content == b'amount\r\n1\r\n'


def test_documents_protocol_does_not_require_data_engine_version(monkeypatch):
    result = parent_with_worker_result(monkeypatch, 'documents', {'operation': 'pdf-merge', 'page_count': 3})
    assert result.result['page_count'] == 3 and result.artifacts


@pytest.mark.skipif(os.environ.get('RIWAQ_TEST_AI_CORE_ENGINE_LIVE') != '1', reason='Opt-in old existing pinned Docker image; no downloads')
def test_actual_old_image_is_blocked_before_api_artifact_publication(tmp_path, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from backend import app as service, local_auth
    root = Path(__file__).resolve().parents[1]
    old_config = root/'reports/ai-core-20261116/data-isolated-workers-pre-version.json'
    assert old_config.is_file()
    config = json.loads(old_config.read_text(encoding='utf-8'))
    (tmp_path/'isolated-workers.json').write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY', str(tmp_path/'no-registry.json'))
    with TestClient(service.app, headers={'X-Riwaq-Client': 'local'}) as client:
        assert client.post('/api/auth/login', json={'pairing_code': local_auth.issue_pairing_code()}).status_code == 200
        pid = client.post('/api/projects', json={'name': 'Old data engine blocked test'}).json()['id']
        before = client.get(f'/api/projects/{pid}/skill-files').json()
        response = client.post(f'/api/projects/{pid}/skill-runs', json={
            'request': 'تصدير بيانات الاختبار', 'requestedSkills': ['csv-import-export'],
            'inputs': {'dataOperation': {'operation': 'csv', 'table': {'columns': ['amount'], 'rows': [[10], [0], [20]]}}},
            'idempotencyKey': 'old-engine-blocked'})
        assert response.status_code == 202, response.text
        run_id = response.json()['id']
        deadline = time.monotonic()+100
        while time.monotonic() < deadline:
            state = client.get(f'/api/projects/{pid}/skill-runs/{run_id}').json()
            if state['status'] not in ('queued', 'running', 'cancelling'):
                break
            time.sleep(.05)
        assert state['status'] == 'failed', state
        assert 'data_engine_version_mismatch' in json.dumps(state)
        assert not state.get('result', {}).get('artifacts')
        assert client.get(f'/api/projects/{pid}/skill-files').json() == before
    report = {'test_kind': 'actual_local_docker_and_authenticated_api', 'image_id': config['images']['skill_transform'],
              'expected': 'data_engine_version_mismatch', 'actual_status': state['status'],
              'artifact_count': 0, 'production_config_changed': False}
    (root/'reports/ai-core-20261116/data-old-image-blocked.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
