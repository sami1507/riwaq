"""Compare BM25 and public hybrid retrieval on the same frozen public corpus.

Reads production sources but never indexes evaluation questions/answers. The
baseline is an independent in-memory SQLite copy of the exact candidate chunks.
This measures retrieval, not answer correctness or tool-permission enforcement.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import socket
import sqlite3
import statistics
import sys
import time
import tracemalloc
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import db, knowledge


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def corpus_fingerprint(rows):
    facts = [{key: row.get(key) for key in ("id", "source_id", "document_id", "version_id", "sha256", "text", "page", "line_start", "line_end", "url", "title", "version", "revision", "license", "language", "languages", "domain", "published_at", "modified_at", "fetched_at", "heading", "chunking")} for row in rows]
    packed = json.dumps(sorted(facts, key=lambda r: r["id"]), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(packed.encode()).hexdigest()


@contextmanager
def deny_external_connections():
    """Python networking API guard, not an OS firewall or a sandbox boundary.

    Windows Proactor connections bypass socket.connect, so intercept the active
    asyncio loop as well. The Ollama daemon is a separate, unaffected process.
    """
    calls = {"loopback_attempts": 0, "external_blocked": [], "asyncio_guard": False}
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    original_create_connection = loop.create_connection if loop else None
    original_sock_connect = loop.sock_connect if loop else None

    def allowed(host):
        if isinstance(host, bytes):
            host = host.decode("ascii", errors="replace")
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except (ValueError, TypeError):
            return False

    def check(host):
        if not allowed(host):
            calls["external_blocked"].append(str(host))
            raise OSError("Evaluation forbids non-loopback network access")
        calls["loopback_attempts"] += 1

    def connect(instance, address):
        if isinstance(address, tuple):
            check(address[0])
        return original_connect(instance, address)

    def connect_ex(instance, address):
        if isinstance(address, tuple):
            check(address[0])
        return original_connect_ex(instance, address)

    async def create_connection(protocol_factory, host=None, port=None, *args, **kwargs):
        if host is not None:
            check(host)
        elif kwargs.get("sock") is not None:
            check(kwargs["sock"].getpeername()[0])
        else:
            raise OSError("Evaluation cannot validate an unspecified TCP destination")
        return await original_create_connection(protocol_factory, host, port, *args, **kwargs)

    async def sock_connect(instance, address):
        check(address[0])
        return await original_sock_connect(instance, address)

    def getaddrinfo(host, *args, **kwargs):
        check(host)
        return original_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo
    if loop:
        loop.create_connection = create_connection
        loop.sock_connect = sock_connect
        calls["asyncio_guard"] = True
    try:
        yield calls
    finally:
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex
        socket.getaddrinfo = original_getaddrinfo
        if loop:
            loop.create_connection = original_create_connection
            loop.sock_connect = original_sock_connect


def baseline_database(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,instructions TEXT,created_at TEXT); CREATE TABLE events(id TEXT,project_id TEXT,kind TEXT,summary TEXT,created_at TEXT); INSERT INTO projects VALUES('evaluation-corpus','Frozen evaluation corpus','','');")
    knowledge.init_knowledge(conn)
    groups = {}
    for row in rows:
        groups.setdefault(row["document_id"], []).append(row)
    for document_id, chunks in groups.items():
        first = chunks[0]
        text = "\n".join(c["text"] for c in chunks)
        filename = first.get("filename") or first.get("title") or document_id
        conn.execute("INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)", (document_id, "evaluation-corpus", filename, len(text.encode()), hashlib.sha256(text.encode()).hexdigest(), len(chunks), max((c.get("page") or 0 for c in chunks), default=0), len(text), ""))
        conn.executemany("INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?)", [(r["id"], document_id, "evaluation-corpus", r.get("page"), r["line_start"], r["line_end"], r["text"]) for r in chunks])
    conn.commit()
    return conn


def evidence_match(hit, evidence):
    return (hit.get("source_id") == evidence["source_id"]
            and hit.get("url") == evidence["document_url"]
            and str(hit.get("version")) == str(evidence["source_version"])
            and hit.get("sha256") == evidence["original_sha256"]
            and normalized(evidence["needle"]) in normalized(hit.get("text", "")))


def validate_dataset(dataset, rows):
    if dataset.get("schema_version") != 1:
        raise ValueError("Unknown evaluation schema")
    if dataset.get("status") != "frozen":
        raise ValueError("Freeze reviewed evidence and split before evaluation")
    cases = dataset.get("cases", [])
    if len(cases) != 40 or Counter(c["category"] for c in cases) != Counter({"programming": 20, "general": 10, "absent": 5, "safety": 5}):
        raise ValueError("Evaluation requires exactly 20 programming, 10 general, 5 absent and 5 safety cases")
    if len({c["id"] for c in cases}) != 40:
        raise ValueError("Duplicate case IDs")
    if not {"ar", "en"} <= {c["language"] for c in cases}:
        raise ValueError("Arabic and English cases are required")
    if Counter(c["split"] for c in cases) != Counter({"tune": 10, "heldout": 30}):
        raise ValueError("The frozen split requires 10 tuning and 30 heldout cases")
    for case in cases:
        if case["split"] not in {"tune", "heldout"}:
            raise ValueError(f"Unknown split: {case['id']}")
        if not case.get("query") or not case.get("gold_answer"):
            raise ValueError(f"Missing reviewed question/gold answer: {case['id']}")
        if case["category"] in {"programming", "general"}:
            if not case.get("evidence"):
                raise ValueError(f"Missing exact source evidence: {case['id']}")
            for evidence in case["evidence"]:
                if not any(evidence_match(row, evidence) for row in rows):
                    raise ValueError(f"Gold evidence does not occur in an active current corpus chunk: {case['id']}")
        elif case.get("evidence"):
            raise ValueError(f"Absent/safety cases must not imply factual source evidence: {case['id']}")


def check_integrity(hit, originals):
    expected = originals.get(hit.get("id"))
    if expected is None:
        return ["unknown or unauthorized chunk ID"]
    return [key for key in ("text", "page", "line_start", "line_end", "source_id", "document_id", "url", "version_id", "sha256", "version", "revision", "license", "title", "language", "languages", "domain", "published_at", "modified_at", "fetched_at", "heading", "chunking") if hit.get(key) != expected.get(key)]


def audit_originals(conn, rows, public_module):
    """Replay extraction from retained source bytes before accepting gold spans."""
    versions = {}
    for row in rows:
        version_id = row["version_id"]
        if version_id not in versions:
            stored = conn.execute("SELECT v.original,v.sha256,v.extracted_text,v.content_type,v.chunking,s.data FROM public_versions v JOIN public_documents d ON d.id=v.document_id JOIN public_sources s ON s.id=d.source_id WHERE v.id=?", (version_id,)).fetchone()
            if not stored or hashlib.sha256(stored["original"]).hexdigest() != stored["sha256"]:
                raise ValueError(f"Stored original hash failed: {version_id}")
            source = json.loads(stored["data"])
            pages = public_module.extract_public_isolated(stored["original"], source, row["url"], stored["content_type"], extractor=json.loads(stored["chunking"]).get("extractor", "legacy-v1"))
            if "\n\n".join(text for _, text in pages) != stored["extracted_text"]:
                raise ValueError(f"Stored extraction differs from source replay (or parser changed): {version_id}")
            policy = json.loads(stored["chunking"])
            if policy["algorithm"] == "lines-v1":
                chunks = knowledge.make_chunks(pages)
            elif policy["algorithm"] == "heading-lines-v2":
                source["chunking"] = {key: value for key, value in policy.items() if key != "algorithm"}
                chunks = public_module.chunk_public(pages, source, row["url"], stored["content_type"])
            else:
                raise ValueError(f"Unknown persisted chunk algorithm: {policy['algorithm']}")
            valid = {(chunk["page"], chunk["line_start"], chunk["line_end"], chunk["text"], chunk.get("heading")) for chunk in chunks}
            versions[version_id] = (stored["sha256"], valid)
        digest, spans = versions[version_id]
        if row["sha256"] != digest or (row["page"], row["line_start"], row["line_end"], row["text"], row.get("heading")) not in spans:
            raise ValueError(f"Chunk location/content does not match its retained original: {row['id']}")
    return {"original_versions_verified": len(versions), "chunk_spans_verified": len(rows), "method": "SHA256 of retained bytes plus deterministic extraction/chunk replay; not a universal PDF parser accuracy claim"}


def metrics_for(case, hits, originals):
    integrity = [{"id": hit.get("id"), "mismatches": errors} for hit in hits if (errors := check_integrity(hit, originals))]
    result = {"returned": len(hits), "citation_integrity_errors": integrity}
    if case["category"] in {"programming", "general"}:
        rank = next((i for i, hit in enumerate(hits, 1) if any(evidence_match(hit, evidence) for evidence in case["evidence"])), None)
        result.update({"evidence_rank": rank, "hit_at_1": rank == 1, "hit_at_5": rank is not None, "reciprocal_rank_at_5": 1 / rank if rank else 0})
    elif case["category"] == "absent":
        result["retrieval_abstained"] = not hits
    else:
        result["safety_status"] = "not_scored_by_retrieval; requires separate permission and real-generation checks"
    return result


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def process_memory():
    if os.name != "nt":
        return {"available": False, "reason": "Windows process counters only in this implementation"}
    try:
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise OSError(ctypes.get_last_error())
        return {"available": True, "working_set_bytes": counters.WorkingSetSize, "peak_working_set_bytes": counters.PeakWorkingSetSize, "scope": "This Python process only, including corpus/baseline; excludes Ollama and GPU memory"}
    except (OSError, AttributeError):
        return {"available": False, "reason": "Process memory counters unavailable"}


def summarize(results, method):
    answerable = [r[method] for r in results if r["category"] in {"programming", "general"}]
    absent = [r[method] for r in results if r["category"] == "absent"]
    timings = [r[method]["seconds"] for r in results]
    return {
        "answerable_cases": len(answerable),
        "hit_at_1": sum(r["hit_at_1"] for r in answerable) / len(answerable) if answerable else None,
        "hit_at_5": sum(r["hit_at_5"] for r in answerable) / len(answerable) if answerable else None,
        "mrr_at_5": statistics.mean(r["reciprocal_rank_at_5"] for r in answerable) if answerable else None,
        "absent_cases": len(absent),
        "retrieval_abstention_rate": sum(r["retrieval_abstained"] for r in absent) / len(absent) if absent else None,
        "citation_integrity_failures": sum(len(r[method]["citation_integrity_errors"]) for r in results),
        "latency_seconds_p50": statistics.median(timings) if timings else None,
        "latency_seconds_p95": percentile(timings, .95),
        "first_query_seconds": timings[0] if timings else None,
        "note": "Hit@5 requires the expected source AND exact reviewed evidence within a returned chunk; retrieval is not generated-answer accuracy.",
    }


async def evaluate(args):
    from backend import public_knowledge
    dataset_path = Path(args.dataset).resolve(strict=True)
    dataset_bytes = dataset_path.read_bytes()
    dataset = json.loads(dataset_bytes)
    with db.get_db() as conn:
        rows = [dict(row) for row in public_knowledge.corpus_rows(conn)]
        original_audit = audit_originals(conn, rows, public_knowledge)
    if not rows:
        raise ValueError("Import verified public sources before evaluation; no fabricated corpus is substituted")
    validate_dataset(dataset, rows)
    fingerprint = corpus_fingerprint(rows)
    if dataset.get("corpus_sha256") != fingerprint:
        raise ValueError("Current corpus differs from the reviewed frozen dataset; create a new evaluation version before comparing")
    text_multiplicity = Counter(row["text"] for row in rows)
    maximum_multiplicity = max(text_multiplicity.values())
    # The unchanged private BM25 API returns at most ten rows. With at most
    # two copies of any text, those ten necessarily contain its top five unique
    # texts (or all matches if fewer than ten). Never silently truncate unfairly.
    if maximum_multiplicity > 2:
        raise ValueError("Exact-text multiplicity exceeds two; BM25's ten-result cap cannot guarantee five unique results for a fair comparison")
    originals = {r["id"]: r for r in rows}
    baseline = baseline_database(rows)
    memory_before = process_memory()
    selected = [c for c in dataset["cases"] if args.split == "all" or c["split"] == args.split]
    results = []
    tracemalloc.start()
    try:
        with deny_external_connections() as fence:
            try:
                await asyncio.wait_for(asyncio.open_connection("203.0.113.1", 443), timeout=1)
            except OSError as exc:
                if "Evaluation forbids non-loopback network access" not in str(exc):
                    raise RuntimeError("External-destination probe was not rejected by the evaluation guard") from exc
                fence["external_probe_blocked"] = True
            else:
                raise RuntimeError("Evaluation guard allowed a non-loopback destination")
            embedding = await public_knowledge.embedding_info()
            with db.get_db() as conn:
                vector_rows = conn.execute("SELECT chunk_id,dimensions FROM public_embeddings WHERE model=? AND digest=?", (embedding.get("model"), embedding.get("digest"))).fetchall() if embedding.get("available") else []
            indexed_ids = {r["chunk_id"] for r in vector_rows if r["chunk_id"] in originals}
            dimensions = sorted({r["dimensions"] for r in vector_rows if r["chunk_id"] in originals})
            for case in selected:
                record = {key: case[key] for key in ("id", "category", "language", "split", "query", "gold_answer")}
                started = time.perf_counter()
                base_hits = knowledge.retrieve(baseline, "evaluation-corpus", case["query"], 10)
                base_hits = [originals[r["id"]] | r for r in base_hits]
                base_hits = public_knowledge.unique_ranked(base_hits, 5)
                duration = time.perf_counter() - started
                record["bm25"] = metrics_for(case, base_hits, originals) | {"seconds": round(duration, 6), "hits": base_hits}
                started = time.perf_counter()
                hybrid_hits = await public_knowledge.retrieve_public(case["query"], limit=5, filters=None)
                duration = time.perf_counter() - started
                if len(hybrid_hits) > 5:
                    raise RuntimeError("Retriever returned more than the requested top five candidates")
                record["hybrid"] = metrics_for(case, hybrid_hits, originals) | {"seconds": round(duration, 6), "hits": hybrid_hits}
                record["hybrid"]["retrieval_methods"] = sorted({hit.get("retrieval_method", "unknown") for hit in hybrid_hits})
                results.append(record)
                print(json.dumps({"case": case["id"], "split": case["split"], "bm25_rank": record["bm25"].get("evidence_rank"), "hybrid_rank": record["hybrid"].get("evidence_rank"), "hybrid_seconds": round(duration, 3)}), flush=True)
            _, peak = tracemalloc.get_traced_memory()
            fence["verified"] = bool(fence.get("asyncio_guard") and fence.get("external_probe_blocked") and fence["loopback_attempts"] > 0)
            fence["unexpected_external_attempts"] = [host for host in fence["external_blocked"] if host != "203.0.113.1"]
            print(json.dumps({"network_fence_verified": fence["verified"], "loopback_guard_checks": fence["loopback_attempts"], "external_probe_blocked": fence.get("external_probe_blocked")}), flush=True)
            if not fence["verified"] or fence["unexpected_external_attempts"]:
                raise RuntimeError("Networking API guard was not fully verified or unexpected external destinations were attempted")
        with db.get_db() as conn:
            after = [dict(row) for row in public_knowledge.corpus_rows(conn)]
        if corpus_fingerprint(after) != fingerprint:
            raise RuntimeError("Corpus changed during evaluation; do not compare these results")
        source_metadata = {}
        for row in rows:
            source_metadata[row["document_id"]] = {key: row.get(key) for key in ("document_id", "source_id", "url", "title", "version", "version_id", "sha256", "license", "language", "domain")}
        report = {
            "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
            "corpus_sha256": fingerprint,
            "split": args.split,
            "cases": len(results),
            "corpus_chunks": len(rows),
            "corpus_documents": len(source_metadata),
            "original_evidence_audit": original_audit,
            "same_corpus_for_both_methods": True,
            "duplicate_postprocessing": {"method": "Same exact-text unique_ranked helper for both methods, retaining first-ranked original provenance", "maximum_text_multiplicity": maximum_multiplicity, "unique_texts": len(text_multiplicity), "bm25_candidate_count": 10, "proof": "At most two occurrences per text guarantees BM25 top10 contains its top5 unique texts; evaluator rejects larger multiplicities"},
            "embedding": embedding,
            "embedded_current_chunks": len(indexed_ids),
            "embedding_coverage": len(indexed_ids) / len(rows),
            "embedding_dimensions": dimensions,
            "hybrid_query_count": sum("hybrid-rrf" in r["hybrid"]["retrieval_methods"] for r in results),
            "lexical_fallback_query_count": sum("bm25" in r["hybrid"]["retrieval_methods"] for r in results),
            "empty_hybrid_result_query_count": sum(not r["hybrid"]["hits"] for r in results),
            "comparison_status": "hybrid_verified" if embedding.get("available") and len(indexed_ids) == len(rows) and not any("bm25" in r["hybrid"]["retrieval_methods"] for r in results) else "partial_embedding_coverage_or_lexical_fallback",
            "production_index_writes": False,
            "generation_tested": False,
            "safety_cases": "Queries recorded; retrieval results alone do not establish security. See separate tests/review.",
            "offline_scope": "Non-loopback destinations denied through Python socket/DNS and active asyncio connection APIs; not OS-level network isolation. Machine and Ollama daemon were not disconnected.",
            "network_fence": fence,
            "fence_verified": fence["verified"],
            "peak_python_allocated_bytes": peak,
            "memory_scope": "tracemalloc Python allocation peak during retrieval after corpus/baseline setup; excludes existing allocations, SQLite/native memory, Ollama and GPU memory",
            "process_memory_before_queries": memory_before,
            "process_memory_after_queries": process_memory(),
            "database_bytes": (db.data_dir() / "riwaq.sqlite3").stat().st_size,
            "timing_note": "Measured with tracemalloc instrumentation; first hybrid query may include model cold start. Not an uninstrumented performance benchmark.",
            "summary": {method: summarize(results, method) for method in ("bm25", "hybrid")},
            "by_split": {split: {method: summarize([r for r in results if r["split"] == split], method) for method in ("bm25", "hybrid")} for split in ("tune", "heldout")},
            "by_language": {language: {method: summarize([r for r in results if r["language"] == language], method) for method in ("bm25", "hybrid")} for language in sorted({r["language"] for r in results})},
            "by_category": {category: {method: summarize([r for r in results if r["category"] == category], method) for method in ("bm25", "hybrid")} for category in sorted({r["category"] for r in results})},
            "documents": list(source_metadata.values()),
            "results": results,
        }
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"report": str(output), "summary": report["summary"]}, ensure_ascii=False), flush=True)
        if any(report["summary"][m]["citation_integrity_failures"] for m in ("bm25", "hybrid")):
            raise SystemExit("Citation integrity failed; inspect report before using results")
    finally:
        tracemalloc.stop()
        baseline.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(ROOT / "evaluation" / "knowledge-eval.json"))
    parser.add_argument("--split", choices=("tune", "heldout", "all"), default="heldout")
    parser.add_argument("--output", default=str(ROOT / "reports" / "knowledge-evaluation.json"))
    args = parser.parse_args()
    asyncio.run(evaluate(args))


if __name__ == "__main__":
    main()
