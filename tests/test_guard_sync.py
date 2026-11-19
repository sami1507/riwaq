"""Synthetic keyring/network fixtures and real temporary SQLite coordination.

No remote account, model, real keyring credential or production database is used.
HTTP pairing itself is exercised in test_guard_auth; these tests explicitly set
an authenticated request context with a real temporary durable session.
"""
import asyncio
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend import local_auth as auth, sync_client as local
from backend.db import get_db
from sync_server import app as server


class Gate:
    def __await__(self):
        yield self
        return None


def completed(coro):
    """Drive only a no-I/O coroutine; an unexpected await fails the test."""
    try:
        with pytest.raises(StopIteration) as result: coro.send(None)
        return result.value.value
    finally: coro.close()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path/'local'))
    monkeypatch.setenv('RIWAQ_SYNC_URL','http://127.0.0.1:8766')
    with get_db() as conn:
        auth.init(conn)
        who={'owner_id':auth.owner_id(conn),'session_id':secrets.token_hex(16)}
        stamp=time.time()
        conn.execute('INSERT INTO local_auth_sessions VALUES(?,?,?,?,?,NULL,?)',
                     (who['session_id'],'synthetic-session-digest',who['owner_id'],stamp,stamp+3600,stamp))
        conn.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        conn.execute("INSERT INTO settings VALUES('mode','\"hybrid\"')")
    context=auth._principal.set(who)
    store={};calls=[]
    def read(service,target):return store.get((service,target))
    def write(service,target,value):calls.append('write');store[(service,target)]=value
    def delete(service,target):calls.append('delete');store.pop((service,target),None)
    monkeypatch.setattr(local.keyring,'get_password',read)
    monkeypatch.setattr(local.keyring,'set_password',write)
    monkeypatch.setattr(local.keyring,'delete_password',delete)
    body=local.Login(email='synthetic@example.test',password='synthetic-password-123')
    yield SimpleNamespace(who=who,store=store,calls=calls,body=body)
    auth._principal.reset(context);auth._key_cache.clear()


def login_now(monkeypatch, isolated, value=None):
    value=value or secrets.token_urlsafe(32)
    async def proxy(*args,**kwargs):return {'token':value}
    monkeypatch.setattr(local,'proxy',proxy)
    assert completed(local.authenticate('login',isolated.body))['authenticated']
    return value


def test_durable_epoch_rejects_login_completed_after_logout(isolated,monkeypatch):
    gate=Gate()
    async def proxy(*args,**kwargs):await gate;return {'token':secrets.token_urlsafe(32)}
    monkeypatch.setattr(local,'proxy',proxy)
    login=local.authenticate('login',isolated.body)
    try:
        assert login.send(None) is gate
        assert completed(local.logout())=={'signed_out':True,'remote_revoked':False}
        with pytest.raises(HTTPException) as result:login.send(None)
        assert result.value.status_code==409
        assert isolated.calls==['delete'] and not isolated.store and local.token() is None
        with get_db() as conn:
            row=conn.execute('SELECT revision,token_hash FROM sync_auth_state').fetchone()
            assert tuple(row)==(2,None)
    finally:login.close()


def test_newer_login_wins_using_shared_sqlite_not_module_counter(isolated,monkeypatch):
    # A second independent module instance uses a separate SQLite connection;
    # this verifies durable coordination, not a real multiprocess stress test.
    spec=importlib.util.spec_from_file_location('synthetic_sync_instance',Path(local.__file__))
    other=importlib.util.module_from_spec(spec);spec.loader.exec_module(other)
    old_gate,new_gate=Gate(),Gate();old_value,new_value=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    async def old_proxy(*args,**kwargs):await old_gate;return {'token':old_value}
    async def new_proxy(*args,**kwargs):await new_gate;return {'token':new_value}
    monkeypatch.setattr(local,'proxy',old_proxy);monkeypatch.setattr(other,'proxy',new_proxy)
    old=local.authenticate('login',isolated.body);new=other.authenticate('login',isolated.body)
    try:
        assert old.send(None) is old_gate and new.send(None) is new_gate
        assert completed(new)['authenticated']
        with pytest.raises(HTTPException) as result:old.send(None)
        assert result.value.status_code==409
        retained_new=local.token()==new_value
        assert retained_new and isolated.calls==['write']
    finally:old.close();new.close()


def test_remote_logout_uses_old_token_and_cannot_delete_new_login(isolated,monkeypatch):
    old_value=login_now(monkeypatch,isolated);gate=Gate();captured=[]
    async def revoke(*args,**kwargs):
        captured.append(kwargs.get('credential'));await gate;return {'ok':True}
    monkeypatch.setattr(local,'proxy',revoke)
    logout=local.logout()
    try:
        assert logout.send(None) is gate
        assert local.token() is None and not isolated.store
        new_value=login_now(monkeypatch,isolated)
        assert completed(logout)=={'signed_out':True,'remote_revoked':True}
        matched=local.token()==new_value and captured==[old_value]
        assert matched
    finally:logout.close()


def test_local_mode_and_remote_failure_still_clear_local_authorization(isolated,monkeypatch):
    login_now(monkeypatch,isolated)
    with get_db() as conn:conn.execute("UPDATE settings SET value='\"local\"' WHERE key='mode'")
    async def forbidden(*args,**kwargs):raise AssertionError('No external call in local mode')
    monkeypatch.setattr(local,'proxy',forbidden)
    assert completed(local.logout())=={'signed_out':True,'remote_revoked':False}
    assert local.token() is None
    with get_db() as conn:conn.execute("UPDATE settings SET value='\"hybrid\"' WHERE key='mode'")
    login_now(monkeypatch,isolated)
    async def failed(*args,**kwargs):raise HTTPException(503,'Synthetic remote outage')
    monkeypatch.setattr(local,'proxy',failed)
    assert completed(local.logout())=={'signed_out':True,'remote_revoked':False}
    assert local.token() is None and not isolated.store


def test_keyring_delete_failure_commits_revocation_and_blocks_token(isolated,monkeypatch):
    login_now(monkeypatch,isolated)
    def fail(*args):raise RuntimeError('sensitive-fixture-error')
    monkeypatch.setattr(local.keyring,'delete_password',fail)
    with pytest.raises(HTTPException) as result:completed(local.logout())
    assert result.value.status_code==503 and 'sensitive-fixture-error' not in str(result.value.detail)
    assert isolated.store and local.token() is None
    with get_db() as conn:
        assert conn.execute('SELECT token_hash FROM sync_auth_state').fetchone()[0] is None


def test_failed_sqlite_commit_cannot_activate_leftover_keyring_value(isolated,monkeypatch):
    gate=Gate()
    async def proxy(*args,**kwargs):await gate;return {'token':secrets.token_urlsafe(32)}
    monkeypatch.setattr(local,'proxy',proxy)
    work=local.authenticate('login',isolated.body)
    real_get_db=local.get_db
    @contextmanager
    def failing_commit():
        with real_get_db() as conn:
            yield conn
            raise sqlite3.OperationalError('synthetic-commit-failure')
    try:
        assert work.send(None) is gate
        monkeypatch.setattr(local,'get_db',failing_commit)
        with pytest.raises(HTTPException) as result:work.send(None)
        assert result.value.status_code==503
        monkeypatch.setattr(local,'get_db',real_get_db)
        assert isolated.store and local.token() is None
    finally:work.close()


def test_revoked_local_session_cannot_finish_sync_login(isolated,monkeypatch):
    gate=Gate()
    async def proxy(*args,**kwargs):await gate;return {'token':secrets.token_urlsafe(32)}
    monkeypatch.setattr(local,'proxy',proxy)
    work=local.authenticate('login',isolated.body)
    try:
        assert work.send(None) is gate
        with get_db() as conn:conn.execute('UPDATE local_auth_sessions SET revoked_at=?',(time.time(),))
        with pytest.raises(HTTPException) as result:work.send(None)
        assert result.value.status_code==401 and not isolated.store
    finally:work.close()


def test_legacy_or_mismatched_keyring_value_not_adopted(isolated,monkeypatch):
    isolated.store[('RiwaqSync',local.endpoint())]=secrets.token_urlsafe(32)
    assert local.token() is None
    login_now(monkeypatch,isolated)
    isolated.store[('RiwaqSync',local.endpoint())]=secrets.token_urlsafe(32)
    assert local.token() is None


def test_sync_body_stall_deadline_releases_slot(monkeypatch):
    monkeypatch.setattr(server,'BODY_DEADLINE_SECONDS',.02)
    monkeypatch.setattr(server,'REQUEST_SLOTS',threading.BoundedSemaphore(1))
    async def scenario():
        sent=[];cancelled=[];dispatched=[]
        async def app(scope,receive,send):dispatched.append(True)
        async def receive():
            try:await asyncio.Event().wait()
            finally:cancelled.append(True)
        async def send(message):sent.append(message)
        await asyncio.wait_for(server.BoundaryMiddleware(app)({'type':'http'},receive,send),1)
        assert sent[0]['status']==408 and cancelled==[True] and not dispatched
        assert server.REQUEST_SLOTS.acquire(blocking=False)
        server.REQUEST_SLOTS.release()
    asyncio.run(scenario())


def test_sync_body_deadline_is_total_not_per_chunk(monkeypatch):
    clock=[0.0];reads=[];sent=[]
    monkeypatch.setattr(server,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    monkeypatch.setattr(server,'BODY_DEADLINE_SECONDS',15)
    monkeypatch.setattr(server,'REQUEST_SLOTS',threading.BoundedSemaphore(1))
    async def scenario():
        async def app(scope,receive,send):raise AssertionError('Incomplete request must not dispatch')
        async def receive():
            reads.append(True);clock[0]+=6
            return {'type':'http.request','body':b'x','more_body':True}
        async def send(message):sent.append(message)
        await server.BoundaryMiddleware(app)({'type':'http'},receive,send)
        assert sent[0]['status']==408 and len(reads)==3
        assert server.REQUEST_SLOTS.acquire(blocking=False)
        server.REQUEST_SLOTS.release()
    asyncio.run(scenario())
