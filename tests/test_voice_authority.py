"""Authenticated API and in-flight revocation; speech providers are not exercised."""
import asyncio
import json
import time
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from backend import app, voice, local_auth, providers
from backend.db import get_db


@pytest.fixture
def scoped_voice(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(voice, 'SESSIONS', {})
    monkeypatch.setattr(voice, 'ACTIVE', {})
    monkeypatch.setattr(voice, 'ENDED', {})
    monkeypatch.setattr(voice, 'VOICE_CACHE', {'voices': [], 'error': None, 'at': time.monotonic()})
    with TestClient(app.app, headers={'X-Riwaq-Client': 'local'}) as client:
        assert client.post('/api/auth/login', json={'pairing_code': local_auth.issue_pairing_code()}).status_code == 200
        pid = client.post('/api/projects', json={'name': 'Synthetic voice authorization'}).json()['id']
        base = f'/api/projects/{pid}/voice'
        made = client.post(base + '/sessions', json={'mode': 'transcription'})
        assert made.status_code == 201
        session = made.json()
        headers = {'X-Riwaq-Voice-Token': session['token']}
        path = base + '/sessions/' + session['id']
        created = client.post(path + '/turns', headers=headers,
            json={'text': 'SYNTHETIC_PRIVATE_VOICE', 'turn_key': 'scope-fixture-turn', 'expected_epoch': 0})
        assert created.status_code == 202
        deadline = time.monotonic() + 2
        while client.get(path, headers=headers).json()['active'] and time.monotonic() < deadline:
            time.sleep(.01)
        with get_db() as conn:
            row = conn.execute('SELECT id,owner_id FROM local_auth_sessions WHERE revoked_at IS NULL').fetchone()
            principal = {'owner_id': row['owner_id'], 'session_id': row['id']}
        yield client, pid, base, path, headers, session, principal


def test_revoked_project_blocks_voice_read_write_and_save(scoped_voice):
    client, pid, base, path, headers, session, _ = scoped_voice
    with get_db() as conn:
        conn.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?', ('other-owner', pid))
    observed = {'read': client.get(path, headers=headers).status_code,
        'start': client.post(base + '/sessions', json={}).status_code,
        'save': client.post(path + '/save', headers=headers, json={'confirm': True}).status_code,
        'update': client.patch(path, headers=headers, json={}).status_code,
        'submit': client.post(path + '/turns', headers=headers, json={'text': 'no write', 'turn_key': 'denied-new-turn', 'expected_epoch': 1}).status_code}
    out = Path('reports/closure-20261116/voice-authority'); out.mkdir(parents=True, exist_ok=True)
    passed = all(code in (401, 403, 404) for code in observed.values())
    (out / ('api-after.json' if passed else 'api-before.json')).write_text(json.dumps(observed, indent=2))
    assert passed, observed
    with get_db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM voice_saved_turns').fetchone()[0] == 0


def test_stale_server_session_cannot_publish_or_read(scoped_voice):
    _, pid, _, path, headers, session, principal = scoped_voice
    token = local_auth._principal.set(principal)
    try:
        with get_db() as conn:
            conn.execute('UPDATE local_auth_sessions SET revoked_at=? WHERE id=?', (time.time(), principal['session_id']))
        with pytest.raises(voice.HTTPException) as caught:
            voice.session(pid, session['id'], session['token'])
        assert caught.value.status_code in (401, 403, 404)
    finally:
        local_auth._principal.reset(token)


@pytest.mark.parametrize('scope', ['project', 'session'])
def test_idle_model_stream_is_stopped_after_revocation(scoped_voice, monkeypatch, scope):
    client, pid, _, path, headers, session, principal = scoped_voice
    entered, cleaned = threading.Event(), threading.Event()
    async def validate(*args):
        return {}
    async def stream(*args):
        try:
            entered.set()
            await asyncio.sleep(15)
            yield {'content': 'LATE_PRIVATE_RESULT'}
        finally:
            cleaned.set()
    monkeypatch.setattr(providers, 'validate_provider', validate)
    monkeypatch.setattr(providers, 'stream', stream)
    update = client.patch(path, headers=headers, json={'mode': 'conversation'})
    assert update.status_code == 200
    posted = client.post(path + '/turns', headers=headers,
        json={'text': 'Synthetic request', 'turn_key': 'idle-scope-check', 'expected_epoch': update.json()['epoch']})
    assert posted.status_code == 202 and entered.wait(2)
    with get_db() as conn:
        if scope == 'project':
            conn.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?', ('other-owner', pid))
        else:
            conn.execute('UPDATE local_auth_sessions SET revoked_at=? WHERE id=?', (time.time(), principal['session_id']))
    assert cleaned.wait(2), 'Idle local model request was not cancelled promptly'
    deadline = time.monotonic() + 2
    while session['id'] in voice.ACTIVE and time.monotonic() < deadline:
        time.sleep(.01)
    assert session['id'] not in voice.ACTIVE and session['id'] not in voice.SESSIONS
    assert client.get(path, headers=headers).status_code in (401, 403, 404)


@pytest.mark.asyncio
async def test_speech_revocation_waits_for_owned_worker_cleanup(scoped_voice):
    _, pid, _, _, _, session, principal = scoped_voice
    token = local_auth._principal.set(principal)
    entered, cleaned = threading.Event(), threading.Event()
    try:
        private = voice.session(pid, session['id'], session['token'])
        state = voice.begin(private, private['epoch'])
        def operation():
            entered.set()
            assert state['stop'].wait(3)
            time.sleep(.05)
            cleaned.set()
            return 'worker stopped'
        task = asyncio.create_task(voice.bounded_speech(private, state, operation))
        await asyncio.to_thread(entered.wait, 2)
        with get_db() as conn:
            conn.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?', ('other-owner', pid))
        assert await asyncio.wait_for(task, 2) == 'worker stopped'
        assert cleaned.is_set() and state['stop'].is_set()
        voice.ACTIVE.pop(private['id'], None)
    finally:
        local_auth._principal.reset(token)
