"""Opt-in current source in real Docker via the authenticated product API."""
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

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/ai-core-20261116'
pytestmark = pytest.mark.skipif(os.environ.get('RIWAQ_TEST_AI_CORE_DATA_LIVE') != '1', reason='Explicit opt-in to existing offline-built pinned Docker worker; no downloads')
NS = {'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart'}


def test_current_data_engine_real_api_sandbox_saved_outputs_and_references(tmp_path, monkeypatch):
    data = tmp_path / 'data'; data.mkdir()
    config = Path(os.environ.get('RIWAQ_TEST_AI_CORE_DATA_CONFIG', str(OUT / 'data-isolated-workers.json')))
    report_dir = Path(os.environ.get('RIWAQ_TEST_AI_CORE_DATA_REPORT_DIR', str(OUT)))
    report_dir.mkdir(parents=True, exist_ok=True)
    assert config.is_file()
    shutil.copyfile(config, data / 'isolated-workers.json')
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(data))
    monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY', str(tmp_path / 'absent-registry.json'))
    report = {'synthetic': True, 'backend_mocked': False, 'worker_mocked': False,
              'models_called': False, 'native_excel_tested': False, 'production_config_changed': False,
              'config_sha256': hashlib.sha256(config.read_bytes()).hexdigest(), 'cases': []}
    started = time.monotonic()
    try:
        with TestClient(service.app, headers={'X-Riwaq-Client': 'local'}) as client:
            assert client.post('/api/auth/login', json={'pairing_code': local_auth.issue_pairing_code()}).status_code == 200
            project = client.post('/api/projects', json={'name': 'AI core synthetic data acceptance'}).json()['id']

            def run(slug, operation, key, source=None, status='completed'):
                spec = deepcopy(operation)
                inputs = {'dataOperation': spec}
                if source:
                    spec['sourceId'] = source['id']
                    inputs['sourceRefs'] = [{k: source[k] for k in ('kind', 'id', 'version', 'sha256')}]
                payload = {'request': 'نفذ عملية البيانات المنظمة المحددة ضمن هذا المشروع', 'requestedSkills': [slug], 'inputs': inputs, 'idempotencyKey': key}
                response = client.post(f'/api/projects/{project}/skill-runs', json=payload)
                assert response.status_code == 202, response.text[:400]
                run_id = response.json()['id']; deadline = time.monotonic() + 110
                while time.monotonic() < deadline:
                    result = client.get(f'/api/projects/{project}/skill-runs/{run_id}').json()
                    if result['status'] not in ('queued', 'running', 'cancelling'):
                        break
                    time.sleep(.05)
                assert result['status'] == status, (key, result.get('error'), result.get('result', {}).get('text'))
                structured = result.get('result', {}).get('structured', {}).get(slug)
                output = structured.get('output') if structured else None
                if status == 'completed':
                    receipt = structured['execution_receipt']
                    assert receipt['status'] == 'completed' and receipt['exit_code'] == 0
                    assert receipt['policy_inspected'] and receipt['cleanup_verified']
                    assert receipt['network_policy'] == 'none' and receipt['host_fallback'] is False
                    assert output['summary']['version'] == DATA_VERSION
                    assert output['engine_version'] == DATA_VERSION
                    report['cases'].append({'key': key, 'status': status, 'operation_id': run_id, 'receipt': receipt})
                else:
                    assert not result.get('result', {}).get('artifacts')
                    report['cases'].append({'key': key, 'status': status, 'operation_id': run_id})
                descriptor, content = None, None
                outputs = result.get('result', {}).get('artifacts', [])
                if outputs:
                    files = client.get(f'/api/projects/{project}/skill-files').json()
                    descriptor = next(item for item in files if item['id'] == outputs[0]['artifact_id'])
                    download = client.get(descriptor['url']); assert download.status_code == 200
                    content = download.content
                    assert hashlib.sha256(content).hexdigest() == descriptor['sha256']
                repeated = client.post(f'/api/projects/{project}/skill-runs', json=payload)
                assert repeated.status_code in (200, 202) and repeated.json()['id'] == run_id
                return output, descriptor, content

            raw = {'columns': ['order', 'customer', 'amount'], 'rows': [
                ['٠١٢', 'C1', '١٠,٠٠'], ['O2', 'C1', '٠'], ['O3', 'C2', '٢٠'], ['O4', 'C9', '']]}
            _, csv_file, _ = run('csv-import-export', {'operation': 'csv', 'table': raw, 'delimiter': ';'}, 'ai-data-csv')
            clean, clean_file, _ = run('spreadsheet-cleaning', {'operation': 'clean', 'source_format': 'csv', 'delimiter': ';', 'format': 'xlsx', 'cleaning': [
                {'operation': 'empty_to_null', 'column': 'amount'},
                {'operation': 'parse_number', 'column': 'amount', 'decimal_separator': ','}]}, 'ai-data-clean', csv_file)
            assert clean['table']['rows'] == [['٠١٢', 'C1', 10], ['O2', 'C1', 0], ['O3', 'C2', 20], ['O4', 'C9', None]]
            stats, _, _ = run('descriptive-statistics', {'operation': 'stats', 'columns': ['amount']}, 'ai-data-stats', clean_file)
            assert stats['summary']['statistics']['amount']['sum'] == '30'
            assert stats['summary']['statistics']['amount']['mean'] == '10'
            corrected, corrected_file, _ = run('formula-debugging', {'operation': 'formulas', 'format': 'xlsx', 'edits': [{'cell': 'C4', 'expected': 20, 'value': 30}]}, 'ai-data-correct', clean_file)
            corrected_stats, _, _ = run('descriptive-statistics', {'operation': 'stats', 'columns': ['amount']}, 'ai-data-corrected-stats', corrected_file)
            final_stats = corrected_stats['summary']['statistics']['amount']
            assert final_stats['sum'] == '40' and final_stats['mean'] == '13.33333333333333333333333333333333'
            customers = {'columns': ['customer', 'region'], 'rows': [['C1', 'الشمال'], ['C2', 'الجنوب']]}
            join_spec = {'left_keys': ['customer'], 'right_keys': ['customer'], 'how': 'left', 'cardinality': 'm:1'}
            joined, joined_file, _ = run('table-joins', {'operation': 'join', 'right': customers, 'join': join_spec, 'format': 'xlsx'}, 'ai-data-join', corrected_file)
            assert joined['summary']['join']['actual_rows'] == 4 and joined['summary']['join']['unmatched_left'] == 1
            duplicate = deepcopy(customers); duplicate['rows'].append(['C1', 'آخر'])
            run('table-joins', {'operation': 'join', 'right': duplicate, 'join': join_spec}, 'ai-data-join-reject', corrected_file, status='failed')
            _, grouped_file, _ = run('pivot-analysis', {'operation': 'aggregate', 'format': 'xlsx', 'aggregate': {'group_by': ['region'], 'value_column': 'amount', 'operation': 'sum', 'record_unit': 'one order'}}, 'ai-data-aggregate', joined_file)
            chart, _, chart_bytes = run('spreadsheet-charts', {'operation': 'chart', 'chart': {'kind': 'bar', 'category': 'region', 'values': ['sum'], 'title': 'الطلبات المصححة', 'unit': 'وحدة'}}, 'ai-data-chart', grouped_file)
            with ZipFile(BytesIO(chart_bytes)) as package:
                xml = ET.fromstring(package.read('xl/charts/chart1.xml'))
                assert [v.text for v in xml.findall('.//c:val/c:numRef/c:numCache/c:pt/c:v', NS)] == ['10', '30']
                assert xml.find('.//c:valAx/c:scaling/c:min', NS).get('val') == '0'
            (report_dir / 'data-corrected-chart.xlsx').write_bytes(chart_bytes)
            weighted, _, _ = run('descriptive-statistics', {'operation': 'stats', 'table': {'columns': ['mean', 'n'], 'rows': [[80, 10], [20, 90]]}, 'columns': ['mean'], 'weight_column': 'n'}, 'ai-data-weighted')
            assert weighted['summary']['statistics']['mean']['weighted_mean'] == '26'
            run('spreadsheet-cleaning', {'operation': 'clean', 'table': {'columns': ['amount'], 'rows': [['0.'+'0'*323+'1']]}, 'cleaning': [{'operation': 'parse_number', 'column': 'amount'}]}, 'ai-data-underflow-reject', status='failed')
            report.update(passed=True, corrected_statistics=final_stats, linked_chart_values=['10', '30'], weighted_mean='26',
                          chart_sha256=hashlib.sha256(chart_bytes).hexdigest(), repeated_posts_reused_original_ids=True)
    except BaseException as exc:
        report.update(passed=False, error_type=type(exc).__name__)
        raise
    finally:
        report['seconds'] = round(time.monotonic()-started, 3)
        (report_dir / 'data-api-live.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
