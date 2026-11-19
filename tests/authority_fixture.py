"""Trusted test-only principal backed by actual active-session/guard tables.

For isolated router/component tests that do not mount HTTP pairing middleware.
The production guard is not mocked. End-to-end API tests pair through HTTP.
"""
from contextlib import contextmanager
import time

from backend import db, guard, knowledge, local_auth, studio


@contextmanager
def fixture_owner(connection=None):
    def seed(c):
        c.executescript('''
          CREATE TABLE IF NOT EXISTS local_auth_owner(slot INTEGER PRIMARY KEY,owner_id TEXT UNIQUE NOT NULL,created_at REAL NOT NULL,paired_at REAL);
          CREATE TABLE IF NOT EXISTS local_auth_sessions(id TEXT PRIMARY KEY,token_hash TEXT UNIQUE NOT NULL,owner_id TEXT NOT NULL,
            created_at REAL NOT NULL,expires_at REAL NOT NULL,revoked_at REAL,last_seen REAL NOT NULL);
        ''')
        stamp=time.time()
        c.execute('INSERT OR IGNORE INTO local_auth_owner VALUES(1,?,?,?)',(db.uid(),stamp,stamp))
        owner=local_auth.owner_id(c);session=db.uid()
        c.execute('INSERT INTO local_auth_sessions VALUES(?,?,?,?,?,NULL,?)',(session,db.uid(),owner,stamp,stamp+3600,stamp))
        knowledge.init_knowledge(c)
        studio.init_studio(c)
        guard.init(c)
        c.commit()
        return {'owner_id':owner,'session_id':session}
    if connection is None:
        with db.get_db() as c:principal=seed(c)
    else:principal=seed(connection)
    token=local_auth._principal.set(principal)
    try:yield guard.Actor(principal['owner_id'],principal['session_id'])
    finally:local_auth._principal.reset(token)
