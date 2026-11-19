"""Manual-apply recovery regression; injected I/O fault, no model/source execution.

ASGI requests run in process against a temporary SQLite database. The proposal
is deterministic test data; fixture source is only read, written and hashed.
"""
import asyncio
import base64
import json
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from backend import coding, coding_edits, workspace
from backend.db import get_db, now
from authority_fixture import fixture_owner

PREFIX = "/api/projects/recovery-project/coding"
FAULT_DETAIL = "sensitive-internal-failure-must-not-be-returned"


@pytest.fixture
def recovery_api(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("RIWAQ_DATA_DIR", str(data_dir))
    with get_db() as conn:
        conn.execute("CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,instructions TEXT,created_at TEXT)")
        conn.execute("INSERT INTO projects VALUES(?,?,?,?)", ("recovery-project", "Recovery fixture", "", now()))
        coding.init_coding(conn)
    root = tmp_path / "user-project"
    root.mkdir()
    baseline = {"one.txt": b"original one\n", "two.txt": b"original two\n"}
    for path, original in baseline.items():
        (root / path).write_bytes(original)
    grant = workspace.create_grant(str(root), "recovery-project")
    proposal = coding_edits.build_proposal(baseline, [
        {"path": "one.txt", "before": "original one", "after": "proposed one"},
        {"path": "two.txt", "before": "original two", "after": "proposed two"},
    ], list(baseline))
    changes = proposal.pop("changes")
    body = {
        "mode": "edit", "authority": "propose", "language": "en",
        "prompt": "Deterministic fixture; no provider", "acceptance": "Preserve external changes",
        "selected_paths": list(baseline), "answer": "Simulated reviewed proposal", "refs": [],
        "context": {"bindings": [{"path": p, "sha256": workspace.digest(b)} for p, b in baseline.items()]},
        "proposal": proposal, "validation": {"status": "not_run", "executed": False, "tests_passed": None},
        "events": [], "error": None, "model": None, "metrics": {}, "_input": {},
        "_baseline": {p: base64.b64encode(b).decode() for p, b in baseline.items()},
        "_changes": changes, "_ever_applied": False,
    }
    with get_db() as conn:
        stamp = now()
        conn.execute("INSERT INTO coding_workspaces VALUES(?,?,?,?,?)", ("recovery-workspace", "recovery-project", grant["id"], "Fixture", stamp))
        conn.execute("INSERT INTO coding_tasks VALUES(?,?,?,?,?,?,?,?,?,?)", (
            "recovery-task", "recovery-workspace", "recovery-project", "fixture-request", "fixture-sha",
            "proposed", 1, stamp, stamp, coding.packed(body),
        ))
    assert not coding.ACTIVE
    monkeypatch.setattr(coding, "busy", lambda: False)
    def forbidden(*args, **kwargs):
        pytest.fail("This regression must not dispatch a provider, a task, or user source")
    monkeypatch.setattr(coding, "launch", forbidden)
    monkeypatch.setattr(coding.providers, "validate_provider", forbidden)
    monkeypatch.setattr(coding.providers, "stream", forbidden)
    app = FastAPI()
    app.include_router(coding.router)
    async def call(method, suffix, **kwargs):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test.local") as client:
            return await client.request(method, PREFIX + suffix, **kwargs)
    # The mini app has no pairing middleware: bind the paired owner explicitly
    # (CORE: fixture predates local pairing). asyncio.run copies this context.
    with fixture_owner():
        yield SimpleNamespace(root=root, baseline=baseline, changes=changes,
                              call=lambda *args, **kwargs: asyncio.run(call(*args, **kwargs)))
    assert not coding.ACTIVE


@pytest.mark.parametrize("external_edit", [False, True], ids=["guarded-undo-recovers", "newer-user-edit-is-preserved"])
def test_manual_partial_apply_exception_has_immediate_durable_recovery(recovery_api, monkeypatch, external_edit):
    api = recovery_api
    real_apply = coding_edits.apply_batch
    faults = []

    def partial_write_then_raise(grant, changes, bindings, undo=False):
        assert undo is False
        # The intent must already have committed before any filesystem mutation.
        with get_db() as conn:
            row = conn.execute("SELECT status,data FROM coding_tasks WHERE id='recovery-task'").fetchone()
            assert row["status"] == "applying"
            assert json.loads(row["data"])["_changes"] == api.changes
        first = changes[0]
        workspace.atomic_replace(grant, first["path"], base64.b64decode(first["new_bytes_b64"]), first["original_sha"])
        faults.append(first["path"])
        raise RuntimeError(FAULT_DETAIL)

    monkeypatch.setattr(coding_edits, "apply_batch", partial_write_then_raise)
    response = api.call("POST", "/tasks/recovery-task/apply", json={"confirm": True, "expected_revision": 1})
    assert response.status_code == 200, response.text
    assert FAULT_DETAIL not in response.text
    assert faults == ["one.txt"]
    assert (api.root / "one.txt").read_bytes() == b"proposed one\n"
    assert (api.root / "two.txt").read_bytes() == api.baseline["two.txt"]

    # No startup hook or service restart is allowed to hide a stuck 'applying'.
    saved_response = api.call("GET", "/tasks/recovery-task")
    assert saved_response.status_code == 200, saved_response.text
    saved = saved_response.json()
    assert saved["status"] == saved["proposal"]["state"] == "recovery_required"
    assert saved["can_undo"] is True and saved["can_resume"] is False and saved["can_apply"] is False
    assert saved["validation"]["executed"] is False and saved["validation"]["tests_passed"] is None
    assert "_changes" not in saved and "_baseline" not in saved
    with get_db() as conn:
        row = conn.execute("SELECT status,data FROM coding_tasks WHERE id='recovery-task'").fetchone()
    internal = json.loads(row["data"])
    assert row["status"] == "recovery_required" and internal["_changes"] == api.changes
    assert FAULT_DETAIL not in row["data"]
    assert not coding.ACTIVE

    monkeypatch.setattr(coding_edits, "apply_batch", real_apply)
    if external_edit:
        (api.root / "one.txt").write_bytes(b"a newer user edit after the failure\n")
    before_undo = {p: (api.root / p).read_bytes() for p in api.baseline}
    resumed = api.call("POST", "/tasks/recovery-task/resume", json={"request_key": "must-not-resume"})
    assert resumed.status_code == 409
    undo = api.call("POST", "/tasks/recovery-task/undo", json={"confirm": True, "expected_revision": saved["revision"]})
    assert undo.status_code == 200, undo.text
    outcome = undo.json()
    if external_edit:
        assert outcome["status"] == outcome["proposal"]["state"] == "conflict"
        assert outcome["apply_receipt"]["written"] == []
        assert all((api.root / p).read_bytes() == b for p, b in before_undo.items())
        assert outcome["can_undo"] is True and outcome["can_resume"] is False and outcome["can_apply"] is False
    else:
        assert outcome["status"] == outcome["proposal"]["state"] == "undone"
        assert outcome["apply_receipt"]["written"] == ["one.txt"]
        assert outcome["apply_receipt"]["skipped"] == ["two.txt"]
        assert all((api.root / p).read_bytes() == b for p, b in api.baseline.items())
        assert outcome["can_undo"] is False and outcome["can_resume"] is False
