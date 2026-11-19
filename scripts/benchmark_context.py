"""Reproducible source-context minimization measurement; no model or network.

All corpus text below is synthetic, authored for this repository. This measures
retrieval of six known file references and characters sent as source context,
not answer correctness, model quality, token count, or user productivity.
"""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.knowledge import init_knowledge, make_chunks, retrieve  # noqa: E402

FIXTURE_VERSION = "riwaq-context-v1"
CORPUS = {
    "launch-ar.md": """# خطة إطلاق المنارة
موعد إطلاق المنارة هو 18 ديسمبر 2026.
المسؤول عن تنسيق إطلاق المنارة هو فريق التشغيل.
تجري مراجعة خطة الإطلاق يوم الثلاثاء.
نحتاج موافقة صاحب المشروع قبل النشر.
""",
    "budget-ar.md": """# ميزانية المعرض
ميزانية المعرض المعتمدة هي 48000 شيكل.
تتضمن ميزانية المعرض تجهيز القاعة والطباعة والترجمة.
يجب توثيق كل مصروف بإيصال في سجل المصروفات.
لم تعتمد أي زيادة على هذه الميزانية.
""",
    "backup-ar.md": """# سياسة النسخ الاحتياطي
فترة الاحتفاظ بالنسخ الاحتياطية هي 21 يوماً.
تتم تجربة الاستعادة كل أسبوع على قاعدة بيانات اختبارية.
تظل نسخة الاستعادة منفصلة عن النسخة المستخدمة يومياً.
لا توجد مزامنة تلقائية قبل تفعيلها من إعدادات المشروع.
""",
    "api-contract.md": """# Export API contract
The export batch maximum is 240 records per request.
Export responses include the request identifier and creation timestamp.
An export request with no records returns an empty list.
Clients must preserve the original order of exported records.
""",
    "accessibility.md": """# Accessibility acceptance criteria
The accessibility contrast target is 7:1 for normal text.
Keyboard navigation must reach every enabled interactive control.
Focus must remain visible when switching between panels.
Arabic content uses right-to-left layout while code uses left-to-right.
""",
    "support-policy.md": """# Support policy
The escalation response deadline is 6 hours during business hours.
Every escalation must include a case identifier and a brief description.
The support coordinator records the outcome in the case history.
No notification is sent before the user has approved sending it.
""",
    "design-notes.md": "\n".join(
        f"Design note {number}: The interface uses consistent spacing, clear boundaries, calm surfaces, readable labels, and explicit actions. This note describes visual treatment, not release facts."
        for number in range(1, 41)
    ),
    "meeting-notes-ar.md": "\n".join(
        f"ملاحظة الاجتماع {number}: ناقش الفريق تنظيم الملفات وتسمية المستندات وتحسين وضوح الواجهة. هذه ملاحظات تنظيمية لا تتضمن اعتماد قرارات مالية أو مواعيد نهائية."
        for number in range(1, 41)
    ),
}
QUERIES = [
    {"id": "ar-launch", "query": "ما موعد إطلاق المنارة؟", "expected_filename": "launch-ar.md"},
    {"id": "ar-budget", "query": "كم ميزانية المعرض المعتمدة؟", "expected_filename": "budget-ar.md"},
    {"id": "ar-backup", "query": "ما فترة الاحتفاظ بالنسخ الاحتياطية؟", "expected_filename": "backup-ar.md"},
    {"id": "en-export", "query": "What is the export batch maximum?", "expected_filename": "api-contract.md"},
    {"id": "en-contrast", "query": "What is the accessibility contrast target?", "expected_filename": "accessibility.md"},
    {"id": "en-response", "query": "What is the escalation response deadline?", "expected_filename": "support-policy.md"},
]


def run_benchmark():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,instructions TEXT,created_at TEXT)")
    conn.execute("INSERT INTO projects VALUES('fixture','Synthetic benchmark','','fixture')")
    init_knowledge(conn)
    for index, (filename, text) in enumerate(CORPUS.items()):
        file_id = f"file-{index}"
        chunks = make_chunks([(None, text)])
        conn.execute("INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)", (file_id, "fixture", filename, len(text.encode("utf-8")), hashlib.sha256(text.encode()).hexdigest(), len(chunks), 0, len(text), "fixture"))
        for chunk_index, chunk in enumerate(chunks):
            conn.execute("INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?)", (f"chunk-{index}-{chunk_index}", file_id, "fixture", None, chunk["line_start"], chunk["line_end"], chunk["text"]))
    # Baseline sends every complete file, including source labels.
    baseline = "\n\n".join(f"{filename}\n{text}" for filename, text in CORPUS.items())
    rows = []
    for case in QUERIES:
        chunks = retrieve(conn, "fixture", case["query"], limit=5)
        # Match the source excerpt serialization used by backend/app.py.
        minimized = "\n\n".join(f"[S{i + 1}] {ch['filename']} page={ch.get('page')} lines={ch['line_start']}-{ch['line_end']}\n{ch['text']}" for i, ch in enumerate(chunks))
        retrieved = list(dict.fromkeys(chunk["filename"] for chunk in chunks))
        rows.append({**case, "baseline_source_characters": len(baseline), "retrieved_source_characters": len(minimized), "source_character_reduction_percent": round(100 * (1 - len(minimized) / len(baseline)), 2), "retrieved_filenames": retrieved, "expected_source_found": case["expected_filename"] in retrieved, "retrieved_chunk_count": len(chunks)})
    conn.close()
    before = sum(row["baseline_source_characters"] for row in rows)
    after = sum(row["retrieved_source_characters"] for row in rows)
    return {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "fixture_version": FIXTURE_VERSION,
        "corpus_sha256": hashlib.sha256(json.dumps(CORPUS, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "corpus_files": len(CORPUS), "query_count": len(QUERIES), "retrieval_limit": 5,
        "method": "local-bm25", "network_calls": 0, "model_calls": 0,
        "metrics": {"expected_source_recall_at_5": sum(row["expected_source_found"] for row in rows) / len(rows), "total_baseline_source_characters": before, "total_retrieved_source_characters": after, "source_character_reduction_percent": round(100 * (1 - after / before), 2)},
        "queries": rows,
        "limits": ["Six hand-authored bilingual queries on eight synthetic files; not representative of real projects.", "Filename recall is not semantic answer correctness or citation entailment.", "Character counts are Unicode characters, not tokenizer tokens or costs.", "Only source context is measured; instructions, history, memory and user question are excluded.", "Lexical retrieval may miss paraphrases and cross-language queries; this is not a semantic RAG evaluation.", "No claims about human productivity, latency, or uniqueness of the feature."],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "context-receipt.json")
    args = parser.parse_args()
    report = run_benchmark()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "metrics": report["metrics"]}, ensure_ascii=False))
