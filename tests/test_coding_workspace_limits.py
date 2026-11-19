"""Trusted filesystem/grant tests, without executing any project fixture code."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import stat
import threading
import time
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from backend import coding_context, workspace


def test_concurrent_cross_owner_grants_cannot_overlap(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    db = tmp_path / "grants.sqlite"
    with sqlite3.connect(db) as conn:
        workspace.init_workspace(conn)
    both_read = threading.Barrier(2)
    begin_seen = []

    class ObservedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            if sql == "BEGIN IMMEDIATE":
                begin_seen.append(threading.get_ident())
            if sql.startswith("SELECT root,owner_project_id FROM workspace_grants"):
                # Force both callers to observe the same old inventory when the
                # protecting transaction is absent. With BEGIN IMMEDIATE, the
                # second connection cannot enter this read before the first commits.
                rows = cursor.fetchall()
                try:
                    both_read.wait(timeout=.25)
                except threading.BrokenBarrierError:
                    pass
                return rows
            return cursor

    @contextmanager
    def get_db():
        conn = sqlite3.connect(db, timeout=3, factory=ObservedConnection)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    monkeypatch.setattr(workspace, "get_db", get_db)
    start = threading.Barrier(2)
    def grant(owner):
        start.wait(timeout=2)
        try:
            return workspace.create_grant(str(project), owner)
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(grant, owner) for owner in (None, "coding-project")]
        results = [f.result(timeout=5) for f in futures]
    assert len(begin_seen) == 2
    assert sum(isinstance(value, dict) for value in results) == 1
    assert results.count(409) == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM workspace_grants WHERE active=1").fetchone()[0] == 1


def test_listing_hides_secret_filenames_without_reading_contents(tmp_path, monkeypatch):
    (tmp_path / "visible.py").write_bytes(b"value = 1\n")
    (tmp_path / "password=private-value.txt").write_bytes(b"hidden")
    (tmp_path / ("sk-proj-" + "a" * 25 + ".txt")).write_bytes(b"hidden")
    (tmp_path / ".env").write_bytes(b"hidden")
    directory = tmp_path / "Bearer privateCredentialValue"
    directory.mkdir()
    (directory / "child.txt").write_bytes(b"hidden")
    monkeypatch.setattr(workspace, "read_bytes", lambda *args: pytest.fail("Listing must not read file contents"))
    listed, truncated = workspace.visible_files({"root": str(tmp_path)})
    assert listed == [{"path": "visible.py", "size": 10}]
    assert not truncated


def test_excluded_entries_still_consume_bounded_scan_budget(tmp_path, monkeypatch):
    visited = 0
    class Entries:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def __iter__(self): return self
        def __next__(self):
            nonlocal visited
            visited += 1
            if visited > 6000: raise StopIteration
            return SimpleNamespace(name=".git", path=str(tmp_path / ".git"))
    monkeypatch.setattr(workspace.os, "scandir", lambda path: Entries())
    assert workspace.visible_files({"root": str(tmp_path)}) == ([], True)
    assert visited == 5000


def test_directory_count_is_bounded_even_when_every_directory_is_empty(tmp_path, monkeypatch):
    opened = []
    class Entries:
        def __init__(self, path):
            opened.append(Path(path))
            self.values = iter([SimpleNamespace(name=f"d{i}", path=str(tmp_path / f"d{i}")) for i in range(800)] if Path(path) == tmp_path else [])
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def __iter__(self): return self
        def __next__(self): return next(self.values)
    with monkeypatch.context() as patch:
        patch.setattr(workspace.os, "scandir", Entries)
        patch.setattr(workspace, "assert_no_links", lambda path: None)
        patch.setattr(Path, "lstat", lambda self: SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0))
        assert workspace.visible_files({"root": str(tmp_path)}) == ([], True)
    assert len(opened) == 512


def test_cooperative_deadline_stops_before_more_filesystem_operations(tmp_path, monkeypatch):
    tick = iter([100.0, 111.0])
    with monkeypatch.context() as patch:
        patch.setattr(time, "monotonic", lambda: next(tick))
        patch.setattr(workspace.os, "scandir", lambda _: pytest.fail("Deadline elapsed before enumeration"))
        result = workspace.visible_files({"root": str(tmp_path)})
    assert result == ([], True)


def test_listing_file_limit_and_scandir_cleanup(tmp_path, monkeypatch):
    for name in ("one.py", "two.py", "three.py"):
        (tmp_path / name).write_bytes(b"pass\n")
    monkeypatch.setattr(workspace, "MAX_LIST_FILES", 2)
    actual_scandir = workspace.os.scandir
    closed = []
    class Wrapped:
        def __init__(self, path): self.stream = actual_scandir(path)
        def __enter__(self): return self.stream
        def __exit__(self, *args):
            self.stream.close()
            closed.append(True)
    monkeypatch.setattr(workspace.os, "scandir", Wrapped)
    found, truncated = workspace.visible_files({"root": str(tmp_path)})
    assert len(found) == 2 and truncated and closed == [True]


@pytest.mark.parametrize("fault", ["missing", "malformed", "raised"])
def test_listing_scanner_failure_is_not_permissive(tmp_path, monkeypatch, fault):
    (tmp_path / "one.py").write_bytes(b"pass\n")
    if fault == "missing": monkeypatch.delattr(coding_context, "scan_secrets")
    elif fault == "malformed": monkeypatch.setattr(coding_context, "scan_secrets", lambda _: None)
    else:
        def fail(_): raise RuntimeError("private exception")
        monkeypatch.setattr(coding_context, "scan_secrets", fail)
    with pytest.raises(HTTPException) as exc:
        workspace.visible_files({"root": str(tmp_path)})
    assert exc.value.status_code == 503
    assert "private exception" not in exc.value.detail
