"""Opt-in authenticated API + actual offline-built Docker candidate; synthetic only."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import time
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from backend import app as service, local_auth
from backend.skill_data import VERSION as DATA_VERSION
from test_ai_core_forecast import chart_request, source_table

OUT = Path(os.environ.get('RIWAQ_TEST_AI_CORE_FORECAST_OUTPUT', str(Path(__file__).resolve().parents[1]/'reports/ai-core-continuation-20261116/data')))
pytestmark = pytest.mark.skipif(os.environ.get('RIWAQ_TEST_AI_CORE_FORECAST_LIVE') != '1', reason='Explicit opt-in to offline-built forecast Docker candidate, no downloads')


def test_actual_forecast_diagnostics_chart_and_edit_via_scoped_api(tmp_path, monkeypatch):
    config = OUT/'isolated-workers.json'
    shutil.copyfile(config, tmp_path/'isolated-workers.json')
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY', str(tmp_path/'not-configured.json'))
    report = {'kind': 'actual Windows HTTP SQLite DPAPI Docker', 'synthetic': True, 'model_calls': 0,
              'production_config_changed': False, 'config_sha256': hashlib.sha256(config.read_bytes()).hexdigest(), 'operations': []}
    started = time.monotonic()
    try:
        with TestClient(service.app, headers={'X-Riwaq-Client': 'local'}) as client:
            assert client.post('/api/auth/login', json={'pairing_code': local_auth.issue_pairing_code()}).status_code == 200
            pid = client.post('/api/projects', json={'name': 'Forecast diagnostics synthetic acceptance'}).json()['id']

            def run(slug, operation, key, source=None, status='partial'):
                operation = deepcopy(operation); inputs = {'dataOperation': operation}
                if source:
                    operation['sourceId'] = source['id']
                    inputs['sourceRefs'] = [{k: source[k] for k in ('kind', 'id', 'version', 'sha256')}]
                payload = {'request': 'نفذ عملية البيانات المؤكدة وافحص حدود النتيجة', 'requestedSkills': [slug],
                           'inputs': inputs, 'idempotencyKey': key}
                response = client.post(f'/api/projects/{pid}/skill-runs', json=payload)
                assert response.status_code == 202, response.text
                rid = response.json()['id']; deadline = time.monotonic()+100
                while time.monotonic() < deadline:
                    state = client.get(f'/api/projects/{pid}/skill-runs/{rid}').json()
                    if state['status'] not in ('queued', 'running', 'cancelling'):
                        break
                    time.sleep(.05)
                assert state['status'] == status, state.get('error', state)
                row = {'id': rid, 'key': key, 'status': status}
                if status == 'failed':
                    assert not state.get('result', {}).get('artifacts'); report['operations'].append(row)
                    return None, None, None
                structured = state['result']['structured'][slug]
                output = structured['output']
                assert output['engine_version'] == DATA_VERSION
                receipt = structured['execution_receipt']
                assert receipt['cleanup_verified'] and receipt['policy_inspected'] and receipt['network_policy'] == 'none'
                row.update(receipt=receipt, summary=output['summary'], source_refs=structured['source_refs'])
                artifact, raw = None, None
                if state['result']['artifacts']:
                    files = client.get(f'/api/projects/{pid}/skill-files').json()
                    artifact = next(f for f in files if f['id'] == state['result']['artifacts'][0]['artifact_id'])
                    response = client.get(artifact['url']); assert response.status_code == 200
                    raw = response.content; assert hashlib.sha256(raw).hexdigest() == artifact['sha256']
                    row['artifact_sha256'] = artifact['sha256']
                repeated = client.post(f'/api/projects/{pid}/skill-runs', json=payload)
                assert repeated.status_code in (200, 202) and repeated.json()['id'] == rid
                report['operations'].append(row)
                return output, artifact, raw

            short = {'columns': ['month', 'sales'], 'rows': [['2026-01', 10], ['2026-02', 20], ['2026-03', 30]]}
            output, _, _ = run('exploratory-analysis', {'operation': 'explore', 'table': short,
                'columns': ['sales'], 'forecast_review': {'time_column': 'month', 'target_column': 'sales', 'frequency': 'month',
                'horizon_periods': 12, 'validation_periods': 12, 'seasonal_periods': 12}}, 'short-history')
            assert output['summary']['forecast_review']['status'] == 'insufficient_data'
            assert output['summary']['forecast_review']['split'] is None
            features = [{'column': 'known', 'available_at_column': 'known_at'}, {'column': 'late', 'available_at_column': 'late_at'}]
            output, _, _ = run('exploratory-analysis', {'operation': 'explore', 'table': source_table(),
                'columns': ['sales'], 'forecast_review': {'time_column': 'month', 'target_column': 'sales', 'frequency': 'month',
                'horizon_periods': 2, 'validation_periods': 2, 'prediction_time_column': 'as_of', 'features': features}}, 'future-feature')
            detail = output['summary']['forecast_review']
            assert detail['eligible_features'] == ['known'] and detail['excluded_features'][0]['column'] == 'late'
            assert detail['split']['training_table']['columns'] == ['month', 'known', 'sales']
            assert detail['split']['validation_table']['rows'] == [['2026-07', 7, 70], ['2026-08', 8, 80]]
            output, descriptor, raw = run('spreadsheet-charts', chart_request(True), 'observed-forecast')
            assert output['summary']['forecast_display']['uncertainty'] == 'supplied_bounds_not_calibrated'
            (OUT/'observed-forecast-api.xlsx').write_bytes(raw)
            (OUT/'observed-forecast-api-output.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
            _, _, edited = run('formula-debugging', {'operation': 'formulas', 'format': 'xlsx',
                'edits': [{'cell': 'C8', 'expected': 30, 'value': 31}]}, 'edit-forecast-input', descriptor, status='completed')
            with ZipFile(BytesIO(edited)) as z:
                xml = ET.fromstring(z.read('xl/charts/chart1.xml'))
                ns = {'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart', 'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
                series = xml.findall('.//c:lineChart/c:ser', ns)
                assert len(series) == 4 and series[1].findtext('c:val/c:numRef/c:numCache/c:pt/c:v', namespaces=ns) == '31'
                assert series[1].find('c:spPr/a:ln/a:prstDash', ns).get('val') == 'dash'
            (OUT/'observed-forecast-edited-api.xlsx').write_bytes(edited)
            bad = chart_request(True); bad['table']['rows'][7][4] = 100
            run('spreadsheet-charts', bad, 'reject-invalid-forecast-bounds', status='failed')
            run('formula-debugging', {'operation': 'formulas', 'format': 'xlsx',
                'edits': [{'cell': 'C8', 'expected': 30, 'value': 40}]}, 'reject-edit-outside-bounds', descriptor, status='failed')
            report['passed'] = True
    finally:
        report['seconds'] = round(time.monotonic()-started, 3)
        (OUT/'api-live.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
