"""Opt-in, rights-gated public corpus. Never imports private project records.

Network work is explicit, sequential, resumable and byte-accounted. Originals,
extracted lines, license evidence and immutable versions are stored atomically.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime
import hashlib
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import socket
import struct
import threading
import time
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from backend.db import get_db, data_dir, now, uid, settings, ROOT
from backend.knowledge import tokenize, make_chunks, _pdf_pages, _docx_pages, MAX_EXTRACTED, MAX_CHUNKS
from backend.knowledge_fetch import FetchError, FetchCancelled, FetchResult, HostPacer, load_limits, normalize_url, validate_scope, fetch_https, robots_policy

router = APIRouter(prefix="/api/knowledge")
LIMITS = load_limits(Path(os.environ.get("RIWAQ_KNOWLEDGE_REGISTRY", str(ROOT / "knowledge" / "sources.json"))))
BUDGET_BYTES = LIMITS["download_bytes"]
MAX_DOCUMENTS = LIMITS["max_documents"]
MAX_SOURCES = LIMITS["max_sources"]
MAX_FILE_BYTES = LIMITS["file_bytes"]
MAX_JOB_SECONDS = LIMITS["run_seconds"]
PUBLIC_PACER = HostPacer()
BUSY = set()
TASKS = {}
STOPS = {}
OLLAMA = "http://127.0.0.1:11434"


class LicenseSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = Field(default="", max_length=300)
    url: str = Field(default="", max_length=4096)
    storage_allowed: StrictBool = False
    redistribution_allowed: StrictBool = False
    training_allowed: Literal[False] = False
    attribution: str = Field(default="", max_length=8000)
    reviewed_at: str | None = Field(default="", max_length=64)
    evidence: str = Field(default="", max_length=8000)
    sha256: str = Field(default="", max_length=64)


class UrlSpec(BaseModel):
    url: str = Field(max_length=4096)
    title: str = Field(default="", max_length=400)
    language: str = Field(default="", max_length=30)
    version: str = Field(default="", max_length=200)
    original_url: str = Field(default="", max_length=4096)
    published_at: str | None = Field(default=None, max_length=64)
    modified_at: str | None = Field(default=None, max_length=64)


class ChunkSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_characters: int = Field(default=1400, ge=400, le=4000)
    max_lines: int = Field(default=24, ge=8, le=80)
    overlap_lines: int = Field(default=2, ge=0, le=10)
    respect_headings: StrictBool = True


class PublisherSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=300)
    url:str=Field(max_length=4096)
    kind:Literal['project','government','community','organization','unknown']='unknown'


class TrustSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    level:Literal['primary_documentation','government_reference','community_reference','unreviewed']='unreviewed'
    scope:str=Field(default='',max_length=2000)
    evidence_urls:list[str]=Field(default_factory=list,max_length=10)
    reviewed_at:str|None=Field(default=None,max_length=64)
    limitations:str=Field(default='',max_length=2000)


class RightsReviewSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status:Literal['reviewed_scoped','requires_material_review','access_blocked']='requires_material_review'
    evidence_urls:list[str]=Field(default_factory=list,max_length=10)
    reviewed_at:str|None=Field(default=None,max_length=64)
    scope:str=Field(default='',max_length=2000)
    limitations:str=Field(default='',max_length=2000)


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(max_length=4096)
    allowed_hosts: list[str] = Field(default_factory=list, max_length=20)
    allowed_prefixes: list[str] = Field(default_factory=list, max_length=50)
    urls: list[UrlSpec | str] = Field(default_factory=list, max_length=100)
    status: Literal["candidate", "verifying", "allowed", "link_only", "suspended"] = "candidate"
    rights_confirmed: StrictBool = False
    license: LicenseSpec = Field(default_factory=LicenseSpec)
    pack: str = Field(default="custom", max_length=100)
    domain: str = Field(default="general", max_length=100)
    language: str = Field(default="en", max_length=30)
    content_type: str = Field(default="text", max_length=80)
    access_method: str = Field(default="HTTPS", max_length=200)
    version: str = Field(default="", max_length=200)
    owner: str = Field(default="", max_length=300)
    update_policy: dict = Field(default_factory=lambda: {"interval_days": 30})
    fetch_limits: dict = Field(default_factory=lambda: {"delay_seconds": 1, "max_documents": 30})
    priority: int = Field(default=50, ge=0, le=1000)
    training_allowed: Literal[False] = False
    chunking: ChunkSpec = Field(default_factory=ChunkSpec)
    publisher:PublisherSpec|None=None
    trust:TrustSpec|None=None
    rights_review:RightsReviewSpec|None=None


def source_validate(value):
    try:
        spec = SourceSpec.model_validate(value).model_dump()
        spec["base_url"] = normalize_url(spec["base_url"])
        if spec.get('publisher'):
            normalize_url(spec['publisher']['url'])
        for key in ('trust','rights_review'):
            review=spec.get(key)
            if review:
                for url in review['evidence_urls']: normalize_url(url)
                if review.get('reviewed_at'): datetime.fromisoformat(review['reviewed_at'].replace('Z','+00:00'))
        for host in spec["allowed_hosts"]:
            if normalize_url("https://" + host + "/").split("/")[2] != host.lower():
                raise ValueError("Allowed hosts must be plain canonical hostnames")
        if not spec["allowed_prefixes"] and value.get("allowed_paths"):
            spec["allowed_prefixes"] = value["allowed_paths"]
        for item in spec["urls"]:
            target = item if isinstance(item, str) else item["url"]
            normalize_url(target)
            if spec["status"] == "allowed":
                validate_scope(target, spec["allowed_hosts"], spec["allowed_prefixes"])
            if isinstance(item, dict):
                for field in ("published_at", "modified_at"):
                    if item.get(field):
                        datetime.fromisoformat(item[field].replace("Z", "+00:00"))
        if spec["license"]["url"]:
            normalize_url(spec["license"]["url"])
        if spec["status"] == "allowed":
            license = spec["license"]
            if not spec["rights_confirmed"] or not license["storage_allowed"] or not all(license.get(k) for k in ("name", "url", "attribution", "reviewed_at", "evidence")):
                raise ValueError("Allowed status requires explicit rights confirmation and license evidence, URL, attribution and review date")
            datetime.fromisoformat(license["reviewed_at"].replace("Z", "+00:00"))
            if not spec["allowed_hosts"] or not spec["allowed_prefixes"]:
                raise ValueError("Allowed sources require exact hosts and explicit path scopes")
        delay = float(spec["fetch_limits"].get("delay_seconds", 1))
        count = int(spec["fetch_limits"].get("max_documents", 30))
        if not math.isfinite(delay) or not .2 <= delay <= 120 or not 1 <= count <= 100:
            raise ValueError("Source limits must use .2–120 seconds and 1–100 documents")
        spec["fetch_limits"] = {"delay_seconds": delay, "max_documents": count}
        if spec["chunking"]["overlap_lines"] >= spec["chunking"]["max_lines"] // 2:
            raise ValueError("Chunk overlap must be less than half the line limit")
        spec["id"] = spec["id"] or uid()
        return spec
    except (ValueError, TypeError, FetchError) as exc:
        raise HTTPException(422, str(exc)[:1000]) from exc


def seed_path():
    return Path(os.environ.get("RIWAQ_KNOWLEDGE_REGISTRY", str(ROOT / "knowledge" / "sources.json")))


def init_public_knowledge(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS public_sources(id TEXT PRIMARY KEY,data TEXT NOT NULL,status TEXT NOT NULL,license_sha256 TEXT NOT NULL DEFAULT '',license_text TEXT NOT NULL DEFAULT '',license_checked_at TEXT,license_etag TEXT,license_last_modified TEXT,pending_license_sha256 TEXT,pending_license_text TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS public_documents(id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES public_sources(id) ON DELETE CASCADE,requested_url TEXT NOT NULL,url TEXT NOT NULL,title TEXT NOT NULL,current_version_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(source_id,requested_url));
    CREATE TABLE IF NOT EXISTS public_versions(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES public_documents(id) ON DELETE CASCADE,revision INTEGER NOT NULL,version TEXT NOT NULL,sha256 TEXT NOT NULL,original BLOB NOT NULL,extracted_text TEXT NOT NULL,byte_size INTEGER NOT NULL,etag TEXT,last_modified TEXT,content_type TEXT NOT NULL,license TEXT NOT NULL,license_text TEXT NOT NULL,language TEXT NOT NULL,fetched_at TEXT NOT NULL,UNIQUE(document_id,revision));
    CREATE TABLE IF NOT EXISTS public_chunks(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES public_documents(id) ON DELETE CASCADE,version_id TEXT NOT NULL REFERENCES public_versions(id) ON DELETE CASCADE,source_id TEXT NOT NULL REFERENCES public_sources(id) ON DELETE CASCADE,page INTEGER,line_start INTEGER NOT NULL,line_end INTEGER NOT NULL,text TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS public_chunks_current ON public_chunks(version_id,source_id);
    CREATE TABLE IF NOT EXISTS public_embeddings(chunk_id TEXT NOT NULL REFERENCES public_chunks(id) ON DELETE CASCADE,model TEXT NOT NULL,digest TEXT NOT NULL,dimensions INTEGER NOT NULL,vector BLOB NOT NULL,PRIMARY KEY(chunk_id,model,digest,dimensions));
    CREATE TABLE IF NOT EXISTS public_jobs(id TEXT PRIMARY KEY,status TEXT NOT NULL,source_ids TEXT NOT NULL,max_documents INTEGER NOT NULL,started_at TEXT,updated_at TEXT NOT NULL,elapsed_seconds REAL NOT NULL DEFAULT 0,downloaded_bytes INTEGER NOT NULL DEFAULT 0,error TEXT,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS public_job_items(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES public_jobs(id) ON DELETE CASCADE,source_id TEXT NOT NULL,url TEXT NOT NULL,metadata TEXT NOT NULL,status TEXT NOT NULL,error TEXT,document_id TEXT,updated_at TEXT NOT NULL,UNIQUE(job_id,source_id,url));
    CREATE TABLE IF NOT EXISTS public_download_ledger(id TEXT PRIMARY KEY,name TEXT NOT NULL,bytes INTEGER NOT NULL,kind TEXT NOT NULL,job_id TEXT,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS public_source_tombstones(id TEXT PRIMARY KEY,deleted_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS public_chunk_metadata(chunk_id TEXT PRIMARY KEY REFERENCES public_chunks(id) ON DELETE CASCADE,heading TEXT);
    CREATE TABLE IF NOT EXISTS public_version_indexes(version_id TEXT PRIMARY KEY REFERENCES public_versions(id) ON DELETE CASCADE,state TEXT NOT NULL,base_version_id TEXT,source_fingerprint TEXT NOT NULL,model TEXT,digest TEXT,dimensions INTEGER,error TEXT,source_url TEXT NOT NULL,title TEXT NOT NULL);
    """)
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(public_versions)")}
    for column, declaration in (("published_at", "TEXT"), ("modified_at", "TEXT"), ("chunking", "TEXT NOT NULL DEFAULT '{\"algorithm\":\"lines-v1\",\"max_characters\":1400,\"max_lines\":24,\"overlap_lines\":0,\"respect_headings\":false}'")):
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE public_versions ADD COLUMN {column} {declaration}")
    job_columns = {row[1] for row in conn.execute('PRAGMA table_info(public_jobs)')}
    for column,declaration in (('run_seconds',f'INTEGER NOT NULL DEFAULT {MAX_JOB_SECONDS}'),('max_bytes','INTEGER'),('max_requests','INTEGER'),('request_count','INTEGER NOT NULL DEFAULT 0')):
        if column not in job_columns:
            conn.execute(f'ALTER TABLE public_jobs ADD COLUMN {column} {declaration}')
    # Startup preserves pending items; explicit resume is required, never automatic network.
    conn.execute("UPDATE public_jobs SET status='paused',error='Service restarted; explicit resume required' WHERE status IN ('running','queued')")
    conn.execute("UPDATE public_job_items SET status='pending' WHERE status IN ('fetching','indexing')")
    # Existing snapshots remain explicitly lexical-ready; a complete namespace is
    # recognized below without reprocessing originals or changing chunk IDs.
    conn.execute("INSERT OR IGNORE INTO public_version_indexes(version_id,state,source_fingerprint,source_url,title) SELECT v.id,'lexical_ready','',d.url,d.title FROM public_versions v JOIN public_documents d ON d.id=v.document_id")
    for version in conn.execute("SELECT version_id FROM public_version_indexes WHERE source_fingerprint='' AND state='lexical_ready'").fetchall():
        full = conn.execute("SELECT e.model,e.digest,e.dimensions FROM public_embeddings e JOIN public_chunks c ON c.id=e.chunk_id WHERE c.version_id=? GROUP BY e.model,e.digest,e.dimensions HAVING COUNT(*)=(SELECT COUNT(*) FROM public_chunks WHERE version_id=?) LIMIT 1", (version[0],version[0])).fetchone()
        if full:
            conn.execute("UPDATE public_version_indexes SET state='hybrid_ready',model=?,digest=?,dimensions=? WHERE version_id=?", (*full,version[0]))
    path = seed_path()
    if path.is_file() and path.stat().st_size <= 2 * 1024 * 1024:
        document = json.loads(path.read_text(encoding="utf-8"))
        for source in document.get("sources", []):
            spec = source_validate(source)
            existing = conn.execute("SELECT data FROM public_sources WHERE id=?", (spec["id"],)).fetchone()
            if not existing and not conn.execute("SELECT 1 FROM public_source_tombstones WHERE id=?", (spec["id"],)).fetchone():
                stamp = now()
                conn.execute("INSERT INTO public_sources(id,data,status,created_at,updated_at) VALUES(?,?,?,?,?)", (spec["id"], json.dumps(spec, ensure_ascii=False), spec["status"], stamp, stamp))
            elif existing:
                old=json.loads(existing['data'])
                # Descriptive metadata may be filled for unchanged seed identity.
                # Never overwrite operator edits, rights, dates, scopes or status.
                if old.get('base_url')==spec['base_url'] and old.get('license',{}).get('url')==spec['license']['url'] and old.get('allowed_hosts')==spec['allowed_hosts']:
                    additions={key:spec[key] for key in ('publisher','trust','rights_review') if key not in old and spec.get(key)}
                    if additions:
                        conn.execute('UPDATE public_sources SET data=? WHERE id=?',(json.dumps(old|additions,ensure_ascii=False),spec['id']))
        for row in document.get("external_downloads", []):
            if type(row.get("bytes")) is not int or row["bytes"] < 0:
                raise ValueError("External download ledger bytes must be nonnegative integers")
            # A seed update may increase accounting; it can never erase previous downloads.
            conn.execute("INSERT INTO public_download_ledger VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET bytes=MAX(bytes,excluded.bytes),name=excluded.name", ("external:" + str(row["id"]), str(row.get("name", row["id"])), row["bytes"], "external", None, now()))


def source_row(conn, source_id):
    row = conn.execute("SELECT * FROM public_sources WHERE id=?", (source_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Public source not found")
    return row


def source_dict(row):
    data = json.loads(row["data"])
    data.setdefault("chunking", ChunkSpec().model_dump())
    return data | {"status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"], "license_sha256": row["license_sha256"], "license_checked_at": row["license_checked_at"], "license_change_pending": bool(row["pending_license_sha256"]), "pending_license_sha256": row["pending_license_sha256"]}


def remaining_budget(job_id=None):
    with get_db() as conn:
        spent = conn.execute("SELECT COALESCE(SUM(bytes),0) FROM public_download_ledger").fetchone()[0]
        remaining=max(0,BUDGET_BYTES-spent)
        job=conn.execute('SELECT max_bytes,downloaded_bytes FROM public_jobs WHERE id=?',(job_id,)).fetchone() if job_id else None
        return min(remaining,max(0,job['max_bytes']-job['downloaded_bytes'])) if job and job['max_bytes'] is not None else remaining


def charge_bytes(job_id, amount):
    if amount <= 0:
        return
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        total = conn.execute("SELECT COALESCE(SUM(bytes),0) FROM public_download_ledger").fetchone()[0]
        if total + amount > BUDGET_BYTES:
            raise FetchError("Configured cumulative download budget exhausted")
        job = conn.execute('SELECT downloaded_bytes,max_bytes FROM public_jobs WHERE id=?',(job_id,)).fetchone()
        if job and job['max_bytes'] is not None and job['downloaded_bytes']+amount > job['max_bytes']:
            raise FetchError('This job download byte limit is exhausted')
        conn.execute("INSERT INTO public_download_ledger VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET bytes=bytes+excluded.bytes", ("job:" + job_id, "Public source/robots/license downloads", amount, "ingestion", job_id, now()))
        conn.execute("UPDATE public_jobs SET downloaded_bytes=downloaded_bytes+?,updated_at=? WHERE id=?", (amount, now(), job_id))


class PlanBody(BaseModel):
    source_ids: list[str] = Field(min_length=1, max_length=MAX_SOURCES)
    max_documents: int = Field(default=min(10, MAX_DOCUMENTS), ge=1, le=MAX_DOCUMENTS)


class StartBody(PlanBody):
    allow_network: StrictBool = False


class ResumeBody(BaseModel):
    allow_network: StrictBool = False


def make_plan(conn, body):
    selected, documents, seen = [], [], set()
    for source_id in dict.fromkeys(body.source_ids):
        source = source_dict(source_row(conn, source_id))
        selected.append(source)
        for value in source["urls"][:source["fetch_limits"]["max_documents"]]:
            item = {"url": value} if isinstance(value, str) else value
            url = normalize_url(item["url"])
            if (source_id, url) in seen:
                continue
            seen.add((source_id, url))
            documents.append({**item, "url": url, "source_id": source_id, "state": "eligible" if source["status"] == "allowed" else "blocked", "reason": "" if source["status"] == "allowed" else "Source rights are not approved for storage"})
            if len(documents) >= body.max_documents:
                break
        if len(documents) >= body.max_documents:
            break
    spent = conn.execute("SELECT COALESCE(SUM(bytes),0) FROM public_download_ledger").fetchone()[0]
    return {"sources": selected, "documents": documents, "document_count": len(documents), "budget_remaining_bytes": max(0, BUDGET_BYTES - spent), "notice": "Local preview only. URLs are explicit registry entries; no crawler or network request ran. robots.txt and license bytes are verified during the authorized job."}


def job_dict(conn, row, detail=False):
    result = dict(row)
    result["source_ids"] = json.loads(row["source_ids"])
    items = []
    for item in conn.execute("SELECT id,source_id,url,status,error,document_id,updated_at,metadata FROM public_job_items WHERE job_id=? ORDER BY rowid", (row["id"],)):
        value = dict(item); metadata = json.loads(value.pop("metadata"))
        value.update(http_status=metadata.get("http_status"), fetched_count=metadata.get("fetched_count", 0), fetched_at=metadata.get("fetched_at"),version_id=metadata.get('version_id'))
        if value['version_id']:
            index=conn.execute('SELECT state,error FROM public_version_indexes WHERE version_id=?',(value['version_id'],)).fetchone()
            value['index_state']=index['state'] if index else 'deleted'
        items.append(value)
    result.update(discovered_links=len(items), fetched_files=sum(i["fetched_count"] for i in items), accepted_documents=sum(i["status"] == "accepted" for i in items), completed_documents=sum(i["status"] in {"accepted", "unchanged"} for i in items), failed_sources=len({i["source_id"] for i in items if i["status"] == "failed"}), indexed_chunks=conn.execute("SELECT COUNT(*) FROM public_chunks c JOIN public_documents d ON d.current_version_id=c.version_id WHERE d.id IN (SELECT document_id FROM public_job_items WHERE job_id=?)", (row["id"],)).fetchone()[0])
    if detail:
        result["items"] = items
        result["errors"] = [i for i in items if i["error"]]
    return result


@router.get("/sources")
def list_sources():
    with get_db() as conn:
        return [source_dict(row) for row in conn.execute("SELECT * FROM public_sources ORDER BY created_at,id")]


@router.get("/packs")
def list_packs():
    grouped = {}
    for source in list_sources():
        pack = source.get("pack", "custom")
        item = grouped.setdefault(pack, {"id": pack, "name": pack.replace("-", " "), "description": "Sources from the editable local registry; each source retains its own rights status", "source_ids": [], "status": "candidate"})
        item["source_ids"].append(source["id"])
        if source["status"] == "allowed":
            item["status"] = "available"
    return list(grouped.values())


@router.post("/sources", status_code=201)
async def add_source(body: SourceSpec):
    value = source_validate(body.model_dump())
    if len(json.dumps(value)) > 256000:
        raise HTTPException(413, "Source metadata is too large")
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM public_sources WHERE id=?", (value["id"],)).fetchone():
            raise HTTPException(409, "Source id already exists")
        stamp = now()
        conn.execute("DELETE FROM public_source_tombstones WHERE id=?", (value["id"],))
        conn.execute("INSERT INTO public_sources(id,data,status,created_at,updated_at) VALUES(?,?,?,?,?)", (value["id"], json.dumps(value, ensure_ascii=False), value["status"], stamp, stamp))
        return source_dict(source_row(conn, value["id"]))


@router.patch("/sources/{source_id}")
async def edit_source(source_id: str, body: dict):
    if BUSY:
        raise HTTPException(409, "Pause ingestion before editing source rights or scope")
    if len(json.dumps(body)) > 256000:
        raise HTTPException(413, "Source metadata is too large")
    with get_db() as conn:
        old = source_row(conn, source_id)
        data = json.loads(old["data"]) | body | {"id": source_id}
        rights_fields = {"allowed_hosts", "allowed_prefixes", "allowed_paths", "license", "base_url"}
        if "urls" in body:
            def targets(items):
                return [item if isinstance(item, str) else item.get("url") if isinstance(item, dict) else None for item in items] if isinstance(items, list) else None
            if targets(body["urls"]) != targets(json.loads(old["data"]).get("urls", [])):
                rights_fields.add("urls")
        if rights_fields.intersection(body) and old["status"] == "allowed" and body.get("rights_confirmed") is not True:
            data.update(status="verifying", rights_confirmed=False)
        value = source_validate(data)
        if value["status"] == "allowed" and old["pending_license_sha256"]:
            if body.get("rights_confirmed") is not True or "license" not in body or value["license"].get("sha256") != old["pending_license_sha256"]:
                raise HTTPException(422, "Review the changed license and explicitly approve its pending SHA-256")
            conn.execute("UPDATE public_sources SET license_sha256=pending_license_sha256,license_text=pending_license_text,pending_license_sha256=NULL,pending_license_text=NULL WHERE id=?", (source_id,))
        conn.execute("UPDATE public_sources SET data=?,status=?,updated_at=? WHERE id=?", (json.dumps(value, ensure_ascii=False), value["status"], now(), source_id))
        return source_dict(source_row(conn, source_id))


@router.get("/sources/{source_id}/rights")
def source_rights(source_id: str):
    with get_db() as conn:
        row = source_row(conn, source_id)
        return {"source": source_dict(row), "license_text": row["license_text"], "pending_license_sha256": row["pending_license_sha256"], "pending_license_text": row["pending_license_text"], "notice": "License evidence for explicit human review; no automatic rights approval"}


@router.delete("/sources/{source_id}")
async def delete_source(source_id: str, confirm: bool = False):
    if not confirm:
        raise HTTPException(403, "Explicit deletion confirmation is required")
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        source_row(conn, source_id)
        invalidate_items(conn, source_id)
        conn.execute("INSERT OR REPLACE INTO public_source_tombstones VALUES(?,?)", (source_id, now()))
        conn.execute("DELETE FROM public_sources WHERE id=?", (source_id,))
    return {"deleted": True, "budget_refunded": False}


@router.post("/preview")
def preview(body: PlanBody):
    with get_db() as conn:
        return make_plan(conn, body)


@router.get("/jobs")
def jobs():
    with get_db() as conn:
        return [job_dict(conn, row) for row in conn.execute("SELECT * FROM public_jobs ORDER BY created_at DESC LIMIT 100")]


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM public_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Ingestion job not found")
        return job_dict(conn, row, True)


def require_network(allow):
    if allow is not True or settings()["mode"] != "hybrid":
        raise HTTPException(403, "Public downloads require hybrid mode and explicit allow_network=true")
    if BUSY:
        raise HTTPException(409, "Only one sequential public ingestion job may run")
    if remaining_budget() <= 0:
        raise HTTPException(413, "The cumulative download budget is exhausted")


class JobControl(threading.Event):
    def __init__(self, seconds=MAX_JOB_SECONDS, job_id=None):
        super().__init__()
        self.deadline = time.monotonic() + seconds
        self.pacer = PUBLIC_PACER
        self.job_id = job_id
        self.connections, self.guard = set(), threading.Lock()
        self.timer = threading.Timer(seconds, self.set)
        self.timer.daemon = True
        self.timer.start()
    def attach(self, connection):
        with self.guard:
            self.connections.add(connection)
    def is_set(self):
        return super().is_set() or time.monotonic()>=self.deadline
    def detach(self, connection):
        with self.guard:
            self.connections.discard(connection)
    def set(self):
        super().set()
        with self.guard:
            for connection in list(self.connections):
                try:
                    if connection.sock is not None:
                        connection.sock.shutdown(socket.SHUT_RDWR)
                    connection.close()
                except OSError:
                    pass
    def finish(self):
        self.timer.cancel()
    def count_request(self):
        if self.is_set():
            raise FetchCancelled('Download cancelled before request')
        if self.job_id:
            with get_db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                row = conn.execute('SELECT request_count,max_requests FROM public_jobs WHERE id=?',(self.job_id,)).fetchone()
                if not row or (row['max_requests'] is not None and row['request_count']>=row['max_requests']):
                    raise FetchError('This job HTTP request limit is exhausted')
                conn.execute('UPDATE public_jobs SET request_count=request_count+1 WHERE id=?',(self.job_id,))


def launch_job(job_id):
    with get_db() as conn:
        seconds = conn.execute('SELECT run_seconds FROM public_jobs WHERE id=?',(job_id,)).fetchone()[0]
    stop = JobControl(seconds,job_id)
    STOPS[job_id] = stop
    BUSY.add(job_id)
    TASKS[job_id] = asyncio.create_task(run_job(job_id, stop))


@router.post("/jobs", status_code=202)
async def start_job(body: StartBody):
    require_network(body.allow_network)
    with get_db() as conn:
        plan = make_plan(conn, body)
        if not plan["documents"] or any(item["state"] != "eligible" for item in plan["documents"]):
            raise HTTPException(422, "Choose allowed sources with explicit document URLs")
        existing = conn.execute("SELECT COUNT(*) FROM public_documents").fetchone()[0]
        new_urls = sum(not conn.execute("SELECT 1 FROM public_documents WHERE source_id=? AND requested_url=?", (item["source_id"], item["url"])).fetchone() for item in plan["documents"])
        if existing + new_urls > MAX_DOCUMENTS:
            raise HTTPException(413, f"Public corpus limit is {MAX_DOCUMENTS} current documents")
        job_id, stamp = uid(), now()
        conn.execute("INSERT INTO public_jobs(id,status,source_ids,max_documents,updated_at,created_at) VALUES(?,?,?,?,?,?)", (job_id, "queued", json.dumps(body.source_ids), body.max_documents, stamp, stamp))
        for item in plan["documents"]:
            conn.execute("INSERT INTO public_job_items VALUES(?,?,?,?,?,?,?,?,?)", (uid(), job_id, item["source_id"], item["url"], json.dumps(item, ensure_ascii=False), "pending", None, None, stamp))
    launch_job(job_id)
    return get_job(job_id)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    current = get_job(job_id)
    if job_id in STOPS:
        STOPS[job_id].set()
    else:
        with get_db() as conn:
            if current["status"] not in {"completed"}:
                conn.execute("UPDATE public_jobs SET status='cancelled',updated_at=? WHERE id=?", (now(), job_id))
    return {"cancel_requested": True, "job": get_job(job_id)}


async def start_selected_job(items, allow_network=True, seconds=120, max_bytes=4*1024**2, max_requests=12):
    """Bounded refresh of at most three exact, current registry URLs; no crawler."""
    require_network(allow_network)
    if not isinstance(items,list) or not 1<=len(items)<=3:
        raise HTTPException(422,'Choose one to three registered URLs')
    for value,cap in ((seconds,MAX_JOB_SECONDS),(max_bytes,BUDGET_BYTES),(max_requests,100)):
        if type(value) is not int or not 1<=value<=cap:
            raise HTTPException(422,'Invalid bounded refresh limit')
    with get_db() as conn:
        selected,seen=[],set()
        for item in items:
            if not isinstance(item,dict) or set(item)!={'source_id','url'} or not all(isinstance(v,str) for v in item.values()):
                raise HTTPException(422,'Each selection requires only source_id and url')
            source=source_dict(source_row(conn,item['source_id']))
            if source['status']!='allowed':
                raise HTTPException(403,'Source is not approved for public storage')
            try:
                url=normalize_url(item['url'])
            except FetchError as exc:
                raise HTTPException(422,str(exc)) from exc
            entry=next((value for value in source['urls'] if normalize_url(value if isinstance(value,str) else value['url'])==url),None)
            if entry is None:
                raise HTTPException(422,'Refresh URL is not an exact registered source entry')
            if (source['id'],url) in seen:
                continue
            seen.add((source['id'],url))
            selected.append(({'url':entry} if isinstance(entry,str) else dict(entry))|{'url':url,'source_id':source['id']})
        new=sum(not conn.execute('SELECT 1 FROM public_documents WHERE source_id=? AND requested_url=?',(v['source_id'],v['url'])).fetchone() for v in selected)
        if conn.execute('SELECT COUNT(*) FROM public_documents').fetchone()[0]+new>MAX_DOCUMENTS:
            raise HTTPException(413,'Public corpus document limit reached')
        job_id,stamp=uid(),now()
        conn.execute('INSERT INTO public_jobs(id,status,source_ids,max_documents,updated_at,created_at,run_seconds,max_bytes,max_requests) VALUES(?,?,?,?,?,?,?,?,?)',(job_id,'queued',json.dumps(list(dict.fromkeys(v['source_id'] for v in selected))),len(selected),stamp,stamp,seconds,max_bytes,max_requests))
        for item in selected:
            conn.execute('INSERT INTO public_job_items VALUES(?,?,?,?,?,?,?,?,?)',(uid(),job_id,item['source_id'],item['url'],json.dumps(item,ensure_ascii=False),'pending',None,None,stamp))
    launch_job(job_id)
    return get_job(job_id)


@router.post("/jobs/{job_id}/resume", status_code=202)
async def resume_job(job_id: str, body: ResumeBody):
    require_network(body.allow_network)
    current = get_job(job_id)
    if current["status"] == "completed":
        return current
    with get_db() as conn:
        conn.execute("UPDATE public_job_items SET status='pending',error=NULL WHERE job_id=? AND (status IN ('failed','fetching','indexing') OR error LIKE 'Original and lexical chunks committed; local embeddings incomplete:%')", (job_id,))
        conn.execute("UPDATE public_jobs SET status='queued',error=NULL,updated_at=? WHERE id=?", (now(), job_id))
    launch_job(job_id)
    return get_job(job_id)


async def shutdown_public_knowledge():
    for stop in STOPS.values():
        stop.set()
    if TASKS:
        await asyncio.gather(*list(TASKS.values()), return_exceptions=True)


from backend.isolated_parsers import LegacyPublicHTML, PublicHTML




def extract_public(body, source, url, content_type, *, extractor=None):
    """Pure parser for controlled unit fixtures; retained/untrusted bytes use isolation."""
    from backend.isolated_parsers import extract_public as shared_extract, ParserError
    try: return shared_extract(body, source, url, content_type, extractor=extractor)
    except ParserError as exc: raise FetchError(exc.detail) from exc


def extract_public_isolated(body, source, url, content_type, *, extractor=None):
    """Offline CLI replay shares the production parser boundary; never fetches."""
    from backend.research_extract import extract_bounded
    return extract_bounded(body, source, url, content_type, extractor=extractor)


def chunking_policy(source, content_type=""):
    configured = dict(source.get("chunking", {}))
    configured.pop("algorithm", None)
    configured.pop("extractor", None)
    policy = {"algorithm": "heading-lines-v2", **ChunkSpec.model_validate(configured).model_dump()}
    if "html" in content_type or source.get("content_type") == "html":
        policy["extractor"] = "html-pre-v2"
    return policy


def chunk_public(pages, source, url, content_type=""):
    """Preserve exact extracted line ranges, real headings, and bounded overlap."""
    if source.get("chunking", {}).get("algorithm") == "lines-v1":
        return [dict(chunk, heading=None) for chunk in make_chunks(pages)]
    policy = chunking_policy(source, content_type)
    suffix = Path(urlsplit(url).path).suffix.lower()
    heading_format = suffix in {".md", ".mdx", ".markdown", ".html", ".htm"} or "html" in content_type or (not suffix and source.get("content_type") in {"markdown", "html"})
    recognize_headings = policy["respect_headings"] and heading_format
    output = []
    for page, text in pages:
        group, heading, fence = [], None, None
        def emit(carry=True):
            nonlocal group
            if group and any(part.strip() for _, part in group):
                output.append({"page": page, "line_start": group[0][0], "line_end": group[-1][0], "text": "\n".join(part for _, part in group), "heading": heading})
                if len(output) > MAX_CHUNKS:
                    raise FetchError("Document exceeds the public chunk count limit")
            overlap = policy["overlap_lines"] if carry else 0
            group = group[-overlap:] if overlap else []
        for number, line in enumerate(text.splitlines(), 1):
            boundary = re.match(r"^\s{0,3}(`{3,}|~{3,})", line) if recognize_headings else None
            is_heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line) if recognize_headings and fence is None else None
            if is_heading:
                emit(carry=False)
                heading = is_heading[2].strip()
            if boundary:
                marker = boundary[1]
                if fence is None:
                    fence = marker
                elif marker[0] == fence[0] and len(marker) >= len(fence):
                    fence = None
            for index in range(0, max(1, len(line)), policy["max_characters"]):
                fragment = line[index:index + policy["max_characters"]]
                if group and (len(group) >= policy["max_lines"] or sum(len(part) + 1 for _, part in group) + len(fragment) > policy["max_characters"]):
                    emit()
                    while group and (len(group) >= policy["max_lines"] or sum(len(part) + 1 for _, part in group) + len(fragment) > policy["max_characters"]):
                        group.pop(0)
                group.append((number, fragment))
        emit(carry=False)
    return output


def source_fingerprint(source):
    keys = ('id','status','rights_confirmed','allowed_hosts','allowed_prefixes','urls','license','content_type','version','language','chunking','domain')
    value = {key:source.get(key) for key in keys}
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf-8')).hexdigest()


def source_allows(source,url):
    if source.get('status')!='allowed' or source.get('rights_confirmed') is not True or source.get('license',{}).get('storage_allowed') is not True:
        return False
    try:
        validate_scope(url,source['allowed_hosts'],source['allowed_prefixes'])
        return True
    except (FetchError,KeyError,TypeError):
        return False


def invalidate_items(conn, source_id, requested_url=None):
    """Deletion revokes old attempts; only a new explicit job can reimport."""
    matches = conn.execute("SELECT DISTINCT job_id FROM public_job_items WHERE source_id=? AND (? IS NULL OR url=?) AND status IN ('pending','fetching','indexing','failed')",(source_id,requested_url,requested_url)).fetchall()
    conn.execute("UPDATE public_job_items SET status='deleted',error='Source/document explicitly deleted',updated_at=? WHERE source_id=? AND (? IS NULL OR url=?)",(now(),source_id,requested_url,requested_url))
    for row in matches:
        if row[0] in STOPS:
            STOPS[row[0]].set()


def index_authority(conn, document_id, version_id, stop=None, item_id=None):
    if stop is not None and stop.is_set():
        raise FetchCancelled('Indexing cancelled before activation')
    row = conn.execute("SELECT i.*,d.current_version_id,d.source_id,s.data,s.status FROM public_version_indexes i JOIN public_versions v ON v.id=i.version_id JOIN public_documents d ON d.id=v.document_id JOIN public_sources s ON s.id=d.source_id WHERE d.id=? AND v.id=?",(document_id,version_id)).fetchone()
    if not row or row['status'] != 'allowed':
        raise FetchCancelled('Document or source permission was revoked')
    source = json.loads(row['data'])
    source['status'] = row['status']
    source.setdefault('chunking',ChunkSpec().model_dump())
    if not source.get('rights_confirmed') or not source.get('license',{}).get('storage_allowed'):
        raise FetchCancelled('Storage permission was revoked')
    try:
        validate_scope(row['source_url'],source['allowed_hosts'],source['allowed_prefixes'])
    except (FetchError,KeyError) as exc:
        raise FetchCancelled('Stored document URL is outside the current permitted source scope') from exc
    if row['source_fingerprint'] and row['source_fingerprint'] != source_fingerprint(source):
        raise FetchCancelled('Source configuration changed during indexing')
    if row['current_version_id'] not in (version_id,row['base_version_id']):
        raise FetchCancelled('A different document version became current')
    if item_id:
        item = conn.execute("SELECT status,document_id FROM public_job_items WHERE id=?",(item_id,)).fetchone()
        if not item or item['status'] != 'indexing' or item['document_id'] != document_id:
            raise FetchCancelled('Ingestion item is no longer authorized')
    return row


def activate_version(conn, document_id, version_id, info=None, stop=None, item_id=None):
    row = index_authority(conn,document_id,version_id,stop,item_id)
    count = conn.execute('SELECT COUNT(*) FROM public_chunks WHERE version_id=?',(version_id,)).fetchone()[0]
    if not count:
        raise FetchError('A document without extracted chunks cannot be activated')
    state, model, digest, dimensions = 'lexical_ready',None,None,None
    if info and info.get('configured'):
        if not info.get('available'):
            raise FetchError('Configured local embedding model is unavailable; previous version remains current')
        full = conn.execute("SELECT e.dimensions,COUNT(*) FROM public_embeddings e JOIN public_chunks c ON c.id=e.chunk_id WHERE c.version_id=? AND e.model=? AND e.digest=? GROUP BY e.dimensions HAVING COUNT(*)=?",(version_id,info['model'],info['digest'],count)).fetchall()
        if len(full) != 1:
            raise FetchError('Complete, dimension-consistent embeddings are required before activation')
        state,model,digest,dimensions = 'hybrid_ready',info['model'],info['digest'],full[0][0]
    conn.execute('UPDATE public_version_indexes SET state=?,model=?,digest=?,dimensions=?,error=NULL WHERE version_id=?',(state,model,digest,dimensions,version_id))
    conn.execute('UPDATE public_documents SET current_version_id=?,url=?,title=?,updated_at=? WHERE id=?',(version_id,row['source_url'],row['title'],now(),document_id))
    return state


def store_document(conn, source, item, fetched, pages, license_text, *, fetched_at=None, staging=False):
    requested = normalize_url(item["url"])
    digest = hashlib.sha256(fetched.body).hexdigest()
    row = conn.execute("SELECT d.*,v.sha256,v.version,v.language,v.chunking,v.published_at,v.modified_at,v.license,i.source_fingerprint FROM public_documents d LEFT JOIN public_versions v ON v.id=d.current_version_id LEFT JOIN public_version_indexes i ON i.version_id=v.id WHERE d.source_id=? AND d.requested_url=?", (source["id"], requested)).fetchone()
    version = item.get("version") or source.get("version", "")
    language = item.get("language") or source.get("language", "en")
    policy = chunking_policy(source, fetched.headers.get("content-type", ""))
    live = source_dict(source_row(conn, source['id']))
    if live['status'] != 'allowed' or not live.get('rights_confirmed') or not live['license']['storage_allowed']:
        raise FetchCancelled('Source rights are no longer available')
    fingerprint = source_fingerprint(live)
    title = item.get("title") or source["name"] + " — " + (urlsplit(requested).path.rsplit("/", 1)[-1] or "document")
    if row and row["sha256"] == digest and row["version"] == version and row["language"] == language and json.loads(row["chunking"]) == policy and row["published_at"] == item.get("published_at") and row["modified_at"] == item.get("modified_at") and json.loads(row['license'])==source['license'] and row['title']==title and row['url']==fetched.url and (not row['source_fingerprint'] or row['source_fingerprint']==fingerprint):
        conn.execute("UPDATE public_versions SET etag=COALESCE(?,etag),last_modified=COALESCE(?,last_modified) WHERE id=?", (fetched.headers.get("etag"), fetched.headers.get("last-modified"), row["current_version_id"]))
        return row["id"], False
    if not row and conn.execute("SELECT COUNT(*) FROM public_documents").fetchone()[0] >= MAX_DOCUMENTS:
        raise FetchError("Public corpus document limit reached")
    document_id = row["id"] if row else uid()
    if row and staging:
        pending = conn.execute("SELECT v.id FROM public_versions v JOIN public_version_indexes i ON i.version_id=v.id WHERE v.document_id=? AND i.state='staged' AND i.base_version_id IS ? AND i.source_fingerprint=? AND v.sha256=? AND v.version=? AND v.language=? AND v.chunking=? AND v.published_at IS ? AND v.modified_at IS ? ORDER BY v.revision DESC LIMIT 1",(document_id,row['current_version_id'],fingerprint,digest,version,language,json.dumps(policy,sort_keys=True),item.get('published_at'),item.get('modified_at'))).fetchone()
        if pending and pending[0]==conn.execute('SELECT id FROM public_versions WHERE document_id=? ORDER BY revision DESC LIMIT 1',(document_id,)).fetchone()[0]:
            conn.execute('UPDATE public_versions SET etag=COALESCE(?,etag),last_modified=COALESCE(?,last_modified) WHERE id=?',(fetched.headers.get('etag'),fetched.headers.get('last-modified'),pending[0]))
            return document_id,True
    version_id, stamp = uid(), now()
    revision = conn.execute("SELECT COALESCE(MAX(revision),0)+1 FROM public_versions WHERE document_id=?", (document_id,)).fetchone()[0]
    if not row:
        conn.execute("INSERT INTO public_documents VALUES(?,?,?,?,?,?,?,?)", (document_id, source["id"], requested, fetched.url, title, None, stamp, stamp))
    chunks = chunk_public(pages, source, fetched.url, fetched.headers.get("content-type", ""))
    extracted = "\n\n".join(text for _, text in pages)
    conn.execute("INSERT INTO public_versions(id,document_id,revision,version,sha256,original,extracted_text,byte_size,etag,last_modified,content_type,license,license_text,language,fetched_at,published_at,modified_at,chunking) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (version_id, document_id, revision, version, digest, fetched.body, extracted, len(fetched.body), fetched.headers.get("etag"), fetched.headers.get("last-modified"), fetched.headers.get("content-type", source.get("content_type", "")), json.dumps(source["license"], ensure_ascii=False), license_text, language, fetched_at or stamp, item.get("published_at"), item.get("modified_at"), json.dumps(policy, sort_keys=True)))
    for chunk in chunks:
        chunk_id = uid()
        conn.execute("INSERT INTO public_chunks VALUES(?,?,?,?,?,?,?,?)", (chunk_id, document_id, version_id, source["id"], chunk["page"], chunk["line_start"], chunk["line_end"], chunk["text"]))
        conn.execute("INSERT INTO public_chunk_metadata VALUES(?,?)", (chunk_id, chunk["heading"]))
    conn.execute('INSERT INTO public_version_indexes(version_id,state,base_version_id,source_fingerprint,source_url,title) VALUES(?,?,?,?,?,?)',(version_id,'staged',row['current_version_id'] if row else None,fingerprint,fetched.url,title))
    if not staging:
        activate_version(conn,document_id,version_id)
    return document_id, True


def verify_license(source, job_id, stop):
    with get_db() as conn:
        previous = source_row(conn, source["id"])
        conditional = {"If-None-Match": previous["license_etag"], "If-Modified-Since": previous["license_last_modified"]}
        old_hash = previous["license_sha256"] or source["license"].get("sha256", "")
    url = normalize_url(source["license"]["url"])
    host = urlsplit(url).hostname
    policy = robots_policy(url, allowed_hosts=[host], charge=lambda n: charge_bytes(job_id, n), remaining_budget=lambda: remaining_budget(job_id), cancelled=stop, max_file_bytes=MAX_FILE_BYTES)
    if not policy["allowed"]:
        raise FetchError("License URL is disallowed by robots.txt")
    fetched = fetch_https(url, allowed_hosts=[host], allowed_prefixes=[urlsplit(url).path], max_bytes=min(1024 * 1024, MAX_FILE_BYTES), conditional=conditional, charge=lambda n: charge_bytes(job_id, n), remaining_budget=lambda: remaining_budget(job_id), cancelled=stop)
    if fetched.status == 304 and previous["license_text"]:
        with get_db() as conn:
            conn.execute("UPDATE public_sources SET license_checked_at=?,license_etag=COALESCE(?,license_etag),license_last_modified=COALESCE(?,license_last_modified) WHERE id=?",(now(),fetched.headers.get('etag'),fetched.headers.get('last-modified'),source['id']))
        return previous["license_text"]
    if fetched.status != 200:
        raise FetchError(f"License evidence fetch failed (HTTP {fetched.status})")
    digest = hashlib.sha256(fetched.body).hexdigest()
    text = fetched.body.decode("utf-8", errors="replace")
    changed = bool(old_hash and digest != old_hash)
    with get_db() as conn:
        if changed:
            conn.execute("UPDATE public_sources SET status='suspended',pending_license_sha256=?,pending_license_text=?,updated_at=? WHERE id=?", (digest, text, now(), source["id"]))
        else:
            conn.execute("UPDATE public_sources SET license_sha256=?,license_text=?,license_checked_at=?,license_etag=?,license_last_modified=? WHERE id=?", (digest, text, now(), fetched.headers.get("etag"), fetched.headers.get("last-modified"), source["id"]))
    if changed:
        raise FetchError("License content changed; source suspended until an explicit rights review")
    return text


async def run_job(job_id, stop):
    start = time.monotonic()
    with get_db() as conn:
        prior = conn.execute("SELECT elapsed_seconds FROM public_jobs WHERE id=?", (job_id,)).fetchone()[0]
        conn.execute("UPDATE public_jobs SET status='running',started_at=COALESCE(started_at,?),updated_at=? WHERE id=?", (now(), now(), job_id))
        # Configure all selected hosts before the first request so two sources
        # sharing a host consistently use the stricter source delay.
        for row in conn.execute("SELECT DISTINCT s.data FROM public_sources s JOIN public_job_items i ON i.source_id=s.id WHERE i.job_id=?", (job_id,)):
            source = json.loads(row["data"])
            hosts = set(source["allowed_hosts"]) | {urlsplit(source["license"]["url"]).hostname}
            for host in hosts - {None}:
                stop.pacer.set_delay(host, source["fetch_limits"]["delay_seconds"])
    license_cache = {}
    final_status, final_error = "completed", None
    try:
        while True:
            elapsed = time.monotonic() - start
            if stop.is_set():
                final_status = "paused" if time.monotonic() >= getattr(stop, "deadline", float("inf")) else "cancelled"; break
            if elapsed >= MAX_JOB_SECONDS or remaining_budget(job_id) <= 0:
                final_status, final_error = "paused", "Job time or cumulative byte allowance reached"; break
            if settings()["mode"] != "hybrid":
                final_status, final_error = "paused", "Network permission is no longer enabled"; break
            with get_db() as conn:
                row = conn.execute("SELECT * FROM public_job_items WHERE job_id=? AND status='pending' ORDER BY rowid LIMIT 1", (job_id,)).fetchone()
                if not row:
                    break
                source = source_dict(source_row(conn, row["source_id"]))
                item = json.loads(row["metadata"])
                conn.execute("UPDATE public_job_items SET status='fetching',updated_at=? WHERE id=?", (now(), row["id"]))
            try:
                if source["status"] != "allowed":
                    raise FetchError("Source is not allowed; rights review is required")
                if source["id"] not in license_cache:
                    license_cache[source["id"]] = await asyncio.to_thread(verify_license, source, job_id, stop)
                url = validate_scope(item["url"], source["allowed_hosts"], source["allowed_prefixes"])
                policy = await asyncio.to_thread(robots_policy, url, allowed_hosts=source["allowed_hosts"], charge=lambda n: charge_bytes(job_id, n), remaining_budget=lambda: remaining_budget(job_id), cancelled=stop, max_file_bytes=MAX_FILE_BYTES)
                if not policy["allowed"]:
                    raise FetchError("Document is disallowed by robots.txt")
                if stop.is_set():
                    raise FetchCancelled("Download cancelled")
                with get_db() as conn:
                    previous = conn.execute("SELECT d.id,d.url,v.original,v.content_type,v.etag,v.last_modified,v.fetched_at FROM public_documents d JOIN public_versions v ON v.id=d.current_version_id WHERE d.source_id=? AND d.requested_url=?", (source["id"], url)).fetchone()
                conditional = {"If-None-Match": previous["etag"], "If-Modified-Since": previous["last_modified"]} if previous else None
                fetched = await asyncio.to_thread(fetch_https, url, allowed_hosts=source["allowed_hosts"], allowed_prefixes=source["allowed_prefixes"], max_bytes=MAX_FILE_BYTES, conditional=conditional, charge=lambda n: charge_bytes(job_id, n), remaining_budget=lambda: remaining_budget(job_id), cancelled=stop)
                item["http_status"] = fetched.status
                if fetched.status == 200:
                    item["fetched_count"] = item.get("fetched_count", 0) + 1
                    item["fetched_at"] = now()
                with get_db() as conn:
                    conn.execute("UPDATE public_job_items SET metadata=? WHERE id=?", (json.dumps(item, ensure_ascii=False), row["id"]))
                original_fetched_at = item.get("fetched_at")
                if fetched.status == 304 and previous:
                    # A new declared version or chunk policy still needs indexing, even
                    # when HTTP confirms the same bytes. Preserve the actual fetch date.
                    headers = {"content-type": previous["content_type"]}
                    headers.update({key: value for key, value in (("etag", previous["etag"]), ("last-modified", previous["last_modified"])) if value})
                    headers.update(fetched.headers)
                    fetched = FetchResult(previous["url"], 200, headers, previous["original"])
                    original_fetched_at = previous["fetched_at"]
                elif fetched.status != 200:
                    raise FetchError(f"Document returned HTTP {fetched.status}")
                from backend.research_extract import extract_bounded
                pages = await asyncio.to_thread(extract_bounded, fetched.body, source, fetched.url, fetched.headers.get("content-type", ""), stop)
                if stop.is_set():
                    raise FetchCancelled("Download cancelled before committing document")
                if shutil.disk_usage(data_dir()).free < len(fetched.body) * 3 + 20 * 1024 * 1024:
                    raise FetchError("Insufficient disk space to preserve the original atomically")
                with get_db() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    current_item = conn.execute('SELECT status FROM public_job_items WHERE id=?',(row['id'],)).fetchone()
                    if stop.is_set() or not current_item or current_item[0] != 'fetching' or source_fingerprint(source_dict(source_row(conn,source['id']))) != source_fingerprint(source):
                        raise FetchCancelled('Source or ingestion authority changed before staging')
                    document_id, changed = store_document(conn, source, item, fetched, pages, license_cache[source["id"]], fetched_at=original_fetched_at, staging=True)
                    version_id = conn.execute('SELECT id FROM public_versions WHERE document_id=? ORDER BY revision DESC LIMIT 1',(document_id,)).fetchone()[0] if changed else conn.execute('SELECT current_version_id FROM public_documents WHERE id=?',(document_id,)).fetchone()[0]
                    item['version_id'] = version_id
                    conn.execute("UPDATE public_job_items SET status='indexing',metadata=?,document_id=?,error=NULL,updated_at=? WHERE id=?", (json.dumps(item,ensure_ascii=False),document_id,now(),row['id']))
                await asyncio.wait_for(embed_document(document_id, stop, version_id=version_id, item_id=row['id']), timeout=max(.01, MAX_JOB_SECONDS - (time.monotonic() - start)))
                with get_db() as conn:
                    conn.execute("UPDATE public_job_items SET status=?,error=NULL,updated_at=? WHERE id=? AND status='indexing'",('accepted' if changed else 'unchanged',now(),row['id']))
            except FetchCancelled:
                with get_db() as conn:
                    conn.execute("UPDATE public_job_items SET status='pending',updated_at=? WHERE id=? AND status!='deleted'", (now(), row["id"]))
                final_status = "paused" if time.monotonic() >= getattr(stop, "deadline", float("inf")) else "cancelled"; break
            except Exception as exc:
                error = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                with get_db() as conn:
                    conn.execute("UPDATE public_job_items SET status='failed',error=?,updated_at=? WHERE id=? AND status!='deleted'", (error[:1000], now(), row["id"]))
                    if item.get('version_id'):
                        conn.execute("UPDATE public_version_indexes SET error=? WHERE version_id=?",(error[:1000],item['version_id']))
            with get_db() as conn:
                conn.execute("UPDATE public_jobs SET elapsed_seconds=?,updated_at=? WHERE id=?", (prior + time.monotonic() - start, now(), job_id))
    except asyncio.CancelledError:
        stop.set(); final_status, final_error = "paused", "Service stopped during ingestion"
        raise
    except Exception as exc:
        final_status, final_error = "failed", str(exc)[:1000]
    finally:
        with get_db() as conn:
            failed = conn.execute("SELECT COUNT(*) FROM public_job_items WHERE job_id=? AND status='failed'", (job_id,)).fetchone()[0]
            if final_status == "completed" and failed:
                final_status = "partial"
            conn.execute("UPDATE public_jobs SET status=?,error=?,elapsed_seconds=?,updated_at=? WHERE id=?", (final_status, final_error, prior + time.monotonic() - start, now(), job_id))
        BUSY.discard(job_id); STOPS.pop(job_id, None); TASKS.pop(job_id, None)
        if hasattr(stop, "finish"):
            stop.finish()


def document_dict(row):
    result = dict(row)
    if "license" in result:
        result["license"] = json.loads(result["license"])
    if "chunking" in result:
        result["chunking"] = json.loads(result["chunking"])
    return result


def document_index(conn, document_id):
    row=conn.execute('SELECT i.state,i.model,i.digest,i.dimensions,i.error FROM public_documents d LEFT JOIN public_version_indexes i ON i.version_id=d.current_version_id WHERE d.id=?',(document_id,)).fetchone()
    pending=[dict(v) for v in conn.execute("SELECT v.id AS version_id,v.revision,i.state,i.error FROM public_versions v JOIN public_version_indexes i ON i.version_id=v.id WHERE v.document_id=? AND i.state='staged' ORDER BY v.revision DESC",(document_id,))]
    return {'index_state':row['state'] if row and row['state'] else 'staged','embedding_namespace':{'model':row['model'],'digest':row['digest'],'dimensions':row['dimensions']} if row and row['model'] else None,'staged_versions':pending}


@router.get("/documents")
def list_documents(source_id: str | None = None):
    with get_db() as conn:
        rows = conn.execute("SELECT d.id,d.source_id,d.url,d.title,d.created_at,d.updated_at,v.id AS version_id,v.version,v.revision,v.sha256,v.byte_size,v.etag,v.last_modified,v.license,v.language,v.fetched_at,v.published_at,v.modified_at,v.chunking FROM public_documents d JOIN public_versions v ON v.id=COALESCE(d.current_version_id,(SELECT id FROM public_versions WHERE document_id=d.id ORDER BY revision DESC LIMIT 1)) WHERE (? IS NULL OR d.source_id=?) ORDER BY d.updated_at DESC", (source_id, source_id))
        return [document_dict(row)|document_index(conn,row['id']) for row in rows]


@router.get("/documents/{document_id}")
def inspect_document(document_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT d.*,v.version,v.revision,v.sha256,v.byte_size,v.etag,v.last_modified,v.license,v.license_text,v.language,v.fetched_at,v.extracted_text,v.published_at,v.modified_at,v.chunking FROM public_documents d JOIN public_versions v ON v.id=COALESCE(d.current_version_id,(SELECT id FROM public_versions WHERE document_id=d.id ORDER BY revision DESC LIMIT 1)) WHERE d.id=?", (document_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Public document not found")
        result = document_dict(row)
        result.update(document_index(conn,document_id))
        source=source_dict(source_row(conn,row['source_id']))
        rights=source['status']
        if rights=='allowed' and not source_allows(source,row['url']):
            rights='scope_or_rights_revoked'
        if rights != "allowed":
            result.pop("extracted_text", None)
            result["content_blocked"] = True
            result["rights_status"] = rights
        result["chunks"] = [dict(item) for item in conn.execute("SELECT c.*,m.heading FROM public_chunks c LEFT JOIN public_chunk_metadata m ON m.chunk_id=c.id WHERE c.version_id=? ORDER BY c.page,c.line_start,c.rowid", (row["current_version_id"],))]
        if rights != "allowed":
            result["chunks"] = []
        result["versions"] = [document_dict(item) for item in conn.execute("SELECT id,version,revision,sha256,byte_size,etag,last_modified,fetched_at,published_at,modified_at,chunking FROM public_versions WHERE document_id=? ORDER BY revision DESC", (document_id,))]
        return result


@router.get("/documents/{document_id}/original")
def original_document(document_id: str, version_id: str | None = None):
    with get_db() as conn:
        row = conn.execute("SELECT v.original,v.content_type,v.license,s.status,s.data,COALESCE(i.source_url,d.url) AS url FROM public_versions v JOIN public_documents d ON d.id=v.document_id JOIN public_sources s ON s.id=d.source_id LEFT JOIN public_version_indexes i ON i.version_id=v.id WHERE d.id=? AND v.id=COALESCE(?,d.current_version_id)", (document_id, version_id)).fetchone()
        if not row:
            raise HTTPException(404, "Public document version not found")
        if not source_allows(json.loads(row['data'])|{'status':row['status']},row['url']):
            raise HTTPException(403, "Original download is blocked pending source rights review")
        return Response(row["original"], media_type="application/octet-stream", headers={"Content-Disposition": 'attachment; filename="source-original.bin"', "X-Content-Type-Options": "nosniff"})


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str, confirm: bool = False):
    if not confirm:
        raise HTTPException(403, "Explicit deletion confirmation is required")
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        doc = conn.execute('SELECT source_id,requested_url FROM public_documents WHERE id=?',(document_id,)).fetchone()
        if not doc:
            raise HTTPException(404, 'Public document not found')
        invalidate_items(conn,doc['source_id'],doc['requested_url'])
        if conn.execute("DELETE FROM public_documents WHERE id=?", (document_id,)).rowcount != 1:
            raise HTTPException(404, "Public document not found")
    return {"deleted": True, "budget_refunded": False}


def corpus_rows(conn, filters=None):
    filters = filters or {}
    rows = conn.execute("SELECT c.*,d.url,d.title,v.version,v.revision,v.sha256,v.language,v.license,v.published_at,v.modified_at,v.fetched_at,v.chunking,m.heading,s.data FROM public_chunks c JOIN public_documents d ON d.current_version_id=c.version_id JOIN public_versions v ON v.id=c.version_id JOIN public_sources s ON s.id=c.source_id LEFT JOIN public_chunk_metadata m ON m.chunk_id=c.id WHERE s.status='allowed'")
    result = []
    for row in rows:
        value = dict(row)
        source = json.loads(value.pop("data"))
        if not source_allows(source|{'status':'allowed'},value['url']):
            continue
        value.update(scope="public", filename=value["title"], domain=source.get("domain", "general"), license=json.loads(value["license"]), chunking=json.loads(value["chunking"]))
        languages = [value["language"]]
        if source.get("content_type") == "wikidata-json":
            languages = sorted(set(re.findall(r"(?:labels|descriptions)\[(en|ar|he)\]", value["text"]))) or languages
        value["languages"] = languages
        if any(filters.get(key) and value.get(key) != filters[key] for key in ("source_id", "domain", "version")) or (filters.get("language") and filters["language"] not in languages):
            continue
        result.append(value)
    return result


def authorize_current(conn, chunks):
    """Recheck rights/current versions after caller awaits, before releasing evidence."""
    current = {row["id"]: row for row in corpus_rows(conn)}
    checked = []
    for chunk in chunks:
        live = current.get(chunk.get("id"))
        if live and live["version_id"] == chunk.get("version_id") and live["text"] == chunk.get("text"):
            checked.append(live | {key:chunk[key] for key in ("score", "retrieval_method", "embedding_model", "embedding_digest", "retrieval_notice") if key in chunk})
    return checked


def lexical_public(conn, query, limit=5, filters=None):
    rows = corpus_rows(conn, filters)
    terms = set(tokenize(query[:4000]))
    if not rows or not terms:
        return []
    documents = [Counter(tokenize(row["text"])) for row in rows]
    lengths = [sum(doc.values()) for doc in documents]
    average = sum(lengths) / len(lengths) or 1
    frequencies = {term: sum(term in doc for doc in documents) for term in terms}
    scored = []
    for row, tokens, length in zip(rows, documents, lengths):
        score = 0
        for term in terms:
            count = tokens.get(term, 0)
            if count:
                weight = math.log(1 + (len(rows) - frequencies[term] + .5) / (frequencies[term] + .5))
                score += weight * count * 2.5 / (count + 1.5 * (.25 + .75 * length / average))
        if score > 0:
            scored.append(row | {"score": round(score, 6)})
    scored.sort(key=lambda value: (-value["score"], value["title"], value["page"] or 0, value["line_start"], value["id"]))
    return scored[:max(1, min(limit, 100))]


async def embedding_info():
    model = os.environ.get("RIWAQ_EMBED_MODEL", "")
    if not model:
        return {"configured": False, "available": False, "model": None, "reason": "Local BM25 is active; no embedding model configured"}
    if any(word in model.lower() for word in (":cloud", "-cloud", "/", "http:")):
        return {"configured": True, "available": False, "model": model, "reason": "Only installed local embedding models are permitted"}
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            show = await client.post(OLLAMA + "/api/show", json={"model": model})
            show.raise_for_status(); details = show.json()
            if details.get("remote_host") or details.get("remote_model") or "embedding" not in details.get("capabilities", []):
                raise ValueError("Model is remote or lacks embedding capability")
            tags = await client.get(OLLAMA + "/api/tags"); tags.raise_for_status()
            item = next((row for row in tags.json().get("models", []) if row.get("name") == model or row.get("model") == model), None)
            if not item or not item.get("digest"):
                raise ValueError("Embedding weights are not installed")
            return {"configured": True, "available": True, "model": model, "digest": item["digest"], "processing": "local"}
    except (httpx.HTTPError, ValueError) as exc:
        return {"configured": True, "available": False, "model": model, "reason": str(exc)[:200]}


async def vectors(texts, info):
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        response = await client.post(OLLAMA + "/api/embed", json={"model": info["model"], "input": texts, "truncate": False, "keep_alive": "5m"})
        response.raise_for_status()
        result = response.json().get("embeddings", [])
    if len(result) != len(texts):
        raise ValueError("Local embedding response count mismatch")
    normalized = []
    for vector in result:
        if not vector or len(vector) > 4096 or any(not isinstance(x, (float, int)) or not math.isfinite(x) for x in vector):
            raise ValueError("Invalid local embedding vector")
        norm = math.sqrt(sum(x * x for x in vector))
        if not norm:
            raise ValueError("Zero embedding vector")
        normalized.append([x / norm for x in vector])
    return normalized


async def embed_document(document_id, stop, *, version_id=None, item_id=None):
    """Index a retained version, then publish it only after a complete namespace."""
    with get_db() as conn:
        if version_id is None:
            doc = conn.execute('SELECT current_version_id FROM public_documents WHERE id=?',(document_id,)).fetchone()
            if not doc or not doc[0]:
                raise FetchCancelled('Document no longer has a current version')
            version_id = doc[0]
        authority=index_authority(conn,document_id,version_id,stop,item_id)
        if not authority['source_fingerprint']:
            conn.execute('UPDATE public_version_indexes SET source_fingerprint=? WHERE version_id=?',(source_fingerprint(source_dict(source_row(conn,authority['source_id']))),version_id))
    info = await embedding_info()
    if info.get('configured') and not info.get('available'):
        raise FetchError('Configured local embedding model unavailable; staged version not activated')
    with get_db() as conn:
        index_authority(conn,document_id,version_id,stop,item_id)
        rows = conn.execute("SELECT c.id,c.text,i.title FROM public_chunks c JOIN public_version_indexes i ON i.version_id=c.version_id WHERE c.version_id=? AND NOT EXISTS (SELECT 1 FROM public_embeddings e WHERE e.chunk_id=c.id AND e.model=? AND e.digest=?) ORDER BY c.rowid",(version_id,info.get('model'),info.get('digest'))).fetchall() if info.get('available') else []
        dimensions = {row[0] for row in conn.execute('SELECT DISTINCT e.dimensions FROM public_embeddings e JOIN public_chunks c ON c.id=e.chunk_id WHERE c.version_id=? AND e.model=? AND e.digest=?',(version_id,info.get('model'),info.get('digest')))}
    for start in range(0,len(rows),8):
        if stop.is_set():
            raise FetchCancelled('Local embedding indexing was cancelled')
        batch = rows[start:start+8]
        task = asyncio.create_task(vectors(['title: '+row['title']+' | text: '+row['text'] for row in batch],info))
        try:
            while not task.done():
                if stop.is_set():
                    raise FetchCancelled('Local embedding indexing was cancelled')
                await asyncio.wait([task],timeout=.1)
            embeddings = task.result()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
        dimensions.update(len(vector) for vector in embeddings)
        if len(embeddings)!=len(batch) or len(dimensions)!=1:
            raise FetchError('Embedding dimensions changed; staged version not activated')
        with get_db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            index_authority(conn,document_id,version_id,stop,item_id)
            for row,vector in zip(batch,embeddings):
                conn.execute('INSERT OR REPLACE INTO public_embeddings VALUES(?,?,?,?,?)',(row['id'],info['model'],info['digest'],len(vector),struct.pack('<'+'f'*len(vector),*vector)))
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        return activate_version(conn,document_id,version_id,info,stop,item_id)


def unique_ranked(rows, limit):
    """Keep the first ranked exact text and its original, verifiable citation.

    This is result selection only: no source rewriting, whitespace normalization,
    score merging, or changes to the indexed corpus and document versions.
    """
    if limit <= 0:
        return []
    output, seen = [], set()
    for row in rows:
        text = row["text"]
        if text in seen:
            continue
        seen.add(text)
        output.append(row)
        if len(output) >= limit:
            break
    return output


async def retrieve_public(query, limit=5, filters=None):
    limit = max(1, min(limit, 10))
    info = await embedding_info()
    with get_db() as conn:
        lexical = lexical_public(conn, query, 60, filters)
        rows = corpus_rows(conn, filters)
    if not info["available"] or not rows:
        return unique_ranked([row | {"retrieval_method": "bm25"} for row in lexical], limit)
    try:
        vector = (await vectors(["task: search result | query: " + query[:4000]], info))[0]
        with get_db() as conn:
            # Rights, deletions and current versions may change while the local
            # embedding request is in flight. Revalidate before releasing text.
            lexical = lexical_public(conn, query, 60, filters)
            allowed = {row["id"]: row for row in corpus_rows(conn, filters)}
            stored = conn.execute("SELECT e.chunk_id,e.vector FROM public_embeddings e JOIN public_chunks c ON c.id=e.chunk_id JOIN public_version_indexes i ON i.version_id=c.version_id WHERE i.state='hybrid_ready' AND e.model=i.model AND e.digest=i.digest AND e.dimensions=i.dimensions AND e.model=? AND e.digest=? AND e.dimensions=?", (info["model"], info["digest"], len(vector))).fetchall()
        semantic = []
        for row in stored:
            if row["chunk_id"] in allowed:
                saved = struct.unpack("<" + "f" * len(vector), row["vector"])
                semantic.append((sum(a * b for a, b in zip(vector, saved)), row["chunk_id"]))
        semantic.sort(reverse=True)
        if not semantic:
            return unique_ranked([row | {"retrieval_method": "bm25"} for row in lexical], limit)
        scores = {}
        for ranking in ([row["id"] for row in lexical], [key for _, key in semantic[:60]]):
            for rank, key in enumerate(ranking, 1):
                scores[key] = scores.get(key, 0) + 1 / (60 + rank)
        ordered = sorted(scores, key=lambda key: (-scores[key], key))
        return unique_ranked([allowed[key] | {"score": scores[key], "retrieval_method": "hybrid-rrf", "embedding_model": info["model"], "embedding_digest": info["digest"]} for key in ordered], limit)
    except (httpx.HTTPError, ValueError, struct.error):
        with get_db() as conn:
            lexical = lexical_public(conn, query, 60, filters)
        return unique_ranked([row | {"retrieval_method": "bm25", "retrieval_notice": "Local semantic retrieval failed; lexical sources only"} for row in lexical], limit)


@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=4000), limit: int = Query(default=5, ge=1, le=10), source_id: str | None = None, domain: str | None = None, language: str | None = None, version: str | None = None):
    chunks = await retrieve_public(q, limit, {"source_id": source_id, "domain": domain, "language": language, "version": version})
    return {"chunks": chunks, "method": chunks[0].get("retrieval_method", "bm25") if chunks else "bm25", "notice": "Public sources only. Locations refer to stored extracted text; retrieval is not proof that a claim is supported. No matching source means no grounded answer."}


@router.get("/status")
async def public_status():
    with get_db() as conn:
        source_count = conn.execute("SELECT COUNT(*) FROM public_sources").fetchone()[0]
        document_count = conn.execute("SELECT COUNT(*) FROM public_documents").fetchone()[0]
        activated_count = conn.execute('SELECT COUNT(*) FROM public_documents WHERE current_version_id IS NOT NULL').fetchone()[0]
        index_states = {row[0]:row[1] for row in conn.execute('SELECT i.state,COUNT(*) FROM public_version_indexes i JOIN public_documents d ON d.current_version_id=i.version_id GROUP BY i.state')}
        staged_count = conn.execute("SELECT COUNT(*) FROM public_version_indexes WHERE state='staged'").fetchone()[0]
        chunk_count = conn.execute("SELECT COUNT(*) FROM public_chunks c JOIN public_documents d ON d.current_version_id=c.version_id").fetchone()[0]
        downloaded = conn.execute("SELECT COALESCE(SUM(bytes),0) FROM public_download_ledger").fetchone()[0]
        items = conn.execute("SELECT status,source_id,url,metadata FROM public_job_items").fetchall()
        original_storage = conn.execute("SELECT COALESCE(SUM(byte_size),0) FROM public_versions").fetchone()[0]
        stored_text = conn.execute("SELECT COALESCE(SUM(length(CAST(extracted_text AS BLOB))+length(CAST(license_text AS BLOB))),0) FROM public_versions").fetchone()[0]
        stored_chunks = conn.execute("SELECT COALESCE(SUM(length(CAST(text AS BLOB))),0) FROM public_chunks").fetchone()[0]
        stored_vectors = conn.execute("SELECT COALESCE(SUM(length(vector)),0) FROM public_embeddings").fetchone()[0]
        active = conn.execute("SELECT * FROM public_jobs WHERE status IN ('queued','running') ORDER BY created_at DESC LIMIT 1").fetchone()
        job = job_dict(conn, active) if active else None
        ledger = [dict(row) for row in conn.execute("SELECT * FROM public_download_ledger ORDER BY created_at")]
    embedding = await embedding_info()
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(DISTINCT e.chunk_id) FROM public_embeddings e JOIN public_chunks c ON c.id=e.chunk_id JOIN public_documents d ON d.current_version_id=c.version_id WHERE e.model=? AND e.digest=?", (embedding.get("model"), embedding.get("digest"))).fetchone()[0]
        dimensions = [row[0] for row in conn.execute("SELECT DISTINCT e.dimensions FROM public_embeddings e JOIN public_chunks c ON c.id=e.chunk_id JOIN public_documents d ON d.current_version_id=c.version_id WHERE e.model=? AND e.digest=? ORDER BY e.dimensions", (embedding.get("model"), embedding.get("digest")))]
    embedding["indexed_vectors"] = count
    embedding["dimensions"] = dimensions[0] if len(dimensions) == 1 else None
    embedding["namespaces"] = dimensions
    embedding["current_chunks"] = chunk_count
    return {"sources": source_count, "documents": document_count, "chunks": chunk_count, "discovered_links": len({(row["source_id"], row["url"]) for row in items}), "fetched_files": sum(json.loads(row["metadata"]).get("fetched_count", 0) for row in items), "accepted_documents": activated_count, "index_states": index_states, "staged_versions": staged_count, "indexed_chunks": chunk_count, "failed_sources": len({row["source_id"] for row in items if row["status"] == "failed"}), "downloaded_bytes": downloaded, "budget_bytes": BUDGET_BYTES, "budget_remaining_bytes": max(0, BUDGET_BYTES - downloaded), "active_job": job, "embedding": embedding, "original_storage_bytes": original_storage, "storage_bytes": original_storage + stored_text + stored_chunks + stored_vectors, "storage_measurement": "Logical public payload bytes across all versions; excludes SQLite metadata, indexes and WAL", "limits": LIMITS | {"max_file_bytes": MAX_FILE_BYTES, "max_job_seconds": MAX_JOB_SECONDS}, "limits_source": str(seed_path()), "ledger": ledger, "training": False, "mode": settings()["mode"]}


def reprocess_html_document(conn, document_id, expected_version):
    """Reprocess retained bytes inside the caller transaction; never fetch a URL."""
    row = conn.execute("SELECT v.*,d.current_version_id,d.requested_url,d.url,d.title,d.source_id,s.status,s.data AS source_data FROM public_documents d JOIN public_versions v ON v.id=d.current_version_id JOIN public_sources s ON s.id=d.source_id WHERE d.id=?", (document_id,)).fetchone()
    if not row or row["status"] != "allowed":
        raise ValueError("Document missing or source rights unavailable")
    if row["current_version_id"] != expected_version:
        raise ValueError("Current version changed; preview reprocessing again")
    if hashlib.sha256(row["original"]).hexdigest() != row["sha256"]:
        raise ValueError("Retained original hash mismatch")
    source = json.loads(row["source_data"])
    if "html" not in row["content_type"] and source.get("content_type") != "html":
        raise ValueError("Only HTML originals require this extractor migration")
    if not source.get("rights_confirmed") or not source.get("license", {}).get("storage_allowed"):
        raise ValueError("Storage rights unavailable")
    # Preserve retained processing parameters, including an empty version label.
    prior_policy = json.loads(row["chunking"])
    if prior_policy.get("algorithm") != "heading-lines-v2":
        raise ValueError("Older chunk algorithms require a separate reviewed migration")
    source["chunking"] = {key:value for key,value in prior_policy.items() if key in ChunkSpec.model_fields}
    source["version"], source["language"] = row["version"], row["language"]
    # Preserve version-specific license evidence and library/date metadata.
    source["license"] = json.loads(row["license"])
    item = {"url":row["requested_url"], "title":row["title"], "version":row["version"], "language":row["language"], "published_at":row["published_at"], "modified_at":row["modified_at"]}
    headers = {"content-type":row["content_type"]}
    headers.update({key:value for key,value in (("etag",row["etag"]),("last-modified",row["last_modified"])) if value})
    fetched = FetchResult(row["url"],200,headers,row["original"])
    pages = extract_public_isolated(row["original"],source,row["url"],row["content_type"],extractor='html-pre-v2')
    identifier, changed = store_document(conn,source,item,fetched,pages,row["license_text"],fetched_at=row["fetched_at"])
    current = conn.execute("SELECT current_version_id FROM public_documents WHERE id=?", (identifier,)).fetchone()[0]
    return {"document_id":identifier,"old_version":expected_version,"current_version":current,"changed":changed,"original_sha256":row["sha256"]}
