from contextlib import contextmanager
import codecs
import os
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import workspace


@pytest.fixture
def setup_workspace(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("print('before')\n", encoding="utf-8")
    (root / "readme.md").write_text("# مشروع\nتعليمات حقيقية\n", encoding="utf-8")
    (root / ".env").write_text("NEVER_READ=secret", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "hidden.js").write_text("hidden", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("secret git auth", encoding="utf-8")
    db_path = tmp_path / "workspace-tests.sqlite"

    @contextmanager
    def get_db():
        conn = sqlite3.connect(db_path)
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

    with get_db() as conn:
        workspace.init_workspace(conn)
    monkeypatch.setattr(workspace, "get_db", get_db)
    app = FastAPI()
    app.include_router(workspace.router)
    with TestClient(app) as client:
        response = client.post("/api/workspace/grant", json={"path": str(root)})
        assert response.status_code == 201, response.text
        yield client, root, response.json()["id"]


def test_grant_listing_read_search_and_revocation(setup_workspace):
    client, root, grant = setup_workspace
    listing = client.get(f"/api/workspace/{grant}/files").json()
    assert {f["path"] for f in listing["files"]} == {"app.py", "readme.md"}
    read = client.get(f"/api/workspace/{grant}/read", params={"path": "app.py"})
    assert read.status_code == 200 and "before" in read.json()["content"]
    found = client.get(f"/api/workspace/{grant}/search", params={"q": "تعليمات"}).json()["matches"]
    assert len(found) == 1 and found[0]["path"] == "readme.md" and found[0]["line"] == 2
    assert client.delete(f"/api/workspace/grants/{grant}").status_code == 200
    assert client.get(f"/api/workspace/{grant}/read", params={"path": "app.py"}).status_code == 404
    assert client.get("/api/workspace/grants").json() == []


@pytest.mark.parametrize("path", ["../outside.txt", "sub/../../outside.txt", "/etc/passwd", "C:\\Windows\\file.txt", "app.py:secret", ".env", ".git/config", "node_modules/hidden.js", "id_rsa", "credentials-prod.json", "secret.key", "app.py."])
def test_grant_rejects_escape_and_secrets(setup_workspace, path):
    client, _, grant = setup_workspace
    assert client.get(f"/api/workspace/{grant}/read", params={"path": path}).status_code in {400, 403}


def test_preview_does_not_write_and_apply_requires_confirmation(setup_workspace):
    client, root, grant = setup_workspace
    proposed = client.post(f"/api/workspace/{grant}/proposals", json={"path": "app.py", "content": "print('after')\n"})
    assert proposed.status_code == 201
    proposal = proposed.json()
    assert "-print('before')" in proposal["diff"] and "+print('after')" in proposal["diff"]
    assert (root / "app.py").read_text() == "print('before')\n"
    url = f"/api/workspace/proposals/{proposal['id']}/apply"
    assert client.post(url, json={}).status_code == 403
    assert client.post(url, json={"confirm": "true"}).status_code == 422
    assert client.post(url, json={"confirm": True}).status_code == 200
    assert (root / "app.py").read_text() == "print('after')\n"
    assert client.post(url, json={"confirm": True}).status_code == 409
    undo = client.post(f"/api/workspace/proposals/{proposal['id']}/undo", json={"confirm": True})
    assert undo.status_code == 200 and undo.json()["state"] == "undone"
    assert (root / "app.py").read_text() == "print('before')\n"
    assert not list(root.glob(".riwaq-*"))
    events = client.get(f"/api/workspace/{grant}/events").json()
    assert {event["action"] for event in events} >= {"granted", "proposed", "applied", "undone"}


def test_stale_proposal_preserves_user_changes(setup_workspace):
    client, root, grant = setup_workspace
    proposal = client.post(f"/api/workspace/{grant}/proposals", json={"path": "app.py", "content": "generated\n"}).json()
    (root / "app.py").write_text("user changed it\n", encoding="utf-8")
    response = client.post(f"/api/workspace/proposals/{proposal['id']}/apply", json={"confirm": True})
    assert response.status_code == 409
    assert (root / "app.py").read_text() == "user changed it\n"


def test_undo_refuses_to_overwrite_newer_edits(setup_workspace):
    client, root, grant = setup_workspace
    proposal = client.post(f"/api/workspace/{grant}/proposals", json={"path": "app.py", "content": "generated\n"}).json()
    assert client.post(f"/api/workspace/proposals/{proposal['id']}/apply", json={"confirm": True}).status_code == 200
    (root / "app.py").write_text("subsequent edit\n", encoding="utf-8")
    assert client.post(f"/api/workspace/proposals/{proposal['id']}/undo", json={"confirm": True}).status_code == 409
    assert (root / "app.py").read_text() == "subsequent edit\n"


def test_revocation_invalidates_pending_edits(setup_workspace):
    client, root, grant = setup_workspace
    proposal = client.post(f"/api/workspace/{grant}/proposals", json={"path": "app.py", "content": "generated\n"}).json()
    client.delete(f"/api/workspace/grants/{grant}")
    assert client.post(f"/api/workspace/proposals/{proposal['id']}/apply", json={"confirm": True}).status_code == 404
    assert "before" in (root / "app.py").read_text()


def test_bom_undo_preserves_original_bytes(setup_workspace):
    client, root, grant = setup_workspace
    original = codecs.BOM_UTF8 + "الأصل\r\n".encode("utf-8")
    (root / "bom.txt").write_bytes(original)
    proposal = client.post(f"/api/workspace/{grant}/proposals", json={"path": "bom.txt", "content": "الجديد\n"}).json()
    client.post(f"/api/workspace/proposals/{proposal['id']}/apply", json={"confirm": True})
    assert (root / "bom.txt").read_bytes().startswith(codecs.BOM_UTF8)
    client.post(f"/api/workspace/proposals/{proposal['id']}/undo", json={"confirm": True})
    assert (root / "bom.txt").read_bytes() == original


def test_size_binary_and_execution_limits(setup_workspace, monkeypatch):
    client, root, grant = setup_workspace
    (root / "binary.dat").write_bytes(b"abc\x00def")
    assert client.get(f"/api/workspace/{grant}/read", params={"path": "binary.dat"}).status_code == 415
    monkeypatch.setattr(workspace, "MAX_FILE_BYTES", 5)
    assert client.get(f"/api/workspace/{grant}/read", params={"path": "app.py"}).status_code == 413
    response = client.post("/api/workspace/execute", json={"code": "anything"})
    assert response.status_code == 503 and "disabled" in response.json()["detail"]


def test_symlink_and_hardlink_escape(setup_workspace, tmp_path):
    client, root, grant = setup_workspace
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    os.link(outside, root / "hard.txt")
    assert client.get(f"/api/workspace/{grant}/read", params={"path": "hard.txt"}).status_code == 403
    try:
        (root / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("This Windows account cannot create symlinks; hardlink case passed")
    assert client.get(f"/api/workspace/{grant}/read", params={"path": "link.txt"}).status_code == 403


def test_root_replacement_requires_new_grant(setup_workspace, tmp_path):
    client, root, grant = setup_workspace
    moved = tmp_path / "old-project"
    root.rename(moved)
    root.mkdir()
    (root / "app.py").write_text("replacement", encoding="utf-8")
    assert client.get(f"/api/workspace/{grant}/read", params={"path": "app.py"}).status_code == 409
