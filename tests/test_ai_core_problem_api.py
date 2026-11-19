"""Actual fixed subprocess through authenticated HTTP; no model or provider calls.

Corrupted-receipt cases are explicitly simulated. All storage uses tmp_path.
"""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from backend import app as service, reasoning
from backend.db import get_db, init_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    def forbidden(*args, **kwargs):
        pytest.fail('Fixed math problem dispatched model, retrieval, context or secrets')
    for name in ('validate_provider', 'stream', 'secret'):
        monkeypatch.setattr(service.providers, name, forbidden)
    monkeypatch.setattr(service.knowledge, 'retrieve', forbidden)
    monkeypatch.setattr(service.public_knowledge, 'retrieve_public', forbidden)
    monkeypatch.setattr(service.facts, 'context_for_ids', forbidden)
    monkeypatch.setattr(service.grounding, 'compose', forbidden)
    service.ACTIVE.clear()
    with TestClient(service.app, headers={'X-Riwaq-Client': 'local'}) as value:
        from backend.local_auth import issue_pairing_code
        assert value.post('/api/auth/login', json={'pairing_code': issue_pairing_code()}).status_code == 200
        yield value
    assert not service.ACTIVE


def conversation(client):
    pid = client.get('/api/projects').json()[0]['id']
    return client.post('/api/conversations', json={'project_id': pid}).json()['id']


def generate(client, cid, text, **options):
    return client.post(f'/api/conversations/{cid}/generate', json={
        'answer_mode': 'problem', 'content': text, **options})


def packets(response):
    assert response.status_code == 200, response.text
    rows = [json.loads(line) for line in response.text.splitlines() if line]
    assert rows[0]['type'] == 'start' and rows[-1]['type'] == 'done', rows
    assert not any(row['type'] == 'error' for row in rows)
    return rows


def receipt(answer, expected, language='ar', status='ok'):
    assert answer['status'] == 'complete' and answer['model'] == 'riwaq-problem-v1'
    assert answer['citations'] == []
    run = answer['run']
    assert run['policy_version'] == 'problem-v1' and run['status'] == 'complete'
    assert run['model_calls'] == 0 and run['tool_calls'] == 1
    assert run['actual_input_tokens'] == run['actual_output_tokens'] == run['reserved_tokens'] == 0
    assert all(stage['status'] != 'running' for stage in run['stages'])
    for options in (answer['retrieval_options'], run['request_options']):
        assert options['answer_mode'] == 'problem' and options['result_language'] == language
        assert not options['use_files'] and not options['use_knowledge']
    check = next(item for item in run['checks'] if item['kind'] == 'solve_problem')
    assert check['status'] == ('pass' if status == 'ok' else 'review_needed')
    data = json.loads(check['details'])
    assert data['process_limited'] and data['memory_limit_mb'] == 256
    assert data['status'] == status
    assert {item['id']: item['result']['exact'] for item in data['results'] if item['status'] == 'ok'} == expected
    for item in data['results']:
        assert item['expression'] in answer['content']
    return data


@pytest.mark.parametrize('text,expected,language', [
    ('100 ترتفع20% ثم تنخفض20% من الجديدة', {'after_increase': '120', 'final_value': '96'}, 'ar'),
    ('١٢٠٠ متر + ٠٫٨ كيلومتر', {'total_length': '2000'}, 'ar'),
    ('10% to 15%; percentage points and relative change', {'percentage_points': '5', 'relative_change': '50'}, 'en'),
    ('سعر50 إلى75', {'absolute_change': '25', 'relative_change': '50'}, 'ar'),
], ids=['AI-07-T01', 'AI-07-T02', 'AI-07-T03', 'AI-07-T05'])
def test_actual_http_problem_result_and_saved_receipt(client, text, expected, language):
    cid = conversation(client)
    rows = packets(generate(client, cid, text, result_language=language,
        provider='openai', model='not-configured'))
    answer = rows[-1]['message']
    receipt(answer, expected, language)
    assert ''.join(row.get('content', '') for row in rows if row['type'] == 'delta') == answer['content']
    history = client.get(f'/api/conversations/{cid}').json()['messages']
    assert history[-1] == answer and history[0]['content'] == text
    assert history[0]['retrieval_options'] == answer['retrieval_options']
    with get_db() as conn:
        for table in ('mail_local_effects', 'productivity_operations', 'skill_folder_operations'):
            assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0


@pytest.mark.parametrize('text,expected,status', [('10/0', {}, 'error'), ('0% to 15%', {'percentage_points': '15'}, 'partial')], ids=['AI-07-T04', 'zero-percentage-base'])
def test_actual_undefined_quantity_has_no_numeric_replacement(client, text, expected, status):
    cid = conversation(client)
    answer = packets(generate(client, cid, text))[-1]['message']
    data = receipt(answer, expected, status=status)
    failures = [item for item in data['results'] if item['status'] == 'error']
    assert failures and all('result' not in item and item['error_code'] == 'undefined_division_by_zero' for item in failures)
    assert 'غير معرف' in answer['content'] and answer['run']['remaining']


@pytest.mark.parametrize('text', ['100 ترتفع20% ثم تنخفض20%', '1200 m + 0.8 kg',
    'price 50 to 75; send a private file', 'اسمي سامر ولدي 12 طلبًا، استنتج السعر'])
def test_actual_ambiguous_or_unsupported_problem_rejected_before_save(client, text):
    cid = conversation(client)
    response = generate(client, cid, text)
    assert response.status_code == 422, response.text
    assert 'Unsupported or ambiguous problem' in response.json()['detail']
    assert client.get(f'/api/conversations/{cid}').json()['messages'] == []
    assert not service.ACTIVE


@pytest.mark.parametrize('options', [{'use_files': True}, {'use_knowledge': True}, {'depth': 'quick'},
    {'review': True}, {'fact_ids': ['other-project']}, {'calculation': {'expression': '2+2'}}])
def test_model_and_source_options_do_not_expand_problem_scope(client, monkeypatch, options):
    cid = conversation(client)
    def forbidden(*args, **kwargs):
        pytest.fail('Rejected options reached checker')
    monkeypatch.setattr(reasoning, 'checker', forbidden)
    response = generate(client, cid, '1200 m + 0.8 km', **options)
    assert response.status_code == 422, response.text
    assert client.get(f'/api/conversations/{cid}').json()['messages'] == []


def test_problem_retry_and_edit_keep_original_and_use_saved_mode(client):
    cid = conversation(client)
    original = packets(generate(client, cid, 'price 50 to 75', result_language='en'))[-1]['message']
    history = client.get(f'/api/conversations/{cid}').json()['messages']
    init_db()
    rows = packets(client.post(f'/api/conversations/{cid}/generate', json={'retry_message_id': original['id']}))
    assert rows[0]['conversation_id'] != cid
    receipt(rows[-1]['message'], {'absolute_change': '25', 'relative_change': '50'}, 'en')
    rows = packets(client.post(f'/api/conversations/{cid}/generate', json={
        'edit_message_id': history[0]['id'], 'content': 'price 80 to 100'}))
    assert rows[0]['conversation_id'] != cid
    receipt(rows[-1]['message'], {'absolute_change': '20', 'relative_change': '25'}, 'en')
    assert client.get(f'/api/conversations/{cid}').json()['messages'] == history


def test_problem_snapshot_keeps_mode_and_labels_receipt_historical(client):
    cid = conversation(client)
    original = packets(generate(client, cid, '80 cm + 250 mm', result_language='en'))[-1]['message']
    project_id = client.get(f'/api/conversations/{cid}').json()['project_id']
    snapshot = client.get(f'/api/projects/{project_id}/snapshot').json()
    response = client.post('/api/projects/import', json={'snapshot': snapshot, 'confirm': True})
    assert response.status_code == 201, response.text
    restored_id = client.get('/api/conversations', params={'project_id': response.json()['id']}).json()[0]['id']
    restored = client.get(f'/api/conversations/{restored_id}').json()['messages'][-1]
    assert restored['content'] == original['content']
    assert restored['run']['origin'] == 'imported_snapshot'
    assert restored['retrieval_options']['answer_mode'] == 'problem'
    replayed = packets(client.post(f'/api/conversations/{restored_id}/generate', json={'retry_message_id': restored['id']}))[-1]['message']
    receipt(replayed, {'total_length': '21/20'}, 'en')
    assert replayed['run']['origin'] == 'local_run'


@pytest.mark.asyncio
async def test_packaged_checker_unavailable_is_explicit_without_fallback(monkeypatch):
    monkeypatch.setattr(reasoning.sys, 'frozen', True, raising=False)
    result = await reasoning.checker('solve_problem', '1200 m + 0.8 km')
    assert result['status'] == 'unavailable' and 'not packaged' in result['error']


@pytest.mark.parametrize('fault', ['input_hash', 'mapping', 'number_type', 'false_completion', 'unlimited_worker'])
def test_simulated_corrupt_worker_receipt_cannot_be_saved_as_success(client, monkeypatch, fault):
    cid = conversation(client)
    actual = reasoning.checker
    async def corrupted(tool, text, **options):
        checked = copy.deepcopy(await actual(tool, text, **options))
        assert checked['status'] == 'ok'
        data = checked['result']
        if fault == 'input_hash': data['input_sha256'] = '0' * 64
        elif fault == 'mapping': data['results'][0]['expression'] = '999'
        elif fault == 'number_type': data['results'][0]['result']['exact'] = 999
        elif fault == 'false_completion': data['status'] = 'partial'
        elif fault == 'unlimited_worker': data['process_limited'] = False
        return checked
    monkeypatch.setattr(reasoning, 'checker', corrupted)
    response = generate(client, cid, 'price 50 to 75')
    assert response.status_code == 503, response.text
    assert client.get(f'/api/conversations/{cid}').json()['messages'] == []
    assert not service.ACTIVE
