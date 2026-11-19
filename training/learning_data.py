"""Offline, bounded preflight for reviewed SFT/DPO records.

Approvals are supplied by trusted operator/server code, never deserialized from
an uploaded dataset. This module grants no training, publishing or access rights.
It does not read conversations, download a model, or change model weights.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping

VERSION = "learning-data-1"
SPLITS = ("train", "validation", "test")
MAX_RECORDS = 500
MAX_BYTES = 2_000_000


def record_sha256(record: dict) -> str:
    """Bind approval to the entire payload, including target ordering."""
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class DataApproval:
    """Trusted-side review receipt. An uploaded dict is deliberately not one.

    Callers must load this from authorized persisted review/consent records and
    recheck current access/revocation; constructing this type is not verification.
    """
    record_sha256: str
    owner_id: str
    project_id: str
    purpose: str
    family_id: str
    source_ids: tuple[str, ...]
    rights_reference: str
    consent_reference: str
    review_reference: str
    reviewed_at: datetime
    expires_at: datetime
    active: bool = True
    allowed_uses: tuple[str, ...] = ("answer",)
    target_verdict: str = "unverified"
    comparison_verdict: str | None = None
    comparison_context_sha256: str | None = None


def _error(errors, item_id, code):
    errors.append({"id": item_id, "code": code})


def _messages(value, *, target: bool) -> str:
    if not isinstance(value, list) or not 1 <= len(value) <= 24:
        raise ValueError("invalid_messages")
    for message in value:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise ValueError("invalid_message_fields")
        if message["role"] not in {"system", "user", "assistant"}:
            raise ValueError("invalid_message_role")
        content = message["content"]
        if not isinstance(content, str) or not content.strip() or len(content) > 10000:
            raise ValueError("invalid_message_content")
    if value[-1]["role"] != ("assistant" if target else "user"):
        raise ValueError("invalid_final_role")
    if not any(m["role"] == "user" for m in value[:-1] if target) and target:
        raise ValueError("missing_user_prompt")
    return "\n".join(m["content"] for m in (value[:-1] if target else value))


def _prompt(record):
    if not isinstance(record, dict):
        raise ValueError("invalid_record")
    kind = record.get("kind")
    keys = {"id", "kind", "messages"} if kind == "sft" else {"id", "kind", "prompt", "chosen", "rejected"}
    if kind not in {"sft", "dpo"} or set(record) != keys:
        raise ValueError("invalid_record_fields")
    if not isinstance(record["id"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", record["id"]):
        raise ValueError("invalid_record_id")
    if kind == "sft":
        return _messages(record["messages"], target=True)
    prompt = _messages(record["prompt"], target=False)
    for field in ("chosen", "rejected"):
        if not isinstance(record[field], str) or not record[field].strip() or len(record[field]) > 10000:
            raise ValueError("invalid_preference_target")
    if _normalized(record["chosen"]) == _normalized(record["rejected"]):
        raise ValueError("indistinguishable_preference_pair")
    return prompt


def _normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def comparison_context_sha256(record: dict) -> str:
    """Bind a DPO comparability review to its one shared, exact prompt/context."""
    return record_sha256({"prompt": record.get("prompt")})


def _template(text):
    text = re.sub(r"\d+(?:[.,]\d+)?", " NUMBER ", _normalized(text))
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def _shingles(text):
    words = _template(text).split()
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)}


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _validate_records(splits: Mapping, *, approvals: Mapping[str, DataApproval],
                     owner_id: str, project_id: str,
                     revoked_sources: frozenset[str], now: datetime,
                     split_names: tuple[str, ...]) -> dict:
    """Validate records and current trusted review receipts, without side effects.

    All three nonempty splits are required. Similarity is a conservative lexical
    leakage screen, not semantic proof; family labels come from trusted review.
    Every error blocks eligibility. No average can hide a permission violation.
    """
    if not _aware(now) or not isinstance(owner_id, str) or not owner_id or not isinstance(project_id, str) or not project_id:
        raise ValueError("trusted_scope_or_time_required")
    if not isinstance(approvals, Mapping) or not isinstance(revoked_sources, frozenset):
        raise ValueError("trusted_review_and_revocations_required")
    if not isinstance(splits, Mapping) or set(splits) != set(split_names):
        raise ValueError("three_splits_required")
    if any(not isinstance(splits[s], list) or not splits[s] for s in split_names):
        raise ValueError("nonempty_splits_required")
    if sum(len(splits[s]) for s in split_names) > MAX_RECORDS:
        raise ValueError("dataset_record_limit")
    try:
        raw = json.dumps(splits, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("dataset_json_required") from exc
    if len(raw) > MAX_BYTES:
        raise ValueError("dataset_byte_limit")
    errors = []
    ids, prompts, templates, families, sources = {}, {}, {}, {}, {}
    previous = []
    for split in split_names:
        for index, record in enumerate(splits[split]):
            item_id = f"{split}:{index + 1}"
            try:
                prompt = _prompt(record)
                item_id = record["id"]
            except ValueError as exc:
                _error(errors, item_id, str(exc))
                continue
            if item_id in ids:
                _error(errors, item_id, "duplicate_record_id")
            ids[item_id] = split
            approval = approvals.get(item_id)
            if not isinstance(approval, DataApproval):
                _error(errors, item_id, "trusted_approval_missing")
                continue
            if approval.owner_id != owner_id or approval.project_id != project_id:
                _error(errors, item_id, "approval_scope_mismatch")
            if approval.record_sha256 != record_sha256(record) or approval.purpose != record["kind"]:
                _error(errors, item_id, "approval_payload_mismatch")
            if approval.active is not True:
                _error(errors, item_id, "approval_revoked")
            if not isinstance(approval.allowed_uses, tuple) or "training" not in approval.allowed_uses:
                _error(errors, item_id, "training_consent_missing")
            if approval.target_verdict not in {"correct", "style_only_facts_preserved"}:
                _error(errors, item_id, "target_unverified_or_incorrect")
            if record["kind"] == "dpo" and (approval.comparison_verdict != "same_information_validated"
                    or approval.comparison_context_sha256 != comparison_context_sha256(record)):
                _error(errors, item_id, "dpo_comparison_unverified")
            if not _aware(approval.reviewed_at) or not _aware(approval.expires_at) or not approval.reviewed_at <= now < approval.expires_at:
                _error(errors, item_id, "approval_time_invalid")
            refs = (approval.rights_reference, approval.consent_reference, approval.review_reference, approval.family_id)
            if any(not isinstance(ref, str) or not ref.strip() or len(ref) > 200 for ref in refs):
                _error(errors, item_id, "review_evidence_missing")
            if not isinstance(approval.source_ids, tuple) or not 1 <= len(approval.source_ids) <= 32 or any(not isinstance(s, str) or not s or len(s) > 120 for s in approval.source_ids):
                _error(errors, item_id, "source_lineage_missing")
                continue
            if revoked_sources.intersection(approval.source_ids):
                _error(errors, item_id, "source_deleted_or_consent_revoked")
            if isinstance(approval.family_id, str):
                if approval.family_id in families and families[approval.family_id] != split:
                    _error(errors, item_id, "cross_split_family_leakage")
                families[approval.family_id] = split
            for source in approval.source_ids:
                if source in sources and sources[source] != split:
                    _error(errors, item_id, "cross_split_source_leakage")
                sources[source] = split
            normalized = _normalized(prompt)
            if normalized in prompts:
                _error(errors, item_id, "duplicate_prompt")
            prompts[normalized] = split
            template = _template(prompt)
            if template in templates and templates[template] != split:
                _error(errors, item_id, "cross_split_template_leakage")
            templates[template] = split
            shingles = _shingles(prompt)
            # At most 500 rows and 124,750 bounded comparisons. This reports
            # suspected leakage; it never certifies independent semantics.
            if len(shingles) >= 6:
                for other_split, other in previous:
                    if other_split != split and len(other) >= 6 and len(shingles & other) / len(shingles | other) >= 0.85:
                        _error(errors, item_id, "cross_split_near_duplicate")
                        break
            previous.append((split, shingles))
    return {"version": VERSION, "status": "blocked" if errors else "passed",
            "counts": {s: len(splits[s]) for s in split_names},
            "dataset_sha256": hashlib.sha256(raw).hexdigest(),
            "errors": errors, "training_started": False, "training_authorized": False,
            "scope": "offline_reviewed_data_preflight", "semantic_labels_human_verified": False,
            "limits": ["lexical_similarity_not_semantic_independence",
                       "server_must_recheck_consent_at_training_start",
                       "deletion_does_not_unlearn_existing_weights"]}


def validate_dataset(splits: Mapping, *, approvals: Mapping[str, DataApproval],
                     owner_id: str, project_id: str,
                     revoked_sources: frozenset[str], now: datetime) -> dict:
    """Full three-way split preflight; passing does not authorize training."""
    return _validate_records(splits, approvals=approvals, owner_id=owner_id,
                             project_id=project_id, revoked_sources=revoked_sources,
                             now=now, split_names=SPLITS)


def validate_examples(rows: list[dict], *, approvals: Mapping[str, DataApproval],
                      owner_id: str, project_id: str,
                      revoked_sources: frozenset[str], now: datetime) -> dict:
    """Validate explicitly exported drafts, without claiming split readiness.

    The report contains IDs and reasons only. The caller separately controls any
    authorized serialization of the actual examples and review provenance.
    """
    report = _validate_records({"draft": rows}, approvals=approvals, owner_id=owner_id,
                                project_id=project_id, revoked_sources=revoked_sources,
                                now=now, split_names=("draft",))
    report.update(scope="draft_examples", training_ready=False,
                  record_ids=[row["id"] for row in rows if isinstance(row, dict)
                              and isinstance(row.get("id"), str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", row["id"])])
    return report


def deletion_impact(approvals: Mapping[str, DataApproval], source_ids: frozenset[str], *,
                    trained_versions: Mapping[str, tuple[str, ...]] | None = None) -> dict:
    """List derived records to quarantine; do not claim weight unlearning."""
    if len(approvals) > MAX_RECORDS or not isinstance(source_ids, frozenset):
        raise ValueError("invalid_deletion_scope")
    affected = sorted(key for key, approval in approvals.items()
                      if isinstance(approval, DataApproval) and source_ids.intersection(approval.source_ids))
    versions = trained_versions or {}
    if not isinstance(versions, Mapping) or len(versions) > MAX_RECORDS:
        raise ValueError("invalid_training_lineage")
    if any(not isinstance(version, str) or not isinstance(lineage, tuple)
           or len(lineage) > MAX_RECORDS or any(not isinstance(source, str) for source in lineage)
           for version, lineage in versions.items()):
        raise ValueError("invalid_training_lineage")
    affected_versions = sorted(version for version, lineage in versions.items() if source_ids.intersection(lineage))
    return {"affected_record_ids": affected, "required_action": "quarantine_and_revalidate",
            "affected_model_versions": affected_versions,
            "weights_unlearned": False, "training_started": False}
