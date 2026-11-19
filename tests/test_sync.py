import base64
import hashlib
import json
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from sync_server import app as sync


PASSWORD = "Synthetic-password-493!"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RIWAQ_SYNC_DATA_DIR", str(tmp_path / "sync"))
    monkeypatch.setattr(sync, "ip_limiter", sync.RateLimiter())
    monkeypatch.setattr(sync, "account_limiter", sync.RateLimiter(max_entries=4096, attempts=8))
    with TestClient(sync.app) as session:
        yield session


def register(client, email="one@example.test"):
    response = client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return response.json()


def headers(session):
    return {"Authorization": "Bearer " + session["token"]}


def envelope(value=b"synthetic-ciphertext-and-tag"):
    # Opaque envelope fixture only; cryptography is evaluated by the client tests.
    return {"version": 1, "kdf": "PBKDF2-SHA256", "iterations": 310000,
            "salt": base64.b64encode(b"salt-for-fixture!").decode(),
            "nonce": base64.b64encode(b"nonce-12byte").decode(),
            "ciphertext": base64.b64encode(value).decode()}


def test_register_login_hashed_password_and_hashed_bearer(client):
    created = register(client)
    assert len(created["token"]) == 43
    assert created["expires_at"] > time.time()
    with sync.database() as conn:
        user = conn.execute("SELECT * FROM users").fetchone()
        session = conn.execute("SELECT * FROM sessions").fetchone()
        assert user["password_hash"] != PASSWORD.encode()
        assert len(user["password_hash"]) == 32 and len(user["password_salt"]) == 16
        assert session["token_hash"] == hashlib.sha256(created["token"].encode()).hexdigest()
        assert created["token"] not in str(dict(session))
    logged_in = client.post("/auth/login", json={"email": "ONE@example.test", "password": PASSWORD})
    assert logged_in.status_code == 200
    assert logged_in.json()["token"] != created["token"]
    assert client.post("/auth/login", json={"email": "one@example.test", "password": "wrong-password-495"}).status_code == 401
    assert client.post("/auth/login", json={"email": "absent@example.test", "password": PASSWORD}).status_code == 401


def test_salts_are_distinct_for_identical_passwords(client):
    register(client, "first@example.test")
    register(client, "second@example.test")
    with sync.database() as conn:
        users = conn.execute("SELECT password_hash,password_salt FROM users").fetchall()
        assert users[0]["password_salt"] != users[1]["password_salt"]
        assert users[0]["password_hash"] != users[1]["password_hash"]


def test_snapshot_isolation_with_same_project_identifier(client):
    one = register(client, "one@example.test")
    two = register(client, "two@example.test")
    first = envelope(b"tenant-one-encrypted-data-and-tag")
    second = envelope(b"tenant-two-encrypted-data-and-tag")
    response = client.put("/snapshots/shared-id", json={"revision": 0, "encrypted_payload": first}, headers=headers(one))
    assert response.status_code == 200 and response.json()["revision"] == 1
    assert client.get("/snapshots/shared-id", headers=headers(two)).status_code == 404
    assert client.get("/snapshots", headers=headers(two)).json() == []
    assert client.put("/snapshots/shared-id", json={"revision": 0, "encrypted_payload": second}, headers=headers(two)).status_code == 200
    assert client.get("/snapshots/shared-id", headers=headers(one)).json()["encrypted_payload"] == first
    assert client.get("/snapshots/shared-id", headers=headers(two)).json()["encrypted_payload"] == second
    assert len(client.get("/snapshots", headers=headers(one)).json()) == 1
    assert len(client.get("/snapshots", headers=headers(two)).json()) == 1


def test_revision_conflicts_preserve_saved_snapshot(client):
    auth = headers(register(client))
    original = envelope()
    changed = envelope(b"replacement-encrypted-payload-and-tag")
    assert client.put("/snapshots/project-1", json={"revision": 0, "encrypted_payload": original}, headers=auth).json()["revision"] == 1
    response = client.put("/snapshots/project-1", json={"revision": 0, "encrypted_payload": changed}, headers=auth)
    assert response.status_code == 409
    assert response.json()["detail"]["current_revision"] == 1
    assert client.get("/snapshots/project-1", headers=auth).json()["encrypted_payload"] == original
    assert client.put("/snapshots/project-1", json={"revision": 1, "encrypted_payload": changed}, headers=auth).json()["revision"] == 2
    assert client.put("/snapshots/nonexistent", json={"revision": 7, "encrypted_payload": changed}, headers=auth).status_code == 409


def test_expired_invalid_missing_and_logged_out_sessions(client):
    session = register(client)
    assert client.get("/snapshots").status_code == 401
    assert client.get("/snapshots", headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.get("/snapshots", headers={"Authorization": "Bearer " + "x" * 43}).status_code == 401
    assert client.post("/auth/logout", headers=headers(session)).status_code == 200
    assert client.get("/snapshots", headers=headers(session)).status_code == 401
    next_session = client.post("/auth/login", json={"email": "one@example.test", "password": PASSWORD}).json()
    with sync.database() as conn:
        conn.execute("UPDATE sessions SET expires_at=?", (time.time() - 1,))
    assert client.get("/snapshots", headers=headers(next_session)).status_code == 401


@pytest.mark.parametrize("field,value", [("salt", "invalid"), ("nonce", base64.b64encode(b"short").decode()), ("ciphertext", base64.b64encode(b"short").decode()), ("iterations", 100), ("kdf", "none"), ("version", 2)])
def test_envelope_validation(client, field, value):
    auth = headers(register(client))
    payload = envelope()
    payload[field] = value
    assert client.put("/snapshots/project", json={"revision": 0, "encrypted_payload": payload}, headers=auth).status_code == 422


def test_body_limit_and_quota(client, monkeypatch):
    auth = headers(register(client))
    oversized = client.put("/snapshots/project", content=b"x" * (sync.MAX_BODY + 1), headers=auth | {"Content-Type": "application/json"})
    assert oversized.status_code == 413
    monkeypatch.setattr(sync, "MAX_SNAPSHOTS", 1)
    assert client.put("/snapshots/one", json={"revision": 0, "encrypted_payload": envelope()}, headers=auth).status_code == 200
    assert client.put("/snapshots/two", json={"revision": 0, "encrypted_payload": envelope()}, headers=auth).status_code == 413
    assert client.put("/snapshots/one", json={"revision": 1, "encrypted_payload": envelope()}, headers=auth).status_code == 200


def test_password_errors_do_not_echo_credentials(client):
    response = client.post("/auth/register", json={"email": "one@example.test", "password": "short-key"})
    assert response.status_code == 422
    assert "short-key" not in response.text
    assert client.post("/auth/register", json={"email": "not-an-email", "password": PASSWORD}).status_code == 422


def test_auth_rate_limit_and_finite_memory(client, monkeypatch):
    monkeypatch.setattr(sync, "ip_limiter", sync.RateLimiter(attempts=2))
    assert client.post("/auth/login", json={"email": "missing@example.test", "password": PASSWORD}).status_code == 401
    assert client.post("/auth/login", json={"email": "missing@example.test", "password": PASSWORD}).status_code == 401
    limited = client.post("/auth/login", json={"email": "missing@example.test", "password": PASSWORD})
    assert limited.status_code == 429 and "retry-after" in limited.headers
    limiter = sync.RateLimiter(max_entries=3, attempts=1)
    for number in range(10):
        limiter.check(str(number))
    assert len(limiter.entries) == 3
    with pytest.raises(HTTPException) as exc:
        limiter.check("9")
    assert exc.value.status_code == 429


def test_cors_rejects_unknown_origin_and_health_has_no_secrets(client):
    response = client.options("/auth/login", headers={"Origin": "https://untrusted.invalid", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    assert response.status_code == 400
    health = client.get("/health")
    assert health.status_code == 200
    assert health.headers["cache-control"] == "no-store"
    assert health.headers["x-frame-options"] == "DENY"
    assert "password" not in json.dumps(health.json())
