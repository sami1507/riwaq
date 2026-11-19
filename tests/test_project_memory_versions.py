"""Synthetic temporary data. Real SQLite/API/auth; provider fake only in race test."""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend import app as service, context_lineage, db, local_auth, providers, voice


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY', str(tmp_path / 'absent.json'))
    with TestClient(service.app, headers={'X-Riwaq-Client': 'local'}) as client:
        assert client.post('/api/auth/login', json={'pairing_code': local_auth.issue_pairing_code()}).status_code == 200
        yield client


def project(client):
    return client.get('/api/projects').json()[0]['id']


def create(client, pid, text='تقنية المشروع Python — גרסה 2'):
    response = client.post('/api/memories', json={'content': text, 'scope': 'project', 'project_id': pid})
    assert response.status_code == 200, response.text
    return response.json()


def correction(client, item, text, version=None):
    return client.patch('/api/memories/' + item['id'], json={
        'content': text, 'scope': 'project', 'project_id': item['project_id'],
        'expected_version': version if version is not None else item['version'],
    })


def test_version_survives_separate_process_read_and_correction(client):
    pid = project(client); item = create(client, pid)
    assert item['version'] == 1
    result = correction(client, item, 'تقنية المشروع TypeScript — גרסה 3')
    assert result.status_code == 200 and result.json()['version'] == 2
    # A fresh Python process reads the same synthetic DB; no API or model mock.
    code = "import json;from backend.db import get_db;\nwith get_db() as c: print(json.dumps(dict(c.execute('SELECT m.content,r.version FROM memories m JOIN memory_revisions r ON r.memory_id=m.id WHERE m.id=?',(\"" + item['id'] + "\",)).fetchone()),ensure_ascii=True))"
    reopened = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', code], env=os.environ.copy(), capture_output=True, text=True, timeout=15, check=True)
    assert json.loads(reopened.stdout) == {'content': result.json()['content'], 'version': 2}
    assert correction(client, item, 'stale').status_code == 409
    assert client.delete('/api/memories/' + item['id'] + '?expected_version=1').status_code == 409
    assert client.delete('/api/memories/' + item['id'] + '?expected_version=2').status_code == 200
    assert client.get('/api/memories?project_id=' + pid).json() == []
    assert correction(client, result.json(), 'cannot resurrect').status_code == 404
    with db.get_db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM memory_revisions').fetchone()[0] == 0


def test_missing_revision_and_moving_project_are_rejected(client):
    pid = project(client); item = create(client, pid)
    other = client.post('/api/projects', json={'name': 'same name'}).json()['id']
    assert client.patch('/api/memories/' + item['id'], json={'content': 'changed', 'scope': 'project', 'project_id': pid}).status_code == 422
    assert client.patch('/api/memories/' + item['id'], json={'content': 'changed', 'scope': 'project', 'project_id': other, 'expected_version': 1}).status_code == 409
    assert client.delete('/api/memories/' + item['id']).status_code == 422
    assert client.get('/api/memories?project_id=' + other).json() == []


def test_concurrent_corrections_have_one_winner(client):
    item = create(client, project(client))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda value: correction(client, item, value), ['new A', 'new B']))
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(r.json() for r in results if r.status_code == 200)
    saved = client.get('/api/memories?project_id=' + item['project_id']).json()[0]
    assert saved['content'] == winner['content'] and saved['version'] == 2


def test_foreign_project_and_revocation_reject_all_memory_routes(client):
    pid = project(client); item = create(client, pid)
    with db.get_db() as conn:
        conn.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?', ('synthetic_other_owner', pid))
    responses = [client.get('/api/memories?project_id=' + pid),
                 client.post('/api/memories', json={'content': 'new', 'scope': 'project', 'project_id': pid}),
                 correction(client, item, 'changed'),
                 client.delete('/api/memories/' + item['id'] + '?expected_version=1')]
    assert [response.status_code for response in responses] == [404] * 4
    assert all(item['content'] not in response.text for response in responses)
    with db.get_db() as conn:
        assert conn.execute('SELECT content FROM memories WHERE id=?', (item['id'],)).fetchone()[0] == item['content']


def test_personal_gate_and_no_secrets_in_audit(client):
    pid = project(client)
    assert client.post('/api/memories', json={'content': 'Preference'}).status_code == 423
    marker = 'SYNTHETIC_MEMORY_NOT_FOR_LOGS'
    item = create(client, pid, marker)
    assert correction(client, item, 'password="SYNTHETIC_NOT_A_REAL_SECRET_123"').status_code == 422
    assert client.post('/api/memories', json={'content': '   ', 'scope': 'project', 'project_id': pid}).status_code == 422
    with db.get_db() as conn:
        rows = [dict(row) for row in conn.execute('SELECT * FROM guard_audit')]
        assert marker not in json.dumps(rows)
        assert any(row['operation'] == 'project_memory_create' for row in rows)
        conn.execute("INSERT INTO memories VALUES('legacy-personal','preference',NULL,'synthetic old preference',?)", (db.now(),))
    assert client.patch('/api/memories/legacy-personal', json={'content': 'x'}).status_code == 423
    assert client.delete('/api/memories/legacy-personal').status_code == 200


def test_memory_bindings_drop_old_history_and_voice_after_change(client):
    pid = project(client); item = create(client, pid)
    cid = client.post('/api/conversations', json={'project_id': pid}).json()['id']
    with db.get_db() as conn:
        links = context_lineage.memory_links(conn, pid, [item])
        conn.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)', ('memory-answer', cid, 'assistant', 'old derived answer', 'complete', 'fixture', db.now(), '[]'))
        context_lineage.save(conn, 'memory-answer', links)
        # Historical pre-versioning answer also must not return to model context.
        conn.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)', ('legacy-answer', cid, 'assistant', 'legacy derived answer', 'complete', 'fixture', db.now(), '[]'))
        context_lineage.save(conn, 'legacy-answer', [])
        assert context_lineage.authorized(conn, pid, links)
    assert correction(client, item, 'corrected').status_code == 200
    with db.get_db() as conn:
        assert not context_lineage.authorized(conn, pid, links)
        history = [dict(row) for row in conn.execute('SELECT * FROM messages WHERE conversation_id=?', (cid,))]
        assert context_lineage.filter_history(conn, pid, history)[0] == []
        assert conn.execute('SELECT content FROM messages WHERE id=?', ('memory-answer',)).fetchone()[0] == 'old derived answer'
    turn = {'_links': links, 'reply': 'old', 'spoken_text': 'old', 'citations': [], 'audio_ready': True, '_audio': b'synthetic', 'epoch': 1}
    assert not voice.check_links({'id': 'not-live', 'project_id': pid}, turn)
    assert turn['state'] == 'revoked' and turn['reply'] == '' and turn['_audio'] == b''


def test_stale_generation_cannot_save_answer_after_memory_correction(client, monkeypatch):
    pid = project(client); item = create(client, pid, 'old project fact')
    cid = client.post('/api/conversations', json={'project_id': pid}).json()['id']
    captured = []
    async def validate(*args):
        pass
    async def stream(provider, model, messages, cfg):
        captured.extend(messages)
        yield {'content': 'based on old project fact'}
        yield {'finish_reason': 'stop'}
        # Changes just before iterator completion exercise the final-save race,
        # not only the per-packet guard. Provider itself is a controlled fake.
        with db.get_db() as conn:
            conn.execute('UPDATE memories SET content=? WHERE id=?', ('corrected project fact', item['id']))
    monkeypatch.setattr(providers, 'validate_provider', validate)
    monkeypatch.setattr(providers, 'stream', stream)
    response = client.post('/api/conversations/' + cid + '/generate', json={'content': 'What is the project fact?'})
    assert response.status_code == 200
    assert 'old project fact' in json.dumps(captured)
    messages = client.get('/api/conversations/' + cid).json()['messages']
    assert messages[-1]['status'] == 'error' and messages[-1]['content'] == ''


def test_revision_migration_is_idempotent_and_keeps_legacy_columns(client):
    item = create(client, project(client))
    assert correction(client, item, 'v2').status_code == 200
    db.init_db()
    with db.get_db() as conn:
        assert len(list(conn.execute('PRAGMA table_info(memories)'))) == 5
        assert conn.execute('SELECT version FROM memory_revisions WHERE memory_id=?', (item['id'],)).fetchone()[0] == 2
        # Existing snapshot's positional five-column insert remains supported.
        conn.execute('INSERT INTO memories VALUES(?,?,?,?,?)', ('snapshot-memory', 'project', item['project_id'], 'restored synthetic', db.now()))
        assert conn.execute('SELECT version FROM memory_revisions WHERE memory_id=?', ('snapshot-memory',)).fetchone()[0] == 1
