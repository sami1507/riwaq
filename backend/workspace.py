"""Explicit local folder grants, reviewable edits, and guarded undo.

This module never executes source code or model tool calls. The local user's UI
must grant each root and explicitly confirm each apply/undo operation.
"""
from __future__ import annotations

import codecs
import difflib
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, StrictBool

from backend.db import get_db, now, uid, ROOT, data_dir

router = APIRouter()
MAX_FILE_BYTES = 1024 * 1024
MAX_LIST_FILES = 2000
MAX_SEARCH_BYTES = 10 * 1024 * 1024
EXCLUDED_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", "target", "dist", "build", ".aws", ".ssh", ".gnupg", ".azure", ".config", ".codex", ".agents", ".idea"}
SECRET_NAMES = {"credentials", "credentials.json", "credentials.ini", "secrets.json", "secrets.yaml", "secrets.yml", "secrets.toml", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".npmrc", ".pypirc", ".netrc", "keychain", "keyring"}
SECRET_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".keystore", ".kdbx", ".sqlite", ".sqlite3", ".db"}
RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.IGNORECASE)


def init_workspace(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS workspace_grants (
            id TEXT PRIMARY KEY, root TEXT NOT NULL, device TEXT NOT NULL,
            inode TEXT NOT NULL, active INTEGER NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workspace_proposals (
            id TEXT PRIMARY KEY, grant_id TEXT NOT NULL REFERENCES workspace_grants(id),
            path TEXT NOT NULL, original_sha TEXT NOT NULL, new_sha TEXT NOT NULL,
            original_bytes BLOB NOT NULL, new_bytes BLOB NOT NULL,
            diff TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workspace_events (
            id TEXT PRIMARY KEY, grant_id TEXT NOT NULL REFERENCES workspace_grants(id),
            action TEXT NOT NULL, path TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL
        );
    """)
    if 'owner_project_id' not in {r[1] for r in conn.execute('PRAGMA table_info(workspace_grants)')}:
        conn.execute('ALTER TABLE workspace_grants ADD COLUMN owner_project_id TEXT')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def journal(conn, grant_id: str, action: str, path: str, summary: str):
    conn.execute("INSERT INTO workspace_events VALUES(?,?,?,?,?,?)", (uid(), grant_id, action, path, summary, now()))


def is_link(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024))


def assert_no_links(path: Path):
    """Check the literal path and each ancestor before resolving or opening it."""
    for item in [path, *path.parents]:
        try:
            info = item.lstat()
        except OSError as exc:
            raise HTTPException(404, "Path is missing or inaccessible") from exc
        if is_link(info):
            raise HTTPException(403, "Symbolic links and reparse points are outside workspace grants")


def forbidden_component(component: str) -> bool:
    lower = component.casefold()
    return (lower in EXCLUDED_DIRS or lower in SECRET_NAMES or lower == ".env"
            or lower.startswith(".env.") or lower.endswith(tuple(SECRET_EXTENSIONS))
            or lower.startswith(".riwaq-") or bool(RESERVED.match(component))
            or bool(re.search(r"(?:^|[._-])(?:credentials?|secrets?)(?:[._-]|$)", lower))
            or any(term in lower for term in ("private_key", "private-key", "service-account", "service_account")))


def relative_parts(relative: str) -> tuple[str, ...]:
    if not relative or len(relative) > 1024 or relative.startswith(("/", "\\")):
        raise HTTPException(400, "A relative file path is required")
    if ":" in relative or "\x00" in relative or any(ord(c) < 32 for c in relative):
        raise HTTPException(400, "Invalid relative path")
    parts = tuple(relative.replace("\\", "/").split("/"))
    if any(part in {"", ".", ".."} or part.endswith((".", " ")) for part in parts):
        raise HTTPException(403, "Path traversal and ambiguous paths are not allowed")
    if any(forbidden_component(part) for part in parts):
        raise HTTPException(403, "Secrets and excluded directories are outside the grant")
    return parts


def protect_service_root(root: Path):
    """A user project may not contain or sit inside the Riwaq service/data tree."""
    selected=root.resolve()
    protected=[ROOT.resolve(),data_dir().resolve()]
    for name in ('SystemRoot','ProgramFiles','ProgramFiles(x86)'):
        if os.environ.get(name):protected.append(Path(os.environ[name]).resolve())
    for target in protected:
        if selected.is_relative_to(target) or target.is_relative_to(selected):
            raise HTTPException(403,'Riwaq, its data, system directories and their ancestors cannot be user workspaces')


def reject_content_secrets(text: str):
    from backend.coding_context import scan_secrets
    if scan_secrets(text):raise HTTPException(422,'Possible credential content: this file is excluded from display, model context and edits. Move secrets into a private configuration first.')


def get_grant(conn, grant_id: str, project_id: str | None = None):
    grant = conn.execute("SELECT * FROM workspace_grants WHERE id=? AND active=1", (grant_id,)).fetchone()
    if grant is None:
        raise HTTPException(404, "Folder permission is missing or revoked")
    if grant['owner_project_id'] is not None and grant['owner_project_id'] != project_id:
        raise HTTPException(404,'Folder permission does not belong to this project')
    root = Path(grant["root"])
    assert_no_links(root)
    protect_service_root(root)
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or (str(info.st_dev), str(info.st_ino)) != (grant["device"], grant["inode"]):
        raise HTTPException(409, "The granted directory changed; grant it again")
    return grant


def checked_path(grant, relative: str) -> Path:
    parts = relative_parts(relative)
    root = Path(grant["root"])
    path = root.joinpath(*parts)
    assert_no_links(path)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(403, "Path is outside the granted directory") from exc
    if not path.is_file():
        raise HTTPException(400, "Only existing regular files can be read or edited")
    if path.stat().st_nlink > 1:
        raise HTTPException(403, "Files with multiple hard links are outside workspace grants")
    return path


def read_bytes(grant, relative: str) -> bytes:
    path = checked_path(grant, relative)
    before = path.stat()
    if before.st_size > MAX_FILE_BYTES:
        raise HTTPException(413, "File exceeds the 1 MB limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or not stat.S_ISREG(opened.st_mode):
                raise HTTPException(409, "File changed while opening; retry")
            assert_no_links(path)
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise HTTPException(403, "The file cannot be read with this grant") from exc
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "File exceeds the 1 MB limit")
    return data


def decode_text(data: bytes) -> str:
    try:
        result = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(415, "Only UTF-8 text files can be opened") from exc
    if "\x00" in result:
        raise HTTPException(415, "Binary files cannot be opened as source")
    return result


def visible_files(grant):
    """Bound directory enumeration too, even when all entries are excluded.

    The ten-second deadline is cooperative between filesystem operations; it is
    not an OS timeout for a stalled filesystem. No file contents are read here.
    """
    import time
    try:
        from backend.coding_context import scan_secrets
        if not callable(scan_secrets):
            raise TypeError()
    except Exception:
        raise HTTPException(503, "Secret inspection is unavailable; listing is blocked") from None
    root = Path(grant["root"])
    pending, found = [root], []
    entries = directories = 0
    truncated = halted = False
    deadline = time.monotonic() + 10
    while pending and not halted:
        if time.monotonic() >= deadline or directories >= 512:
            truncated = True
            break
        directory = pending.pop()
        directories += 1
        try:
            assert_no_links(directory)
            with os.scandir(directory) as stream:
                for entry in stream:
                    entries += 1
                    if entries >= 5000 or time.monotonic() >= deadline:
                        truncated = halted = True
                        break
                    if forbidden_component(entry.name):
                        continue
                    try:
                        findings = scan_secrets(entry.name)
                        stem_findings = scan_secrets(Path(entry.name).stem)
                        if not isinstance(findings, list) or not isinstance(stem_findings, list):
                            raise TypeError()
                    except Exception:
                        raise RuntimeError("secret_scanner_unavailable") from None
                    if findings or stem_findings:
                        continue
                    candidate = Path(entry.path)
                    try:
                        info = candidate.lstat()
                        if is_link(info):
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(candidate)
                            continue
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1 or info.st_size > MAX_FILE_BYTES:
                            continue
                        relative = candidate.relative_to(root).as_posix()
                        checked_path(grant, relative)
                    except (HTTPException, OSError, ValueError):
                        truncated = True
                        continue
                    found.append({"path": relative, "size": info.st_size})
                    if len(found) >= MAX_LIST_FILES:
                        truncated = halted = True
                        break
        except (HTTPException, OSError):
            truncated = True
        except RuntimeError:
            raise HTTPException(503, "Secret inspection is unavailable; listing is blocked") from None
    return sorted(found, key=lambda item: item["path"]), truncated


class GrantRequest(BaseModel):
    path: str = Field(min_length=1, max_length=2048)


class ProposalRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=MAX_FILE_BYTES)


class Confirmation(BaseModel):
    confirm: StrictBool = False


@router.get("/api/workspace/grants")
def list_grants():
    with get_db() as conn:
        return [dict(row) for row in conn.execute("SELECT id,root,active,created_at FROM workspace_grants WHERE active=1 AND owner_project_id IS NULL ORDER BY created_at DESC")]


@router.post("/api/workspace/grant", status_code=201)
def grant_folder(body: GrantRequest):
    return create_grant(body.path)


def create_grant(selected_path: str, owner_project_id: str | None = None):
    reject_content_secrets(selected_path)
    if not Path(selected_path).is_absolute() or "\x00" in selected_path:
        raise HTTPException(400, "Select an absolute project folder path")
    root = Path(os.path.abspath(selected_path))
    if root == Path(root.anchor):
        raise HTTPException(403, "Grant a project folder, not an entire drive")
    if any(forbidden_component(part) for part in root.parts[1:]):
        raise HTTPException(403, "This directory is excluded from workspace grants")
    assert_no_links(root)
    protect_service_root(root)
    if not root.is_dir():
        raise HTTPException(400, "The selected path is not a folder")
    info = root.stat()
    with get_db() as conn:
        # Legacy routes run in a threadpool and can race project-scoped grants.
        # Reserve the write transaction BEFORE checking the overlap invariant.
        conn.execute("BEGIN IMMEDIATE")
        for other in conn.execute('SELECT root,owner_project_id FROM workspace_grants WHERE active=1'):
            other_root=Path(other['root']).resolve()
            overlaps=root.is_relative_to(other_root) or other_root.is_relative_to(root)
            if overlaps and other['owner_project_id'] != owner_project_id:
                raise HTTPException(409,'This folder overlaps an active grant in a different project or the legacy workspace. Revoke that grant explicitly first.')
        existing = conn.execute("SELECT id,root,active,created_at FROM workspace_grants WHERE root=? AND device=? AND inode=? AND active=1 AND owner_project_id IS ?", (str(root), str(info.st_dev), str(info.st_ino),owner_project_id)).fetchone()
        if existing:
            return dict(existing)
        grant_id = uid()
        conn.execute("INSERT INTO workspace_grants VALUES(?,?,?,?,?,?,?)", (grant_id, str(root), str(info.st_dev), str(info.st_ino), 1, now(),owner_project_id))
        journal(conn, grant_id, "granted", "", "User granted access to this project folder")
        return dict(conn.execute("SELECT id,root,active,created_at FROM workspace_grants WHERE id=?", (grant_id,)).fetchone())


@router.delete("/api/workspace/grants/{grant_id}")
def revoke_grant(grant_id: str):
    with get_db() as conn:
        found = conn.execute("UPDATE workspace_grants SET active=0 WHERE id=? AND active=1 AND owner_project_id IS NULL", (grant_id,))
        if found.rowcount != 1:
            raise HTTPException(404, "Folder grant not found")
        journal(conn, grant_id, "revoked", "", "User revoked folder access")
        return {"revoked": True}


@router.get("/api/workspace/{grant_id}/files")
def list_workspace_files(grant_id: str):
    with get_db() as conn:
        grant = get_grant(conn, grant_id)
        files, truncated = visible_files(grant)
        return {"files": files, "truncated": truncated, "limit": MAX_LIST_FILES}


@router.get("/api/workspace/{grant_id}/read")
def read_workspace_file(grant_id: str, path: str = Query(min_length=1, max_length=1024)):
    with get_db() as conn:
        grant = get_grant(conn, grant_id)
        data = read_bytes(grant, path)
        content = decode_text(data)
        reject_content_secrets(content)
        journal(conn, grant_id, "read", path, "Read source file")
        return {"path": path, "content": content, "sha256": digest(data), "size": len(data)}


@router.get("/api/workspace/{grant_id}/search")
def search_workspace(grant_id: str, q: str = Query(min_length=1, max_length=200)):
    with get_db() as conn:
        grant = get_grant(conn, grant_id)
        files, truncated = visible_files(grant)
        matches, scanned = [], 0
        for item in files:
            if scanned + item["size"] > MAX_SEARCH_BYTES:
                truncated = True
                break
            try:
                data = read_bytes(grant, item["path"])
                scanned += len(data)
                content = decode_text(data)
                reject_content_secrets(content)
            except HTTPException:
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if q.casefold() in line.casefold():
                    matches.append({"path": item["path"], "line": number, "text": line[:500]})
                    if len(matches) >= 100:
                        return {"matches": matches, "truncated": True, "scanned_bytes": scanned}
        return {"matches": matches, "truncated": truncated, "scanned_bytes": scanned}


def public_proposal(row):
    return {key: row[key] for key in ("id", "grant_id", "path", "original_sha", "new_sha", "diff", "state", "created_at", "updated_at")}


@router.post("/api/workspace/{grant_id}/proposals", status_code=201)
def propose_edit(grant_id: str, body: ProposalRequest):
    with get_db() as conn:
        grant = get_grant(conn, grant_id)
        before = read_bytes(grant, body.path)
        old = decode_text(before)
        reject_content_secrets(old)
        reject_content_secrets(body.content)
        after = body.content.encode("utf-8")
        if before.startswith(codecs.BOM_UTF8):
            after = codecs.BOM_UTF8 + after
        if len(after) > MAX_FILE_BYTES or "\x00" in body.content:
            raise HTTPException(413, "New text exceeds the file limit or contains binary data")
        if before == after:
            raise HTTPException(400, "No file changes to propose")
        diff = "\n".join(difflib.unified_diff(old.splitlines(), body.content.splitlines(), fromfile=f"a/{body.path}", tofile=f"b/{body.path}", lineterm=""))
        proposal_id, timestamp = uid(), now()
        conn.execute("INSERT INTO workspace_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?)", (proposal_id, grant_id, body.path, digest(before), digest(after), before, after, diff, "proposed", timestamp, timestamp))
        journal(conn, grant_id, "proposed", body.path, f"Prepared edit {proposal_id}; file unchanged")
        return public_proposal(conn.execute("SELECT * FROM workspace_proposals WHERE id=?", (proposal_id,)).fetchone())


def atomic_replace(grant, relative: str, data: bytes, expected_sha: str):
    path = checked_path(grant, relative)
    if digest(read_bytes(grant, relative)) != expected_sha:
        raise HTTPException(409, "File changed after review; create a new proposal")
    original_mode = path.stat().st_mode
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".riwaq-", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, stat.S_IMODE(original_mode))
        assert_no_links(path)
        if digest(read_bytes(grant, relative)) != expected_sha:
            raise HTTPException(409, "File changed while applying; no edit was written")
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise HTTPException(403, "The edit could not be written; check folder permissions") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def change_proposal(proposal_id: str, body: Confirmation, undo: bool):
    if body.confirm is not True:
        raise HTTPException(403, "Explicit user confirmation is required")
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM workspace_proposals WHERE id=?", (proposal_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Edit proposal not found")
        grant = get_grant(conn, row["grant_id"])
        expected_state = "applied" if undo else "proposed"
        if row["state"] != expected_state:
            raise HTTPException(409, f"Only a {expected_state} edit supports this action")
        data = row["original_bytes"] if undo else row["new_bytes"]
        reject_content_secrets(decode_text(data))
        expected_sha = row["new_sha"] if undo else row["original_sha"]
        atomic_replace(grant, row["path"], data, expected_sha)
        state = "undone" if undo else "applied"
        conn.execute("UPDATE workspace_proposals SET state=?,updated_at=? WHERE id=?", (state, now(), proposal_id))
        journal(conn, row["grant_id"], state, row["path"], f"User confirmed edit {proposal_id}")
        return public_proposal(conn.execute("SELECT * FROM workspace_proposals WHERE id=?", (proposal_id,)).fetchone())


@router.post("/api/workspace/proposals/{proposal_id}/apply")
def apply_proposal(proposal_id: str, body: Confirmation):
    return change_proposal(proposal_id, body, undo=False)


@router.post("/api/workspace/proposals/{proposal_id}/undo")
def undo_proposal(proposal_id: str, body: Confirmation):
    return change_proposal(proposal_id, body, undo=True)


@router.get("/api/workspace/{grant_id}/events")
def list_workspace_events(grant_id: str):
    with get_db() as conn:
        get_grant(conn, grant_id)
        return [dict(row) for row in conn.execute("SELECT * FROM workspace_events WHERE grant_id=? ORDER BY created_at DESC LIMIT 200", (grant_id,))]


@router.post("/api/workspace/execute")
def execute_disabled():
    raise HTTPException(503, "Code execution is disabled: no tested OS sandbox with time, memory and permission limits is configured. Review source and diffs only.")
