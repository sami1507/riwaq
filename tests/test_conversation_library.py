"""Authenticated HTTP + SQLite acceptance for organization, isolation and moves.

Fixtures are synthetic local records; no provider calls or production data.
"""
import json
import pytest
from fastapi.testclient import TestClient
from backend import app as service, db, local_auth, context_lineage, conversation_library


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    with TestClient(service.app, headers={'X-Riwaq-Client': 'local'}) as value:
        assert value.post('/api/auth/login', json={'pairing_code': local_auth.issue_pairing_code()}).status_code == 200
        yield value


def setup(client):
    pid = client.get('/api/projects').json()[0]['id']
    cid = client.post('/api/conversations', json={'project_id': pid}).json()['id']
    return pid, cid, f'/api/projects/{pid}/conversation-library'


def patch(client, path, cid, version=1, **changes):
    return client.patch(f'{path}/{cid}', json={'expected_version': version, **changes})


def move(client, path, cid, target, version=1, ack=True):
    return client.post(f'{path}/{cid}/move', json={'expected_version': version, 'target_project_id': target, 'acknowledge_context_change': ack})


def message(cid, *, role='assistant', text='Synthetic source-free answer', links=(), citations='[]', status='complete'):
    mid = db.uid()
    with db.get_db() as conn:
        conn.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)', (mid, cid, role, text, status, 'test-fixture', db.now(), citations))
        if links is not None:
            context_lineage.save(conn, mid, list(links))
    return mid


def test_rename_archive_restore_and_restart(client):
    pid, cid, path = setup(client)
    response = patch(client, path, cid, title='مراجعة 2026 — Test')
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['version'] == 2
    assert patch(client, path, cid, 2, archived=True).json()['version'] == 3
    assert client.get(path).json()['items'] == []
    assert client.get('/api/conversations', params={'project_id': pid}).json() == []
    assert client.get(path, params={'archived': 'archived'}).json()['items'][0]['id'] == cid
    assert client.get('/api/conversations/' + cid).status_code == 200
    # Reopen the actual database initializer, not an in-process dictionary.
    db.init_db()
    with db.get_db() as conn:
        conversation_library.initialize(conn)
    state = client.get(f'{path}/{cid}').json()
    assert state['archived'] is True and state['title'] == 'مراجعة 2026 — Test'
    restored = patch(client, path, cid, 3, archived=False)
    assert restored.status_code == 200 and restored.json()['version'] == 4
    assert client.get(path).json()['items'][0]['id'] == cid


def test_cas_protects_concurrent_changes_and_automatic_title(client):
    _, cid, path = setup(client)
    assert patch(client, path, cid, title='First').status_code == 200
    denied = patch(client, path, cid, archived=True)
    assert denied.status_code == 409 and denied.json()['detail']['code'] == 'version_conflict'
    assert client.get(f'{path}/{cid}').json()['archived'] is False
    # Existing generator's title change must invalidate an old dialog version.
    with db.get_db() as conn:
        conn.execute('UPDATE conversations SET title=? WHERE id=?', ('Generated title', cid))
    assert patch(client, path, cid, 2, title='Stale dialog').status_code == 409
    assert client.get(f'{path}/{cid}').json()['title'] == 'Generated title'
    # A combined change is one metadata version, and a semantic no-op is stable.
    response = patch(client, path, cid, 3, title='Combined', archived=True)
    assert response.json()['version'] == 4
    assert patch(client, path, cid, 4, title='Combined', archived=True).json()['version'] == 4


@pytest.mark.parametrize('payload', [
    {'title': '  '}, {'title': 'line\nbreak'}, {'title': 'x' * 201},
    {'title': 'Valid', 'actor': 'admin'}, {'archived': 'true'},
    {'expected_version': 0, 'title': 'Valid'}, {},
])
def test_invalid_or_untrusted_mutation_rejected(client, payload):
    _, cid, path = setup(client)
    result = client.patch(f'{path}/{cid}', json={'expected_version': 1, **payload})
    assert result.status_code == 422
    assert client.get(f'{path}/{cid}').json()['version'] == 1


def test_search_is_project_scoped_and_never_matches_revoked_answer(client):
    pid, cid, path = setup(client)
    other = client.post('/api/projects', json={'name': 'Other'}).json()['id']
    other_cid = client.post('/api/conversations', json={'project_id': other}).json()['id']
    message(cid, role='user', text='needle safe question')
    message(cid, text='SECRET_REVOKED_MARKER', links=[{'kind': 'project_memory', 'id': 'missing-memory', 'version': 1}])
    message(other_cid, text='SECOND_PROJECT_MARKER')
    assert client.get(path, params={'q': 'needle'}).json()['items'][0]['id'] == cid
    assert client.get(path, params={'q': 'SECRET_REVOKED_MARKER'}).json()['items'] == []
    assert client.get(path, params={'q': 'SECOND_PROJECT_MARKER'}).json()['items'] == []
    assert client.get(path, params={'q': 'x' * 301}).status_code == 422
    assert client.get(path, params={'archived': 'invented'}).status_code == 422


def test_session_and_both_project_permissions_checked_on_server(client):
    pid, cid, path = setup(client)
    target = client.post('/api/projects', json={'name': 'Foreign'}).json()['id']
    with db.get_db() as conn:
        conn.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?', ('foreign-owner', target))
    assert move(client, path, cid, target).status_code == 404
    assert client.get(f'/api/projects/{target}/conversation-library/{cid}').status_code == 404
    with db.get_db() as conn:
        conn.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?', ('foreign-owner', pid))
    assert client.get(path).status_code == 404
    assert patch(client, path, cid, title='Forbidden').status_code == 404
    assert client.post('/api/auth/logout').status_code == 200
    assert client.get(path).status_code == 401
    with db.get_db() as conn:
        assert conn.execute('SELECT project_id FROM conversations WHERE id=?', (cid,)).fetchone()[0] == pid


@pytest.mark.parametrize('with_messages', [False, True])
def test_eligible_move_preserves_conversation_identity_and_only_changes_context(client, with_messages):
    pid, cid, path = setup(client)
    target = client.post('/api/projects', json={'name': 'Target', 'instructions': 'New context'}).json()['id']
    if with_messages:
        message(cid, role='user', text='Hello')
        message(cid, links=[])
    with db.get_db() as conn:
        stamp = db.now()
        file_id, memory_id = db.uid(), db.uid()
        conn.execute('INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)', (file_id, pid, 'unrelated.txt', 10, 'synthetic-hash', 0, 1, 10, stamp))
        conn.execute('INSERT INTO memories VALUES(?,?,?,?,?)', (memory_id, 'project', pid, 'Unrelated project-only memory', stamp))
    before = client.get('/api/conversations/' + cid).json()['messages']
    assert client.get(f'{path}/{cid}').json()['move']['eligible'] is True
    assert move(client, path, cid, target, ack=False).status_code == 422
    assert move(client, path, cid, pid).status_code == 422
    result = move(client, path, cid, target)
    assert result.status_code == 200, result.text
    assert result.json()['id'] == cid and result.json()['project_id'] == target and result.json()['version'] == 2
    assert client.get(f'{path}/{cid}').status_code == 404
    after = client.get('/api/conversations/' + cid).json()
    assert after['messages'] == before and after['project_id'] == target
    assert move(client, f'/api/projects/{target}/conversation-library', cid, pid).status_code == 409
    with db.get_db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM messages WHERE conversation_id=?', (cid,)).fetchone()[0] == len(before)
        assert conn.execute('SELECT project_id FROM memories WHERE id=?', (memory_id,)).fetchone()[0] == pid
        assert conn.execute('SELECT project_id FROM knowledge_files WHERE id=?', (file_id,)).fetchone()[0] == pid
        assert conn.execute('SELECT COUNT(*) FROM memories WHERE project_id=?', (target,)).fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM knowledge_files WHERE project_id=?', (target,)).fetchone()[0] == 0


@pytest.mark.parametrize('case', ['legacy', 'citations', 'links', 'malformed', 'fact_receipt', 'selected_file', 'imported'])
def test_sources_and_unknown_legacy_receipts_block_move(client, case):
    pid, cid, path = setup(client)
    target = client.post('/api/projects', json={'name': 'Target'}).json()['id']
    mid = message(cid, links=None if case == 'legacy' else [{'kind': 'unbound_history'}] if case == 'links' else [],
                  citations='[{"file_id":"private"}]' if case == 'citations' else '{broken' if case == 'malformed' else '[]')
    with db.get_db() as conn:
        if case in ('fact_receipt', 'imported'):
            conn.execute('INSERT INTO reasoning_runs VALUES(?,?)', (mid, json.dumps({'fact_bindings': [{'id': 'fact'}]} if case == 'fact_receipt' else {'origin': 'imported_snapshot'})))
        if case == 'selected_file':
            conn.execute('INSERT INTO message_retrieval_options VALUES(?,?)', (mid, '{"file_scope":[{"file_id":"file"}]}'))
    result = move(client, path, cid, target)
    assert result.status_code == 409 and result.json()['detail']['code'] == 'source_dependencies'
    assert client.get(f'{path}/{cid}').json()['project_id'] == pid


def test_active_generation_prevents_all_organization(client):
    _, cid, path = setup(client)
    target = client.post('/api/projects', json={'name': 'Target'}).json()['id']
    service.ACTIVE[cid] = {'synthetic_test_only': True}
    try:
        for result in [patch(client, path, cid, title='Busy'), patch(client, path, cid, archived=True), move(client, path, cid, target)]:
            assert result.status_code == 409 and result.json()['detail']['code'] == 'conversation_busy'
    finally:
        service.ACTIVE.pop(cid, None)
    message(cid, status='streaming')
    assert patch(client, path, cid, title='Still streaming').status_code == 409


@pytest.mark.parametrize('case', ['generation_origin', 'generation_result', 'branch', 'draft', 'studio', 'fact', 'learning'])
def test_dependents_are_not_silently_relocated(client, case):
    pid, cid, path = setup(client)
    target = client.post('/api/projects', json={'name': 'Target'}).json()['id']
    mid = message(cid)
    with db.get_db() as conn:
        stamp = db.now()
        if case.startswith('generation'):
            owner = conn.execute('SELECT owner_id FROM guard_projects WHERE project_id=?', (pid,)).fetchone()[0]
            origin = cid
            if case == 'generation_result':
                origin = db.uid()
                conn.execute('INSERT INTO conversations VALUES(?,?,?,?,?,?)', (origin, pid, 'Origin', stamp, stamp, None))
            conn.execute('INSERT INTO generation_requests VALUES(?,?,?,?,?,?,?,?,?,?)', (owner, db.uid(), origin, pid, 'hash', 'bound', cid, mid, stamp, stamp))
        elif case == 'branch':
            conn.execute('INSERT INTO conversations VALUES(?,?,?,?,?,?)', (db.uid(), pid, 'Child', stamp, stamp, cid))
        elif case == 'draft':
            owner = conn.execute('SELECT owner_id FROM guard_projects WHERE project_id=?', (pid,)).fetchone()[0]
            conn.execute('INSERT INTO composer_drafts VALUES(?,?,?,?,?,?)', (owner, pid, cid, 1, '{"assistant":"Unsent"}', stamp))
        elif case == 'studio':
            entry = db.uid()
            conn.execute('INSERT INTO studio_entries VALUES(?,?,?,?,?,?,?,?)', (entry, pid, 'decision', '{}', 1, stamp, stamp, None))
            conn.execute('INSERT INTO studio_versions VALUES(?,?,?,?)', (entry, 1, json.dumps({'provenance': {'message_id': mid}}), stamp))
        elif case == 'fact':
            fid = db.uid()
            conn.execute('INSERT INTO fact_entries VALUES(?,?,?,?,?,?)', (fid, pid, 1, db.uid(), stamp, stamp))
            conn.execute('INSERT INTO fact_versions VALUES(?,?,?,?)', (fid, 1, json.dumps({'sources': [{'message_id': mid}]}), stamp))
        elif case == 'learning':
            owner = conn.execute('SELECT owner_id FROM guard_projects WHERE project_id=?', (pid,)).fetchone()[0]
            conn.execute('''INSERT INTO learning_feedback VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (db.uid(), owner, pid, cid, 'message', mid, '1', '[]', 'negative', '[]', '', '', '{}', 'conversation', '[]', 'test', 'received', '{}', 1, db.uid(), 'hash', stamp, stamp))
    result = move(client, path, cid, target)
    assert result.status_code == 409, result.text
    expected = {'generation_origin': 'generation_history', 'generation_result': 'generation_history', 'branch': 'branch_history',
                'draft': 'unsent_draft', 'studio': 'saved_output_dependencies', 'fact': 'saved_output_dependencies', 'learning': 'learning_dependencies'}[case]
    assert result.json()['detail']['code'] == expected
    assert client.get(f'{path}/{cid}').json()['project_id'] == pid


def test_metadata_cascades_on_delete_without_changing_conversation_shape(client):
    _, cid, path = setup(client)
    assert patch(client, path, cid, archived=True).status_code == 200
    assert client.delete('/api/conversations/' + cid).status_code == 200
    with db.get_db() as conn:
        assert len(conn.execute('PRAGMA table_info(conversations)').fetchall()) == 6
        assert conn.execute('SELECT 1 FROM conversation_library_state WHERE conversation_id=?', (cid,)).fetchone() is None


def test_metadata_survives_real_service_lifespan_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    with TestClient(service.app, headers={'X-Riwaq-Client': 'local'}) as first:
        assert first.post('/api/auth/login', json={'pairing_code': local_auth.issue_pairing_code()}).status_code == 200
        pid, cid, path = setup(first)
        assert patch(first, path, cid, title='Restart محفوظ', archived=True).status_code == 200
    with TestClient(service.app, headers={'X-Riwaq-Client': 'local'}) as second:
        assert second.post('/api/auth/login', json={'pairing_code': local_auth.issue_pairing_code()}).status_code == 200
        saved = second.get(f'{path}/{cid}').json()
        assert saved['title'] == 'Restart محفوظ' and saved['version'] == 2 and saved['archived'] is True
        assert second.get('/api/conversations', params={'project_id': pid}).json() == []
        assert patch(second, path, cid, 2, archived=False).status_code == 200
