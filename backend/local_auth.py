"""Local-owner pairing, not a hosted multi-user identity provider.

Only an explicitly invoked local CLI can issue a pairing code. HTTP never
returns that code or the installation key. Browser sessions use an HttpOnly
cookie; SQLite stores keyed digests, expiry and an opaque revocable session id.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from contextvars import ContextVar
import ctypes
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import threading
import time

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.db import data_dir, get_db

COOKIE = 'riwaq_owner_session'
PAIR_SECONDS = 300
SESSION_SECONDS = 12 * 60 * 60
IDLE_SECONDS = 30 * 60
MAX_SESSIONS = 16
TOKEN_PATTERN = re.compile(r'^[A-Za-z0-9_-]{43}$')
router = APIRouter(prefix='/api/auth')
_principal: ContextVar[dict | None] = ContextVar('riwaq_local_owner', default=None)
_key_cache: dict = {}
_key_lock = threading.RLock()
_attempts: dict = {}
_attempt_lock = threading.Lock()


class AuthStorageError(RuntimeError):
    pass


class SessionRevoked(Exception):
    """Internal stream termination; never include token or response content."""


def _no_link(path: Path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise AuthStorageError('Authentication storage must not use links or reparse points')


def _windows_private_acl(path: Path, directory: bool):
    """Replace the DACL with the current Windows user's SID only, no shell."""
    from ctypes import wintypes as w
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    adv.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.BOOL)]
    adv.SetNamedSecurityInfoW.argtypes = [w.LPWSTR, ctypes.c_int, w.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    token = w.HANDLE(); sid_text = w.LPWSTR(); descriptor = ctypes.c_void_p()
    try:
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise AuthStorageError('Cannot read the local Windows owner identity')
        needed = w.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not adv.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
            raise AuthStorageError('Cannot read the local Windows owner identity')
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise AuthStorageError('Cannot encode the local Windows owner identity')
        sddl = 'D:P(A;' + ('OICI' if directory else '') + ';FA;;;' + sid_text.value + ')'
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise AuthStorageError('Cannot construct private authentication permissions')
        present, defaulted, dacl = w.BOOL(), w.BOOL(), ctypes.c_void_p()
        if not adv.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)) or not present.value:
            raise AuthStorageError('Cannot construct private authentication permissions')
        if adv.SetNamedSecurityInfoW(str(path), 1, 0x80000004, None, None, dacl, None) != 0:
            raise AuthStorageError('Cannot restrict authentication storage to the local owner')
    finally:
        if descriptor: kernel.LocalFree(descriptor)
        if sid_text: kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        if token: kernel.CloseHandle(token)


def _private(path: Path, directory=False):
    _no_link(path)
    if os.name == 'nt':
        _windows_private_acl(path, directory)
    else:
        if path.stat().st_uid != os.getuid():
            raise AuthStorageError('Authentication storage belongs to another OS user')
        path.chmod(0o700 if directory else 0o600)
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise AuthStorageError('Authentication storage permissions are not private')


def _dpapi(raw: bytes, protect: bool) -> bytes:
    from ctypes import wintypes as w
    class Blob(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    raw_buffer = ctypes.create_string_buffer(raw)
    entropy_buffer = ctypes.create_string_buffer(b'Riwaq.LocalOwner.v1')
    source = Blob(len(raw), ctypes.cast(raw_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy = Blob(len(b'Riwaq.LocalOwner.v1'), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    out = Blob()
    if protect:
        crypt.CryptProtectData.argtypes = [ctypes.POINTER(Blob), w.LPCWSTR, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ctypes.POINTER(Blob)]
        ok = crypt.CryptProtectData(ctypes.byref(source), 'Riwaq local owner', ctypes.byref(entropy), None, None, 1, ctypes.byref(out))
    else:
        crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ctypes.POINTER(Blob)]
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(out))
    if not ok:
        raise AuthStorageError('Windows DPAPI could not protect or unlock local authentication storage')
    try:
        return ctypes.string_at(out.data, out.size)
    finally:
        kernel.LocalFree(out.data)


@contextmanager
def _installation_lock(folder):
    lock = folder / 'initialization.lock'
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _private(lock)
        if os.fstat(fd).st_size == 0: os.write(fd, b'0')
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == 'nt':
            import msvcrt
            # Bounded native lock retries; no unbounded background helper.
            until = time.monotonic() + 3
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= until: raise AuthStorageError('Authentication initialization is busy')
                    time.sleep(.02)
            try: yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError: raise AuthStorageError('Authentication initialization is busy')
            try: yield
            finally: fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _master_key(create=False):
    folder = data_dir() / 'local-auth'
    with _key_lock:
        if not folder.exists():
            if not create: raise AuthStorageError('Local authentication storage is missing')
            folder.mkdir(mode=0o700)
        _private(folder, directory=True)
        path = folder / 'owner.key'
        if not path.exists():
            if not create: raise AuthStorageError('Local authentication key is missing; explicit local recovery is required')
            with _installation_lock(folder):
                if not path.exists():
                    key = secrets.token_bytes(32)
                    protected = b'RWA1W' + _dpapi(key, True) if os.name == 'nt' else b'RWA1P' + key
                    temporary = folder / ('key-' + secrets.token_hex(8) + '.tmp')
                    try:
                        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(fd, 'wb') as out:
                            out.write(protected); out.flush(); os.fsync(out.fileno())
                        _private(temporary)
                        os.replace(temporary, path)
                    finally:
                        temporary.unlink(missing_ok=True)
        _private(path)
        info = path.stat()
        stamp = (str(path.resolve()), info.st_size, info.st_mtime_ns)
        if stamp in _key_cache: return _key_cache[stamp]
        if not 32 <= info.st_size <= 4096: raise AuthStorageError('Local authentication key is invalid')
        raw = path.read_bytes()
        expected = b'RWA1W' if os.name == 'nt' else b'RWA1P'
        if not raw.startswith(expected): raise AuthStorageError('Authentication key format does not match this OS')
        key = _dpapi(raw[5:], False) if os.name == 'nt' else raw[5:]
        if len(key) != 32: raise AuthStorageError('Local authentication key is invalid')
        _key_cache.clear(); _key_cache[stamp] = key
        return key


def init(conn):
    conn.executescript('''
      CREATE TABLE IF NOT EXISTS local_auth_owner(slot INTEGER PRIMARY KEY CHECK(slot=1),owner_id TEXT UNIQUE NOT NULL,created_at REAL NOT NULL,paired_at REAL);
      CREATE TABLE IF NOT EXISTS local_auth_pairings(code_hash TEXT PRIMARY KEY,owner_id TEXT NOT NULL,created_at REAL NOT NULL,expires_at REAL NOT NULL,consumed_at REAL);
      CREATE TABLE IF NOT EXISTS local_auth_sessions(id TEXT PRIMARY KEY,token_hash TEXT UNIQUE NOT NULL,owner_id TEXT NOT NULL,created_at REAL NOT NULL,expires_at REAL NOT NULL,revoked_at REAL,last_seen REAL NOT NULL);
      CREATE INDEX IF NOT EXISTS local_auth_session_owner ON local_auth_sessions(owner_id);
    ''')
    if 'last_seen' not in {row[1] for row in conn.execute('PRAGMA table_info(local_auth_sessions)')}:
        conn.execute('ALTER TABLE local_auth_sessions ADD COLUMN last_seen REAL NOT NULL DEFAULT 0')
        conn.execute('UPDATE local_auth_sessions SET last_seen=created_at')
    exists = conn.execute('SELECT 1 FROM local_auth_owner WHERE slot=1').fetchone()
    _master_key(create=not bool(exists))
    conn.execute('INSERT OR IGNORE INTO local_auth_owner VALUES(1,?,?,NULL)', (secrets.token_hex(16), time.time()))
    conn.execute('DELETE FROM local_auth_pairings WHERE expires_at<=? OR consumed_at IS NOT NULL', (time.time(),))
    conn.execute('DELETE FROM local_auth_sessions WHERE expires_at<=? OR revoked_at IS NOT NULL', (time.time(),))


def owner_id(conn):
    row = conn.execute('SELECT owner_id FROM local_auth_owner WHERE slot=1').fetchone()
    if row is None: raise AuthStorageError('Local owner has not been initialized')
    return row[0]


def _digest(kind, value):
    return hmac.new(_master_key(), (kind + '\0' + value).encode('ascii'), hashlib.sha256).hexdigest()


def issue_pairing_code():
    """Local CLI only. No route may expose this function or its return value."""
    with get_db() as conn:
        init(conn)
        conn.execute('BEGIN IMMEDIATE') if not conn.in_transaction else None
        code = secrets.token_urlsafe(32); stamp = time.time()
        conn.execute('DELETE FROM local_auth_pairings')
        conn.execute('INSERT INTO local_auth_pairings VALUES(?,?,?,?,NULL)', (_digest('pair', code), owner_id(conn), stamp, stamp + PAIR_SECONDS))
    return code


def mint_pairing_code():
    """Trusted local CLI/test fixture alias; deliberately absent from the router."""
    return issue_pairing_code()


def session_active(conn, owner, session_id):
    stamp = time.time()
    return bool(conn.execute('SELECT 1 FROM local_auth_sessions WHERE id=? AND owner_id=? AND owner_id=(SELECT owner_id FROM local_auth_owner WHERE slot=1) AND revoked_at IS NULL AND expires_at>? AND last_seen>?', (session_id, owner, stamp, stamp - IDLE_SECONDS)).fetchone())


def current_principal():
    value = _principal.get()
    if value is None: raise HTTPException(401, 'Local owner pairing is required')
    return dict(value)


def _cookie_principal(request):
    token = request.cookies.get(COOKIE, '')
    if not TOKEN_PATTERN.fullmatch(token): return None
    digest = _digest('session', token)
    with get_db() as conn:
        stamp = time.time()
        row = conn.execute('SELECT id,owner_id,last_seen FROM local_auth_sessions WHERE token_hash=? AND revoked_at IS NULL AND expires_at>? AND last_seen>? AND owner_id=(SELECT owner_id FROM local_auth_owner WHERE slot=1)', (digest, stamp, stamp - IDLE_SECONDS)).fetchone()
        if row and stamp - row['last_seen'] >= 60:
            conn.execute('UPDATE local_auth_sessions SET last_seen=? WHERE id=? AND revoked_at IS NULL', (stamp, row['id']))
    return {'owner_id': row['owner_id'], 'session_id': row['id']} if row else None


def local_peer(scope):
    client = scope.get('client')
    if not client or not isinstance(client[0], str): return False
    # These are synthetic ASGITransport/TestClient peer labels, never socket IPs.
    # No environment variable, Host header or client-supplied forwarded header
    # can create this exemption in an actual HTTP listener.
    if client[0] in {'testclient', 'testserver'}: return True
    try: return ipaddress.ip_address(client[0]).is_loopback
    except ValueError: return False


def restore_quarantined():
    """Operator-only recovery barrier; HTTP cannot remove either marker."""
    marker = data_dir() / 'RECOVERY_QUARANTINE'
    # lexists also recognizes a broken link; uncertain marker state is blocked.
    if os.path.lexists(marker): return True
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='guard_control'").fetchone():
            return False
        row = conn.execute("SELECT value FROM guard_control WHERE key='restore_quarantine'").fetchone()
    return bool(row and row[0] == '1')


class LocalAuthMiddleware:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api'):
            return await self.app(scope, receive, send)
        if not local_peer(scope):
            return await JSONResponse({'detail': 'Local service accepts loopback peers only'}, 403, headers={'Cache-Control': 'no-store'})(scope, receive, send)
        status_only = scope['method'] == 'GET' and scope['path'] == '/api/auth/status'
        if not status_only:
            try: quarantined = restore_quarantined()
            except (OSError, sqlite3.Error):
                return await JSONResponse({'detail': 'Local recovery authorization is unavailable'}, 503, headers={'Cache-Control': 'no-store'})(scope, receive, send)
            if quarantined:
                return await JSONResponse({'detail': 'Offline permission reconciliation is required for this restored installation'}, 423, headers={'Cache-Control': 'no-store'})(scope, receive, send)
        request = Request(scope)
        try: principal = _cookie_principal(request)
        except (AuthStorageError, OSError, sqlite3.Error):
            return await JSONResponse({'detail': 'Local authentication storage is unavailable'}, 503, headers={'Cache-Control': 'no-store'})(scope, receive, send)
        allowed = scope['method'] == 'OPTIONS' or (scope['method'], scope['path']) in {('GET', '/api/auth/status'), ('POST', '/api/auth/login'), ('POST', '/api/auth/identity')}
        if not allowed and principal is None:
            return await JSONResponse({'detail': 'Local owner pairing is required'}, 401, headers={'Cache-Control': 'no-store'})(scope, receive, send)
        context = _principal.set(principal)
        scope['riwaq_principal'] = principal
        # Force file responses through guarded chunks, not an unobservable
        # server-side path-send extension.
        if principal and 'http.response.pathsend' in scope.get('extensions', {}):
            scope = dict(scope)
            scope['extensions'] = {k: v for k, v in scope['extensions'].items() if k != 'http.response.pathsend'}
        started = False
        transition_ack = scope['method'] == 'POST' and scope['path'] in {'/api/auth/login', '/api/auth/logout'}

        async def guarded_send(message):
            nonlocal started
            checked = message['type'] == 'http.response.start' or (message['type'] == 'http.response.body' and message.get('body'))
            if principal and not transition_ack and checked:
                status_code = 401
                try:
                    if not status_only and restore_quarantined():
                        active = False; status_code = 423
                    else:
                        with get_db() as conn:
                            active = session_active(conn, principal['owner_id'], principal['session_id'])
                except (AuthStorageError, OSError, sqlite3.Error):
                    active = False; status_code = 503
                if not active:
                    if started:
                        # Headers already left the process: do not send a second
                        # start or replace bytes with a pretend successful answer.
                        await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
                    else:
                        await JSONResponse({'detail': 'Local session authorization is no longer available'}, status_code, headers={'Cache-Control': 'no-store'})(scope, receive, send)
                    raise SessionRevoked()
            if message['type'] == 'http.response.start': started = True
            await send(message)

        try:
            try: await self.app(scope, receive, guarded_send)
            except* SessionRevoked:
                pass  # StreamingResponse/ManagedStreamingResponse finally ran.
        finally:
            _principal.reset(context)


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pairing_code: str = Field(min_length=43, max_length=43, pattern=r'^[A-Za-z0-9_-]{43}$', repr=False)


class IdentityChallenge(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    nonce: str = Field(min_length=43, max_length=43, pattern=r'^[A-Za-z0-9_-]{43}$')


@router.post('/identity')
def identity(body: IdentityChallenge, response: Response):
    """Public service proof, not user identity and never a session grant."""
    response.headers['Cache-Control'] = 'no-store'
    try: return {'proof': _digest('identity', body.nonce)}
    except (AuthStorageError, OSError):
        raise HTTPException(503, 'Local service identity is unavailable')


def verify_service():
    """CLI-only challenge of this installation at the fixed loopback address.

    Not a defense against a process holding the same OS identity or a relay to
    the real service. Never mint a pairing code before this check succeeds.
    """
    nonce = secrets.token_urlsafe(32)
    expected = _digest('identity', nonce)  # Existing OS-protected key only.
    deadline = time.monotonic() + 3
    try:
        with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
            with client.stream('POST', 'http://127.0.0.1:8765/api/auth/identity',
                               headers={'X-Riwaq-Client':'local','Accept-Encoding':'identity'},
                               json={'nonce':nonce}) as response:
                if response.status_code != 200 or response.headers.get('content-encoding','identity').strip().lower() != 'identity':
                    raise AuthStorageError('Local service identity could not be verified')
                raw = bytearray()
                for part in response.iter_raw():
                    if time.monotonic() > deadline or len(raw) + len(part) > 4096:
                        raise AuthStorageError('Local service identity could not be verified')
                    raw.extend(part)
                value = json.loads(raw)
        proof = value.get('proof') if isinstance(value,dict) and set(value) == {'proof'} else None
        if time.monotonic() > deadline or not isinstance(proof,str) or not re.fullmatch(r'[0-9a-f]{64}',proof) or not hmac.compare_digest(expected,proof):
            raise AuthStorageError('Local service identity could not be verified')
    except (httpx.HTTPError, ValueError, UnicodeError, OSError):
        raise AuthStorageError('Local service identity could not be verified') from None
    return True


def _throttle(key, failure=False, success=False):
    now = time.monotonic()
    with _attempt_lock:
        if success:
            _attempts.pop(key, None)
            return
        start, count = _attempts.get(key, (now, 0))
        if now - start >= 60: start, count = now, 0
        if count >= 12: raise HTTPException(429, 'Too many pairing attempts; try again shortly', headers={'Retry-After': str(max(1, int(60 - now + start)))})
        if failure:
            if len(_attempts) >= 128 and key not in _attempts: _attempts.pop(next(iter(_attempts)))
            _attempts[key] = (start, count + 1)


@router.get('/status')
def status(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    try:
        with get_db() as conn:
            row = conn.execute('SELECT paired_at FROM local_auth_owner WHERE slot=1').fetchone()
        return {'paired': bool(row and row[0] is not None), 'authenticated': _principal.get() is not None}
    except (OSError, sqlite3.Error):
        raise HTTPException(503, 'Local authentication storage is unavailable')


@router.post('/login')
def login(body: Login, request: Request, response: Response):
    try:
        attempt_key = (str(data_dir().resolve()), request.client.host if request.client else 'unknown')
        _throttle(attempt_key)
        digest = _digest('pair', body.pairing_code)
        with get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            stamp = time.time()
            row = conn.execute('SELECT owner_id FROM local_auth_pairings WHERE code_hash=? AND consumed_at IS NULL AND expires_at>? AND owner_id=(SELECT owner_id FROM local_auth_owner WHERE slot=1)', (digest, stamp)).fetchone()
            if row is None:
                _throttle(attempt_key, failure=True)
                raise HTTPException(401, 'Pairing code is invalid, expired or already used')
            token = secrets.token_urlsafe(32)
            conn.execute('UPDATE local_auth_pairings SET consumed_at=? WHERE code_hash=?', (stamp, digest))
            prior = _principal.get()
            if prior:
                conn.execute('UPDATE local_auth_sessions SET revoked_at=? WHERE id=? AND owner_id=?', (stamp, prior['session_id'], prior['owner_id']))
            conn.execute('DELETE FROM local_auth_sessions WHERE expires_at<=? OR revoked_at IS NOT NULL', (stamp,))
            conn.execute('DELETE FROM local_auth_sessions WHERE owner_id=? AND id NOT IN (SELECT id FROM local_auth_sessions WHERE owner_id=? ORDER BY created_at DESC LIMIT ?)', (row['owner_id'], row['owner_id'], MAX_SESSIONS - 1))
            conn.execute('INSERT INTO local_auth_sessions VALUES(?,?,?,?,?,NULL,?)', (secrets.token_hex(16), _digest('session', token), row['owner_id'], stamp, stamp + SESSION_SECONDS, stamp))
            conn.execute('UPDATE local_auth_owner SET paired_at=COALESCE(paired_at,?) WHERE slot=1', (stamp,))
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=request.url.scheme == 'https', samesite='strict', path='/')
        _throttle(attempt_key, success=True)
        response.headers['Cache-Control'] = 'no-store'
        return {'paired': True, 'authenticated': True}
    except (AuthStorageError, OSError, sqlite3.Error):
        raise HTTPException(503, 'Local authentication storage is unavailable')


@router.post('/logout')
def logout(response: Response):
    principal = current_principal()
    try:
        with get_db() as conn:
            conn.execute('UPDATE local_auth_sessions SET revoked_at=? WHERE id=? AND owner_id=?', (time.time(), principal['session_id'], principal['owner_id']))
    except (OSError, sqlite3.Error):
        raise HTTPException(503, 'Local authentication storage is unavailable')
    response.delete_cookie(COOKIE, path='/', httponly=True, samesite='strict')
    response.headers['Cache-Control'] = 'no-store'
    return {'signed_out': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Explicit local-owner browser pairing; never run automatically by the service.')
    parser.add_argument('command', choices=['pair'])
    parser.add_argument('--show', action='store_true', help='Explicitly output the short-lived one-use pairing code; do not log this output.')
    args = parser.parse_args(argv)
    if not args.show: parser.error('Explicit --show is required; use scripts/pair-browser.ps1 -Copy for clipboard-only display')
    try:
        verify_service()
        code = issue_pairing_code()
    except (AuthStorageError, OSError, sqlite3.Error):
        parser.exit(1, 'Local service identity or pairing storage could not be verified. No pairing code was issued.\n')
    print(code)


if __name__ == '__main__':
    main()
