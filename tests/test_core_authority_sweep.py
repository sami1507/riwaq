"""GAP-002, GAP-037..040: route-table sweeps of the real application.

The route list is read from the live application, so a route added later is covered
without editing this file. Identifiers of real sub-resources (glossary entry, decision,
voice session, files, artifacts, messages, chunks) are harvested from the project's own
list routes before the withdrawal and substituted into every path parameter afterwards.

Request bodies are empty: a 4xx validation refusal is acceptable; any 2xx/5xx answer or
private marker is a finding. Valid-body mutations are covered by each area's own tests.
The last test keeps fixtures honest."""
import json
import re
from pathlib import Path

from backend import app as service, context_lineage, db
from test_learning_api import client  # noqa: F401  (real paired session fixture)

MARKERS = ('PRIVATE', 'UNIQUE_SOURCE_DERIVATIVE', 'secret marker')


def _collect_ids(value, found):
    if isinstance(value, dict):
        for key, item in value.items():
            if (key == 'id' or key.endswith('_id')) and isinstance(item, str) and 6 < len(item) < 80:
                found.add(item)
            _collect_ids(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_ids(item, found)


def _seed(client):
    project = client.post('/api/projects', json={'name': 'Private revoked project', 'instructions': 'PRIVATE_INSTRUCTION'}).json()['id']
    chat = client.post('/api/conversations', json={'project_id': project, 'title': 'PRIVATE_CONVERSATION'}).json()['id']
    upload = client.post(f'/api/projects/{project}/files', files={'file': ('private.txt', b'PRIVATE_EVIDENCE secret marker', 'text/plain')})
    assert upload.status_code == 201, upload.text
    artifact = client.post(f'/api/projects/{project}/artifacts', json={'title': 'PRIVATE_ARTIFACT', 'content': 'PRIVATE_ARTIFACT_BODY'})
    assert artifact.status_code == 201, artifact.text
    assert client.post(f'/api/projects/{project}/glossary', json={'term': 'PRIVATE_TERM', 'definition': 'PRIVATE_DEFINITION'}).status_code == 201
    assert client.post(f'/api/projects/{project}/decisions', json={'title': 'PRIVATE_DECISION', 'decision': 'PRIVATE_CHOICE', 'rationale': 'PRIVATE_WHY'}).status_code == 201
    assert client.post(f'/api/projects/{project}/voice/sessions', json={'mode': 'transcription'}).status_code == 201
    assert client.post('/api/memories', json={'scope': 'project', 'project_id': project, 'content': 'PRIVATE_MEMORY'}).status_code == 200
    file_id = upload.json()['id']
    with db.get_db() as c:
        message = db.uid()
        c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)', (message, chat, 'assistant', 'PRIVATE_HISTORY', 'complete', 'fixture', db.now(), '[]'))
        chunk = dict(c.execute('SELECT k.*,f.filename FROM knowledge_chunks k JOIN knowledge_files f ON f.id=k.file_id WHERE k.file_id=?', (file_id,)).fetchone())
        derived = db.uid()
        c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)', (derived, chat, 'assistant', 'UNIQUE_SOURCE_DERIVATIVE', 'complete', 'fixture', db.now(), json.dumps([chunk])))
        context_lineage.save(c, derived, context_lineage.source_links(c, project, [chunk]))
    assert 'UNIQUE_SOURCE_DERIVATIVE' in client.get(f'/api/conversations/{chat}').text
    spec = service.app.openapi()['paths']
    # Identifiers are grouped by the route family that listed them (glossary, voice, ...), so
    # each parameterised route is probed with the identifiers that can actually belong to it.
    families = {'': {derived, chunk['id'], message, file_id}}
    for path, operations in spec.items():
        if 'get' in operations and path.startswith('/api/projects/{') and path.count('{') == 1:
            response = client.get(re.sub(r'{[^}]+}', project, path))
            if response.status_code == 200 and 'json' in response.headers.get('content-type', ''):
                found = set()
                _collect_ids(response.json(), found)
                found.discard(project)
                families.setdefault(path.split('/')[4] if path.count('/') > 3 else '', set()).update(found)
    assert sum(len(ids) for ids in families.values()) > 15, families
    known = {'projects': project, 'conversations': chat, 'files': file_id, 'artifacts': artifact.json()['id'], 'messages': message}
    return spec, known, families, file_id


def _sweep(client, spec, known, families, *, scoped_only, methods=None, limit=None):
    for path, operations in spec.items():
        if not path.startswith('/api') or '{' not in path:
            continue
        parts, free = path.split('/'), []
        for index, part in enumerate(parts):
            if part.startswith('{'):
                value = known.get(parts[index - 1])
                if value:
                    parts[index] = value
                else:
                    free.append(index)
        if scoped_only and known['projects'] not in parts and known['conversations'] not in parts:
            continue
        family = parts[4] if len(parts) > 4 and parts[2] == 'projects' else ''
        candidates = sorted(families.get(family, set()) | families['']) + ['x']
        if limit:
            candidates = candidates[:limit] + ['x']
        for candidate in (candidates if free else [None]):
            for index in free:
                parts[index] = candidate
            for method in operations:
                method = method.upper()
                if methods and method not in methods:
                    continue
                yield method, path, client.request(method, '/'.join(parts), json={} if method in ('POST', 'PUT', 'PATCH') else None)


def test_core_every_scoped_route_refuses_after_ownership_withdrawal(client):
    spec, known, families, _ = _seed(client)
    with db.get_db() as c:
        c.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?', ('other-synthetic-owner', known['projects']))
    probed, findings = 0, []
    for method, path, response in _sweep(client, spec, known, families, scoped_only=True):
        probed += 1
        if response.status_code < 400 or response.status_code >= 500 or any(marker in response.text for marker in MARKERS):
            findings.append((method, path, response.status_code, response.text[:120]))
    assert probed > 800, probed
    assert findings == []


def test_core_every_route_refuses_after_session_withdrawal(client):
    """The browser still presents its cookie; the server-side session is gone."""
    spec, known, families, _ = _seed(client)
    with db.get_db() as c:
        c.execute('DELETE FROM local_auth_sessions')
    probed, findings = 0, []
    # The refusal precedes any lookup, so three real identifiers and one unknown are enough.
    for method, path, response in _sweep(client, spec, known, families, scoped_only=False, limit=3):
        probed += 1
        if response.status_code != 401:
            findings.append((method, path, response.status_code, response.text[:120]))
    assert probed > 500, probed
    assert findings == []


def test_core_no_read_route_or_search_returns_content_of_a_withdrawn_source(client):
    """GAP-037/040: the project stays owned; one source file is withdrawn. Neither the file
    text nor a reply derived from it may come back from any read route or search field."""
    spec, known, families, file_id = _seed(client)
    with db.get_db() as c:
        c.execute('UPDATE guard_file_policies SET revoked=1,revision=revision+1 WHERE file_id=?', (file_id,))
    leaked = ('PRIVATE_EVIDENCE', 'UNIQUE_SOURCE_DERIVATIVE', 'secret marker')
    probed, findings = 0, []
    for method, path, response in _sweep(client, spec, known, families, scoped_only=False, methods={'GET'}):
        probed += 1
        if response.status_code >= 500 or any(marker in response.text for marker in leaked):
            findings.append((method, path, response.status_code, response.text[:120]))
    searched = 0
    for path, operations in spec.items():
        names = [p['name'] for p in operations.get('get', {}).get('parameters', []) if p['in'] == 'query']
        if not names:
            continue
        parts = path.split('/')
        for index, part in enumerate(parts):
            if part.startswith('{'):
                parts[index] = known.get(parts[index - 1], 'x')
        for term in leaked:
            searched += 1
            response = client.get('/'.join(parts), params={name: term for name in names})
            echoed = response.text.replace(term, '')  # a response may repeat the query itself
            if response.status_code >= 500 or any(marker in echoed for marker in leaked):
                findings.append(('GET?', path, response.status_code, response.text[:120]))
    assert probed > 400 and searched > 30, (probed, searched)
    assert findings == []


def test_core_every_route_refuses_without_a_paired_session(tmp_path, monkeypatch):
    """GAP-002/G-01: only the three pairing endpoints answer an unpaired caller."""
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    from fastapi.testclient import TestClient
    public = {('GET', '/api/auth/status'), ('POST', '/api/auth/login'), ('POST', '/api/auth/identity')}
    probed, findings = 0, []
    with TestClient(service.app, headers={'X-Riwaq-Client': 'local'}) as unpaired:
        for path, operations in service.app.openapi()['paths'].items():
            if not path.startswith('/api'):
                continue
            for method in operations:
                method = method.upper()
                if (method, path) in public:
                    continue
                probed += 1
                response = unpaired.request(method, re.sub(r'{[^}]+}', 'x', path), json={} if method in ('POST', 'PUT', 'PATCH') else None)
                if response.status_code != 401:
                    findings.append((method, path, response.status_code, response.text[:120]))
        assert unpaired.get('/api/auth/status').json() == {'paired': False, 'authenticated': False}
    assert probed > 200, probed
    assert findings == []


def test_core_fixtures_do_not_bypass_authority():
    names = ('require_project', 'current_principal', 'current_owner', 'authorized', 'require_session')
    pattern = re.compile(r"setattr\([^,\n]+,\s*['\"](?:%s)['\"]" % '|'.join(names))
    offenders = []
    for source in sorted(Path(__file__).parent.glob('*.py')):
        text = source.read_text(encoding='utf-8')
        if 'dependency_' + 'overrides' in text or pattern.search(text):
            offenders.append(source.name)
    assert offenders == []
