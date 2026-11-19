"""Trusted project/session authority for every visual storage and worker path."""
from contextlib import contextmanager
from contextvars import ContextVar
from fastapi import HTTPException
from backend import guard,local_auth
from backend.db import get_db
_worker_actor=ContextVar('visual_worker_actor',default=None)

def actor():
    bound=_worker_actor.get()
    if bound is not None:return bound
    principal=local_auth.current_principal()
    return guard.Actor(principal['owner_id'],principal['session_id'])

def require_project(conn,project_id,acting=None):
    acting=acting or actor()
    if not isinstance(acting,guard.Actor):raise HTTPException(401,'Visual authority is required')
    try:guard.check_actor(conn,acting,project_id)
    except guard.Denied:raise HTTPException(404,'Visual resource unavailable') from None
    if conn.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone() is None:raise HTTPException(404,'Visual resource unavailable')
    return acting

def allowed(project_id,acting):
    try:
        with get_db() as conn:require_project(conn,project_id,acting)
        return True
    except HTTPException:return False

@contextmanager
def worker_scope(acting):
    if not isinstance(acting,guard.Actor):raise HTTPException(401,'Visual authority is required')
    token=_worker_actor.set(acting)
    try:yield
    finally:_worker_actor.reset(token)
