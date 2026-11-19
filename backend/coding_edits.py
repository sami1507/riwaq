"""Exact UTF-8 edit proposals and guarded, reversible multi-file writes.

No source is executed. The caller owns project authorization, grant revalidation,
the durable operation journal and serialization. Hashes are integrity checks, not
authorization. workspace.atomic_replace checks hashes before replacing a file;
it is not an OS-level compare-and-swap against unrelated external writers.
"""
from __future__ import annotations

import base64
import binascii
import codecs
import difflib
import importlib
import re

from backend import workspace

MAX_HUNKS = 8
MAX_FILES = 12
SHA256 = re.compile(r"[a-f0-9]{64}\Z")


class CodingEditError(ValueError):
    """Stable codes only: never include source text or an arbitrary exception."""

    def __init__(self, code: str, path: str = ""):
        self.code = code
        self.path = path
        super().__init__(code)


def _scanner():
    try:
        scan = getattr(importlib.import_module("backend.coding_context"), "scan_secrets")
        if not callable(scan):
            raise TypeError()
        return scan
    except Exception:
        raise CodingEditError("secret_scanner_unavailable") from None


def _scan(scan, text: str, path: str = ""):
    try:
        findings = scan(text)
        if not isinstance(findings, list):
            raise TypeError()
    except Exception:
        raise CodingEditError("secret_scanner_unavailable", path) from None
    if findings:
        raise CodingEditError("potential_credentials", path)


def _path(value, scan) -> str:
    try:
        if not isinstance(value, str) or any(c in value for c in '<>"|?*'):
            raise ValueError()
        value.encode("utf-8", errors="strict")
        path = "/".join(workspace.relative_parts(value))
    except Exception:
        raise CodingEditError("invalid_path") from None
    _scan(scan, path)  # A credential embedded in a filename is also not disclosed.
    for part in path.split("/"):
        _scan(scan, part)
        if "." in part:
            _scan(scan, part.rsplit(".", 1)[0])
    return path


def _text(data, path: str = "") -> str:
    if not isinstance(data, bytes) or len(data) > workspace.MAX_FILE_BYTES:
        raise CodingEditError("file_size_or_type", path)
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeError:
        raise CodingEditError("invalid_utf8", path) from None
    if "\x00" in text:
        raise CodingEditError("binary_content", path)
    return text


def _snippet(value, path: str) -> bytes:
    if not isinstance(value, str) or "\x00" in value or "\ufeff" in value:
        raise CodingEditError("invalid_hunk_text", path)
    try:
        result = value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise CodingEditError("invalid_hunk_text", path) from None
    if len(result) > workspace.MAX_FILE_BYTES:
        raise CodingEditError("hunk_size_limit", path)
    return result


def _mapping(values, scan, *, contents: bool):
    if not isinstance(values, (dict if contents else list)) or len(values) > MAX_FILES:
        raise CodingEditError("file_count_or_type")
    result, identities = {}, set()
    for raw in values:
        path = _path(raw, scan)
        identity = path.casefold()
        if identity in identities:
            raise CodingEditError("duplicate_path", path)
        identities.add(identity)
        result[path] = values[raw] if contents else None
    return result


def _diff(path: str, old: str, new: str) -> str:
    # Keep missing-final-newline visible in the review, not as merged +/- lines.
    result = []
    for line in difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                     fromfile="a/" + path, tofile="b/" + path):
        result.append(line)
        if not line.endswith(("\n", "\r")):
            result.append("\n\\ No newline at end of file\n")
    return "".join(result)


def build_proposal(baseline: dict[str, bytes], edits: list[dict], allowed_paths: list[str]) -> dict:
    """Match each hunk exactly once in the ORIGINAL bytes; no fuzzy/newline repair.

    All hunks use baseline coordinates. Overlaps and hunks matching only a prior
    replacement are rejected. The original UTF-8 BOM and untouched bytes survive.
    """
    scan = _scanner()
    original = _mapping(baseline, scan, contents=True)
    allowed = _mapping(allowed_paths, scan, contents=False)
    if not isinstance(edits, list) or len(edits) > MAX_HUNKS:
        raise CodingEditError("hunk_count_or_type")
    texts = {}
    for path, data in original.items():
        texts[path] = _text(data, path)
        _scan(scan, texts[path], path)
    ranges: dict[str, list[tuple[int, int, bytes]]] = {}
    for item in edits:
        if not isinstance(item, dict) or set(item) != {"path", "before", "after"}:
            raise CodingEditError("invalid_hunk")
        path = _path(item["path"], scan)
        if path not in original or path not in allowed:
            raise CodingEditError("path_not_selected", path)
        before, after = _snippet(item["before"], path), _snippet(item["after"], path)
        if not before:
            raise CodingEditError("empty_before", path)
        data = original[path]
        bom = len(codecs.BOM_UTF8) if data.startswith(codecs.BOM_UTF8) else 0
        start = data.find(before, bom)
        if start < 0:
            raise CodingEditError("before_not_found", path)
        if data.find(before, start + 1) >= 0:
            raise CodingEditError("before_not_unique", path)
        end = start + len(before)
        group = ranges.setdefault(path, [])
        if any(start < stop and prior < end for prior, stop, _ in group):
            raise CodingEditError("overlapping_hunks", path)
        group.append((start, end, after))
    prepared = []
    for path, group in ranges.items():
        data = original[path]
        for start, end, after in sorted(group, reverse=True):
            data = data[:start] + after + data[end:]
        text = _text(data, path)
        if data.startswith(codecs.BOM_UTF8) != original[path].startswith(codecs.BOM_UTF8):
            raise CodingEditError("bom_changed", path)
        _scan(scan, text, path)
        if data != original[path]:
            prepared.append((path, data, text))
    # Only construct diffs after EVERY file has passed the secret/validity checks.
    public, changes = [], []
    for path, data, text in prepared:
        hashes = {"original_sha": workspace.digest(original[path]), "new_sha": workspace.digest(data)}
        public.append({"path": path, **hashes, "diff": _diff(path, texts[path], text)})
        changes.append({"path": path, **hashes,
                        "original_bytes_b64": base64.b64encode(original[path]).decode("ascii"),
                        "new_bytes_b64": base64.b64encode(data).decode("ascii")})
    return {"state": "proposed" if changes else "none", "edits": public, "changes": changes}


def _unpack(value, path: str) -> bytes:
    if not isinstance(value, str) or len(value) > 4 * ((workspace.MAX_FILE_BYTES + 2) // 3):
        raise CodingEditError("invalid_bytes", path)
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise CodingEditError("invalid_bytes", path) from None
    if base64.b64encode(data).decode("ascii") != value:
        raise CodingEditError("noncanonical_bytes", path)
    return data


def _hash(value, path: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise CodingEditError("invalid_hash", path)
    return value


def _changes(values, scan):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_FILES:
        raise CodingEditError("change_count_or_type")
    result, identities = [], set()
    for item in values:
        if not isinstance(item, dict) or set(item) != {"path", "original_bytes_b64", "new_bytes_b64", "original_sha", "new_sha"}:
            raise CodingEditError("invalid_change")
        path = _path(item["path"], scan)
        if path.casefold() in identities:
            raise CodingEditError("duplicate_path", path)
        identities.add(path.casefold())
        row = {"path": path}
        for prefix in ("original", "new"):
            data = _unpack(item[prefix + "_bytes_b64"], path)
            text = _text(data, path)
            _scan(scan, text, path)
            sha = _hash(item[prefix + "_sha"], path)
            if workspace.digest(data) != sha:
                raise CodingEditError("bytes_hash_mismatch", path)
            row[prefix + "_bytes"], row[prefix + "_sha"] = data, sha
        if row["original_bytes"].startswith(codecs.BOM_UTF8) != row["new_bytes"].startswith(codecs.BOM_UTF8):
            raise CodingEditError("bom_changed", path)
        if row["original_sha"] == row["new_sha"]:
            raise CodingEditError("unchanged_file", path)
        result.append(row)
    return result


def _bindings(values, scan):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_FILES:
        raise CodingEditError("binding_count_or_type")
    result, identities = {}, set()
    for item in values:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise CodingEditError("invalid_binding")
        path = _path(item["path"], scan)
        if path.casefold() in identities:
            raise CodingEditError("duplicate_binding", path)
        identities.add(path.casefold())
        result[path] = _hash(item["sha256"], path)
    return result


def _receipt(state, conflicts=None, written=None, *, rolled_back=None, skipped=None):
    return {"state": state, "conflicts": conflicts or [], "written": written or [],
            "rolled_back": rolled_back or [], "skipped": skipped or []}


def apply_batch(grant, changes: list[dict], bindings: list[dict], undo: bool = False) -> dict:
    """Caller must hold its operation lock and freshly authorize the grant.

    Apply checks ALL context bindings before any write. Undo checks edited files
    only and accepts already-original files for explicit crash recovery. A partial
    failure is rolled back only while bytes still match this operation's write.
    There is no implicit retry, creation, deletion, or journal replay here.
    """
    try:
        if type(undo) is not bool:
            raise CodingEditError("invalid_operation")
        scan = _scanner()
        rows = _changes(changes, scan)
        expected = {} if undo else _bindings(bindings, scan)
        if not undo:
            for row in rows:
                if expected.get(row["path"]) != row["original_sha"]:
                    raise CodingEditError("missing_or_mismatched_binding", row["path"])
    except CodingEditError as exc:
        return _receipt("conflict", [{"path": exc.path, "reason": exc.code}])

    conflicts, skipped, pending = [], [], []
    if not undo:
        for path, sha in expected.items():
            try:
                actual = workspace.digest(workspace.read_bytes(grant, path))
            except Exception:
                conflicts.append({"path": path, "reason": "binding_unreadable"})
                continue
            if actual != sha:
                conflicts.append({"path": path, "reason": "binding_changed"})
        pending = rows
    else:
        for row in rows:
            path = row["path"]
            try:
                actual = workspace.digest(workspace.read_bytes(grant, path))
            except Exception:
                conflicts.append({"path": path, "reason": "file_unreadable"})
                continue
            if actual == row["original_sha"]:
                skipped.append(path)
            elif actual == row["new_sha"]:
                pending.append(row)
            else:
                conflicts.append({"path": path, "reason": "edited_file_changed"})
    if conflicts:
        return _receipt("conflict", conflicts, skipped=skipped)

    source, target = ("new", "original") if undo else ("original", "new")
    attempted, completed = [], []
    for row in pending:
        attempted.append(row)
        try:
            workspace.atomic_replace(grant, row["path"], row[target + "_bytes"], row[source + "_sha"])
            completed.append(row["path"])
        except Exception:
            conflicts = [{"path": row["path"], "reason": "write_failed"}]
            rolled_back, unresolved = [], []
            for prior in reversed(attempted):
                path = prior["path"]
                try:
                    current = workspace.digest(workspace.read_bytes(grant, path))
                    if current == prior[source + "_sha"]:
                        continue
                    if current != prior[target + "_sha"]:
                        # A failed write may coincide with an unrelated edit. It
                        # is preserved, and uncertain state requires review.
                        raise CodingEditError("rollback_conflict", path)
                    workspace.atomic_replace(grant, path, prior[source + "_bytes"], prior[target + "_sha"])
                    rolled_back.append(path)
                except Exception:
                    unresolved.append(path)
                    conflicts.append({"path": path, "reason": "rollback_not_safe"})
            return _receipt("recovery_required" if unresolved else "conflict", conflicts,
                            unresolved, rolled_back=rolled_back, skipped=skipped)
    return _receipt("undone" if undo else "applied", written=completed, skipped=skipped)
