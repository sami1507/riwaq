"""Durable admission receipts for generation, without a second message store.

A key identifies one intended attempt. Repetition returns its saved result or
its still-running/unknown state; it never repeats a provider call. Request text
is not copied here. Retention follows the original conversation's deletion.
"""
import hashlib
import json
from fastapi import HTTPException
from backend.db import now


def initialize(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS generation_requests(
      owner_id TEXT NOT NULL,request_id TEXT NOT NULL,
      conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
      project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      payload_sha256 TEXT NOT NULL,state TEXT NOT NULL,
      result_conversation_id TEXT, message_id TEXT,
      created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
      PRIMARY KEY(owner_id,request_id))''')


def payload_hash(body):
    value=body.model_dump(exclude={'request_id'})
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def find(conn,actor,conversation_id,body):
    if body.request_id is None:return None
    row=conn.execute('SELECT * FROM generation_requests WHERE owner_id=? AND request_id=?',(actor.owner_id,body.request_id)).fetchone()
    if row is not None and (row['conversation_id']!=conversation_id or row['payload_sha256']!=payload_hash(body)):
        raise HTTPException(409,{'code':'generation_key_conflict','message':'This request key already identifies a different attempt. No additional generation was started.'})
    return dict(row) if row is not None else None


def reserve(conn,actor,conversation_id,project_id,body):
    if body.request_id is None:return
    conn.execute('INSERT INTO generation_requests VALUES(?,?,?,?,?,?,NULL,NULL,?,?)',
        (actor.owner_id,body.request_id,conversation_id,project_id,payload_hash(body),'preparing',now(),now()))


def bind(conn,job,conversation_id,message_id):
    key=job.get('request_id')
    if key is not None:
        changed=conn.execute("UPDATE generation_requests SET state='bound',result_conversation_id=?,message_id=?,updated_at=? WHERE owner_id=? AND request_id=? AND state='preparing'",
            (conversation_id,message_id,now(),job['actor'].owner_id,key)).rowcount
        if changed!=1:raise HTTPException(409,'Generation admission receipt changed; no message was committed.')


def reject(conn,job):
    if job.get('request_id') is not None:
        conn.execute("UPDATE generation_requests SET state='not_started',updated_at=? WHERE owner_id=? AND request_id=? AND state='preparing'",
            (now(),job['actor'].owner_id,job['request_id']))


def unavailable(row):
    code='generation_in_progress_or_interrupted' if row['state'] in ('preparing','bound') else 'generation_not_started'
    raise HTTPException(409,{'code':code,'message':'This attempt has no completed saved response available. Check the conversation before explicitly starting a new attempt; the original request was not repeated.'})
