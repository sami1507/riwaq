"""CORE journeys that need no live model: real API + SQLite, labelled simulated stream.

The live counterparts run through scripts/core_acceptance.py against a
running service. These keep the core paths in the regular regression suite.
"""
import json

from backend import app as service
from test_research_chat import client  # noqa: F401  (paired TestClient on tmp data)


def fake_model(monkeypatch, reply='رد محاكى.'):
    calls = []

    async def model(provider, name, messages, cfg):
        calls.append(messages)
        yield {'content': reply}
        yield {'finish_reason': 'stop'}
    monkeypatch.setattr(service.providers, 'stream', model)
    return calls


def generate(client, chat, **body):
    response = client.post(f'/api/conversations/{chat}/generate', json=body)
    assert response.status_code == 200, response.text
    return [json.loads(line) for line in response.text.splitlines()]


def new_chat(client):
    project = client.get('/api/projects').json()[0]['id']
    return project, client.post('/api/conversations', json={'project_id': project}).json()['id']


def test_core_006_returning_to_local_restores_a_local_model(client):
    assert client.patch('/api/settings', json={'model': 'qwen3:4b-instruct'}).json()['model'] == 'qwen3:4b-instruct'
    hybrid = client.patch('/api/settings', json={'mode': 'hybrid', 'provider': 'openai', 'model': 'gpt-4.1-mini'})
    assert hybrid.status_code == 200 and hybrid.json()['model'] == 'gpt-4.1-mini'
    local = client.patch('/api/settings', json={'mode': 'local'}).json()
    assert local['provider'] == 'ollama' and local['model'] == 'qwen3:4b-instruct'
    # Internal bookkeeping never leaks into the user's settings.
    assert not any(key.startswith('_') for key in client.get('/api/settings').json())
    # An explicit model in the same request still wins.
    client.patch('/api/settings', json={'mode': 'hybrid', 'provider': 'openai', 'model': 'gpt-4.1-mini'})
    assert client.patch('/api/settings', json={'mode': 'local', 'model': 'qwen3-vl:2b'}).json()['model'] == 'qwen3-vl:2b'


def test_core_006_invalid_budget_is_rejected_with_reason(client):
    response = client.patch('/api/settings', json={'context_size': 2048, 'max_tokens': 2048})
    assert response.status_code == 422 and '768' in response.text


def test_core_010_edit_creates_branch_with_edited_context_and_keeps_original(client, monkeypatch):
    calls = fake_model(monkeypatch)
    _, chat = new_chat(client)
    generate(client, chat, content='اذكر لونًا واحدًا.')
    original = client.get(f'/api/conversations/{chat}').json()['messages']
    user = next(m for m in original if m['role'] == 'user')
    packets = generate(client, chat, content='اذكر حيوانًا واحدًا.', edit_message_id=user['id'])
    branch = packets[0]['conversation_id']
    assert branch != chat
    assert client.get(f'/api/conversations/{chat}').json()['messages'] == original
    assert client.get(f'/api/conversations/{branch}').json()['parent_id'] == chat
    sent = json.dumps(calls[-1], ensure_ascii=False)
    assert 'حيوانًا' in sent and 'لونًا' not in sent


def test_core_008_retry_branches_and_preserves_the_previous_answer(client, monkeypatch):
    fake_model(monkeypatch)
    _, chat = new_chat(client)
    generate(client, chat, content='سؤال أول')
    before = client.get(f'/api/conversations/{chat}').json()['messages']
    packets = generate(client, chat, retry_message_id=before[-1]['id'])
    branch = packets[0]['conversation_id']
    assert branch != chat and client.get(f'/api/conversations/{chat}').json()['messages'] == before
    users = [m for m in client.get(f'/api/conversations/{branch}').json()['messages'] if m['role'] == 'user']
    assert [u['content'] for u in users] == ['سؤال أول']


def test_core_017_rejected_uploads_leave_no_usable_file(client):
    project = client.get('/api/projects').json()[0]['id']
    before = client.get(f'/api/projects/{project}/files').json()
    bad = client.post(f'/api/projects/{project}/files', files={'file': ('tool.exe', b'MZ\x90\x00binary', 'application/octet-stream')})
    assert bad.status_code == 415 and 'Supported formats' in bad.text
    assert client.get(f'/api/projects/{project}/files').json() == before


def test_core_011_029_unavailable_web_search_is_explicit(client):
    status = client.get('/api/status').json()['capabilities']['web_search']
    assert status['available'] is False and status['reason']
    response = client.get('/api/search/web', params={'q': 'سعر الذهب اليوم'})
    assert response.status_code == 403 and 'results' not in response.text
