"""Separate optional sync service. The local assistant never imports this module.

Run: python -m uvicorn sync_server.app:app --host 127.0.0.1 --port 8766
Remote use requires a separately configured HTTPS reverse proxy. Encryption keys
are client-owned; this service stores only validated opaque ciphertext envelopes.
"""
from __future__ import annotations

import base64
import asyncio
import binascii
from collections import OrderedDict
from contextlib import asynccontextmanager, contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from typing import Literal
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

MAX_BODY = 4 * 1024 * 1024
BODY_DEADLINE_SECONDS = 15
MAX_CIPHERTEXT = 3 * 1024 * 1024
MAX_SNAPSHOTS = 100
MAX_ACCOUNT_BYTES = 100 * 1024 * 1024
SCRYPT_N, SCRYPT_R, SCRYPT_P = 32768, 8, 1
AUTH_WORKERS = threading.BoundedSemaphore(2)
REQUEST_SLOTS = threading.BoundedSemaphore(16)
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}$")
PROJECT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def data_dir() -> Path:
    folder = Path(os.environ.get("RIWAQ_SYNC_DATA_DIR", str(Path(__file__).resolve().parents[1] / "sync_data")))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@contextmanager
def database():
    conn = sqlite3.connect(data_dir() / "accounts.sqlite3", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database():
    with database() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                password_hash BLOB NOT NULL, password_salt BLOB NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at REAL NOT NULL, created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
            CREATE TABLE IF NOT EXISTS snapshots (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                project_id TEXT NOT NULL, title TEXT NOT NULL,
                revision INTEGER NOT NULL, encrypted_payload TEXT NOT NULL,
                byte_size INTEGER NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,project_id)
            );
        """)
        conn.execute("DELETE FROM sessions WHERE expires_at<=?", (time.time(),))


class RateLimiter:
    """Bounded process-local fixed windows; proxy IP headers are never trusted."""
    def __init__(self, max_entries=2048, window=300, attempts=10):
        self.max_entries, self.window, self.attempts = max_entries, window, attempts
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def check(self, key: str):
        timestamp = time.monotonic()
        with self.lock:
            start, count = self.entries.get(key, (timestamp, 0))
            if timestamp - start >= self.window:
                start, count = timestamp, 0
            if count >= self.attempts:
                raise HTTPException(429, "Too many authentication attempts; try again later", headers={"Retry-After": str(max(1, int(self.window - (timestamp - start))))})
            self.entries[key] = (start, count + 1)
            self.entries.move_to_end(key)
            while len(self.entries) > self.max_entries:
                self.entries.popitem(last=False)


ip_limiter = RateLimiter()
account_limiter = RateLimiter(max_entries=4096, attempts=8)


def throttle(request: Request, email: str):
    ip_limiter.check(request.client.host if request.client else "unknown")
    account_limiter.check(hashlib.sha256(email.encode()).hexdigest())


def password_hash(password: str, salt: bytes) -> bytes:
    if not AUTH_WORKERS.acquire(blocking=False):
        raise HTTPException(429, "Authentication service is busy; retry shortly", headers={"Retry-After": "2"})
    try:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32, maxmem=64 * 1024 * 1024)
    finally:
        AUTH_WORKERS.release()


def issue_session(conn, user):
    token = secrets.token_urlsafe(32)
    created = time.time()
    try:
        ttl = int(os.environ.get("RIWAQ_SYNC_SESSION_SECONDS", "86400"))
    except ValueError:
        ttl = 86400
    expires = created + min(max(ttl, 300), 30 * 86400)
    conn.execute("DELETE FROM sessions WHERE expires_at<=?", (created,))
    # Keep a bounded number of active sessions per account.
    conn.execute("DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN (SELECT token_hash FROM sessions WHERE user_id=? ORDER BY created_at DESC LIMIT 9)", (user["id"], user["id"]))
    conn.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user["id"], expires, created))
    return {"token": token, "token_type": "Bearer", "expires_at": expires, "user": {"id": user["id"], "email": user["email"]}}


def bearer(request: Request) -> str:
    value = request.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise HTTPException(401, "A valid bearer session is required", headers={"WWW-Authenticate": "Bearer"})
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(request: Request):
    token_hash = bearer(request)
    with database() as conn:
        user = conn.execute("SELECT u.id,u.email,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?", (token_hash, time.time())).fetchone()
        if user is None:
            raise HTTPException(401, "Session is invalid or expired", headers={"WWW-Authenticate": "Bearer"})
        return dict(user) | {"token_hash": token_hash}


class AuthBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128, repr=False)

    @field_validator("email")
    @classmethod
    def email_format(cls, value: str):
        value = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(value) or ".." in value:
            raise ValueError("Use a valid ASCII email address")
        return value


def decode_base64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Encryption envelope fields must use standard base64") from exc


class EncryptedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    kdf: Literal["PBKDF2-SHA256"] = "PBKDF2-SHA256"
    iterations: int = Field(default=310000, ge=100000, le=2000000)
    salt: str = Field(min_length=24, max_length=44)
    nonce: str = Field(min_length=16, max_length=16)
    ciphertext: str = Field(min_length=24, max_length=4 * 1024 * 1024)

    @field_validator("salt")
    @classmethod
    def salt_size(cls, value: str):
        if not 16 <= len(decode_base64(value)) <= 32:
            raise ValueError("Client salt must contain 16 to 32 bytes")
        return value

    @field_validator("nonce")
    @classmethod
    def nonce_size(cls, value: str):
        if len(decode_base64(value)) != 12:
            raise ValueError("AES-GCM nonce must contain 12 bytes")
        return value

    @field_validator("ciphertext")
    @classmethod
    def ciphertext_size(cls, value: str):
        if not 16 <= len(decode_base64(value)) <= MAX_CIPHERTEXT:
            raise ValueError("Ciphertext must include a tag and fit within 3 MB")
        return value


class SnapshotBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, le=2147483646)
    title: str = Field(default="Encrypted project", min_length=1, max_length=200)
    encrypted_payload: EncryptedPayload


class BoundaryMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if not REQUEST_SLOTS.acquire(blocking=False):
            return await JSONResponse({"detail": "Service concurrency limit reached"}, 429)(scope, receive, send)
        try:
            chunks = []
            total = 0
            deadline = time.monotonic() + BODY_DEADLINE_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                try:
                    if remaining <= 0:raise TimeoutError
                    message = await asyncio.wait_for(receive(), remaining)
                except TimeoutError:
                    return await JSONResponse({"detail":"Request body deadline exceeded"},408)(scope,receive,send)
                if message["type"] == "http.disconnect":
                    return
                total += len(message.get("body", b""))
                if total > MAX_BODY:
                    return await JSONResponse({"detail": "Request body exceeds 4 MB"}, 413)(scope, receive, send)
                chunks.append(message)
                if not message.get("more_body", False):
                    break

            async def replay():
                if chunks:
                    return chunks.pop(0)
                return await receive()

            async def secure_send(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.extend([(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY")])
                    message["headers"] = headers
                await send(message)

            await self.app(scope, replay, secure_send)
        finally:
            REQUEST_SLOTS.release()


@asynccontextmanager
async def lifespan(app):
    init_database()
    yield


app = FastAPI(title="Riwaq optional encrypted sync service", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None,
              telemetry={'auto_configure':False,'tracing':False,'metrics':False,'logs':False,'operation_spans':False})
origins = [v.strip() for v in os.environ.get("RIWAQ_SYNC_ALLOWED_ORIGINS", "http://127.0.0.1:8765,http://localhost:8765,http://tauri.localhost,https://tauri.localhost,tauri://localhost").split(",") if v.strip() and v.strip() != "*"]
hosts = [v.strip() for v in os.environ.get("RIWAQ_SYNC_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver").split(",") if v.strip() and v.strip() != "*"]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PUT", "OPTIONS"], allow_headers=["Content-Type", "Authorization"], allow_credentials=False)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
app.add_middleware(BoundaryMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Never echo passwords or multi-megabyte ciphertext in validation responses/logs.
    return JSONResponse({"detail": [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]} for item in exc.errors()]}, 422)


@app.get("/health")
def health():
    return {"status": "ok", "service": "riwaq-sync", "version": "0.1.0", "encrypted_payloads_only": True}


@app.post("/auth/register", status_code=201)
def register(body: AuthBody, request: Request):
    throttle(request, body.email)
    salt = secrets.token_bytes(16)
    hashed = password_hash(body.password, salt)
    with database() as conn:
        user_id = uuid.uuid4().hex
        try:
            conn.execute("INSERT INTO users VALUES(?,?,?,?,?)", (user_id, body.email, hashed, salt, time.time()))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Unable to register this account") from exc
        return issue_session(conn, {"id": user_id, "email": body.email})


@app.post("/auth/login")
def login(body: AuthBody, request: Request):
    throttle(request, body.email)
    with database() as conn:
        user = conn.execute("SELECT * FROM users WHERE email=?", (body.email,)).fetchone()
        # Derive even for unknown accounts to avoid a cheap account-existence timing signal.
        salt = bytes(user["password_salt"]) if user else b"RiwaqDummySalt16!"
        candidate = password_hash(body.password, salt)
        expected = bytes(user["password_hash"]) if user else bytes(32)
        if user is None or not hmac.compare_digest(candidate, expected):
            raise HTTPException(401, "Invalid email or password")
        return issue_session(conn, user)


@app.post("/auth/logout")
def logout(user=Depends(current_user)):
    with database() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=? AND user_id=?", (user["token_hash"], user["id"]))
    return {"logged_out": True}


def project_id_checked(project_id: str):
    if not PROJECT_PATTERN.fullmatch(project_id) or project_id in {".", ".."}:
        raise HTTPException(422, "Project id must use 1 to 128 ASCII letters, digits, dots, hyphens or underscores")


@app.get("/snapshots")
def list_snapshots(user=Depends(current_user)):
    with database() as conn:
        return [dict(row) for row in conn.execute("SELECT project_id,title,revision,byte_size,updated_at FROM snapshots WHERE user_id=? ORDER BY updated_at DESC", (user["id"],))]


@app.get("/snapshots/{project_id}")
def get_snapshot(project_id: str, user=Depends(current_user)):
    project_id_checked(project_id)
    with database() as conn:
        row = conn.execute("SELECT project_id,title,revision,encrypted_payload,updated_at FROM snapshots WHERE user_id=? AND project_id=?", (user["id"], project_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Snapshot not found")
        return dict(row) | {"encrypted_payload": json.loads(row["encrypted_payload"])}


@app.put("/snapshots/{project_id}")
def put_snapshot(project_id: str, body: SnapshotBody, user=Depends(current_user)):
    project_id_checked(project_id)
    encoded = json.dumps(body.encrypted_payload.model_dump(), separators=(",", ":"))
    byte_size = len(encoded.encode("utf-8"))
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT revision,byte_size FROM snapshots WHERE user_id=? AND project_id=?", (user["id"], project_id)).fetchone()
        current_revision = existing["revision"] if existing else 0
        if body.revision != current_revision:
            raise HTTPException(409, {"message": "Snapshot changed; fetch it before uploading again", "current_revision": current_revision})
        count, total_bytes = conn.execute("SELECT COUNT(*),COALESCE(SUM(byte_size),0) FROM snapshots WHERE user_id=?", (user["id"],)).fetchone()
        if (existing is None and count >= MAX_SNAPSHOTS) or total_bytes - (existing["byte_size"] if existing else 0) + byte_size > MAX_ACCOUNT_BYTES:
            raise HTTPException(413, "Account snapshot quota exceeded")
        revision, timestamp = current_revision + 1, time.time()
        conn.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,project_id) DO UPDATE SET title=excluded.title,revision=excluded.revision,encrypted_payload=excluded.encrypted_payload,byte_size=excluded.byte_size,updated_at=excluded.updated_at", (user["id"], project_id, body.title, revision, encoded, byte_size, timestamp))
        return {"project_id": project_id, "title": body.title, "revision": revision, "byte_size": byte_size, "updated_at": timestamp}
