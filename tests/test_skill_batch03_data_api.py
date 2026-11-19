"""Opt-in real HTTP/SQLite/Guard/Docker data workflow; synthetic sources only."""
from copy import deepcopy
from io import BytesIO
import hashlib
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

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/skills-batch-03-20261116/data'
pytestmark = pytest.mark.skipif(os.environ.get('RIWAQ_TEST_BATCH03_LIVE') != '1', reason='Opt in to the existing pinned local Docker worker; no downloads')
ORDERS = {'columns': ['order_id', 'customer_id', 'amount'], 'rows': [['O1', 'C1', 10], ['O2', 'C1', 0], ['O3', 'C2', 20], ['O4', 'C9', None]]}
CUSTOMERS = {'columns': ['customer_id', 'region'], 'rows': [['C1', 'north'], ['C2', 'south']]}
NS = {'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart'}


def test_real_csv_clean_stats_join_aggregate_chart_and_scope(tmp_path, monkeypatch):
    data = tmp_path / 'data'; data.mkdir()
    config = ROOT / 'reports/skills-batch-03-20261116/documents/isolated-workers.json'
    assert config.is_file(), 'Reviewed pinned worker configuration required; no setup fallback'
    shutil.copyfile(config, data / 'isolated-workers.json')
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(data))
    monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY', str(tmp_path / 'absent-registry.json'))
    report = {'kind': 'actual HTTP/SQLite/Guard and pinned Docker worker', 'synthetic': True, 'models_called': False, 'cloud_called': False, 'automatic_downloads': False, 'cases': [], 'config_sha256': hashlib.sha256(config.read_bytes()).hexdigest()}
    started = time.monotonic()
    try:
        with TestClient(service.app, headers={'X-Riwaq-Client': 'local'}) as client:
            assert client.post('/api/auth/login', json={'pairing_code': local_auth.issue_pairing_code()}).status_code == 200
            project = client.post('/api/projects', json={'name': 'QA skills batch03 data acceptance'}).json()['id']
            other = client.post('/api/projects', json={'name': 'QA skills batch03 different scope'}).json()['id']
            def run(slug, operation, key, source=None, expected='completed'):
                inputs = {'dataOperation': deepcopy(operation)}
                if source:
                    inputs['dataOperation']['sourceId'] = source['id']
                    inputs['sourceRefs'] = [{k: source[k] for k in ('kind', 'id', 'version', 'sha256')}]
                payload = {'request': 'نفّذ عملية البيانات المنظمة المحددة فقط', 'requestedSkills': [slug], 'inputs': inputs, 'idempotencyKey': key}
                response = client.post(f'/api/projects/{project}/skill-runs', json=payload)
                assert response.status_code == 202, response.text[:400]
                rid = response.json()['id']; deadline = time.monotonic()+100
                while time.monotonic() < deadline:
                    response = client.get(f'/api/projects/{project}/skill-runs/{rid}')
                    assert response.status_code == 200, response.text[:400]
                    result = response.json()
                    if result['status'] not in ('queued', 'running', 'cancelling'):
                        break
                    time.sleep(.05)
                assert result['status'] == expected, (result['status'], result.get('error'), result.get('result', {}).get('text'))
                structured = result.get('result', {}).get('structured', {}).get(slug)
                if expected == 'completed':
                    assert structured
                    receipt = structured['execution_receipt']
                    assert receipt['status'] == 'completed' and receipt['exit_code'] == 0
                    assert receipt['policy_inspected'] and receipt['cleanup_verified']
                    assert receipt['network_policy'] == 'none' and receipt['host_fallback'] is False
                    assert receipt['isolation_verified'] is False
                    report['cases'].append({'key': key, 'status': result['status'], 'operation_id': rid, 'receipt': receipt})
                else:
                    assert result.get('result', {}).get('artifacts', []) == []
                    report['cases'].append({'key': key, 'status': result['status'], 'operation_id': rid, 'error': result.get('error')})
                repeated = client.post(f'/api/projects/{project}/skill-runs', json=payload)
                assert repeated.status_code in (200, 202) and repeated.json()['id'] == rid
                outputs = result.get('result', {}).get('artifacts', [])
                descriptor = None
                if outputs:
                    files = client.get(f'/api/projects/{project}/skill-files').json()
                    descriptor = next(item for item in files if item['id'] == outputs[0]['artifact_id'])
                    downloaded = client.get(descriptor['url'])
                    assert downloaded.status_code == 200
                    assert hashlib.sha256(downloaded.content).hexdigest() == descriptor['sha256']
                return (structured or {}).get('output'), descriptor
            csv_output, csv_file = run('csv-import-export', {'operation': 'csv', 'table': ORDERS, 'delimiter': ';'}, 'data-live-csv')
            assert csv_file['mime'] == 'text/csv'
            read, _ = run('csv-import-export', {'operation': 'csv', 'source_format': 'csv', 'delimiter': ';'}, 'data-live-read', csv_file)
            assert read['table']['rows'][1][2] == '0' and read['table']['rows'][3][2] == ''
            clean, clean_file = run('spreadsheet-cleaning', {'operation': 'clean', 'source_format': 'csv', 'delimiter': ';', 'cleaning': [{'operation': 'empty_to_null', 'column': 'amount'}, {'operation': 'parse_number', 'column': 'amount'}], 'format': 'xlsx'}, 'data-live-clean', csv_file)
            assert clean['table']['rows'] == ORDERS['rows']
            stats, _ = run('descriptive-statistics', {'operation': 'stats', 'columns': ['amount'], 'record_unit': 'one order'}, 'data-live-stats', clean_file)
            actual = stats['summary']['statistics']['amount']
            assert {k: actual[k] for k in ('count', 'numeric_count', 'missing', 'sum', 'mean', 'median')} == {'count': 4, 'numeric_count': 3, 'missing': 1, 'sum': '30', 'mean': '10', 'median': '10'}
            join_spec = {'left_keys': ['customer_id'], 'right_keys': ['customer_id'], 'how': 'left', 'cardinality': 'm:1'}
            joined, joined_file = run('table-joins', {'operation': 'join', 'right': CUSTOMERS, 'join': join_spec, 'format': 'xlsx'}, 'data-live-join', clean_file)
            assert len(joined['table']['rows']) == 4 and joined['summary']['join']['unmatched_left'] == 1
            duplicate = deepcopy(CUSTOMERS); duplicate['rows'].append(['C1', 'alternate'])
            run('table-joins', {'operation': 'join', 'right': duplicate, 'join': join_spec, 'format': 'xlsx'}, 'data-live-join-reject', clean_file, expected='failed')
            aggregate, aggregate_file = run('pivot-analysis', {'operation': 'aggregate', 'aggregate': {'group_by': ['region'], 'operation': 'sum', 'value_column': 'amount', 'record_unit': 'one order'}, 'format': 'xlsx'}, 'data-live-aggregate', joined_file)
            assert aggregate['table']['rows'] == [['north', 10], ['south', 20], [None, None]]
            assert 'not an Excel PivotTable' in aggregate['summary']['aggregation']['kind']
            chart, chart_file = run('spreadsheet-charts', {'operation': 'chart', 'chart': {'kind': 'bar', 'category': 'region', 'values': ['sum'], 'title': 'إجمالي الطلبات', 'unit': 'وحدة'}}, 'data-live-chart', aggregate_file)
            with ZipFile(BytesIO(client.get(chart_file['url']).content)) as package:
                chart_xml = ET.fromstring(package.read('xl/charts/chart1.xml'))
                assert chart_xml.findtext('.//c:numRef/c:f', namespaces=NS) == "'Data'!$B$2:$B$4"
            edited, edited_file = run('formula-debugging', {'operation': 'formulas', 'format': 'xlsx', 'edits': [{'cell': 'B2', 'expected': 10, 'value': 15}]}, 'data-live-chart-edit', chart_file)
            raw = client.get(edited_file['url']).content
            with ZipFile(BytesIO(raw)) as package:
                chart_xml = ET.fromstring(package.read('xl/charts/chart1.xml'))
                assert chart_xml.findtext('.//c:numCache/c:pt/c:v', namespaces=NS) == '15'
            (OUT / 'api-chart-edited.xlsx').write_bytes(raw)
            forbidden = client.post(f'/api/projects/{other}/skill-runs', json={'request': 'Read data', 'requestedSkills': ['spreadsheet-reading'], 'inputs': {'dataOperation': {'operation': 'read', 'sourceId': chart_file['id']}, 'sourceRefs': [{k: chart_file[k] for k in ('kind', 'id', 'version', 'sha256')}]}, 'idempotencyKey': 'data-cross-project'})
            assert forbidden.status_code == 404
            assert client.get(f'/api/projects/{other}/skill-files/{chart_file["id"]}/download').status_code == 404
            report.update(passed=True, reference_statistics=actual, linked_chart_edit_preserved=True, cross_project_rejected=True, repeated_posts_reused_original_ids=True, output_sha256=hashlib.sha256(raw).hexdigest(), native_excel_tested=False)
    except BaseException as exc:
        report.update(passed=False, error_type=type(exc).__name__)
        raise
    finally:
        report['seconds'] = round(time.monotonic()-started, 3)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'api-live.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
