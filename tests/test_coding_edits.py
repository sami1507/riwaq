"""Trusted helper tests only: fixture source is data and is never executed."""
import base64
import codecs
import copy
import os

import pytest

from backend import coding_context, coding_edits as edits, workspace


def proposal(baseline, hunks):
    return edits.build_proposal(baseline, hunks, list(baseline))


def bindings(baseline):
    return [{"path": path, "sha256": workspace.digest(data)} for path, data in baseline.items()]


@pytest.fixture
def files(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    baseline = {"one.py": b"# user's unsaved-to-git work\nvalue = 1\n",
                "two.py": b"result = 2\n", "dependency.txt": b"dependency version 3\n"}
    for path, data in baseline.items():
        (root / path).write_bytes(data)
    info = root.stat()
    grant = {"root": str(root), "device": str(info.st_dev), "inode": str(info.st_ino), "active": 1}
    return root, grant, baseline


def two_changes(baseline):
    return proposal(baseline, [{"path": "one.py", "before": "value = 1", "after": "value = 11"},
                               {"path": "two.py", "before": "result = 2", "after": "result = 22"}])["changes"]


def test_two_file_apply_and_undo_preserve_existing_dirty_bytes(files):
    root, grant, baseline = files
    result = proposal(baseline, [{"path": "one.py", "before": "value = 1", "after": "value = 11"},
                                 {"path": "two.py", "before": "result = 2", "after": "result = 22"}])
    assert result["state"] == "proposed"
    assert "-value = 1" in result["edits"][0]["diff"]
    assert "+value = 11" in result["edits"][0]["diff"]
    assert all((root / p).read_bytes() == data for p, data in baseline.items())
    applied = edits.apply_batch(grant, result["changes"], bindings(baseline))
    assert applied["state"] == "applied" and applied["written"] == ["one.py", "two.py"]
    assert (root / "one.py").read_bytes() == baseline["one.py"].replace(b"value = 1", b"value = 11")
    assert (root / "dependency.txt").read_bytes() == baseline["dependency.txt"]
    undone = edits.apply_batch(grant, result["changes"], bindings(baseline), undo=True)
    assert undone["state"] == "undone"
    assert all((root / p).read_bytes() == data for p, data in baseline.items())
    assert not list(root.glob(".riwaq-*.tmp"))


def test_bom_crlf_and_no_final_newline_preserved(tmp_path):
    original = codecs.BOM_UTF8 + "# عربي\r\nvalue = 1\r\nlast".encode("utf-8")
    result = proposal({"main.py": original}, [{"path": "main.py", "before": "value = 1\r\n", "after": "value = 2\r\n"}])
    data = base64.b64decode(result["changes"][0]["new_bytes_b64"])
    assert data == original.replace(b"value = 1", b"value = 2")
    assert data.startswith(codecs.BOM_UTF8) and not data.endswith(b"\n")
    assert "No newline at end of file" in result["edits"][0]["diff"]


def test_lf_hunk_does_not_silently_match_crlf():
    with pytest.raises(edits.CodingEditError, match="before_not_found"):
        proposal({"x.py": b"x = 1\r\ny = 2\r\n"}, [{"path": "x.py", "before": "x = 1\ny = 2\n", "after": "x = 3\n"}])


def test_multiple_hunks_use_original_coordinates():
    result = proposal({"x.txt": b"alpha beta gamma\n"}, [
        {"path": "x.txt", "before": "gamma", "after": "G"},
        {"path": "x.txt", "before": "alpha", "after": "alpha much longer"}])
    assert base64.b64decode(result["changes"][0]["new_bytes_b64"]) == b"alpha much longer beta G\n"
    with pytest.raises(edits.CodingEditError, match="before_not_found"):
        proposal({"x.txt": b"first"}, [{"path": "x.txt", "before": "first", "after": "second"},
                                         {"path": "x.txt", "before": "second", "after": "third"}])


@pytest.mark.parametrize("original,hunks,code", [
    (b"same same", [("same", "new")], "before_not_unique"),
    (b"aaa", [("aa", "x")], "before_not_unique"),
    (b"abcdef", [("abc", "x"), ("cde", "y")], "overlapping_hunks"),
    (b"abc", [("", "x")], "empty_before"),
    (b"abc", [("abc", "\ufeffx")], "invalid_hunk_text"),
    (b"abc", [("abc", "\x00")], "invalid_hunk_text"),
    (b"abc", [("abc", "\ud800")], "invalid_hunk_text"),
])
def test_reject_ambiguous_or_invalid_hunks(original, hunks, code):
    with pytest.raises(edits.CodingEditError, match=code):
        proposal({"x.py": original}, [{"path": "x.py", "before": a, "after": b} for a, b in hunks])


def test_noop_is_none_and_empty_after_does_not_delete_path(files):
    root, grant, baseline = files
    assert proposal(baseline, []) == {"state": "none", "edits": [], "changes": []}
    assert proposal({"x": b"x"}, [{"path": "x", "before": "x", "after": "x"}])["state"] == "none"
    result = proposal(baseline, [{"path": "two.py", "before": baseline["two.py"].decode(), "after": ""}])
    assert edits.apply_batch(grant, result["changes"], bindings(baseline))["state"] == "applied"
    assert (root / "two.py").exists() and (root / "two.py").read_bytes() == b""


@pytest.mark.parametrize("path", ["../x", "a/../../x", "/etc/passwd", "C:\\x.py", "x.py:stream", ".env",
                                 ".git/config", "node_modules/x.py", "secrets-prod.json", "key.pem", "x.py.",
                                 "x.py ", "a//x.py", "a/./x.py", "x\n.py", "x|other.py", "*.py", "CON.py",
                                 "password=private-value.txt", "sub/password=private-value.txt"])
def test_unsafe_paths_never_appear_in_error(path):
    with pytest.raises(edits.CodingEditError) as error:
        proposal({path: b"before"}, [{"path": path, "before": "before", "after": "after"}])
    assert error.value.path == ""
    assert path not in str(error.value)


def test_case_alias_and_unselected_creation_rejected():
    with pytest.raises(edits.CodingEditError, match="duplicate_path"):
        proposal({"a.py": b"x", "A.py": b"x"}, [])
    with pytest.raises(edits.CodingEditError, match="path_not_selected"):
        edits.build_proposal({"a.py": b"x"}, [{"path": "new.py", "before": "x", "after": "y"}], ["new.py"])
    with pytest.raises(edits.CodingEditError, match="path_not_selected"):
        edits.build_proposal({"a.py": b"x"}, [{"path": "a.py", "before": "x", "after": "y"}], [])


def test_backslash_paths_normalized_without_duplicate_alias():
    result = edits.build_proposal({"sub\\x.py": b"x"}, [{"path": "sub/x.py", "before": "x", "after": "y"}], ["sub/x.py"])
    assert result["changes"][0]["path"] == "sub/x.py"
    with pytest.raises(edits.CodingEditError, match="duplicate_path"):
        proposal({"sub/x.py": b"x", "sub\\x.py": b"x"}, [])


def test_counts_sizes_and_types_bounded():
    with pytest.raises(edits.CodingEditError, match="hunk_count_or_type"):
        proposal({"x": b"x"}, [{"path": "x", "before": "x", "after": "y"}] * 9)
    with pytest.raises(edits.CodingEditError, match="file_count_or_type"):
        proposal({f"x{i}": b"x" for i in range(13)}, [])
    with pytest.raises(edits.CodingEditError, match="file_size_or_type"):
        proposal({"x": b"x" * (workspace.MAX_FILE_BYTES + 1)}, [])
    with pytest.raises(edits.CodingEditError, match="file_size_or_type"):
        proposal({"x": "not bytes"}, [])
    with pytest.raises(edits.CodingEditError, match="invalid_utf8"):
        proposal({"x": b"\xff"}, [])
    with pytest.raises(edits.CodingEditError, match="invalid_hunk"):
        proposal({"x": b"x"}, [{"path": "x", "before": "x", "after": "y", "network": True}])


def test_secrets_in_any_baseline_or_result_reject_before_diff(monkeypatch):
    credential = "password = 'private-real-value'\n"
    def no_diff(*args):
        pytest.fail("No diff may be constructed before all content is checked")
    monkeypatch.setattr(edits, "_diff", no_diff)
    for baseline, hunks in [
        ({"x.py": credential.encode()}, [{"path": "x.py", "before": credential, "after": "safe"}]),
        ({"x.py": b"safe", "other.py": credential.encode()}, [{"path": "x.py", "before": "safe", "after": "changed"}]),
        ({"x.py": b"safe"}, [{"path": "x.py", "before": "safe", "after": credential}]),
    ]:
        with pytest.raises(edits.CodingEditError, match="potential_credentials") as exc:
            proposal(baseline, hunks)
        assert "private-real-value" not in str(exc.value)


@pytest.mark.parametrize("fault", ["missing", "raises", "malformed"])
def test_missing_or_failed_scanner_is_fail_closed(monkeypatch, fault):
    if fault == "missing":
        monkeypatch.delattr(coding_context, "scan_secrets")
    elif fault == "raises":
        def fail(_): raise RuntimeError("private-source-must-not-leak")
        monkeypatch.setattr(coding_context, "scan_secrets", fail)
    else:
        monkeypatch.setattr(coding_context, "scan_secrets", lambda _: None)
    with pytest.raises(edits.CodingEditError, match="secret_scanner_unavailable") as exc:
        proposal({"x": b"x"}, [])
    assert "private-source" not in str(exc.value)


def test_stale_dependency_preflights_every_file_before_any_write(files, monkeypatch):
    root, grant, baseline = files
    changes = two_changes(baseline)
    (root / "dependency.txt").write_bytes(b"changed externally")
    monkeypatch.setattr(workspace, "atomic_replace", lambda *args: pytest.fail("No write before complete preflight"))
    result = edits.apply_batch(grant, changes, bindings(baseline))
    assert result["state"] == "conflict" and result["written"] == []
    assert result["conflicts"] == [{"path": "dependency.txt", "reason": "binding_changed"}]
    assert (root / "one.py").read_bytes() == baseline["one.py"]


def test_apply_missing_binding_no_writes(files):
    root, grant, baseline = files
    result = edits.apply_batch(grant, two_changes(baseline), bindings({"one.py": baseline["one.py"]}))
    assert result["state"] == "conflict"
    assert result["conflicts"][0]["reason"] == "missing_or_mismatched_binding"
    assert all((root / p).read_bytes() == data for p, data in baseline.items())


def test_undo_ignores_changed_dependencies_and_skips_already_original(files):
    root, grant, baseline = files
    changes = two_changes(baseline)
    assert edits.apply_batch(grant, changes, bindings(baseline))["state"] == "applied"
    (root / "dependency.txt").write_bytes(b"new user dependency")
    (root / "one.py").write_bytes(baseline["one.py"])
    result = edits.apply_batch(grant, changes, [], undo=True)
    assert result["state"] == "undone" and result["written"] == ["two.py"] and result["skipped"] == ["one.py"]
    assert (root / "dependency.txt").read_bytes() == b"new user dependency"
    again = edits.apply_batch(grant, changes, [], undo=True)
    assert again["state"] == "undone" and again["written"] == []


def test_manual_edit_after_apply_prevents_entire_undo(files):
    root, grant, baseline = files
    changes = two_changes(baseline)
    assert edits.apply_batch(grant, changes, bindings(baseline))["state"] == "applied"
    applied_one = (root / "one.py").read_bytes()
    (root / "two.py").write_bytes(b"user later edit")
    result = edits.apply_batch(grant, changes, [], undo=True)
    assert result["state"] == "conflict" and result["written"] == []
    assert (root / "one.py").read_bytes() == applied_one
    assert (root / "two.py").read_bytes() == b"user later edit"


def test_partial_write_failure_rolls_back_without_losing_dirty_baseline(files, monkeypatch):
    root, grant, baseline = files
    real = workspace.atomic_replace
    calls = []
    def fail_second(grant, path, data, expected):
        calls.append(path)
        if path == "two.py": raise OSError("private system detail")
        return real(grant, path, data, expected)
    monkeypatch.setattr(workspace, "atomic_replace", fail_second)
    result = edits.apply_batch(grant, two_changes(baseline), bindings(baseline))
    assert result["state"] == "conflict" and result["written"] == [] and result["rolled_back"] == ["one.py"]
    assert calls == ["one.py", "two.py", "one.py"]
    assert all((root / p).read_bytes() == data for p, data in baseline.items())
    assert "private system" not in str(result)


def test_failure_after_actual_write_reconciles_that_file_too(files, monkeypatch):
    root, grant, baseline = files
    real = workspace.atomic_replace
    failed = False
    def fail_after(grant, path, data, expected):
        nonlocal failed
        real(grant, path, data, expected)
        if path == "two.py" and not failed:
            failed = True
            raise OSError("after replace")
    monkeypatch.setattr(workspace, "atomic_replace", fail_after)
    result = edits.apply_batch(grant, two_changes(baseline), bindings(baseline))
    assert result["state"] == "conflict" and result["rolled_back"] == ["two.py", "one.py"]
    assert all((root / p).read_bytes() == data for p, data in baseline.items())


def test_partial_undo_failure_restores_the_applied_state(files, monkeypatch):
    root, grant, baseline = files
    changes = two_changes(baseline)
    assert edits.apply_batch(grant, changes, bindings(baseline))["state"] == "applied"
    applied = {p: (root / p).read_bytes() for p in baseline}
    real = workspace.atomic_replace
    def fail_second(grant, path, data, expected):
        if path == "two.py": raise PermissionError()
        return real(grant, path, data, expected)
    monkeypatch.setattr(workspace, "atomic_replace", fail_second)
    result = edits.apply_batch(grant, changes, [], undo=True)
    assert result["state"] == "conflict" and result["written"] == []
    assert result["rolled_back"] == ["one.py"]
    assert all((root / p).read_bytes() == data for p, data in applied.items())


def test_rollback_preserves_external_edit_and_reports_recovery(files, monkeypatch):
    root, grant, baseline = files
    real = workspace.atomic_replace
    def racing_failure(grant, path, data, expected):
        if path == "two.py":
            (root / "one.py").write_bytes(b"user edit made after first write")
            raise OSError("second failed")
        return real(grant, path, data, expected)
    monkeypatch.setattr(workspace, "atomic_replace", racing_failure)
    result = edits.apply_batch(grant, two_changes(baseline), bindings(baseline))
    assert result["state"] == "recovery_required" and result["written"] == ["one.py"]
    assert (root / "one.py").read_bytes() == b"user edit made after first write"
    assert (root / "two.py").read_bytes() == baseline["two.py"]


def test_rollback_failure_preserves_recoverable_new_bytes(files, monkeypatch):
    root, grant, baseline = files
    real = workspace.atomic_replace
    count = 0
    def fail_after_first(*args):
        nonlocal count
        count += 1
        if count > 1: raise PermissionError()
        return real(*args)
    monkeypatch.setattr(workspace, "atomic_replace", fail_after_first)
    changes = two_changes(baseline)
    result = edits.apply_batch(grant, changes, bindings(baseline))
    assert result["state"] == "recovery_required" and result["written"] == ["one.py"]
    monkeypatch.setattr(workspace, "atomic_replace", real)
    recovery = edits.apply_batch(grant, changes, [], undo=True)
    assert recovery["state"] == "undone" and recovery["skipped"] == ["two.py"]
    assert all((root / p).read_bytes() == data for p, data in baseline.items())


@pytest.mark.parametrize("kind", ["hash", "bytes", "canonical", "extra", "duplicate", "path", "bom", "secret", "empty"])
def test_sealed_manifest_validation_prevents_writes(files, monkeypatch, kind):
    _, grant, baseline = files
    changes = copy.deepcopy(two_changes(baseline))
    if kind == "hash": changes[0]["new_sha"] = "0" * 64
    elif kind == "bytes": changes[0]["new_bytes_b64"] = "not base64!"
    elif kind == "canonical": changes[0]["new_bytes_b64"] += "\n"
    elif kind == "extra": changes[0]["command"] = "ignored?"
    elif kind == "duplicate": changes.append(copy.deepcopy(changes[0]))
    elif kind == "path": changes[0]["path"] = "../outside.py"
    elif kind == "empty": changes = []
    else:
        data = codecs.BOM_UTF8 + b"value = 2\n" if kind == "bom" else b"password = 'actual-private-value'\n"
        changes[0]["new_bytes_b64"] = base64.b64encode(data).decode()
        changes[0]["new_sha"] = workspace.digest(data)
    monkeypatch.setattr(workspace, "read_bytes", lambda *args: pytest.fail("Invalid manifest must not reach filesystem"))
    assert edits.apply_batch(grant, changes, bindings(baseline))["state"] == "conflict"


def test_hardlinked_file_is_refused(files):
    root, grant, baseline = files
    try:
        os.link(root / "one.py", root / "alias.py")
    except OSError:
        pytest.skip("Hardlink creation is not available on this test volume")
    result = edits.apply_batch(grant, two_changes(baseline), bindings(baseline))
    assert result["state"] == "conflict" and result["written"] == []
    assert (root / "two.py").read_bytes() == baseline["two.py"]
