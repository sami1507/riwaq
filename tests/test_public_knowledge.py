import asyncio
from contextlib import contextmanager
import hashlib
import json
import socket
import sqlite3
import struct
import threading
import time
from datetime import datetime, timezone
from email.utils import format_datetime

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend import public_knowledge as public
from backend import knowledge_fetch as fetch


def approved_source(identifier="source-one"):
    return {"id": identifier, "name": "Approved fixture", "base_url": "https://docs.example.org/", "allowed_hosts": ["docs.example.org"], "allowed_prefixes": ["/docs/"], "urls": [{"url": "https://docs.example.org/docs/start.md", "title": "Launch policy", "language": "en", "version": "1.0"}], "status": "allowed", "rights_confirmed": True, "content_type": "markdown", "domain": "testing", "language": "en", "version": "1.0", "fetch_limits": {"delay_seconds": .2, "max_documents": 10}, "license": {"name": "MIT", "url": "https://docs.example.org/license", "storage_allowed": True, "redistribution_allowed": True, "attribution": "Fixture author", "reviewed_at": "2026-11-12", "evidence": "Synthetic test license fixture; not a real network source"}}


@pytest.fixture
def environment(tmp_path, monkeypatch):
    from backend import research_extract
    # This API suite isolates orchestration. Real fixed-worker parsing has its
    # own tests; no subprocess/network/model is implied by these fixtures.
    monkeypatch.setattr(research_extract,'extract_bounded',lambda body,source,url,content_type,stop=None: public.extract_public(body,source,url,content_type))
    monkeypatch.setenv("RIWAQ_KNOWLEDGE_REGISTRY", str(tmp_path / "missing.json"))
    monkeypatch.setenv("RIWAQ_DATA_DIR", str(tmp_path))
    path = tmp_path / "test-public.sqlite3"
    @contextmanager
    def db():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
    monkeypatch.setattr(public, "get_db", db)
    monkeypatch.setattr(public, "settings", lambda: {"mode": "hybrid"})
    async def no_embedding():
        return {"configured": False, "available": False, "model": None}
    monkeypatch.setattr(public, "embedding_info", no_embedding)
    with db() as conn:
        public.init_public_knowledge(conn)
    app = FastAPI()
    app.include_router(public.router)
    with TestClient(app) as client:
        yield client, db, tmp_path


def add_fixture(client, identifier="source-one"):
    response = client.post("/api/knowledge/sources", json=approved_source(identifier))
    assert response.status_code == 201, response.text
    return response.json()


def fake_network(monkeypatch, document=b"# Launch policy\nLaunch occurs in December.\nBudget is 41 units."):
    def read(url, **kwargs):
        body = b"Fixture MIT license" if url.endswith("/license") else document
        if kwargs.get("charge"):
            kwargs["charge"](len(body))
        return fetch.FetchResult(url, 200, {"content-type": "text/plain", "etag": '"v1"', "last-modified": "Mon, 02 Nov 2026 00:00:00 GMT"}, body)
    monkeypatch.setattr(public, "fetch_https", read)
    monkeypatch.setattr(public, "robots_policy", lambda *args, **kwargs: {"allowed": True, "delay_seconds": None, "status": 200})
    return read


def complete_job(client, source_id="source-one"):
    response = client.post("/api/knowledge/jobs", json={"source_ids": [source_id], "max_documents": 1, "allow_network": True})
    assert response.status_code == 202, response.text
    job = response.json()
    for _ in range(100):
        current = client.get("/api/knowledge/jobs/" + job["id"]).json()
        if current["status"] not in {"running", "queued"}:
            return current
        time.sleep(.02)
    raise AssertionError("Synthetic ingestion did not finish within two seconds")


@pytest.mark.parametrize("url", ["http://docs.example.org/docs/a", "https://user:secret@docs.example.org/docs/a", "https://127.0.0.1/docs/a", "https://docs.example.org:8443/docs/a", "https://docs.example.org/docs/%2e%2e/secrets", "https://docs.example.org/docs/%252e%252e/secrets", "https://docs.example.org/docs/a%00", "https://evil.example/docs/a"])
def test_fetch_rejects_unsafe_urls_and_scope(url):
    with pytest.raises(fetch.FetchError):
        fetch.validate_scope(url, ["docs.example.org"], ["/docs/"])


def test_scope_boundary_is_not_string_prefix():
    assert fetch.validate_scope("https://docs.example.org/docs/a#section", ["docs.example.org"], ["/docs/"]) == "https://docs.example.org/docs/a"
    with pytest.raises(fetch.FetchError):
        fetch.validate_scope("https://docs.example.org/docs-evil/a", ["docs.example.org"], ["/docs/"])


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "100.64.0.1", "::1", "::ffff:8.8.8.8", "224.0.0.1", "2002:7f00:1::", "2001:0000:4136:e378:8000:63bf:3fff:fdd2"])
def test_dns_rejects_nonpublic_and_mapped_addresses(monkeypatch, address):
    monkeypatch.setattr(fetch.socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))])
    with pytest.raises(fetch.FetchError):
        fetch.resolve_public("docs.example.org")


def test_dns_mixed_public_private_fails_closed(monkeypatch):
    monkeypatch.setattr(fetch.socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ("8.8.8.8", "127.0.0.1")])
    with pytest.raises(fetch.FetchError):
        fetch.resolve_public("docs.example.org")


def test_budget_is_never_overread_and_connections_are_pinned(monkeypatch):
    charged, addresses = [], []
    class Reply:
        status = 200
        length = 6
        def getheaders(self): return []
        def read1(self, amount):
            size = min(amount, self.length); self.length -= size
            return b"x" * size
    class Connection:
        sock = None
        def __init__(self, host, address, timeout): addresses.append((host, address))
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Reply()
        def close(self): pass
    monkeypatch.setattr(fetch, "resolve_public", lambda *args, **kwargs: ["8.8.8.8"])
    monkeypatch.setattr(fetch, "PinnedHTTPSConnection", Connection)
    with pytest.raises(fetch.FetchError, match="budget"):
        fetch.fetch_https("https://docs.example.org/docs/a", allowed_hosts=["docs.example.org"], max_bytes=100, charge=charged.append, remaining_budget=lambda: 5 - sum(charged))
    assert sum(charged) == 5
    assert addresses == [("docs.example.org", "8.8.8.8")]


def test_redirect_revalidates_allowlist(monkeypatch):
    class Reply:
        status = 302
        def getheaders(self): return [("location", "https://evil.example/private")]
    class Connection:
        def __init__(self, *args, **kwargs): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Reply()
        def close(self): pass
    monkeypatch.setattr(fetch, "resolve_public", lambda *args, **kwargs: ["8.8.8.8"])
    monkeypatch.setattr(fetch, "PinnedHTTPSConnection", Connection)
    with pytest.raises(fetch.FetchError, match="allowlist"):
        fetch.fetch_https("https://docs.example.org/docs/a", allowed_hosts=["docs.example.org"])


def test_robots_missing_allowed_denied_and_failure(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_https", lambda *args, **kwargs: fetch.FetchResult(args[0], 404, {}, b""))
    assert fetch.robots_policy("https://docs.example.org/docs/a", allowed_hosts=["docs.example.org"])["allowed"]
    monkeypatch.setattr(fetch, "fetch_https", lambda *args, **kwargs: fetch.FetchResult(args[0], 200, {}, b"User-agent: *\nDisallow: /docs/\n"))
    assert not fetch.robots_policy("https://docs.example.org/docs/a", allowed_hosts=["docs.example.org"])["allowed"]
    monkeypatch.setattr(fetch, "fetch_https", lambda *args, **kwargs: fetch.FetchResult(args[0], 503, {}, b""))
    with pytest.raises(fetch.FetchError):
        fetch.robots_policy("https://docs.example.org/docs/a", allowed_hosts=["docs.example.org"])


def test_rights_gate_and_local_preview(environment, monkeypatch):
    client, _, _ = environment
    body = approved_source(); body["status"] = "candidate"; body["rights_confirmed"] = False; body["license"]["reviewed_at"] = None
    assert client.post("/api/knowledge/sources", json=body).status_code == 201
    denied = client.patch("/api/knowledge/sources/source-one", json={"status": "allowed"})
    assert denied.status_code == 422
    monkeypatch.setattr(public, "fetch_https", lambda *args, **kwargs: pytest.fail("Preview must not fetch"))
    plan = client.post("/api/knowledge/preview", json={"source_ids": ["source-one"]}).json()
    assert plan["documents"][0]["state"] == "blocked"
    assert client.post("/api/knowledge/jobs", json={"source_ids": ["source-one"], "allow_network": True}).status_code == 422


def test_network_requires_mode_and_explicit_consent(environment, monkeypatch):
    client, _, _ = environment; add_fixture(client)
    assert client.post("/api/knowledge/jobs", json={"source_ids": ["source-one"]}).status_code == 403
    monkeypatch.setattr(public, "settings", lambda: {"mode": "local"})
    assert client.post("/api/knowledge/jobs", json={"source_ids": ["source-one"], "allow_network": True}).status_code == 403


def test_real_storage_atomic_versions_idempotence_and_public_scope(environment, monkeypatch):
    client, db, _ = environment; add_fixture(client); fake_network(monkeypatch)
    first = complete_job(client)
    assert first["status"] == "completed", first
    assert first["fetched_files"] == 1 and first["accepted_documents"] == 1
    doc = client.get("/api/knowledge/documents").json()[0]
    source = client.get("/api/knowledge/search", params={"q": "budget"}).json()["chunks"][0]
    assert source["scope"] == "public" and source["line_start"] == 1 and source["line_end"] == 3
    assert source["version"] == "1.0" and source["sha256"] == doc["sha256"]
    assert client.get("/api/knowledge/search", params={"q": "budget", "language": "ar"}).json()["chunks"] == []
    again = complete_job(client)
    assert again["accepted_documents"] == 0 and again["fetched_files"] == 1
    assert len(client.get(f"/api/knowledge/documents/{doc['id']}").json()["versions"]) == 1
    fake_network(monkeypatch, b"# Launch policy\nBudget is now 52 units.")
    assert complete_job(client)["accepted_documents"] == 1
    details = client.get(f"/api/knowledge/documents/{doc['id']}").json()
    assert len(details["versions"]) == 2 and details["revision"] == 2
    with db() as conn:
        from backend.knowledge import init_knowledge
        conn.execute("CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,instructions TEXT,created_at TEXT)")
        conn.execute("INSERT INTO projects VALUES('private-project','Private','private-instruction-sentinel','2026-11-12')")
        conn.execute("CREATE TABLE memories(id TEXT PRIMARY KEY,scope TEXT,project_id TEXT,content TEXT,created_at TEXT)")
        conn.execute("INSERT INTO memories VALUES('private-memory','project','private-project','private-memory-sentinel','2026-11-12')")
        init_knowledge(conn)
        conn.execute("INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)", ('private-file','private-project','private.txt',20,'a'*64,1,0,20,'2026-11-12'))
        conn.execute("INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?)", ('private-chunk','private-file','private-project',None,1,1,'private-sentinel-783'))
        assert not public.lexical_public(conn, "private-sentinel-783")
        assert not public.lexical_public(conn, "private-memory-sentinel")
        assert all(row['id'] != 'private-chunk' and 'private-sentinel' not in row['text'] for row in public.corpus_rows(conn))
        assert {row["version_id"] for row in public.corpus_rows(conn)} == {details["current_version_id"]}
    original = client.get(f"/api/knowledge/documents/{doc['id']}/original").content
    assert hashlib.sha256(original).hexdigest() == details["sha256"]


def test_changed_license_suspends_durably_and_blocks_content(environment, monkeypatch):
    client, db, _ = environment; add_fixture(client); fake_network(monkeypatch); complete_job(client)
    doc = client.get("/api/knowledge/documents").json()[0]
    def changed(url, **kwargs):
        return fetch.FetchResult(url, 200, {}, b"Changed license requires review")
    monkeypatch.setattr(public, "fetch_https", changed)
    job = complete_job(client)
    assert job["status"] == "partial"
    source = client.get("/api/knowledge/sources").json()[0]
    assert source["status"] == "suspended" and source["license_change_pending"]
    assert client.get("/api/knowledge/search", params={"q": "budget"}).json()["chunks"] == []
    assert client.get(f"/api/knowledge/documents/{doc['id']}/original").status_code == 403
    inspected = client.get(f"/api/knowledge/documents/{doc['id']}").json()
    assert inspected["content_blocked"] and inspected["chunks"] == []
    assert "extracted_text" not in inspected


def test_delete_cascades_and_never_refunds_budget(environment, monkeypatch):
    client, db, _ = environment; add_fixture(client); fake_network(monkeypatch); complete_job(client)
    before = client.get("/api/knowledge/status").json()["downloaded_bytes"]
    doc = client.get("/api/knowledge/documents").json()[0]
    assert client.delete(f"/api/knowledge/documents/{doc['id']}").status_code == 403
    assert client.delete(f"/api/knowledge/documents/{doc['id']}?confirm=true").status_code == 200
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM public_chunks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM public_versions").fetchone()[0] == 0
    assert client.get("/api/knowledge/status").json()["downloaded_bytes"] == before


def test_deleted_seed_source_stays_deleted(environment, monkeypatch):
    client, db, folder = environment
    registry = folder / "seed.json"; registry.write_text(json.dumps({"sources": [approved_source()]}))
    monkeypatch.setenv("RIWAQ_KNOWLEDGE_REGISTRY", str(registry))
    with db() as conn: public.init_public_knowledge(conn)
    assert client.delete("/api/knowledge/sources/source-one?confirm=true").status_code == 200
    with db() as conn:
        public.init_public_knowledge(conn)
        assert conn.execute("SELECT COUNT(*) FROM public_sources").fetchone()[0] == 0


def test_fetched_counter_includes_parse_failure(environment, monkeypatch):
    client, _, _ = environment; add_fixture(client); fake_network(monkeypatch, b"bad\x00binary")
    job = complete_job(client)
    assert job["status"] == "partial" and job["fetched_files"] == 1 and job["accepted_documents"] == 0


def test_external_ledger_seed_cannot_reduce_cumulative_bytes(environment, monkeypatch):
    client, db, folder = environment
    registry = folder / "seed.json"; monkeypatch.setenv("RIWAQ_KNOWLEDGE_REGISTRY", str(registry))
    for amount in (500, 100):
        registry.write_text(json.dumps({"external_downloads": [{"id": "model", "name": "model", "bytes": amount}]}))
        with db() as conn: public.init_public_knowledge(conn)
    assert client.get("/api/knowledge/status").json()["downloaded_bytes"] == 500


def test_cancellation_preserves_pending_work_and_resume(environment, monkeypatch):
    client, _, _ = environment; add_fixture(client)
    original = fake_network(monkeypatch)
    entered = threading.Event()
    def blocking(url, **kwargs):
        if not url.endswith("/license"):
            entered.set()
            kwargs["cancelled"].wait(2)
            if kwargs["cancelled"].is_set(): raise fetch.FetchCancelled("cancelled")
        return original(url, **kwargs)
    monkeypatch.setattr(public, "fetch_https", blocking)
    response = client.post("/api/knowledge/jobs", json={"source_ids": ["source-one"], "allow_network": True}).json()
    assert entered.wait(1)
    client.post(f"/api/knowledge/jobs/{response['id']}/cancel")
    for _ in range(100):
        job = client.get(f"/api/knowledge/jobs/{response['id']}").json()
        if job["status"] == "cancelled": break
        time.sleep(.01)
    assert job["status"] == "cancelled" and job["items"][0]["status"] == "pending"
    monkeypatch.setattr(public, "fetch_https", original)
    resumed = client.post(f"/api/knowledge/jobs/{response['id']}/resume", json={"allow_network": True})
    assert resumed.status_code == 202
    for _ in range(100):
        job = client.get(f"/api/knowledge/jobs/{response['id']}").json()
        if job["status"] == "completed": break
        time.sleep(.01)
    assert job["status"] == "completed"


def test_deadline_closes_active_connection():
    called = []
    class Connection:
        sock = None
        def close(self): called.append(True)
    control = public.JobControl(seconds=.02)
    control.attach(Connection())
    try:
        assert control.wait(.5)
        for _ in range(20):
            if called: break
            time.sleep(.005)
        assert called
    finally:
        control.finish()


def test_wikidata_text_preserves_three_languages_and_numeric_claims():
    payload = {"entities": {"Q2": {"labels": {"en": {"value": "Earth"}, "ar": {"value": "الأرض"}, "he": {"value": "כדור הארץ"}}, "descriptions": {"en": {"value": "planet"}}, "claims": {"P1": [{"mainsnak": {"datavalue": {"type": "quantity", "value": {"amount": "+42", "unit": "1"}}}}]}}}}
    pages = public.extract_public(json.dumps(payload).encode(), {"content_type": "wikidata-json"}, "https://www.wikidata.org/w/api.php", "application/json")
    assert all(value in pages[0][1] for value in ("Earth", "الأرض", "כדור הארץ", "+42"))


def test_heading_chunks_preserve_spans_and_do_not_treat_code_as_headings():
    text = "# First section\n" + "\n".join(f"First fact {i}." for i in range(15)) + "\n# Second section\n```python\n# code comment\nprint('ok')\n```\nLast fact."
    source = approved_source() | {"chunking": {"max_characters": 400, "max_lines": 8, "overlap_lines": 2, "respect_headings": True}}
    chunks = public.chunk_public([(None, text)], source, "https://docs.example.org/docs/start.md", "text/plain")
    assert {chunk["heading"] for chunk in chunks} == {"First section", "Second section"}
    lines = text.splitlines()
    assert all(chunk["text"] == "\n".join(lines[chunk["line_start"] - 1:chunk["line_end"]]) for chunk in chunks)
    assert all(len(chunk["text"]) <= 400 and chunk["line_end"] - chunk["line_start"] + 1 <= 8 for chunk in chunks)
    assert chunks[1]["line_start"] == chunks[0]["line_end"] - 1
    assert all("First fact" not in chunk["text"] for chunk in chunks if chunk["heading"] == "Second section")


def test_policy_change_on_304_rebuilds_from_original_without_refetch(environment, monkeypatch):
    client, db, _ = environment; add_fixture(client)
    document = ("# Heading\n" + "\n".join(f"Verified fact {i}." for i in range(30))).encode()
    fake_network(monkeypatch, document)
    assert complete_job(client)["accepted_documents"] == 1
    first = client.get("/api/knowledge/documents").json()[0]
    detail = client.get(f"/api/knowledge/documents/{first['id']}").json()
    first_chunk_ids = {row["id"] for row in detail["chunks"]}
    policy = {"max_characters": 400, "max_lines": 8, "overlap_lines": 1, "respect_headings": True}
    assert client.patch("/api/knowledge/sources/source-one", json={"chunking": policy}).status_code == 200
    calls = []
    def unchanged(url, **kwargs):
        calls.append((url, kwargs.get("conditional")))
        return fetch.FetchResult(url, 304, {"etag": '"v1"'}, b"")
    monkeypatch.setattr(public, "fetch_https", unchanged)
    job = complete_job(client)
    assert job["status"] == "completed", job
    assert job["fetched_files"] == 0 and job["accepted_documents"] == 1
    updated = client.get(f"/api/knowledge/documents/{first['id']}").json()
    assert updated["revision"] == 2 and len(updated["versions"]) == 2
    assert updated["sha256"] == first["sha256"]
    assert first_chunk_ids.isdisjoint({row["id"] for row in updated["chunks"]})
    assert len(updated["chunks"]) > len(detail["chunks"])
    assert client.get(f"/api/knowledge/documents/{first['id']}/original").content == document
    assert any(url.endswith("/start.md") and conditional["If-None-Match"] == '"v1"' for url, conditional in calls)
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM public_chunks WHERE version_id=?", (detail["current_version_id"],)).fetchone()[0] == len(first_chunk_ids)
        assert {row["version_id"] for row in public.corpus_rows(conn)} == {updated["current_version_id"]}
    assert complete_job(client)["accepted_documents"] == 0


def test_unknown_publication_dates_stay_null_and_known_dates_are_provenance(environment, monkeypatch):
    client, _, _ = environment; add_fixture(client); fake_network(monkeypatch)
    complete_job(client)
    first = client.get("/api/knowledge/documents").json()[0]
    assert first["published_at"] is None and first["modified_at"] is None
    assert first["fetched_at"] and first["last_modified"]  # HTTP freshness is not a publication date.
    citation = client.get("/api/knowledge/search", params={"q": "budget"}).json()["chunks"][0]
    assert citation["published_at"] is None and citation["modified_at"] is None
    item = approved_source()["urls"][0] | {"published_at": "2025-10-03", "modified_at": "2026-11-01T09:00:00Z"}
    assert client.patch("/api/knowledge/sources/source-one", json={"urls": [item]}).status_code == 200
    assert complete_job(client)["accepted_documents"] == 1
    current = client.get(f"/api/knowledge/documents/{first['id']}").json()
    assert current["published_at"] == item["published_at"] and current["modified_at"] == item["modified_at"]
    assert len(current["versions"]) == 2 and current["versions"][1]["published_at"] is None
    bad = item | {"published_at": "unknown, perhaps yesterday"}
    assert client.patch("/api/knowledge/sources/source-one", json={"urls": [bad]}).status_code == 422


@pytest.mark.parametrize("mutation,embedding_failure", [("suspend", False), ("delete", False), ("suspend", True)])
def test_retrieval_rechecks_authorization_after_embedding(environment, monkeypatch, mutation, embedding_failure):
    client, db, _ = environment; add_fixture(client); fake_network(monkeypatch)
    complete_job(client)
    info = {"configured": True, "available": True, "model": "fixture-embedding", "digest": "d" * 64}
    with db() as conn:
        chunk = public.corpus_rows(conn)[0]
        conn.execute("INSERT INTO public_embeddings VALUES(?,?,?,?,?)", (chunk["id"], info["model"], info["digest"], 2, struct.pack("<ff", 1., 0.)))
    async def available():
        return info
    async def delayed_vectors(*args):
        # Simulate a user changing source access while the local embed awaits.
        with db() as conn:
            if mutation == "suspend":
                conn.execute("UPDATE public_sources SET status='suspended' WHERE id='source-one'")
            else:
                conn.execute("DELETE FROM public_documents WHERE id=?", (chunk["document_id"],))
        if embedding_failure:
            raise ValueError("Local embedding failed after source access changed")
        return [[1., 0.]]
    monkeypatch.setattr(public, "embedding_info", available)
    monkeypatch.setattr(public, "vectors", delayed_vectors)
    assert asyncio.run(public.retrieve_public("budget")) == []


class FakePacerClock:
    def __init__(self):
        self.seconds = 0.
        self.wall_origin = datetime(2026, 11, 12, tzinfo=timezone.utc).timestamp()
        self.waits = []
    def clock(self):
        return self.seconds
    def wall_clock(self):
        return self.wall_origin + self.seconds
    def wait(self, seconds, cancelled):
        self.waits.append(seconds)
        self.seconds += seconds
        return bool(cancelled and cancelled.is_set())
    def pacer(self):
        return fetch.HostPacer(clock=self.clock, wall_clock=self.wall_clock, wait=self.wait)


def test_host_pacer_uses_completion_gap_strictest_delay_and_independent_hosts():
    clock = FakePacerClock(); pacer = clock.pacer()
    pacer.set_delay("a.example", 1)
    pacer.acquire("a.example")
    clock.seconds += .25
    pacer.finish("a.example")
    pacer.set_delay("a.example", 3)
    pacer.set_delay("a.example", .5)
    pacer.acquire("b.example")
    assert clock.seconds == .25
    pacer.acquire("a.example")
    assert clock.seconds == 3.25 and clock.waits == [3.]


def test_host_pacer_retry_after_numeric_date_and_invalid_header():
    clock = FakePacerClock(); pacer = clock.pacer()
    assert pacer.defer_retry_after("a.example", "5")
    assert pacer.defer_retry_after("a.example", "2")  # Never shorten a prior cooldown.
    assert not pacer.defer_retry_after("a.example", "-1")
    assert not pacer.defer_retry_after("a.example", "tomorrow")
    pacer.acquire("a.example")
    assert clock.seconds == 5
    date = format_datetime(datetime.fromtimestamp(clock.wall_origin + 20, timezone.utc), usegmt=True)
    assert pacer.defer_retry_after("a.example", date)
    pacer.acquire("a.example")
    assert clock.seconds == 20


def test_host_pacer_wait_cancels_without_starting_request():
    clock = FakePacerClock(); stop = threading.Event()
    def cancelled_wait(seconds, cancelled):
        stop.set()
        return True
    pacer = fetch.HostPacer(clock=clock.clock, wall_clock=clock.wall_clock, wait=cancelled_wait)
    pacer.defer_retry_after("a.example", "100")
    with pytest.raises(fetch.FetchCancelled):
        pacer.acquire("a.example", stop)
    assert clock.seconds == 0
    with pytest.raises(fetch.FetchCancelled):
        pacer.acquire("b.example", stop)


def test_host_pacer_retry_after_cannot_overrun_job_deadline():
    clock = FakePacerClock(); pacer = clock.pacer(); stop = threading.Event()
    stop.deadline = 2.
    pacer.defer_retry_after("a.example", "5")
    with pytest.raises(fetch.FetchCancelled):
        pacer.acquire("a.example", stop)
    assert clock.seconds == 2 and clock.waits == [2.]


def test_decimal_robots_crawl_delay_rounds_up(monkeypatch):
    clock = FakePacerClock(); pacer = clock.pacer()
    monkeypatch.setattr(fetch, "fetch_https", lambda url, **kwargs: fetch.FetchResult(url, 200, {}, b"User-agent: *\nCrawl-delay: 0.5\nAllow: /\n"))
    policy = fetch.robots_policy("https://docs.example.org/docs/a", allowed_hosts=["docs.example.org"], pacer=pacer)
    assert policy["allowed"] and policy["delay_seconds"] == 1
    pacer.acquire("docs.example.org"); pacer.finish("docs.example.org")
    pacer.acquire("docs.example.org")
    assert clock.seconds == 1


def test_robots_license_redirect_and_document_all_share_host_pacing(monkeypatch):
    clock = FakePacerClock(); pacer = clock.pacer(); requests = []
    pacer.set_delay("docs.example.org", 1)
    replies = {
        "/robots.txt": (200, [], b"User-agent: *\nAllow: /\nCrawl-delay: 3\n"),
        "/license": (200, [], b"Fixture license"),
        "/docs/start": (302, [("location", "/docs/end"), ("retry-after", "5")], b""),
        "/docs/end": (200, [], b"Stored document"),
    }
    class Reply:
        def __init__(self, status, headers, body):
            self.status, self.headers, self.body = status, headers, body
            self.length = len(body)
        def getheaders(self): return self.headers
        def read1(self, amount):
            block, self.body = self.body[:amount], self.body[amount:]
            self.length = len(self.body)
            return block
    class Connection:
        sock = None
        def __init__(self, *args, **kwargs): pass
        def request(self, method, path, **kwargs):
            self.path = path
            requests.append((path, clock.seconds))
        def getresponse(self): return Reply(*replies[self.path])
        def close(self): clock.seconds += .25
    monkeypatch.setattr(fetch, "resolve_public", lambda *args, **kwargs: ["8.8.8.8"])
    monkeypatch.setattr(fetch, "PinnedHTTPSConnection", Connection)
    assert fetch.robots_policy("https://docs.example.org/docs/start", allowed_hosts=["docs.example.org"], pacer=pacer)["allowed"]
    fetch.fetch_https("https://docs.example.org/license", allowed_hosts=["docs.example.org"], pacer=pacer)
    result = fetch.fetch_https("https://docs.example.org/docs/start", allowed_hosts=["docs.example.org"], allowed_prefixes=["/docs/"], pacer=pacer)
    assert result.body == b"Stored document"
    assert requests == [("/robots.txt", 0.), ("/license", 3.25), ("/docs/start", 6.5), ("/docs/end", 11.5)]


def test_operator_limits_defaults_and_explicit_reductions(tmp_path):
    path = tmp_path / "registry.json"
    assert fetch.load_limits(path) == fetch.DEFAULT_LIMITS
    settings = {"max_documents": 7, "max_sources": 2, "download_bytes": 4096, "file_bytes": 256, "run_seconds": 5, "concurrent_domains": 1}
    path.write_text(json.dumps({"limits": settings}), encoding="utf-8")
    assert fetch.load_limits(path) == settings


@pytest.mark.parametrize("limits", [
    {"download_bytes": True}, {"file_bytes": 1.5}, {"run_seconds": 0},
    {"max_sources": 6}, {"download_bytes": 1024 ** 3 + 1},
    {"concurrent_domains": 2}, {"max_documents": 1001}, {"unknown_limit": 10}, ["not-a-mapping"],
])
def test_invalid_operator_limits_fail_closed(tmp_path, limits):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"limits": limits}), encoding="utf-8")
    with pytest.raises(ValueError):
        fetch.load_limits(path)


@pytest.fixture
def index_db(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY',str(tmp_path/'absent.json'))
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    path=tmp_path/'index.sqlite'
    @contextmanager
    def db():
        conn=sqlite3.connect(path)
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
    monkeypatch.setattr(public,'get_db',db)
    monkeypatch.setattr(public,'settings',lambda:{'mode':'hybrid'})
    with db() as conn:
        public.init_public_knowledge(conn)
        source=public.source_validate(approved_source())
        conn.execute('INSERT INTO public_sources(id,data,status,created_at,updated_at) VALUES(?,?,?,?,?)',(source['id'],json.dumps(source),'allowed',public.now(),public.now()))
    return db,source


def store_fixture(db,source,text='Old source facts.',*,staging=False):
    body=text.encode()
    with db() as conn:
        doc,changed=public.store_document(conn,source,source['urls'][0],fetch.FetchResult(source['urls'][0]['url'],200,{'content-type':'text/markdown','etag':'fixture'},body),[(1,text)],'Fixture MIT license',staging=staging)
        version=conn.execute('SELECT id FROM public_versions WHERE document_id=? ORDER BY revision DESC LIMIT 1',(doc,)).fetchone()[0]
    return doc,version


def embedding_fixture(monkeypatch, function=None):
    async def info(): return {'configured':True,'available':True,'model':'fixture-embed','digest':'fixture-digest'}
    async def vector(texts,info): return [[1.0,0.0] for _ in texts]
    monkeypatch.setattr(public,'embedding_info',info)
    monkeypatch.setattr(public,'vectors',function or vector)


@pytest.mark.asyncio
async def test_staged_partial_embeddings_are_invisible_and_resume_atomically(index_db,monkeypatch):
    db,source=index_db
    doc,old=store_fixture(db,source)
    doc,new=store_fixture(db,source,'\n'.join('New budget fact '+str(i)+' x'*90 for i in range(220)),staging=True)
    calls=0
    async def fail_second(texts,info):
        nonlocal calls
        calls+=1
        if calls==2: raise ValueError('fixture embedding outage')
        with db() as conn:
            assert conn.execute('SELECT current_version_id FROM public_documents WHERE id=?',(doc,)).fetchone()[0]==old
            assert all(row['version_id']==old for row in public.corpus_rows(conn))
        return [[1.0,0.0] for _ in texts]
    embedding_fixture(monkeypatch,fail_second)
    with pytest.raises(ValueError,match='outage'):
        await public.embed_document(doc,threading.Event(),version_id=new)
    with db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM public_embeddings').fetchone()[0]==8
        assert conn.execute('SELECT state FROM public_version_indexes WHERE version_id=?',(new,)).fetchone()[0]=='staged'
        assert all(row['version_id']==old for row in public.corpus_rows(conn))
    embedding_fixture(monkeypatch)
    assert await public.embed_document(doc,threading.Event(),version_id=new)=='hybrid_ready'
    with db() as conn:
        assert all(row['version_id']==new for row in public.corpus_rows(conn))
        assert conn.execute('SELECT COUNT(*) FROM public_versions').fetchone()[0]==2
        assert conn.execute('SELECT original FROM public_versions WHERE id=?',(old,)).fetchone()[0]==b'Old source facts.'
        count=conn.execute('SELECT COUNT(*) FROM public_chunks WHERE version_id=?',(new,)).fetchone()[0]
        assert conn.execute('SELECT COUNT(*) FROM public_embeddings').fetchone()[0]==count


@pytest.mark.asyncio
@pytest.mark.parametrize('configured',[False,True])
async def test_lexical_readiness_is_explicit_and_configured_failure_keeps_old(index_db,monkeypatch,configured):
    db,source=index_db;doc,old=store_fixture(db,source);_,new=store_fixture(db,source,'Updated facts.',staging=True)
    async def absent(): return {'available':False,'configured':configured,'model':'absent' if configured else None}
    monkeypatch.setattr(public,'embedding_info',absent)
    if configured:
        with pytest.raises(fetch.FetchError,match='unavailable'):
            await public.embed_document(doc,threading.Event(),version_id=new)
    else:
        assert await public.embed_document(doc,threading.Event(),version_id=new)=='lexical_ready'
    with db() as conn:
        assert conn.execute('SELECT current_version_id FROM public_documents WHERE id=?',(doc,)).fetchone()[0]==(old if configured else new)


@pytest.mark.asyncio
@pytest.mark.parametrize('action',['document','source','rights','scope','cancel'])
async def test_index_rechecks_deletion_rights_scope_and_cancellation(index_db,monkeypatch,action):
    db,source=index_db;doc,old=store_fixture(db,source);_,new=store_fixture(db,source,'Updated facts.',staging=True)
    stop=threading.Event()
    async def during_await(texts,info):
        if action=='document': await public.delete_document(doc,confirm=True)
        elif action=='source': await public.delete_source(source['id'],confirm=True)
        elif action=='cancel': stop.set()
        else:
            with db() as conn:
                if action=='rights': conn.execute("UPDATE public_sources SET status='suspended'")
                else:
                    changed=source|{'allowed_prefixes':['/elsewhere/']}
                    conn.execute('UPDATE public_sources SET data=?',(json.dumps(changed),))
        return [[1.0,0.0] for _ in texts]
    embedding_fixture(monkeypatch,during_await)
    with pytest.raises(fetch.FetchCancelled):
        await public.embed_document(doc,stop,version_id=new)
    with db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM public_embeddings').fetchone()[0]==0
        current=conn.execute('SELECT current_version_id FROM public_documents WHERE id=?',(doc,)).fetchone()
        assert current is None if action in {'source','document'} else current[0]==old


@pytest.mark.asyncio
async def test_dimension_change_does_not_publish_partial_namespace(index_db,monkeypatch):
    db,source=index_db;doc,old=store_fixture(db,source)
    _,new=store_fixture(db,source,'\n'.join('Long source '+str(i)+' x'*90 for i in range(220)),staging=True)
    calls=0
    async def changing(texts,info):
        nonlocal calls
        calls+=1
        return [[1.0]+[0.0]*calls for _ in texts]
    embedding_fixture(monkeypatch,changing)
    with pytest.raises(fetch.FetchError,match='dimensions'):
        await public.embed_document(doc,threading.Event(),version_id=new)
    with db() as conn:
        assert conn.execute('SELECT current_version_id FROM public_documents').fetchone()[0]==old


def test_staging_rollback_reuse_and_restart_preserve_original(index_db):
    db,source=index_db;doc,old=store_fixture(db,source)
    with pytest.raises(RuntimeError):
        with db() as conn:
            public.store_document(conn,source,source['urls'][0],fetch.FetchResult(source['urls'][0]['url'],200,{},b'Rollback'),[(1,'Rollback')],'MIT',staging=True)
            raise RuntimeError('transaction fails')
    _,new=store_fixture(db,source,'Retry retained source',staging=True)
    _,again=store_fixture(db,source,'Retry retained source',staging=True)
    assert new==again
    with db() as conn:
        public.init_public_knowledge(conn)
        assert conn.execute('SELECT COUNT(*) FROM public_versions').fetchone()[0]==2
        assert conn.execute('SELECT current_version_id FROM public_documents').fetchone()[0]==old
        assert conn.execute('SELECT state FROM public_version_indexes WHERE version_id=?',(new,)).fetchone()[0]=='staged'


@pytest.mark.asyncio
async def test_selected_refresh_exact_registry_and_persisted_limits(index_db,monkeypatch):
    db,source=index_db
    monkeypatch.setattr(public,'launch_job',lambda job_id:None)
    with pytest.raises(HTTPException) as error:
        await public.start_selected_job([{'source_id':source['id'],'url':'https://docs.example.org/docs/not-registered.md'}])
    assert error.value.status_code==422
    job=await public.start_selected_job([{'source_id':source['id'],'url':source['urls'][0]['url']}],seconds=30,max_bytes=10,max_requests=2)
    assert (job['run_seconds'],job['max_bytes'],job['max_requests'])==(30,10,2)
    control=public.JobControl(30,job['id'])
    try:
        control.count_request();control.count_request()
        with pytest.raises(fetch.FetchError,match='request limit'): control.count_request()
    finally: control.finish()
    public.charge_bytes(job['id'],7)
    assert public.remaining_budget(job['id'])==3
    with pytest.raises(fetch.FetchError,match='byte limit'): public.charge_bytes(job['id'],4)
    public.charge_bytes(job['id'],3)
    assert public.remaining_budget(job['id'])==0
    await public.resume_job(job['id'],public.ResumeBody(allow_network=True))
    refreshed=public.get_job(job['id'])
    assert refreshed['request_count']==2 and refreshed['downloaded_bytes']==10


@pytest.mark.asyncio
async def test_delete_invalidates_old_job_and_resume_cannot_recreate(index_db,monkeypatch):
    db,source=index_db;doc,old=store_fixture(db,source)
    monkeypatch.setattr(public,'launch_job',lambda job_id:None)
    job=await public.start_selected_job([{'source_id':source['id'],'url':source['urls'][0]['url']}])
    await public.delete_document(doc,confirm=True)
    await public.resume_job(job['id'],public.ResumeBody(allow_network=True))
    resumed=public.get_job(job['id'])
    assert resumed['items'][0]['status']=='deleted'
    with db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM public_documents').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM public_version_indexes').fetchone()[0]==0


def test_registry_provenance_does_not_promote_candidates():
    from pathlib import Path
    registry=json.loads((Path(__file__).resolve().parents[1]/'knowledge'/'sources.json').read_text(encoding='utf-8'))
    sources={value['id']:public.source_validate(value) for value in registry['sources']}
    assert len(sources)==18
    assert {key for key,value in sources.items() if value['status']=='allowed'}=={'python312','react19','typescript59','fastapi0162','usgs-water'}
    assert sources['wikidata-general']['status']=='link_only'
    assert sources['cpp']['trust']['level']=='community_reference'
    for source in sources.values():
        assert source['publisher']['url'].startswith('https://')
        assert source['training_allowed'] is False
        assert source['license']['training_allowed'] is False
        assert source['trust']['limitations']
        if source['status']=='candidate':
            assert source['rights_review']['status']=='requires_material_review'
            assert source['rights_review']['reviewed_at'] is None
            assert not source['license']['storage_allowed']


@pytest.mark.asyncio
async def test_same_bytes_changed_rights_metadata_create_immutable_new_version(index_db,monkeypatch):
    db,source=index_db;doc,old=store_fixture(db,source)
    updated=source|{'license':source['license']|{'attribution':'Fixture author; revised attribution'}}
    with db() as conn: conn.execute('UPDATE public_sources SET data=? WHERE id=?',(json.dumps(updated),source['id']))
    _,new=store_fixture(db,updated,staging=True)
    assert old!=new
    with db() as conn:
        assert conn.execute('SELECT current_version_id FROM public_documents').fetchone()[0]==old
        old_license=json.loads(conn.execute('SELECT license FROM public_versions WHERE id=?',(old,)).fetchone()[0])
        assert old_license['attribution']=='Fixture author'
    async def no_embeddings(): return {'configured':False,'available':False}
    monkeypatch.setattr(public,'embedding_info',no_embeddings)
    await public.embed_document(doc,threading.Event(),version_id=new)
    with db() as conn:
        assert conn.execute('SELECT current_version_id FROM public_documents').fetchone()[0]==new


@pytest.mark.parametrize('change',['scope','storage','confirmation'])
def test_public_acl_follows_current_source_scope_and_storage_rights(index_db,change):
    db,source=index_db;store_fixture(db,source)
    if change=='scope': source['allowed_prefixes']=['/elsewhere/']
    elif change=='storage': source['license']['storage_allowed']=False
    else: source['rights_confirmed']=False
    with db() as conn:
        conn.execute('UPDATE public_sources SET data=? WHERE id=?',(json.dumps(source),source['id']))
        assert public.corpus_rows(conn)==[]
        assert public.lexical_public(conn,'source')==[]


@pytest.mark.parametrize('change',['scope','storage','confirmation'])
def test_fact_public_history_revoked_by_live_scope_and_rights(index_db,change):
    from backend import facts
    db,source=index_db;doc,old=store_fixture(db,source);store_fixture(db,source,'New source version.')
    with db() as conn:
        old_chunk=conn.execute('SELECT id FROM public_chunks WHERE version_id=?',(old,)).fetchone()[0]
        row,state=facts.source_raw(conn,'any-local-project','public_chunk',old_chunk)
        assert row and state=='stale_version'
        if change=='scope':source['allowed_prefixes']=['/elsewhere/']
        elif change=='storage':source['license']['storage_allowed']=False
        else:source['rights_confirmed']=False
        conn.execute('UPDATE public_sources SET data=? WHERE id=?',(json.dumps(source),source['id']))
        assert facts.source_raw(conn,'any-local-project','public_chunk',old_chunk)==(None,'revoked')
    assert public.inspect_document(doc)['chunks']==[]
    with pytest.raises(HTTPException) as error:public.original_document(doc,version_id=old)
    assert error.value.status_code==403


def test_staged_version_is_not_historical_fact_authority(index_db):
    from backend import facts
    db,source=index_db;doc,old=store_fixture(db,source);_,staged=store_fixture(db,source,'Not published yet',staging=True)
    with db() as conn:
        chunk=conn.execute('SELECT id FROM public_chunks WHERE version_id=?',(staged,)).fetchone()[0]
        assert facts.source_raw(conn,'p1','public_chunk',chunk)==(None,'revoked')
        assert facts.source_raw(conn,'p1','public_chunk','snapshot:'+chunk)==(None,'revoked')
        assert facts.source_raw(conn,'p1','invented_kind',chunk)==(None,'revoked')
