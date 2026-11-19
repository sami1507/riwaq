"""Temporary local-owner fixtures. No production data, remote calls or real user codes.

These tests use the real pairing/login path, not a bypass token or disabled
middleware. HTTP tests need the parent test runner on this Windows environment.
"""
import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import stat
import time

import httpx
import pytest
from fastapi import FastAPI, Response, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend import local_auth as auth
from backend.db import get_db


@pytest.fixture
def initialized(tmp_path, monkeypatch):
    folder = tmp_path / 'owner-data'
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(folder))
    with get_db() as conn: auth.init(conn)
    yield folder
    auth._key_cache.clear()


@pytest.fixture
def service(initialized):
    # The existing browser header/origin guard remains in force with auth.
    from backend.app import RequestGuard
    app = FastAPI()
    app.include_router(auth.router)
    app.add_middleware(auth.LocalAuthMiddleware)
    app.add_middleware(RequestGuard)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, error):
        return JSONResponse({'detail': [{'loc': e['loc'], 'type': e['type']} for e in error.errors()]}, 422)

    @app.get('/api/private')
    def private(): return auth.current_principal()

    @app.get('/')
    def public_shell(): return {'pairing_shell': True}

    return app


@pytest.fixture
def client(service):
    with TestClient(service, headers={'X-Riwaq-Client': 'local'}) as client:
        yield client


def pair(client):
    code = auth.issue_pairing_code()
    result = client.post('/api/auth/login', json={'pairing_code': code})
    status = result.status_code
    assert status == 200
    return result


def test_real_private_key_and_hash_only_pairing_storage(initialized):
    code = auth.issue_pairing_code()
    key_path = initialized / 'local-auth' / 'owner.key'
    first = auth._master_key()
    auth._key_cache.clear()
    same_after_reload = auth._master_key() == first
    assert same_after_reload
    raw = key_path.read_bytes()
    plaintext_key_stored = first in raw
    if os.name == 'nt':
        assert raw.startswith(b'RWA1W')
        assert not plaintext_key_stored
    else:
        assert raw.startswith(b'RWA1P')
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(key_path.parent.stat().st_mode) == 0o700
    with get_db() as conn:
        row = dict(conn.execute('SELECT * FROM local_auth_pairings').fetchone())
        principal = auth.owner_id(conn)
    raw_code_stored = code in json.dumps(row)
    simple_hash_stored = hashlib.sha256(code.encode()).hexdigest() == row['code_hash']
    assert not raw_code_stored and not simple_hash_stored
    assert row['owner_id'] == principal
    assert 0 < row['expires_at'] - time.time() <= auth.PAIR_SECONDS


def test_key_corruption_and_missing_key_fail_closed(initialized):
    path = initialized / 'local-auth' / 'owner.key'
    path.write_bytes(b'corrupt-test-fixture')
    auth._key_cache.clear()
    with pytest.raises(auth.AuthStorageError): auth.issue_pairing_code()
    path.unlink()
    with pytest.raises(auth.AuthStorageError):
        with get_db() as conn: auth.init(conn)


def test_status_no_private_data_and_static_header_is_not_auth(client):
    response = client.get('/api/auth/status')
    assert response.json() == {'paired': False, 'authenticated': False}
    assert response.headers['cache-control'] == 'no-store'
    assert client.get('/api/private').status_code == 401
    assert client.get('/').status_code == 200
    # No network endpoint can mint a local pairing secret.
    assert client.post('/api/auth/pair', json={}).status_code == 401
    assert client.get('/api/auth/login').status_code == 401


def test_pairing_cookie_flags_hash_storage_and_context_reset(client):
    response = pair(client)
    flags = response.headers['set-cookie'].split(';')[1:]
    flags = [flag.strip().lower() for flag in flags]
    assert 'httponly' in flags and 'samesite=strict' in flags and 'path=/' in flags
    assert 'secure' not in flags  # Explicitly local HTTP; HTTPS uses Secure.
    assert set(response.json()) == {'paired', 'authenticated'}
    principal = client.get('/api/private').json()
    assert set(principal) == {'owner_id', 'session_id'}
    with get_db() as conn:
        assert auth.session_active(conn, principal['owner_id'], principal['session_id'])
        assert not auth.session_active(conn, 'different-owner', principal['session_id'])
        row = dict(conn.execute('SELECT * FROM local_auth_sessions WHERE id=?', (principal['session_id'],)).fetchone())
    token = client.cookies.get(auth.COOKIE)
    raw_token_stored = token in json.dumps(row)
    assert not raw_token_stored
    with pytest.raises(Exception) as result: auth.current_principal()
    assert getattr(result.value, 'status_code', None) == 401


def test_codes_rotate_expire_and_cannot_be_replayed(client):
    old = auth.issue_pairing_code()
    fresh = auth.issue_pairing_code()
    assert client.post('/api/auth/login', json={'pairing_code': old}).status_code == 401
    result = client.post('/api/auth/login', json={'pairing_code': fresh})
    assert result.status_code == 200
    assert client.post('/api/auth/login', json={'pairing_code': fresh}).status_code == 401
    code = auth.issue_pairing_code()
    with get_db() as conn: conn.execute('UPDATE local_auth_pairings SET expires_at=?', (time.time() - 1,))
    assert client.post('/api/auth/login', json={'pairing_code': code}).status_code == 401


def test_logout_durable_revoke_and_reauthentication_rotation(client):
    pair(client)
    prior_token = client.cookies.get(auth.COOKIE)
    prior = client.get('/api/private').json()
    pair(client)
    newer = client.get('/api/private').json()
    assert prior['session_id'] != newer['session_id'] and prior['owner_id'] == newer['owner_id']
    with get_db() as conn:
        assert not auth.session_active(conn, prior['owner_id'], prior['session_id'])
        assert auth.session_active(conn, newer['owner_id'], newer['session_id'])
    old_request = client.get('/api/private', headers={'Cookie': auth.COOKIE + '=' + prior_token})
    assert old_request.status_code == 401
    assert client.post('/api/auth/logout').json() == {'signed_out': True}
    assert client.get('/api/private').status_code == 401
    with get_db() as conn: assert not auth.session_active(conn, newer['owner_id'], newer['session_id'])
    assert client.get('/api/auth/status').json() == {'paired': True, 'authenticated': False}


@pytest.mark.parametrize('expiry', ['absolute', 'idle'])
def test_absolute_and_idle_expiry_are_durable(client, expiry):
    pair(client)
    principal = client.get('/api/private').json()
    with get_db() as conn:
        if expiry == 'absolute': conn.execute('UPDATE local_auth_sessions SET expires_at=?', (time.time() - 1,))
        else: conn.execute('UPDATE local_auth_sessions SET last_seen=?', (time.time() - auth.IDLE_SECONDS - 1,))
        assert not auth.session_active(conn, principal['owner_id'], principal['session_id'])
    assert client.get('/api/private').status_code == 401


def test_normal_activity_updates_idle_timestamp_without_extending_absolute_expiry(client):
    pair(client)
    with get_db() as conn:
        original_expiry = conn.execute('SELECT expires_at FROM local_auth_sessions WHERE revoked_at IS NULL').fetchone()[0]
        conn.execute('UPDATE local_auth_sessions SET last_seen=?', (time.time() - 120,))
    assert client.get('/api/private').status_code == 200
    with get_db() as conn:
        row = conn.execute('SELECT last_seen,expires_at FROM local_auth_sessions WHERE revoked_at IS NULL').fetchone()
    assert time.time() - row['last_seen'] < 5 and row['expires_at'] == original_expiry


def test_peer_gate_never_trusts_host_or_forwarded_headers(service):
    with TestClient(service, client=('203.0.113.9', 55001), headers={'X-Riwaq-Client':'local','Host':'127.0.0.1','X-Forwarded-For':'127.0.0.1'}) as foreign:
        assert foreign.get('/api/auth/status').status_code == 403
        assert foreign.post('/api/auth/login', json={'pairing_code': secrets.token_urlsafe(32)}).status_code == 403


def test_hostile_origin_and_missing_browser_header_remain_blocked(client):
    code = auth.issue_pairing_code()
    assert client.post('/api/auth/login', json={'pairing_code': code}, headers={'Origin':'https://untrusted.invalid'}).status_code == 403
    assert client.post('/api/auth/login', json={'pairing_code': code}, headers={'X-Riwaq-Client':''}).status_code == 403
    assert client.post('/api/auth/login', json={'pairing_code': code}).status_code == 200


def test_successes_do_not_exhaust_failure_budget_and_owner_cannot_be_chosen(client):
    for _ in range(14): pair(client)
    assert client.get('/api/private').status_code == 200
    code = auth.issue_pairing_code()
    rejected = client.post('/api/auth/login', json={'pairing_code': code, 'owner_id':'chosen-owner'})
    assert rejected.status_code == 422
    assert client.post('/api/auth/login', json={'pairing_code': code}).status_code == 200


def test_failed_attempts_are_bounded_and_storage_failure_is_sanitized(client, monkeypatch):
    for _ in range(12):
        response = client.post('/api/auth/login', json={'pairing_code': secrets.token_urlsafe(32)})
        assert response.status_code == 401
    assert client.post('/api/auth/login', json={'pairing_code': secrets.token_urlsafe(32)}).status_code == 429
    auth._attempts.clear()
    def fail(*args, **kwargs): raise auth.AuthStorageError('internal-sensitive-fixture-detail')
    monkeypatch.setattr(auth, '_master_key', fail)
    response = client.post('/api/auth/login', json={'pairing_code': secrets.token_urlsafe(32)})
    assert response.status_code == 503
    leaked = 'internal-sensitive-fixture-detail' in response.text
    assert not leaked


def test_session_cap_and_owner_are_persistent(service, initialized):
    # Distinct browser sessions; reauthentication of one cookie is tested above.
    for _ in range(auth.MAX_SESSIONS + 2):
        with TestClient(service, headers={'X-Riwaq-Client':'local'}) as browser: pair(browser)
    with get_db() as conn:
        identity = auth.owner_id(conn)
        assert conn.execute('SELECT COUNT(*) FROM local_auth_sessions WHERE revoked_at IS NULL').fetchone()[0] == auth.MAX_SESSIONS
        auth.init(conn)
        assert auth.owner_id(conn) == identity


def stream_scope(client, spec_version='2.4'):
    return {'type':'http', 'asgi':{'version':'3.0','spec_version':spec_version},
            'http_version':'1.1','method':'GET','scheme':'http','path':'/api/private-stream',
            'raw_path':b'/api/private-stream','query_string':b'','root_path':'',
            'headers':[(b'cookie',(auth.COOKIE+'='+client.cookies.get(auth.COOKIE)).encode('ascii'))],
            'client':('127.0.0.1',43210),'server':('127.0.0.1',8765),
            'extensions':{'http.response.pathsend':{}}}


@pytest.mark.parametrize('spec_version', ['2.0','2.4'])
def test_logout_during_stream_denies_late_bytes_and_runs_finalizer(client, spec_version):
    """Synthetic response only: no provider, worker or production data involved."""
    from backend.app import ManagedStreamingResponse
    pair(client)
    scope = stream_scope(client, spec_version)

    async def scenario():
        blocked, release = asyncio.Event(), asyncio.Event()
        sent, state = [], {'finalized':False,'generator_closed':False,'continued':False}
        async def source():
            try:
                yield b'authorized-before-logout'
                blocked.set()
                await release.wait()
                yield b'private-late-byte-must-not-leave'
                state['continued'] = True
            finally: state['generator_closed'] = True
        iterator = source()
        async def finalize():
            state['finalized'] = True
            await iterator.aclose()
        async def app(inner, receive, send):
            assert 'http.response.pathsend' not in inner.get('extensions', {})
            await ManagedStreamingResponse(iterator, finalizer=finalize)(inner, receive, send)
        async def receive():
            await asyncio.Event().wait()
        async def send(message): sent.append(message.copy())
        work = asyncio.create_task(auth.LocalAuthMiddleware(app)(scope, receive, send))
        try:
            await asyncio.wait_for(blocked.wait(), 2)
            assert client.post('/api/auth/logout').status_code == 200
            release.set()
            await asyncio.wait_for(work, 2)
        finally:
            if not work.done():
                work.cancel(); await asyncio.gather(work, return_exceptions=True)
        assert [m['status'] for m in sent if m['type']=='http.response.start'] == [200]
        assert b''.join(m.get('body',b'') for m in sent) == b'authorized-before-logout'
        assert sent[-1] == {'type':'http.response.body','body':b'','more_body':False}
        assert state == {'finalized':True,'generator_closed':True,'continued':False}
    asyncio.run(scenario())


def test_logout_before_response_start_sends_only_unauthorized(client):
    pair(client)
    scope = stream_scope(client)
    async def scenario():
        blocked, release = asyncio.Event(), asyncio.Event()
        sent, cleanup = [], []
        async def app(inner, receive, send):
            try:
                blocked.set(); await release.wait()
                await send({'type':'http.response.start','status':200,'headers':[]})
                await send({'type':'http.response.body','body':b'private-late-response'})
            finally: cleanup.append(True)
        async def receive(): return {'type':'http.request','body':b''}
        async def send(message): sent.append(message.copy())
        work = asyncio.create_task(auth.LocalAuthMiddleware(app)(scope,receive,send))
        try:
            await asyncio.wait_for(blocked.wait(),2)
            assert client.post('/api/auth/logout').status_code == 200
            release.set(); await asyncio.wait_for(work,2)
        finally:
            if not work.done(): work.cancel(); await asyncio.gather(work,return_exceptions=True)
        assert [m['status'] for m in sent if m['type']=='http.response.start'] == [401]
        assert b'private-late-response' not in b''.join(m.get('body',b'') for m in sent)
        assert cleanup == [True]
    asyncio.run(scenario())


@pytest.mark.parametrize('marker_kind', ['sentinel','table'])
def test_recovered_installation_quarantine_blocks_all_api_except_status(client, initialized, marker_kind):
    pair(client)
    if marker_kind == 'sentinel':
        (initialized / 'RECOVERY_QUARANTINE').write_text('Offline reconciliation required', encoding='utf-8')
    else:
        with get_db() as conn:
            conn.execute('CREATE TABLE guard_control(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            conn.execute("INSERT INTO guard_control VALUES('restore_quarantine','1')")
    assert client.get('/api/auth/status').status_code == 200
    assert client.get('/').status_code == 200
    for method, path in [('GET','/api/private'),('POST','/api/auth/login'),('POST','/api/auth/logout'),('POST','/api/auth/identity'),('OPTIONS','/api/private')]:
        response = client.request(method,path,json={} if method=='POST' else None)
        assert response.status_code == 423
        assert response.json()['detail'] == 'Offline permission reconciliation is required for this restored installation'


def test_recovery_sentinel_blocks_before_auth_key_or_schema_access(initialized):
    (initialized / 'RECOVERY_QUARANTINE').write_text('Recovery in progress', encoding='utf-8')
    (initialized / 'local-auth' / 'owner.key').unlink()
    called = []
    async def app(scope, receive, send): called.append(True)
    async def scenario():
        sent=[]
        async def receive(): return {'type':'http.request','body':b''}
        async def send(message): sent.append(message)
        scope={'type':'http','path':'/api/projects','method':'GET','client':('127.0.0.1',1),'headers':[]}
        await auth.LocalAuthMiddleware(app)(scope,receive,send)
        assert sent[0]['status'] == 423
    asyncio.run(scenario())
    assert called == []


def test_logout_storage_failure_after_authorization_is_sanitized(initialized, monkeypatch):
    with get_db() as conn: owner=auth.owner_id(conn)
    context=auth._principal.set({'owner_id':owner,'session_id':'synthetic-authorized-request'})
    def failed_db():raise sqlite3.OperationalError('sensitive-fixture-path')
    monkeypatch.setattr(auth,'get_db',failed_db)
    try:
        with pytest.raises(HTTPException) as result:auth.logout(Response())
        assert result.value.status_code==503
        assert result.value.detail=='Local authentication storage is unavailable'
    finally:auth._principal.reset(context)


def mock_identity_http(monkeypatch, handler):
    """Only synthetic loopback HTTP; never contact a listening service."""
    real_client=httpx.Client
    options=[]
    def client(**kwargs):
        options.append(kwargs.copy())
        return real_client(transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(auth.httpx,'Client',client)
    return options


def proof_response(nonce, proof=None):
    payload=json.dumps({'proof':auth._digest('identity',nonce) if proof is None else proof}).encode()
    return httpx.Response(200,headers={'Content-Type':'application/json'},stream=httpx.ByteStream(payload))


def test_identity_endpoint_is_public_proof_only_without_minting(client):
    nonce=secrets.token_urlsafe(32)
    response=client.post('/api/auth/identity',json={'nonce':nonce})
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    assert set(response.json())=={'proof'}
    matched=response.json()['proof']==auth._digest('identity',nonce)
    domain_separated=response.json()['proof']!=auth._digest('pair',nonce)
    assert matched and domain_separated and auth.COOKIE not in response.cookies
    assert client.get('/api/private').status_code==401
    assert client.get('/api/auth/status').json()=={'paired':False,'authenticated':False}
    with get_db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM local_auth_pairings').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM local_auth_sessions').fetchone()[0]==0
    for body in [{'nonce':'too-short'}, {'nonce':'a'*42+'/'}, {'nonce':nonce,'owner_id':'chosen'}, {'nonce':43}]:
        assert client.post('/api/auth/identity',json=body).status_code==422
    assert client.post('/api/auth/identity',json={'nonce':nonce},headers={'Origin':'https://untrusted.invalid'}).status_code==403
    assert client.post('/api/auth/identity',json={'nonce':nonce},headers={'X-Riwaq-Client':''}).status_code==403


def test_verify_service_fixed_address_fresh_nonces_and_no_proxy(initialized,monkeypatch):
    nonces=[]
    def handler(request):
        assert str(request.url)=='http://127.0.0.1:8765/api/auth/identity'
        assert request.method=='POST' and request.headers['x-riwaq-client']=='local'
        assert request.headers['accept-encoding']=='identity'
        payload=json.loads(request.content)
        assert set(payload)=={'nonce'} and auth.TOKEN_PATTERN.fullmatch(payload['nonce'])
        nonces.append(payload['nonce'])
        return proof_response(payload['nonce'])
    options=mock_identity_http(monkeypatch,handler)
    assert auth.verify_service() and auth.verify_service()
    assert len(nonces)==2 and nonces[0]!=nonces[1]
    assert all(o=={'timeout':3,'trust_env':False,'follow_redirects':False} for o in options)


@pytest.mark.parametrize('failure',['wrong_proof','redirect','oversize','compression','extra_field'])
def test_verify_service_rejects_foreign_or_invalid_response_without_minting(initialized,monkeypatch,failure):
    requests=[]
    def handler(request):
        requests.append(str(request.url));nonce=json.loads(request.content)['nonce']
        if failure=='redirect':return httpx.Response(307,headers={'Location':'http://127.0.0.1:8766/api/auth/identity'})
        if failure=='oversize':return httpx.Response(200,stream=httpx.ByteStream(b'x'*4097))
        if failure=='compression':return httpx.Response(200,headers={'Content-Encoding':'gzip'},stream=httpx.ByteStream(b'not-read'))
        if failure=='extra_field':return httpx.Response(200,stream=httpx.ByteStream(json.dumps({'proof':auth._digest('identity',nonce),'other':True}).encode()))
        return proof_response(nonce,'0'*64)
    mock_identity_http(monkeypatch,handler)
    with pytest.raises(auth.AuthStorageError):auth.verify_service()
    assert len(requests)==1
    with get_db() as conn:assert conn.execute('SELECT COUNT(*) FROM local_auth_pairings').fetchone()[0]==0


def test_verify_service_missing_key_never_connects_or_creates_key(initialized,monkeypatch):
    path=initialized/'local-auth'/'owner.key';path.unlink();auth._key_cache.clear()
    calls=[]
    def forbidden(**kwargs):calls.append(True);raise AssertionError('No HTTP without an existing local key')
    monkeypatch.setattr(auth.httpx,'Client',forbidden)
    with pytest.raises(auth.AuthStorageError):auth.verify_service()
    assert not calls and not path.exists()


@pytest.mark.parametrize('frozen_dispatch',[False,True])
def test_cli_refuses_to_issue_code_until_service_proof_matches(initialized,monkeypatch,capsys,frozen_dispatch):
    actions=[]
    def handler(request):actions.append('challenge');return proof_response(json.loads(request.content)['nonce'],'0'*64)
    mock_identity_http(monkeypatch,handler)
    def forbidden_issue():actions.append('issue');raise AssertionError('Must not mint before successful proof')
    monkeypatch.setattr(auth,'issue_pairing_code',forbidden_issue)
    if frozen_dispatch:
        # Exercise the actual frozen dispatch branch in source, not a native build.
        from backend import launcher
        monkeypatch.setattr(launcher.sys,'argv',['riwaq-service.exe','--pair-browser','--show'])
        call=launcher.main
    else:call=lambda:auth.main(['pair','--show'])
    with pytest.raises(SystemExit) as result:call()
    captured=capsys.readouterr()
    assert result.value.code==1 and actions==['challenge'] and captured.out==''
    assert 'No pairing code was issued' in captured.err


def test_cli_verifies_then_issues_code_only_on_explicit_show(initialized,monkeypatch,capsys):
    actions=[];synthetic=secrets.token_urlsafe(32)
    def handler(request):actions.append('challenge');return proof_response(json.loads(request.content)['nonce'])
    mock_identity_http(monkeypatch,handler)
    def issue():actions.append('issue');return synthetic
    monkeypatch.setattr(auth,'issue_pairing_code',issue)
    auth.main(['pair','--show'])
    printed_only_code=capsys.readouterr().out==synthetic+'\n'
    assert actions==['challenge','issue'] and printed_only_code
