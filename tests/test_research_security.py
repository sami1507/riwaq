"""Research scope/receipt regressions. Network and jobs are explicitly simulated.
Evidence routes use real temporary SQLite tables and source-binding helpers.
"""
import asyncio
import copy
import json
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
from authority_fixture import fixture_owner
import pytest

from backend import research, public_knowledge as public, knowledge
from backend.db import get_db, now
from backend.knowledge_fetch import FetchResult
from backend.research_schema import ResearchOptions


@pytest.fixture
def evidence_api(tmp_path, monkeypatch):
    monkeypatch.setenv("RIWAQ_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RIWAQ_KNOWLEDGE_REGISTRY", str(tmp_path / "no-registry.json"))
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,instructions TEXT,created_at TEXT);
        CREATE TABLE conversations(id TEXT PRIMARY KEY,project_id TEXT REFERENCES projects(id),title TEXT,created_at TEXT,updated_at TEXT,parent_id TEXT);
        CREATE TABLE messages(id TEXT PRIMARY KEY,conversation_id TEXT REFERENCES conversations(id),role TEXT,content TEXT,status TEXT,model TEXT,created_at TEXT,citations TEXT);
        """)
        for pid in ("p1", "p2"):
            conn.execute("INSERT INTO projects VALUES(?,?,?,?)", (pid, pid, "", now()))
            conn.execute("INSERT INTO conversations VALUES(?,?,?,?,?,?)", ("c" + pid, pid, "fixture", now(), now(), None))
        knowledge.init_knowledge(conn)
        public.init_public_knowledge(conn)
        research.initialize(conn)
        source = public.source_validate({
            "id": "source-test", "name": "Original public fixture", "base_url": "https://docs.example.org/",
            "allowed_hosts": ["docs.example.org"], "allowed_prefixes": ["/docs/"],
            "urls": [{"url": "https://docs.example.org/docs/test.txt", "title": "Original public fixture"}],
            "status": "allowed", "rights_confirmed": True, "content_type": "text", "version": "fixture-v1",
            "license": {"name": "Original test data", "url": "https://docs.example.org/license", "storage_allowed": True,
                        "attribution": "Test authors", "reviewed_at": "2026-11-15", "evidence": "Original synthetic fixture, no network"},
        })
        conn.execute("INSERT INTO public_sources(id,data,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                     (source["id"], json.dumps(source), "allowed", now(), now()))
        body = b"Original public evidence reports 42 units."
        fetched = FetchResult(source["urls"][0]["url"], 200, {"content-type": "text/plain"}, body)
        document_id, _ = public.store_document(conn, source, source["urls"][0], fetched, [(None, body.decode())], "Original test data")
        citation = next(c for c in public.corpus_rows(conn) if c["document_id"] == document_id)
        for pid in ("p1", "p2"):
            text = "Private source for " + pid
            conn.execute("INSERT INTO knowledge_files(id,project_id,filename,byte_size,sha256,chunk_count,page_count,char_count,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         ("f" + pid, pid, "note.txt", len(text), "a" * 64, 1, 1, len(text), now()))
            conn.execute("INSERT INTO knowledge_chunks(id,file_id,project_id,page,line_start,line_end,text) VALUES(?,?,?,?,?,?,?)",
                         ("k" + pid, "f" + pid, pid, 1, 1, 1, text))
    app = FastAPI(); app.include_router(research.router)
    def save_message(citation, *, pid="p1", mid="m1"):
        with get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO messages VALUES(?,?,?,?,?,?,?,?)", (mid, "c" + pid, "assistant", "Stored answer [S1]", "complete", "fixture", now(), json.dumps([citation])))
        return mid
    async def request(pid="p1", mid="m1", index=0):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture.local") as client:
            return await client.get(f"/api/projects/{pid}/research/evidence/{mid}/{index}")
    with fixture_owner():
        yield SimpleNamespace(citation=citation, source=source, document_id=document_id,
                              save=save_message, get=lambda *args, **kwargs: asyncio.run(request(*args, **kwargs)))


def test_evidence_endpoint_binds_message_project_and_actual_quote(evidence_api):
    api = evidence_api; api.save(api.citation)
    response = api.get()
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["excerpt"] == api.citation["text"] and response.json()["current"] is True
    assert api.get("p2").status_code == 404
    assert api.get(index=-1).status_code == 404 and api.get(index=99).status_code == 404
    bad = copy.deepcopy(api.citation); bad["text"] = "Invented quote"
    api.save(bad)
    assert api.get().status_code == 404


def test_private_citation_cannot_cross_projects(evidence_api):
    api = evidence_api
    api.save({"id": "kp2", "scope": "private", "text": "Private source for p2"})
    assert api.get().status_code == 404
    api.save({"id": "kp1", "scope": "private", "text": "Private source for p1"})
    assert api.get().status_code == 200
    with get_db() as conn: conn.execute("DELETE FROM knowledge_files WHERE id='fp1'")
    assert api.get().status_code == 404


@pytest.mark.parametrize("mutation,status", [("snapshot", 409), ("hash", 409), ("version", 409), ("suspend", 404), ("delete", 404)])
def test_inert_import_and_revoked_or_mismatched_public_bindings(evidence_api, mutation, status):
    api = evidence_api; citation = copy.deepcopy(api.citation)
    if mutation == "snapshot": citation["provenance"] = "stored_excerpt"
    elif mutation == "hash": citation["sha256"] = "0" * 64
    elif mutation == "version": citation["version_id"] = "invented-version"
    elif mutation == "suspend":
        with get_db() as conn: conn.execute("UPDATE public_sources SET status='suspended' WHERE id=?", (api.source["id"],))
    elif mutation == "delete":
        with get_db() as conn: conn.execute("DELETE FROM public_documents WHERE id=?", (api.document_id,))
    api.save(citation)
    assert api.get().status_code == status


def test_historical_evidence_never_claims_current_verification(evidence_api):
    api = evidence_api; api.save(api.citation)
    with get_db() as conn:
        body = b"A newer original public fixture reports 43 units."
        public.store_document(conn, api.source, api.source["urls"][0],
            FetchResult(api.source["urls"][0]["url"], 200, {"content-type": "text/plain"}, body), [(None, body.decode())], "Original test data")
    response = api.get()
    assert response.status_code == 200 and response.json()["current"] is False
    assert "Historical" in response.json()["notice"] and response.json()["excerpt"] == api.citation["text"]


@pytest.mark.parametrize("saved_scope", [False, True], ids=["web-only", "web-and-saved-knowledge"])
def test_live_read_stamp_cannot_migrate_to_a_different_version(monkeypatch, saved_scope):
    old = {"id": "old-chunk", "document_id": "document", "version_id": "version-A", "text": "Original content"}
    newer = {"id": "new-chunk", "document_id": "document", "version_id": "version-B", "text": "Unfetched newer content"}
    async def scenario():
        calls = []
        async def retrieve(*args, **kwargs):
            calls.append(True)
            return [copy.deepcopy(old if len(calls) == 1 else newer)]
        async def selected(items, **kwargs):
            completed = asyncio.get_running_loop().create_future(); completed.set_result(None)
            public.TASKS["job"] = completed
            return {"id": "job"}
        job = {"id": "job", "status": "completed", "downloaded_bytes": 31, "request_count": 3,
               "items": [{"status": "accepted", "document_id": "document", "version_id": "version-A", "updated_at": "2026-11-15T00:00:00+00:00"}]}
        monkeypatch.setattr(research, "settings", lambda: {"mode": "hybrid"})
        monkeypatch.setattr(public, "retrieve_public", retrieve)
        monkeypatch.setattr(public, "start_selected_job", selected)
        monkeypatch.setattr(public, "get_job", lambda _: copy.deepcopy(job))
        monkeypatch.setattr(public, "TASKS", {})
        monkeypatch.setattr(research, "catalogue_candidates", lambda *args: [{"source_id": "source", "url": "https://docs.example.org/test"}])
        options = ResearchOptions(scopes=["web", "knowledge"] if saved_scope else ["web"], allow_network=True)
        receipt = research.new_receipt(options)
        rows = await research.prepare("Question", options, asyncio.Event(), receipt)
        assert receipt["documents_read"] == 1
        if saved_scope:
            assert rows and rows[0]["version_id"] == "version-B" and rows[0].get("checked_at") is None
        else:
            assert rows == []
    asyncio.run(scenario())


def test_cancelling_live_read_waits_for_owned_job_cleanup(monkeypatch):
    async def scenario():
        stop, released, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
        cancel_calls = []
        async def retrieve(*args, **kwargs): return []
        async def worker():
            try: await released.wait()
            finally: cleaned.set()
        async def selected(*args, **kwargs):
            public.TASKS["job"] = asyncio.create_task(worker())
            return {"id": "job"}
        async def cancel(job_id): cancel_calls.append(job_id); released.set()
        def progress(_):
            stop.set()
            return {"downloaded_bytes": 0, "request_count": 0, "items": []}
        monkeypatch.setattr(research, "settings", lambda: {"mode": "hybrid"})
        monkeypatch.setattr(public, "retrieve_public", retrieve)
        monkeypatch.setattr(public, "start_selected_job", selected)
        monkeypatch.setattr(public, "cancel_job", cancel)
        monkeypatch.setattr(public, "get_job", progress)
        monkeypatch.setattr(public, "TASKS", {})
        monkeypatch.setattr(research, "catalogue_candidates", lambda *args: [{"source_id": "source", "url": "https://docs.example.org/test"}])
        options = ResearchOptions(scopes=["web"], allow_network=True)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(research.prepare("Question", options, stop, research.new_receipt(options)), 2)
        assert cancel_calls and cleaned.is_set() and public.TASKS["job"].done()
    asyncio.run(scenario())


@pytest.mark.parametrize("mode,consent", [("local", True), ("hybrid", False)])
def test_live_read_requires_both_mode_and_request_consent(monkeypatch, mode, consent):
    async def forbidden(*args, **kwargs): pytest.fail("Denied live read must not retrieve or create a network job")
    monkeypatch.setattr(research, "settings", lambda: {"mode": mode})
    monkeypatch.setattr(public, "retrieve_public", forbidden)
    monkeypatch.setattr(public, "start_selected_job", forbidden)
    options = ResearchOptions(scopes=["web"], allow_network=consent)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(research.prepare("Question", options, None, research.new_receipt(options)))
    assert exc.value.status_code == 403
