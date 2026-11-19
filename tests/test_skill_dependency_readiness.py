"""Actual local package graphs; no provider calls or synthetic release approval."""
from copy import deepcopy
import hashlib

import pytest

from backend import skill_registry
from tests.test_app import client


@pytest.fixture
def graph(tmp_path):
    skills = []
    for number, slug in enumerate(('parent', 'dependency', 'leaf', 'independent'), 1):
        raw = f'---\nname: {slug}\ndescription: Read {slug}.\n---\nReviewed instructions.\n'.encode()
        folder = tmp_path / 'skills' / slug
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_bytes(raw)
        skills.append({'number': number, 'slug': slug, 'display_name': slug,
                       'description': f'Read {slug}.', 'version': '1.0.0',
                       'instructions_sha256': hashlib.sha256(raw).hexdigest(),
                       'status': 'partial', 'execution_kind': 'core', 'tools': [],
                       'dependencies': []})
    skills[0]['skill_dependencies'] = ['dependency']
    skills[1]['skill_dependencies'] = ['leaf']
    return tmp_path, {'version': '1.0.0', 'skills': skills}


@pytest.mark.parametrize('index', [1, 2])
def test_unapproved_required_release_blocks_parent_and_preserves_independent(graph, index):
    root, manifest = graph
    blocked = manifest['skills'][index]
    blocked['release_profile'] = 'batch05-fast'
    observed = {s['slug']: s for s in skill_registry.discovery(manifest, root)['skills']}
    assert not observed['parent']['ready']
    assert {'code': 'release_not_approved', 'skill': blocked['slug']} in observed['parent']['blockers']
    assert not observed[blocked['slug']]['ready']
    assert observed['independent']['ready']
    assert all(not s['tested'] for s in observed.values())


def test_dependency_release_status_change_invalidates_parent_observation(graph):
    root, manifest = graph
    manifest['skills'][2]['release_profile'] = 'batch05-fast'
    # Trusted runner evidence is represented as a server observation here. This
    # test verifies propagation only; it does not certify a real release.
    release = {'leaf': {'approved': True, 'tested_scope': 'local_contract'}}
    before = skill_registry.discovery(manifest, root, release_status=release)['skills'][0]
    assert before['ready'] and not before['tested']
    release['leaf'] = {'approved': False, 'tested_scope': 'unverified', 'reasons': ['release_source_stale']}
    after = skill_registry.discovery(manifest, root, release_status=release)['skills'][0]
    assert not after['ready']
    assert before['discovery_fingerprint'] != after['discovery_fingerprint']


def test_dependency_gate_does_not_replace_missing_package_or_tool_checks(graph):
    root, manifest = graph
    manifest['skills'][2]['release_profile'] = 'batch05-fast'
    manifest['skills'][2]['tools'] = ['actual-tool-required']
    release = {'leaf': {'approved': True, 'tested_scope': 'local_contract'}}
    observed = skill_registry.discovery(manifest, root, release_status=release)['skills'][0]
    assert not observed['ready']
    assert any(b['code'] == 'required_capability_unavailable' for b in observed['blockers'])
    (root / 'skills' / 'leaf' / 'SKILL.md').unlink()
    observed = skill_registry.discovery(manifest, root, tool_status={'actual-tool-required': True}, release_status=release)['skills'][0]
    assert not observed['ready']
    assert any(b['code'] == 'dependency_package_unavailable' for b in observed['blockers'])


def test_catalog_input_not_mutated_by_observation(graph):
    root, manifest = graph
    manifest['skills'][2]['release_profile'] = 'batch05-fast'
    original = deepcopy(manifest)
    skill_registry.discovery(manifest, root)
    assert manifest == original


def test_dependency_version_changes_parent_fingerprint_even_when_both_installed(graph):
    root, manifest = graph
    before = skill_registry.discovery(manifest, root)['skills'][0]
    manifest['skills'][2]['version'] = '1.0.1'
    after = skill_registry.discovery(manifest, root)['skills'][0]
    assert before['ready'] and after['ready']
    assert before['discovery_fingerprint'] != after['discovery_fingerprint']


def test_actual_catalog_and_activation_agree_on_missing_required_release(client, monkeypatch):
    from backend import skill_core, skills
    from backend.db import get_db
    manifest = deepcopy(skill_core.catalog())
    parent = next(s for s in manifest['skills'] if s['slug'] == 'intent-routing')
    child = next(s for s in manifest['skills'] if s['slug'] == 'completion-review')
    parent['skill_dependencies'] = [child['slug']]
    child['release_profile'] = 'batch05-fast'
    monkeypatch.setattr(skill_core, 'catalog', lambda: manifest)
    monkeypatch.setattr(skills, 'catalog', lambda: manifest)
    response = client.get('/api/skills/catalog')
    assert response.status_code == 200, response.text
    observed = next(s for s in response.json()['skills'] if s['slug'] == parent['slug'])
    assert not observed['ready']
    assert {'code': 'release_not_approved', 'skill': child['slug']} in observed['blockers']
    pid = client.get('/api/projects').json()[0]['id']
    response = client.post(f'/api/projects/{pid}/skill-runs', json={
        'request': 'Review only', 'requestedSkills': ['intent-routing'],
        'idempotencyKey': 'required-release-test'})
    assert response.status_code == 422, response.text
    with get_db() as db:
        assert db.execute('SELECT COUNT(*) FROM skill_runs').fetchone()[0] == 0
