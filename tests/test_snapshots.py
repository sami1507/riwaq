import json

from authority_fixture import fixture_owner
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import db, knowledge, snapshots


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RIWAQ_DATA_DIR", str(tmp_path))
    db.init_db()
    app = FastAPI()
    app.include_router(knowledge.router)
    app.include_router(snapshots.router)
    with fixture_owner(), TestClient(app) as session:
        yield session


def seed(client):
    stamp = db.now()
    with db.get_db() as conn:
        pid = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()[0]
        other = db.uid()
        conn.execute("INSERT INTO projects VALUES(?,?,?,?)", (other, "Other", "SECRET OTHER INSTRUCTIONS", stamp))
        conn.execute("INSERT INTO memories VALUES(?,?,?,?,?)", (db.uid(), "preference", None, "SECRET GLOBAL PREFERENCE", stamp))
        conn.execute("INSERT INTO memories VALUES(?,?,?,?,?)", (db.uid(), "project", other, "SECRET OTHER MEMORY", stamp))
        conn.execute("INSERT INTO memories VALUES(?,?,?,?,?)", (db.uid(), "project", pid, "This project memory", stamp))
        conn.execute("INSERT INTO settings VALUES(?,?)", ("mode", '"hybrid"'))
        db.event(conn, other, "goal", "SECRET OTHER GOAL")
    uploaded = client.post(f"/api/projects/{pid}/files", files={"file": ("facts.txt", b"The launch budget is 731 dollars.")}).json()
    other_file = client.post(f"/api/projects/{other}/files", files={"file": ("other.txt", b"SECRET OTHER FILE")}).json()
    with db.get_db() as conn:
        chunk = dict(conn.execute("SELECT * FROM knowledge_chunks WHERE file_id=?", (uploaded["id"],)).fetchone())
        foreign_chunk = dict(conn.execute("SELECT * FROM knowledge_chunks WHERE file_id=?", (other_file["id"],)).fetchone())
        chunk["filename"] = "facts.txt"
        foreign_chunk["filename"] = "other.txt"
        cid = db.uid()
        conn.execute("INSERT INTO conversations VALUES(?,?,?,?,?,?)", (cid, pid, "Source conversation", stamp, stamp, None))
        conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)", (db.uid(), cid, "assistant", "Budget: 731 [S1]", "streaming", "qwen3:4b-instruct", stamp, json.dumps([chunk, foreign_chunk])))
    artifact = client.post(f"/api/projects/{pid}/artifacts", json={"title": "Plan", "content": "First version"}).json()
    client.patch(f"/api/artifacts/{artifact['id']}", json={"content": "Second version"})
    return pid, other, chunk


def test_snapshot_excludes_other_projects_preferences_and_settings(client):
    pid, other, chunk = seed(client)
    response = client.get(f"/api/projects/{pid}/snapshot")
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert "SECRET" not in response.text
    assert "settings" not in snapshot and "workspace_grants" not in snapshot
    assert snapshot["memories"] == [{"content": "This project memory", "created_at": snapshot["memories"][0]["created_at"]}]
    citations = snapshot["conversations"][0]["messages"][0]["citations"]
    assert len(citations) == 1 and citations[0]["id"] == chunk["id"]
    assert "project_id" not in citations[0]
    assert client.get("/api/projects/missing/snapshot").status_code == 404


def test_roundtrip_new_ids_sources_versions_and_interrupted_status(client):
    pid, other, chunk = seed(client)
    snapshot = client.get(f"/api/projects/{pid}/snapshot").json()
    result = client.post("/api/projects/import", json={"snapshot": snapshot, "confirm": True})
    assert result.status_code == 201, result.text
    restored_id = result.json()["id"]
    assert restored_id not in (pid, other)
    restored = client.get(f"/api/projects/{restored_id}/snapshot").json()
    assert restored["project"]["name"].endswith(" (restored)")
    assert restored["files"][0]["id"] != snapshot["files"][0]["id"]
    restored_chunk = restored["files"][0]["chunks"][0]
    assert restored_chunk["id"] != chunk["id"]
    message = restored["conversations"][0]["messages"][0]
    assert message["status"] == "interrupted"
    assert message["citations"][0]["id"] == restored_chunk["id"]
    assert message["citations"][0]["file_id"] == restored["files"][0]["id"]
    assert message["citations"][0]["text"] == restored_chunk["text"]
    assert restored["artifacts"] == snapshot["artifacts"]
    found = client.get(f"/api/projects/{restored_id}/retrieve?q=budget").json()
    assert found["found"] and found["chunks"][0]["project_id"] == restored_id
    assert client.get(f"/api/projects/{pid}/snapshot").json() == snapshot


@pytest.mark.parametrize("mutation", [
    lambda body: body.update(confirm=False),
    lambda body: body.update(confirm="true"),
    lambda body: body["snapshot"].update(version=2),
    lambda body: body["snapshot"].update(version=True),
    lambda body: body["snapshot"].update(workspace_grants=[{"root": "C:/"}]),
    lambda body: body["snapshot"]["files"][0].update(filename="../secret.txt"),
    lambda body: body["snapshot"]["files"][0]["chunks"][0].update(line_end=0),
    lambda body: body["snapshot"]["artifacts"][0]["versions"].pop(),
    lambda body: body["snapshot"]["conversations"][0]["messages"][0].update(role="system"),
])
def test_invalid_import_creates_nothing(client, mutation):
    pid, _, _ = seed(client)
    body = {"snapshot": client.get(f"/api/projects/{pid}/snapshot").json(), "confirm": True}
    mutation(body)
    with db.get_db() as conn:
        before = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    assert client.post("/api/projects/import", json=body).status_code == 422
    with db.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == before


def test_dangling_and_mismatched_citation_ids_are_not_authoritative(client):
    pid, _, _ = seed(client)
    snapshot = client.get(f"/api/projects/{pid}/snapshot").json()
    citation = snapshot["conversations"][0]["messages"][0]["citations"][0]
    citation["text"] = "FORGED SOURCE CONTENT"
    result = client.post("/api/projects/import", json={"snapshot": snapshot, "confirm": True})
    restored = client.get(f"/api/projects/{result.json()['id']}/snapshot").json()
    assert "FORGED" not in restored["conversations"][0]["messages"][0]["citations"][0]["text"]
    citation["file_id"] = "foreign-file-id"
    result = client.post("/api/projects/import", json={"snapshot": snapshot, "confirm": True})
    assert result.json()["dropped_citations"] == 1
    restored = client.get(f"/api/projects/{result.json()['id']}/snapshot").json()
    assert restored["conversations"][0]["messages"][0]["citations"] == []
    assert restored["conversations"][0]["messages"][0]["content"] == "Budget: 731 [unverified citation]"


def test_filtering_sources_preserves_the_meaning_of_citation_numbers(client):
    pid, _, _ = seed(client)
    with db.get_db() as conn:
        message = conn.execute("SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.project_id=?", (pid,)).fetchone()
        citations = json.loads(message["citations"])
        conn.execute("UPDATE messages SET citations=?,content=? WHERE id=?", (json.dumps(list(reversed(citations))), "Foreign [S1], real budget [S2]", message["id"]))
    exported = client.get(f"/api/projects/{pid}/snapshot").json()
    assert exported["conversations"][0]["messages"][0]["content"] == "Foreign [unverified citation], real budget [S1]"


def test_payload_limit_and_transaction_rollback(client, monkeypatch):
    assert client.post("/api/projects/import", content=b" " * (snapshots.MAX_SNAPSHOT_BYTES + snapshots.MAX_SNAPSHOT_ENVELOPE_OVERHEAD + 1)).status_code == 413
    pid, _, _ = seed(client)
    snapshot = client.get(f"/api/projects/{pid}/snapshot").json()
    with db.get_db() as conn:
        before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("projects", "messages", "knowledge_chunks", "artifacts")}
    def fail_event(*args):
        raise RuntimeError("simulated transaction failure")
    monkeypatch.setattr(snapshots, "event", fail_event)
    with pytest.raises(RuntimeError, match="transaction failure"):
        client.post("/api/projects/import", json={"snapshot": snapshot, "confirm": True})
    with db.get_db() as conn:
        after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}
    assert before == after


def public_excerpt():
    return {
        "id": "public-chunk-original", "scope": "public", "page": None,
        "line_start": 12, "line_end": 15, "text": "Public documented fact.",
        "filename": "Public reference", "title": "Public reference",
        "source_id": "public-source", "document_id": "public-document",
        "version_id": "public-version", "version": "3.12", "revision": 2,
        "sha256": "a" * 64, "url": "https://docs.example.org/reference",
        "language": "en", "languages": ["en"], "domain": "programming",
        "published_at": None, "modified_at": "2026-11-10", "fetched_at": "2026-11-12T10:00:00Z",
        "heading": "Public section", "chunking": {"algorithm": "heading-lines-v2", "max_characters": 1400, "max_lines": 24, "overlap_lines": 2, "respect_headings": True},
        "license": {"name": "CC-BY-4.0", "url": "https://creativecommons.org/licenses/by/4.0/",
                    "storage_allowed": True, "redistribution_allowed": True, "training_allowed": False,
                    "attribution": "Example authors", "reviewed_at": "2026-11-12",
                    "evidence": "Reviewed license text", "sha256": "b" * 64},
        "score": .025, "retrieval_method": "hybrid-rrf",
        "embedding_model": "embeddinggemma-2:270m-q8_0-text", "embedding_digest": "c" * 64,
    }


def test_public_excerpt_roundtrip_preserves_provenance_without_indexing(client, monkeypatch):
    pid, _, chunk = seed(client)
    citation = public_excerpt()
    with db.get_db() as conn:
        message = conn.execute("SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.project_id=?", (pid,)).fetchone()
        private, foreign = json.loads(message["citations"])
        conn.execute("UPDATE messages SET citations=?,content=? WHERE id=?", (json.dumps([foreign, citation, private]), "Foreign [S1], public [S2], private [S3]", message["id"]))
        public_tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'public_%'")]
        public_before = {table: list(conn.execute(f'SELECT * FROM "{table}"')) for table in public_tables}
    # Even unavailable/deleted public sources retain their historical excerpt.
    # Export/import must not connect or resolve these public IDs into a live corpus.
    import socket
    def denied(*args, **kwargs):
        raise AssertionError("Snapshot provenance must not cause network access")
    monkeypatch.setattr(socket, "create_connection", denied)
    snapshot = client.get(f"/api/projects/{pid}/snapshot").json()
    exported_message = snapshot["conversations"][0]["messages"][0]
    assert exported_message["content"] == "Foreign [unverified citation], public [S1], private [S2]"
    public = exported_message["citations"][0]
    assert public["scope"] == "public"
    for field, value in citation.items():
        assert public[field] == ({"extractor":"legacy-v1"} | value if field=="chunking" else value)
    assert public["provenance"] == "stored_excerpt"
    assert len(snapshot["files"]) == 1
    restored_response = client.post("/api/projects/import", json={"snapshot": snapshot, "confirm": True})
    assert restored_response.status_code == 201, restored_response.text
    assert restored_response.json()["dropped_citations"] == 0
    restored = client.get(f"/api/projects/{restored_response.json()['id']}/snapshot").json()
    restored_message = restored["conversations"][0]["messages"][0]
    assert restored_message["citations"][0] == public
    assert restored_message["citations"][1]["id"] != chunk["id"]
    assert restored_message["content"] == exported_message["content"]
    with db.get_db() as conn:
        for table, before in public_before.items():
            assert list(conn.execute(f'SELECT * FROM "{table}"')) == before
        assert conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE text=?", (citation["text"],)).fetchone()[0] == 0


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(file_id="foreign-private-file"),
    lambda value: value.update(scope="private"),
    lambda value: value.update(url="javascript:alert(1)"),
    lambda value: value["license"].update(url="file:///C:/secret"),
    lambda value: value.update(sha256="not-a-hash"),
    lambda value: value.update(embedding_digest="not-a-hash"),
    lambda value: value.update(languages=["en\x00"]),
    lambda value: value.update(published_at="yesterday"),
    lambda value: value["chunking"].update(shell="run me"),
    lambda value: value.update(workspace_grants=["C:/"]),
])
def test_invalid_public_provenance_rejected_atomically(client, mutation):
    pid, _, _ = seed(client)
    snapshot = client.get(f"/api/projects/{pid}/snapshot").json()
    citation = public_excerpt()
    mutation(citation)
    snapshot["conversations"][0]["messages"][0]["citations"].append(citation)
    with db.get_db() as conn:
        before = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    assert client.post("/api/projects/import", json={"snapshot": snapshot, "confirm": True}).status_code == 422
    with db.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == before

from backend import facts,context_lineage
from fastapi import HTTPException

def test_fact_history_roundtrip_preserves_claims_but_revokes_review_authority(client):
 pid,_,chunk=seed(client)
 with db.get_db() as conn:digest=conn.execute('SELECT sha256 FROM knowledge_files WHERE id=?',(chunk['file_id'],)).fetchone()[0]
 fields={'entity_key':'project:launch','property_key':'budget','value':{'type':'number','value':'731'},'unit':'USD'}
 selector={'kind':'private_chunk','chunk_id':chunk['id'],'expected_hash':digest}
 first=facts.create_fact(pid,facts.Create(**fields,sources=[selector]))
 reviewed=facts.review_fact(pid,first['id'],facts.Review(expected_version=1,confirm=True,review_note='User attestation only'))
 facts.edit_fact(pid,first['id'],facts.Patch(**(fields|{'value':{'type':'number','value':'732'}}),expected_version=2))
 snapshot=client.get(f'/api/projects/{pid}/snapshot').json();assert snapshot['fact_entries'][0]['version']==3
 response=client.post('/api/projects/import',json={'snapshot':snapshot,'confirm':True});assert response.status_code==201,response.text
 restored_pid=response.json()['id'];entry=facts.list_facts(restored_pid)[0]
 assert entry['id']!=first['id'] and entry['version']==3 and entry['value']['value']=='732'
 assert entry['review_state']=='draft' and entry['source_state']=='unbound' and not entry['usable']
 history=facts.versions(restored_pid,entry['id']);assert [v['version'] for v in history]==[3,2,1];past=history[1]['data']
 assert past['value']['value']=='731' and past['review_state']=='draft'
 assert past['archived_review']=={'review_state':'user_reviewed','reviewed_at':reviewed['reviewed_at'],'review_note':'User attestation only'}
 with db.get_db() as conn:
  raws=[json.loads(r['payload']) for r in conn.execute('SELECT payload FROM fact_versions WHERE fact_id=?',(entry['id'],))]
  assert all(e['origin']=='imported_snapshot' for p in raws for e in p['evidence'])
  with pytest.raises(HTTPException) as error:facts.context_for_ids(conn,restored_pid,[entry['id']])
  assert error.value.status_code==409
 with pytest.raises(HTTPException) as error:facts.review_fact(restored_pid,entry['id'],facts.Review(expected_version=3,confirm=True))
 assert error.value.status_code==409
 again=client.get(f'/api/projects/{restored_pid}/snapshot').json();next_pid=client.post('/api/projects/import',json={'snapshot':again,'confirm':True}).json()['id'];next_entry=facts.list_facts(next_pid)[0]
 assert facts.versions(next_pid,next_entry['id'])[1]['data']['archived_review']==past['archived_review']
 with db.get_db() as conn:current=conn.execute('SELECT k.id,f.sha256 FROM knowledge_chunks k JOIN knowledge_files f ON f.id=k.file_id WHERE k.project_id=? AND f.project_id=?',(restored_pid,restored_pid)).fetchone()
 rebound=facts.edit_fact(restored_pid,entry['id'],facts.Patch(**fields,expected_version=3,sources=[{'kind':'private_chunk','chunk_id':current['id'],'expected_hash':current['sha256']}]))
 assert rebound['review_state']=='draft' and not rebound['usable']
 assert facts.review_fact(restored_pid,entry['id'],facts.Review(expected_version=4,confirm=True))['usable']

@pytest.mark.parametrize('metadata',['present','missing','forged_empty'])
def test_indirect_revoked_history_cannot_be_laundered_by_snapshot(client,metadata):
 pid,_,chunk=seed(client)
 with db.get_db() as conn:
  direct=conn.execute('SELECT m.id,m.conversation_id FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.project_id=?',(pid,)).fetchone()
  conn.execute("UPDATE messages SET status='complete',content=?,citations=? WHERE id=?",('DIRECT_DEPENDENT_731 [S1]',json.dumps([chunk]),direct['id']))
  links=context_lineage.source_links(conn,pid,[chunk]);assert context_lineage.authorized(conn,pid,links);context_lineage.save(conn,direct['id'],links)
  indirect=db.uid();conn.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',(indirect,direct['conversation_id'],'assistant','INDIRECT_DEPENDENT_731','complete','test',db.now(),'[]'));context_lineage.save(conn,indirect,context_lineage.merge(links))
  conn.execute('DELETE FROM knowledge_files WHERE id=?',(chunk['file_id'],))
  history=[dict(r) for r in conn.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY rowid',(direct['conversation_id'],))];kept,_=context_lineage.filter_history(conn,pid,history);assert not any(m['role']=='assistant' for m in kept)
 exported=client.get(f'/api/projects/{pid}/snapshot');assert exported.status_code==200,exported.text
 assert 'DIRECT_DEPENDENT_731' not in exported.text and 'INDIRECT_DEPENDENT_731' not in exported.text
 snapshot=exported.json()
 for conversation in snapshot['conversations']:
  for message in conversation['messages']:
   if metadata=='missing':message.pop('context_dependencies',None)
   elif metadata=='forged_empty':message['context_dependencies']=[]
 response=client.post('/api/projects/import',json={'snapshot':snapshot,'confirm':True});assert response.status_code==201,response.text
 with db.get_db() as conn:
  rows=[dict(r) for r in conn.execute('SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.project_id=? ORDER BY m.rowid',(response.json()['id'],))]
  assert not any('DEPENDENT_731' in m['content'] for m in rows)
  assert all(context_lineage.read(conn,m['id'])==[context_lineage.BLOCKED] for m in rows if m['role']=='assistant')
  kept,dependencies=context_lineage.filter_history(conn,response.json()['id'],rows);assert not any(m['role']=='assistant' for m in kept) and dependencies==[]
