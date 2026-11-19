"""CLI replay must use the same isolated boundary; no real network or documents."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from backend import public_knowledge as public
from backend.knowledge_fetch import FetchError
from scripts.evaluate_knowledge import audit_originals
from backend import research_extract, isolated_extract, isolated_parsers


def row_fixture():
    body = b'<pre>def f():\n    return 1</pre>'
    return {
        'original': body, 'sha256': hashlib.sha256(body).hexdigest(),
        'status': 'allowed', 'current_version_id': 'v1',
        'source_data': json.dumps({'id': 'fixture', 'content_type': 'html',
            'rights_confirmed': True, 'license': {'storage_allowed': True}}),
        'chunking': json.dumps({'algorithm': 'heading-lines-v2', 'extractor': 'legacy-v1'}),
        'content_type': 'text/html', 'version': '1', 'language': 'en',
        'license': '{}', 'license_text': 'Synthetic license',
        'requested_url': 'https://docs.example.org/a', 'url': 'https://docs.example.org/a',
        'title': 'Fixture', 'published_at': None, 'modified_at': None,
        'etag': None, 'last_modified': None, 'fetched_at': '2026-11-15T00:00:00Z',
    }


class ReadOnlyFixtureConnection:
    def __init__(self, value): self.value = value
    def execute(self, sql, params):
        assert sql.lstrip().startswith('SELECT'), 'No write permitted after failed isolation'
        return SimpleNamespace(fetchone=lambda: self.value)


def forbidden_host_parser(*args, **kwargs):
    pytest.fail('Historical CLI reached the host parser instead of isolation')


def test_reprocess_missing_container_fails_closed_before_database_write(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(public, 'extract_public', forbidden_host_parser)
    with pytest.raises(FetchError, match='not configured'):
        public.reprocess_html_document(ReadOnlyFixtureConnection(row_fixture()), 'doc1', 'v1')


def test_evaluation_original_replay_missing_container_never_uses_host(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(public, 'extract_public', forbidden_host_parser)
    row = row_fixture(); row['data'] = row['source_data']; row['extracted_text'] = 'unused'
    with pytest.raises(FetchError, match='not configured'):
        audit_originals(ReadOnlyFixtureConnection(row), [{'version_id': 'v1', 'url': row['url']}], public)


@pytest.mark.parametrize('extractor', ['legacy-v1', 'html-pre-v2'])
def test_replay_routes_exact_recipe_and_minimal_metadata(monkeypatch, extractor):
    calls = []
    def simulated_boundary(body, metadata, stop=None):
        calls.append(metadata)
        return [(None, 'controlled output')], {'simulated': True}
    monkeypatch.setattr(isolated_extract, 'extract', simulated_boundary)
    result = public.extract_public_isolated(b'<p>fixture</p>',
        {'content_type': 'html', 'private_marker': 'MUST_NOT_ENTER_WORKER'},
        'https://docs.example.org/a', 'text/html', extractor=extractor)
    assert result == [(None, 'controlled output')]
    assert calls == [{'mode': 'public', 'source': {'content_type': 'html'},
        'url': 'https://docs.example.org/a', 'content_type': 'text/html', 'extractor': extractor}]


@pytest.mark.parametrize('extractor', ['', 'unknown', '../module.py', [], None])
def test_worker_rejects_unrecognized_explicit_recipe(extractor):
    with pytest.raises(isolated_parsers.ParserError):
        isolated_parsers.validate_metadata({'mode': 'public', 'source': {'content_type': 'html'},
            'url': 'https://docs.example.org/a', 'content_type': 'text/html', 'extractor': extractor})


def test_unknown_recipe_rejected_before_worker_dispatch(monkeypatch):
    monkeypatch.setattr(isolated_extract, 'extract', forbidden_host_parser)
    with pytest.raises(FetchError, match='metadata is invalid'):
        research_extract.extract_bounded(b'<p>x</p>', {'content_type': 'html'},
            'https://docs.example.org/a', 'text/html', extractor='unreviewed')
