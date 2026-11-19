"""Local, project-scoped documents and versioned artifacts.

Retrieval deliberately returns source excerpts rather than purported answers.
Imported content is data, never an instruction or an executable tool call.
"""
from __future__ import annotations

import asyncio
import threading
from collections import Counter
from datetime import datetime, timezone
import difflib
import hashlib
import io
import math
import re
import sqlite3
import unicodedata
import uuid
import zipfile
from xml.etree import ElementTree

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, Request
from pydantic import BaseModel, Field

from backend.db import get_db

router = APIRouter()
MAX_UPLOAD = 8 * 1024 * 1024
MAX_EXTRACTED = 2 * 1024 * 1024
MAX_ARCHIVE_EXPANDED = 32 * 1024 * 1024
MAX_PAGES = 200
MAX_CHUNKS = 4000
MAX_PROJECT_FILES = 100
MAX_PROJECT_EXTRACTED = 20 * 1024 * 1024
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".py", ".js", ".jsx",
    ".ts", ".tsx", ".json", ".jsonl", ".html", ".css", ".scss", ".rs",
    ".go", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php",
    ".sh", ".ps1", ".yaml", ".yml", ".toml", ".sql", ".xml", ".vue",
    ".svelte", ".kt", ".swift", ".r", ".log", ".ini", ".cfg",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_knowledge(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge_files (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            filename TEXT NOT NULL, byte_size INTEGER NOT NULL, sha256 TEXT NOT NULL,
            chunk_count INTEGER NOT NULL, page_count INTEGER NOT NULL,
            char_count INTEGER NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(project_id, sha256, filename)
        );
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id TEXT PRIMARY KEY, file_id TEXT NOT NULL REFERENCES knowledge_files(id) ON DELETE CASCADE,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            page INTEGER, line_start INTEGER NOT NULL, line_end INTEGER NOT NULL, text TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_knowledge_project ON knowledge_chunks(project_id);
        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title TEXT NOT NULL, content TEXT NOT NULL, version INTEGER NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS artifact_versions (
            artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
            version INTEGER NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(artifact_id, version)
        );
    """)


def require_project(conn: sqlite3.Connection, project_id: str, actor=None):
    from backend import guard
    return guard.require_project(conn, project_id, actor)


def require_file(conn, project_id, file_id, actor=None):
    actor = require_project(conn, project_id, actor)
    row = conn.execute('''SELECT f.* FROM knowledge_files f JOIN guard_file_policies p ON p.file_id=f.id
        WHERE f.id=? AND f.project_id=? AND p.project_id=f.project_id AND p.owner_id=? AND p.revoked=0''',
        (file_id, project_id, actor.owner_id)).fetchone()
    if row is None: raise HTTPException(404, 'Project resource unavailable')
    return row


def record_event(conn: sqlite3.Connection, project_id: str, kind: str, summary: str) -> None:
    conn.execute("INSERT INTO events(id,project_id,kind,summary,created_at) VALUES(?,?,?,?,?)",
                 (str(uuid.uuid4()), project_id, kind, summary, now()))


def safe_filename(filename: str | None) -> str:
    # No supplied path is ever written to disk. Reject paths to avoid misleading citations.
    if not filename or len(filename) > 200 or any(c in filename for c in ("/", "\\", "\x00")):
        raise HTTPException(400, "Choose a file with a plain filename (no paths)")
    if any(ord(c) < 32 for c in filename) or filename in {".", ".."}:
        raise HTTPException(400, "Invalid filename")
    if filename.lower() == ".env" or filename.lower().startswith(".env."):
        raise HTTPException(400, "Secret environment files cannot be indexed")
    return filename


def _docx_pages(data: bytes) -> list[tuple[int | None, str]]:
    from backend import isolated_parsers
    try: return isolated_parsers._docx_pages(data)
    except isolated_parsers.ParserError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


def _pdf_pages(data: bytes) -> list[tuple[int | None, str]]:
    from backend import isolated_parsers
    try: return isolated_parsers._pdf_pages(data)
    except isolated_parsers.ParserError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


def extract_document(filename: str, data: bytes) -> list[tuple[int | None, str]]:
    filename = safe_filename(filename)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Upload limit is 8 MB")
    if not data:
        raise HTTPException(422, "The file is empty")
    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension == ".pdf":
        pages = _pdf_pages(data)
    elif extension == ".docx":
        pages = _docx_pages(data)
    elif extension in TEXT_EXTENSIONS:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(422, "Text files must use UTF-8 encoding") from exc
        if "\x00" in text:
            raise HTTPException(422, "Binary content is not accepted as text")
        pages = [(None, text)]
    elif extension in {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}:
        raise HTTPException(422, "Image OCR is not configured; import a text document instead")
    else:
        raise HTTPException(415, "Supported formats: PDF, DOCX, TXT, Markdown, CSV and UTF-8 source code")
    if sum(len(text) for _, text in pages) > MAX_EXTRACTED:
        raise HTTPException(413, "Extracted text exceeds the 2 MB limit")
    if not any(text.strip() for _, text in pages):
        raise HTTPException(422, "No readable text was found")
    return pages


def make_chunks(pages: list[tuple[int | None, str]]) -> list[dict]:
    chunks = []
    # Line locations are 1-based within the extracted page/text; DOCX uses extracted paragraphs.
    for page, text in pages:
        group: list[str] = []
        start = 1
        size = 0
        lines = text.splitlines()
        for number, line in enumerate(lines, 1):
            # Preserve all source text, splitting long source lines while keeping their location.
            fragments = [line[i:i + 1200] for i in range(0, len(line), 1200)] or [""]
            for fragment in fragments:
                if group and (size + len(fragment) > 1400 or len(group) >= 24):
                    body = "\n".join(group)
                    if body.strip():
                        chunks.append({"page": page, "line_start": start, "line_end": previous_number, "text": body})
                    group, size, start = [], 0, number
                if not group:
                    start = number
                group.append(fragment)
                previous_number = number
                size += len(fragment) + 1
        if group:
            body = "\n".join(group)
            if body.strip():
                chunks.append({"page": page, "line_start": start, "line_end": len(lines), "text": body})
        if len(chunks) > MAX_CHUNKS:
            raise HTTPException(413, "Document exceeds the chunk limit")
    return chunks


_ARABIC_DIACRITICS = re.compile("[\u064b-\u065f\u0670\u06d6-\u06ed]")
_STOP_WORDS = {"the", "a", "an", "and", "or", "is", "are", "of", "to", "in", "for", "what", "how", "with", "from", "this", "that", "it", "be", "on", "من", "في", "على", "الي", "الى", "عن", "ما", "ماذا", "هو", "هي", "هل", "كيف", "و", "او", "هذا", "هذه", "التي", "الذي"}


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower()
    text = _ARABIC_DIACRITICS.sub("", text).replace("ـ", "")
    text = text.translate(str.maketrans("أإآى", "اااي"))
    tokens = re.findall(r"[^\W_]+", text, re.UNICODE)
    # Arabic prose commonly joins a preposition to a year/version: في2026.
    # Keep the original token (including API identifiers); add numeric aliases only
    # at Arabic-letter/digit boundaries. Stored text and citation locations stay exact.
    extra = []
    for token in tokens:
        if re.search(r"[\u0621-\u064a\u066e-\u06d3]", token) and any(c.isdecimal() for c in token):
            for number in re.findall(r"\d+", token):
                extra.append("".join(str(unicodedata.decimal(c)) for c in number))
        elif token.isdecimal() and not token.isascii():
            extra.append("".join(str(unicodedata.decimal(c)) for c in token))
    tokens.extend(extra)
    # Keep exact terms and add a conservative Arabic article-normalized form.
    normalized = []
    for token in tokens:
        if token in _STOP_WORDS or len(token) < 2:
            continue
        normalized.append(token)
        if token.startswith("ال") and len(token) > 4:
            normalized.append(token[2:])
    return normalized


def retrieve(conn: sqlite3.Connection, project_id: str, query: str, limit: int = 5, file_scope=None, file_ids=None, *, actor=None) -> list[dict]:
    actor = require_project(conn, project_id, actor)
    from backend import documents as document_store
    if file_scope:document_store.validate_scope(conn,project_id,file_scope,actor=actor)
    terms = set(tokenize(query[:4000]))
    if not terms:
        return []
    rows = conn.execute("""SELECT c.*,f.filename FROM knowledge_chunks c
        JOIN knowledge_files f ON f.id=c.file_id JOIN guard_file_policies p ON p.file_id=f.id
        WHERE c.project_id=? AND f.project_id=c.project_id AND p.project_id=f.project_id
        AND p.owner_id=? AND p.revoked=0 ORDER BY c.id""", (project_id,actor.owner_id)).fetchall()
    # The authenticated caller authorizes this exact set before retrieval. Filter
    # before scoring so unselected files cannot displace relevant selected rows.
    if file_ids is not None:
        if not isinstance(file_ids,list) or not 1<=len(file_ids)<=8 or any(not isinstance(x,str) for x in file_ids):
            raise HTTPException(422,'Invalid explicit file selection')
        for fid in file_ids:require_file(conn,project_id,fid,actor)
        rows=[row for row in rows if row['file_id'] in set(file_ids)]
    if file_scope:rows=document_store.filter_scope(conn,project_id,rows,file_scope,actor=actor)
    if not rows:
        return []
    documents = [Counter(tokenize(row["text"])) for row in rows]
    sizes = [sum(doc.values()) for doc in documents]
    average = sum(sizes) / len(sizes) or 1
    frequencies = {term: sum(term in doc for doc in documents) for term in terms}
    scored = []
    for row, document, size in zip(rows, documents, sizes):
        score = 0.0
        for term in terms:
            count = document.get(term, 0)
            if count:
                frequency = frequencies[term]
                weight = math.log(1 + (len(rows) - frequency + .5) / (frequency + .5))
                score += weight * count * 2.5 / (count + 1.5 * (1 - .75 + .75 * size / average))
        if score > 0:
            item = dict(row)
            item["score"] = round(score, 6)
            scored.append(item)
    scored.sort(key=lambda item: (-item["score"], item["filename"], item["page"] or 0, item["line_start"]))
    selected=scored[:max(1, min(limit, 10))]
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='document_chunk_refs'").fetchone():
        document_store.enrich(conn,selected)
    return selected


@router.get("/api/projects/{project_id}/files")
def list_files(project_id: str):
    with get_db() as conn:
        actor = require_project(conn, project_id)
        return [dict(row) for row in conn.execute('''SELECT f.* FROM knowledge_files f JOIN guard_file_policies p ON p.file_id=f.id
            WHERE f.project_id=? AND p.project_id=f.project_id AND p.owner_id=? AND p.revoked=0 ORDER BY f.created_at DESC''', (project_id,actor.owner_id))]


@router.post("/api/projects/{project_id}/files", status_code=201)
async def upload_file(project_id: str, request: Request, file: UploadFile = File(...)):
    try:return await _upload_file(project_id,request,file)
    finally:await file.close()


async def _upload_file(project_id,request,file):
    filename = safe_filename(file.filename)
    with get_db() as conn:
        actor = require_project(conn, project_id)
        count = conn.execute("SELECT COUNT(*) FROM knowledge_files WHERE project_id=?", (project_id,)).fetchone()[0]
        if count >= MAX_PROJECT_FILES:
            raise HTTPException(413, "Project file limit is 100")
    data = await file.read(MAX_UPLOAD + 1)
    await file.close()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Upload limit is 8 MB")
    # Complex parsers run only in a killable fixed worker. Plain UTF-8 decoding
    # remains bounded by MAX_UPLOAD/MAX_EXTRACTED and never invokes a parser.
    extension = filename.rsplit(".", 1)[-1].lower()
    if extension in {"pdf", "docx"}:
        from backend.knowledge_worker import extract_bounded
        stop = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(extract_bounded, filename, data, stop))
        try:
            while not task.done():
                if await request.is_disconnected():
                    stop.set()
                with get_db() as conn:
                    try:require_project(conn,project_id,actor)
                    except HTTPException:stop.set();raise
                await asyncio.wait({task}, timeout=.1)
            pages = await task
            if stop.is_set():
                raise HTTPException(499, "Document extraction cancelled")
        finally:
            stop.set()
            if not task.done():
                # Keep ownership until the supervisor has killed/reaped the child.
                try:
                    await asyncio.shield(task)
                except Exception:
                    pass
    else:
        pages = extract_document(filename, data)
    chunks = make_chunks(pages)
    digest = hashlib.sha256(data).hexdigest()
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        require_project(conn, project_id,actor)
        existing = conn.execute("SELECT * FROM knowledge_files WHERE project_id=? AND sha256=? AND filename=?", (project_id, digest, filename)).fetchone()
        if existing:
            return dict(require_file(conn,project_id,existing['id'],actor))
        count, extracted = conn.execute("SELECT COUNT(*),COALESCE(SUM(char_count),0) FROM knowledge_files WHERE project_id=?", (project_id,)).fetchone()
        if count >= MAX_PROJECT_FILES or extracted + sum(len(t) for _, t in pages) > MAX_PROJECT_EXTRACTED:
            raise HTTPException(413, "Project limits: 100 files and 20 MB extracted text")
        file_id = str(uuid.uuid4())
        conn.execute("INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)", (file_id, project_id, filename, len(data), digest, len(chunks), len(pages) if pages[0][0] is not None else 0, sum(len(t) for _, t in pages), now()))
        conn.executemany("INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?)", [(str(uuid.uuid4()), file_id, project_id, c["page"], c["line_start"], c["line_end"], c["text"]) for c in chunks])
        record_event(conn, project_id, "file_indexed", f"Indexed {filename}: {len(chunks)} source chunks")
        return dict(conn.execute("SELECT * FROM knowledge_files WHERE id=?", (file_id,)).fetchone())


@router.delete("/api/projects/{project_id}/files/{file_id}")
def delete_file(project_id: str, file_id: str):
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = require_file(conn,project_id,file_id)
        from backend.documents import cancel_file
        cancel_file(file_id)
        conn.execute("DELETE FROM knowledge_chunks WHERE file_id=? AND project_id=?", (file_id, project_id))
        conn.execute("DELETE FROM knowledge_files WHERE id=? AND project_id=?", (file_id, project_id))
        record_event(conn, project_id, "file_deleted", f"Removed {row['filename']}")
        return {"deleted": True}


@router.get("/api/projects/{project_id}/retrieve")
def retrieve_files(project_id: str, q: str = Query(min_length=1, max_length=4000), limit: int = Query(default=5, ge=1, le=10)):
    with get_db() as conn:
        chunks = retrieve(conn, project_id, q, limit)
        return {"chunks": chunks, "method": "local-bm25", "found": bool(chunks), "notice": "Locations refer to extracted text; PDF pages are 1-based. No matching source means no grounded answer."}


class ArtifactCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=200000)


class ArtifactUpdate(BaseModel):
    content: str = Field(max_length=200000)
    expected_version: int | None = Field(default=None, ge=1)


class ArtifactRestore(BaseModel):
    version: int = Field(ge=1)


def require_artifact(conn: sqlite3.Connection, artifact_id: str):
    row = conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Document not found")
    require_project(conn,row['project_id'])
    from backend.studio import artifact_allowed
    if not artifact_allowed(conn,row['project_id'],artifact_id):raise HTTPException(404,'Project resource unavailable')
    return row


@router.get("/api/projects/{project_id}/artifacts")
def list_artifacts(project_id: str):
    with get_db() as conn:
        require_project(conn, project_id)
        from backend.studio import artifact_allowed
        return [dict(row) for row in conn.execute("SELECT * FROM artifacts WHERE project_id=? ORDER BY updated_at DESC", (project_id,)) if artifact_allowed(conn,project_id,row['id'])]


@router.post("/api/projects/{project_id}/artifacts", status_code=201)
def create_artifact(project_id: str, body: ArtifactCreate):
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        require_project(conn, project_id)
        artifact_id, timestamp = str(uuid.uuid4()), now()
        conn.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)", (artifact_id, project_id, body.title.strip() or "Document", body.content, 1, timestamp, timestamp))
        conn.execute("INSERT INTO artifact_versions VALUES(?,?,?,?)", (artifact_id, 1, body.content, timestamp))
        record_event(conn, project_id, "artifact_created", f"Created document: {body.title}")
        return dict(require_artifact(conn, artifact_id))


@router.get("/api/artifacts/{artifact_id}")
def get_artifact(artifact_id: str):
    with get_db() as conn:
        return dict(require_artifact(conn, artifact_id))


def save_artifact_version(conn: sqlite3.Connection, row, content: str, event: str):
    require_project(conn,row['project_id'])
    if content == row["content"]:
        return dict(row)
    version, timestamp = row["version"] + 1, now()
    updated = conn.execute("UPDATE artifacts SET content=?,version=?,updated_at=? WHERE id=? AND version=?", (content, version, timestamp, row["id"], row["version"]))
    if updated.rowcount != 1:
        raise HTTPException(409, "Document changed; reload before saving")
    conn.execute("INSERT INTO artifact_versions VALUES(?,?,?,?)", (row["id"], version, content, timestamp))
    record_event(conn, row["project_id"], "artifact_updated", f"{event}: {row['title']} (v{version})")
    return dict(require_artifact(conn, row["id"]))


@router.patch("/api/artifacts/{artifact_id}")
def update_artifact(artifact_id: str, body: ArtifactUpdate):
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = require_artifact(conn, artifact_id)
        if body.expected_version is not None and body.expected_version != row["version"]:
            raise HTTPException(409, "Document changed; reload before saving")
        return save_artifact_version(conn, row, body.content, "Saved")


@router.get("/api/artifacts/{artifact_id}/versions")
def list_versions(artifact_id: str):
    with get_db() as conn:
        require_artifact(conn, artifact_id)
        return [dict(row) for row in conn.execute("SELECT * FROM artifact_versions WHERE artifact_id=? ORDER BY version DESC", (artifact_id,))]


@router.get("/api/artifacts/{artifact_id}/diff")
def artifact_diff(artifact_id: str, from_version: int = Query(ge=1), to_version: int = Query(ge=1)):
    with get_db() as conn:
        artifact = require_artifact(conn, artifact_id)
        old = conn.execute("SELECT content FROM artifact_versions WHERE artifact_id=? AND version=?", (artifact_id, from_version)).fetchone()
        new = conn.execute("SELECT content FROM artifact_versions WHERE artifact_id=? AND version=?", (artifact_id, to_version)).fetchone()
        if old is None or new is None:
            raise HTTPException(404, "Document version not found")
        diff = "\n".join(difflib.unified_diff(old[0].splitlines(), new[0].splitlines(), fromfile=f"{artifact['title']} v{from_version}", tofile=f"{artifact['title']} v{to_version}", lineterm=""))
        return {"diff": diff, "from_version": from_version, "to_version": to_version}


@router.post("/api/artifacts/{artifact_id}/restore")
def restore_artifact(artifact_id: str, body: ArtifactRestore):
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = require_artifact(conn, artifact_id)
        saved = conn.execute("SELECT content FROM artifact_versions WHERE artifact_id=? AND version=?", (artifact_id, body.version)).fetchone()
        if saved is None:
            raise HTTPException(404, "Document version not found")
        return save_artifact_version(conn, row, saved[0], f"Restored v{body.version}")
