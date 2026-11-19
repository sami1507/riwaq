"""Explicit calculator mode: real fixed-worker proofs and separately labelled fault simulations.

All persistence uses tmp_path. No provider, source retrieval, keyring or model call is
allowed in calculator tests. Fault simulations do not establish real worker accuracy.
"""
import asyncio
import json
import re
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from backend import app as service, context_lineage, reasoning
from backend.db import get_db, init_db, now, uid


@pytest.fixture
def no_model_or_context(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Calculator called a model, secret store, retrieval, or context builder')
    for name in ('validate_provider', 'stream', 'secret'):
        monkeypatch.setattr(service.providers, name, forbidden)
    monkeypatch.setattr(service.knowledge, 'retrieve', forbidden)
    monkeypatch.setattr(service.public_knowledge, 'retrieve_public', forbidden)
    monkeypatch.setattr(service.facts, 'context_for_ids', forbidden)
    monkeypatch.setattr(service.grounding, 'compose', forbidden)


@pytest.fixture
def client(tmp_path, monkeypatch, no_model_or_context):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    service.ACTIVE.clear()
    with TestClient(service.app, headers={'X-Riwaq-Client': 'local'}) as value:
        from backend.local_auth import issue_pairing_code
        assert value.post('/api/auth/login', json={'pairing_code': issue_pairing_code()}).status_code == 200
        yield value
    assert not service.ACTIVE


def chat(client):
    pid = client.get('/api/projects').json()[0]['id']
    cid = client.post('/api/conversations', json={'project_id': pid}).json()['id']
    return pid, cid


def saved(client, cid):
    return client.get(f'/api/conversations/{cid}').json()['messages']


def counts():
    with get_db() as conn:
        return {table: conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                for table in ('conversations', 'messages', 'reasoning_runs',
                              'message_retrieval_options', 'events')}


def packets(response):
    assert response.status_code == 200, response.text
    rows = [json.loads(line) for line in response.text.splitlines() if line]
    assert rows[0]['type'] == 'start' and rows[-1]['type'] == 'done', rows
    assert not any(row['type'] == 'error' for row in rows)
    return rows


def calculate(client, cid, expression, **options):
    return packets(client.post(f'/api/conversations/{cid}/generate', json={
        'content': expression, 'answer_mode': 'calculator', **options}))


def assert_receipt(answer, exact, language='ar'):
    assert answer['status'] == 'complete'
    assert answer['model'] == 'riwaq-calculator-v1'
    assert answer['citations'] == []
    run = answer['run']
    assert run['policy_version'] == 'calculator-v1' and run['status'] == 'complete'
    assert run['model_calls'] == 0 and run['tool_calls'] == 1
    assert run['reserved_tokens'] == run['actual_input_tokens'] == run['actual_output_tokens'] == 0
    assert not run.get('fact_bindings')
    for options in (answer['retrieval_options'], run['request_options']):
        assert options['answer_mode'] == 'calculator'
        assert options.get('result_language', 'ar') == language
        assert not options['use_files'] and not options['use_knowledge']
    checks = [item for item in run['checks'] if item['method'] == 'deterministic' and item['status'] == 'pass']
    assert checks, run
    result = next(json.loads(item['details']) for item in checks if item['kind'] == 'calculate')
    assert result['exact'] == exact
    assert result['process_limited'] and result['memory_limit_mb'] == 256
    displayed = result['decimal'] if result['decimal_exact'] else result['exact']
    assert displayed in answer['content'], answer['content']
    assert all(stage['status'] != 'running' for stage in run['stages'])


@pytest.mark.parametrize('expression,exact,decimal,language', [
    ('0.1+0.2', '3/10', '0.3', 'ar'),
    ('1/3', '1/3', '0.333', 'en'),
    ('\u0661\u0662\u066b\u0665 \xd7 \u0662', '25', '25', 'ar'),
    ('(19.95*3)*1.17-10', '120049/2000', '60.0245', 'en'),
])
def test_real_worker_exact_result_stream_and_persistence(client, expression, exact, decimal, language):
    pid, cid = chat(client)
    # Private instructions and memory must not enter this deterministic operation.
    with get_db() as conn:
        conn.execute('UPDATE projects SET instructions=? WHERE id=?', ('PRIVATE_CONTEXT_SENTINEL', pid))
        conn.execute('INSERT INTO memories VALUES(?,?,?,?,?)', (uid(), 'project', pid, 'PRIVATE_MEMORY_SENTINEL', now()))
    rows = calculate(client, cid, expression, result_language=language)
    answer = rows[-1]['message']
    assert_receipt(answer, exact, language)
    result = next(json.loads(item['details']) for item in answer['run']['checks'] if item['kind'] == 'calculate')
    assert decimal in result['decimal']
    assert 'PRIVATE_' not in json.dumps(rows)
    assert ''.join(row.get('content', '') for row in rows if row['type'] == 'delta') == answer['content']
    history = saved(client, cid)
    assert [item['role'] for item in history] == ['user', 'assistant']
    assert history[-1] == answer and history[0]['content'] == expression
    assert history[0]['retrieval_options'] == answer['retrieval_options']
    if language == 'ar':
        assert re.search('[\u0600-\u06ff]', answer['content'])


@pytest.mark.parametrize('provider', ['ollama', 'openai', 'anthropic'])
def test_real_worker_ignores_missing_selected_model_and_cloud_provider(client, provider):
    _, cid = chat(client)
    result = client.patch('/api/settings', json={
        'mode': 'local' if provider == 'ollama' else 'hybrid',
        'provider': provider, 'model': 'deliberately-missing-model'})
    assert result.status_code == 200, result.text
    answer = calculate(client, cid, '7*6', provider=provider, model='also-missing')[-1]['message']
    assert_receipt(answer, '42')


@pytest.mark.parametrize('option', [
    {'use_files': True}, {'use_knowledge': True}, {'depth': 'quick'},
    {'calculation': {'expression': '2+2'}}, {'hypothesis': {'success_metric': 'metric'}},
    {'review': True}, {'fact_ids': ['foreign-fact']},
    {'output_contract': {'schema': {'type': 'object', 'properties': {},
                                   'required': [], 'additionalProperties': False}}},
])
def test_incompatible_options_rejected_before_checker_or_persistence(client, monkeypatch, option):
    _, cid = chat(client)
    def forbidden(*args, **kwargs):
        pytest.fail('Incompatible calculator request reached the worker')
    monkeypatch.setattr(reasoning, 'checker', forbidden)
    before = counts()
    response = client.post(f'/api/conversations/{cid}/generate', json={
        'content': '2+2', 'answer_mode': 'calculator', **option})
    assert response.status_code == 422, response.text
    assert counts() == before and not service.ACTIVE


@pytest.mark.parametrize('expression', [
    '\u0627\u062d\u0633\u0628 2+2', 'The invoice has 12 items and 3 fees', '1/0',
    '__import__("os")', '2**9', '(' * 17 + '1' + ')' * 17, '1+' * 260 + '1',
])
def test_real_invalid_expression_is_422_without_messages_or_fallback(client, expression):
    _, cid = chat(client)
    before = counts()
    response = client.post(f'/api/conversations/{cid}/generate', json={
        'content': expression, 'answer_mode': 'calculator'})
    assert response.status_code == 422, response.text
    assert response.json()['detail']
    assert counts() == before and not service.ACTIVE


def test_real_restart_retry_and_edit_inherit_mode_language_preserve_original(client):
    _, cid = chat(client)
    original = calculate(client, cid, '1/3', result_language='en')[-1]['message']
    history = saved(client, cid)
    init_db()  # Same SQLite file reopened through startup recovery, no in-memory transcript.
    assert saved(client, cid) == history
    retry = packets(client.post(f'/api/conversations/{cid}/generate', json={'retry_message_id': original['id']}))
    retry_cid = retry[0]['conversation_id']
    assert retry_cid != cid and saved(client, cid) == history
    assert_receipt(retry[-1]['message'], '1/3', 'en')
    edited = packets(client.post(f'/api/conversations/{cid}/generate', json={
        'edit_message_id': history[0]['id'], 'content': '2/3'}))
    assert edited[0]['conversation_id'] != cid
    assert_receipt(edited[-1]['message'], '2/3', 'en')
    assert saved(client, cid) == history
    assert saved(client, edited[0]['conversation_id'])[0]['content'] == '2/3'
    override = packets(client.post(f'/api/conversations/{retry_cid}/generate', json={
        'retry_message_id': retry[-1]['message']['id'], 'result_language': 'ar'}))
    assert_receipt(override[-1]['message'], '1/3', 'ar')


def test_real_snapshot_restores_preferences_but_does_not_claim_reexecution(client):
    pid, cid = chat(client)
    original = calculate(client, cid, '6/7', result_language='en')[-1]['message']
    snapshot = client.get(f'/api/projects/{pid}/snapshot')
    assert snapshot.status_code == 200, snapshot.text
    response = client.post('/api/projects/import', json={'snapshot': snapshot.json(), 'confirm': True})
    assert response.status_code == 201, response.text
    restored_cid = client.get('/api/conversations', params={'project_id': response.json()['id']}).json()[0]['id']
    restored = saved(client, restored_cid)
    assert restored[-1]['run']['origin'] == 'imported_snapshot'
    assert restored[-1]['content'] == original['content']
    assert restored[-1]['retrieval_options'] == original['retrieval_options']
    with get_db() as conn:
        assert context_lineage.read(conn, restored[-1]['id']) == [context_lineage.BLOCKED]
    retry = packets(client.post(f'/api/conversations/{restored_cid}/generate', json={
        'retry_message_id': restored[-1]['id']}))[-1]['message']
    assert_receipt(retry, '6/7', 'en')
    assert retry['run']['origin'] == 'local_run' and retry['run']['id'] != original['run']['id']


def test_invalid_edit_retry_targets_fail_before_worker_and_no_branch(client, monkeypatch):
    _, cid = chat(client)
    answer = calculate(client, cid, '2+2')[-1]['message']
    history = saved(client, cid)
    other_pid = client.post('/api/projects', json={'name': 'Other project'}).json()['id']
    other_cid = client.post('/api/conversations', json={'project_id': other_pid}).json()['id']
    def forbidden(*args, **kwargs):
        pytest.fail('Unauthorized or wrong-role branch reached checker')
    monkeypatch.setattr(reasoning, 'checker', forbidden)
    before = counts()
    requests = [
        (cid, {'edit_message_id': answer['id'], 'content': '9'}, 400),
        (cid, {'retry_message_id': history[0]['id']}, 400),
        (other_cid, {'retry_message_id': answer['id']}, 404),
        (other_cid, {'edit_message_id': history[0]['id'], 'content': '9'}, 404),
    ]
    for target, body, status in requests:
        response = client.post(f'/api/conversations/{target}/generate', json={'answer_mode': 'calculator', **body})
        assert response.status_code == status, response.text
        assert counts() == before and not service.ACTIVE


@pytest.mark.parametrize('fault,status', [('timeout', 504), ('unavailable', 503)])
def test_simulated_worker_failure_is_atomic_no_fallback(client, monkeypatch, fault, status):
    _, cid = chat(client)
    original = calculate(client, cid, '3+4')[-1]['message']
    called = []
    async def failed(tool, value, **kwargs):
        called.append((tool, value))
        if fault == 'timeout':
            raise TimeoutError('simulated fixed-worker deadline')
        return {'status': 'unavailable', 'error': 'simulated unavailable fixed worker'}
    monkeypatch.setattr(reasoning, 'checker', failed)
    before = counts()
    response = client.post(f'/api/conversations/{cid}/generate', json={'retry_message_id': original['id']})
    assert response.status_code == status, response.text
    assert response.json()['detail'] and called == [('calculate', '3+4')]
    assert counts() == before and not service.ACTIVE


@pytest.mark.asyncio
async def test_simulated_preparation_cancel_cleans_worker_no_messages(tmp_path, monkeypatch, no_model_or_context):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    service.ACTIVE.clear()
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def blocked(tool, value, **kwargs):
        assert tool == 'calculate' and value == '9*9'
        entered.set()
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()
    monkeypatch.setattr(reasoning, 'checker', blocked)
    async with service.lifespan(service.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),
                base_url='http://127.0.0.1:8765', headers={'X-Riwaq-Client': 'local'}) as client:
            from backend.local_auth import issue_pairing_code
            assert (await client.post('/api/auth/login',json={'pairing_code':issue_pairing_code()})).status_code == 200
            pid = (await client.get('/api/projects')).json()[0]['id']
            cid = (await client.post('/api/conversations', json={'project_id': pid})).json()['id']
            before = counts()
            task = asyncio.create_task(client.post(f'/api/conversations/{cid}/generate', json={
                'answer_mode': 'calculator', 'content': '9*9'}))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                busy = await client.post(f'/api/conversations/{cid}/generate', json={'answer_mode': 'calculator', 'content': '1'})
                assert busy.status_code == 409
                assert (await client.post(f'/api/conversations/{cid}/stop')).json()['stopped']
                response = await asyncio.wait_for(task, 2)
                assert response.status_code == 499, response.text
                assert cancelled.is_set() and counts() == before and not service.ACTIVE
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)


def test_legacy_receipt_preferences_keep_existing_serialized_shape():
    assert reasoning.RetrievalOptions().model_dump() == {'use_files': False, 'use_knowledge': False}
    assert reasoning.RequestOptions().model_dump() == {
        'use_files': False, 'use_knowledge': False, 'calculation': None, 'review': False}



def test_real_checker_expired_preparation_deadline_never_saves(client, monkeypatch):
    _, cid = chat(client)
    actual_checker = reasoning.checker
    calls = []

    async def expire_after_calculation(tool, value, **kwargs):
        result = await actual_checker(tool, value, **kwargs)
        assert result['status'] == 'ok' and result['result']['exact'] == '42'
        calls.append((tool, value))
        service.ACTIVE[cid]['deadline'] = time.monotonic() - 1
        return result

    monkeypatch.setattr(reasoning, 'checker', expire_after_calculation)
    before = counts()
    response = client.post(f'/api/conversations/{cid}/generate', json={
        'answer_mode': 'calculator', 'content': '6*7'})
    assert response.status_code == 504, response.text
    assert response.json()['detail']
    assert calls == [('calculate', '6*7')]
    assert counts() == before and saved(client, cid) == []
    assert not service.ACTIVE


@pytest.mark.asyncio
async def test_real_result_not_emitted_after_stream_deadline(client):
    _, cid = chat(client)

    class ConnectedRequest:
        async def is_disconnected(self):
            return False

    from tests.authority_fixture import fixture_owner
    with fixture_owner():
        response = await service.generate(cid, service.GenerateInput(
            answer_mode='calculator', content='6*7'), ConnectedRequest())
        try:
            first = json.loads(await anext(response.body_iterator))
            assert first['type'] == 'start'
            assert first['run']['model_calls'] == 0 and first['run']['tool_calls'] == 1
            computed = next(json.loads(check['details']) for check in first['run']['checks']
                            if check['kind'] == 'calculate')
            assert computed['exact'] == '42' and computed['process_limited']
            service.ACTIVE[cid]['deadline'] = time.monotonic() - 1
            tail = [json.loads(packet) async for packet in response.body_iterator]
            assert tail[-1]['type'] == 'done'
            assert any(packet['type'] == 'error' for packet in tail)
            assert not any(packet['type'] == 'delta' for packet in tail)
            answer = tail[-1]['message']
            assert answer['status'] == answer['run']['status'] == 'interrupted'
            assert answer['content'] == '' and answer['run']['model_calls'] == 0
            assert answer['run']['tool_calls'] == 1
            assert saved(client, cid)[-1] == answer
            assert not service.ACTIVE
        finally:
            # Direct iterator consumption does not call StreamingResponse.__call__.
            # Run its normal finalizer explicitly even when an assertion fails.
            await response.finalizer()
        assert not service.ACTIVE
