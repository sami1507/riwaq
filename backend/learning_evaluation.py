"""Fixed, read-only evaluations of scoped rendering-rule candidates.

The caller loads a candidate and current baseline from authorized server storage.
Candidate data cannot provide cases, callbacks, commands, gates or permissions.
Passing these deterministic resolver checks is not model-quality certification.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import time

VERSION = "learning-candidate-evaluation-1"
REQUIRED_CASE_IDS = (
    "schema_boundary", "candidate_applies", "original_behavior_preserved",
    "task_boundary", "conversation_boundary", "explicit_current_override",
    "expiration_boundary", "scope_precedence", "rollback_restores_baseline",
    "objective_correctness", "read_only_replay",
)
CRITERIA = {"version": VERSION, "required_cases": REQUIRED_CASE_IDS,
            "critical_failure_allowance": 0, "blocked_allowance": 0,
            "model_calls": 0, "external_effects": 0, "max_rules": 100}
ROOT = Path(__file__).resolve().parents[1]


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _schema(row):
    from backend.learning import validate_rule
    if not isinstance(row, dict) or any(key in row for key in ("tests", "commands", "permissions", "criteria", "verified")):
        raise ValueError("candidate_authority_or_schema_invalid")
    if not isinstance(row.get("id"), str) or not row["id"] or type(row.get("version")) is not int or row["version"] < 1:
        raise ValueError("candidate_identity_invalid")
    if row.get("scope") not in {"conversation", "project"}:
        raise ValueError("candidate_scope_unsupported")
    if row["scope"] == "conversation" and (not isinstance(row.get("conversation_id"), str) or not row["conversation_id"]):
        raise ValueError("candidate_conversation_required")
    rule = row.get("rule")
    if not isinstance(rule, dict):
        raise ValueError("candidate_rule_invalid")
    kind = rule.get("kind")
    options = {"language": {"ar", "en", "he"}, "tone": {"formal", "friendly", "neutral"},
               "brevity": {"short", "standard", "detailed"}}
    if kind in options:
        if set(rule) != {"kind", "value"} or rule["value"] not in options[kind]:
            raise ValueError("candidate_rule_invalid")
    elif kind == "terminology":
        if set(rule) != {"kind", "source", "target"} or any(not isinstance(rule[k], str) or not 1 <= len(rule[k].strip()) <= 120 for k in ("source", "target")):
            raise ValueError("candidate_terminology_invalid")
        if rule["source"] == rule["target"]:
            raise ValueError("candidate_has_no_effect")
    else:
        raise ValueError("candidate_kind_unsupported")
    # Reuse the production content boundary as well as the fixed evaluator's
    # strict shape: evaluation is not an alternate route around admission.
    validate_rule(rule)
    app = row.get("applicability")
    if not isinstance(app, dict) or set(app) - {"task", "expires_at"} or app.get("task") not in {"any", "comparison", "translation", "math"}:
        raise ValueError("candidate_applicability_invalid")
    if app.get("expires_at") is not None:
        expiry = datetime.fromisoformat(app["expires_at"].replace("Z", "+00:00"))
        if expiry.tzinfo is None or expiry.utcoffset() is None:
            raise ValueError("candidate_expiry_timezone_required")


def _key(row):
    rule = row["rule"]
    return rule["kind"], rule.get("source")


def _ids(resolved):
    if not isinstance(resolved, dict) or not isinstance(resolved.get("rules"), list):
        raise ValueError("resolver_contract_invalid")
    return {row["id"] for row in resolved["rules"]}


def evaluate_candidate(candidate, baseline_rules, *, now=None, max_seconds=3, cancelled=None):
    """Produce fresh evidence using the production resolver and fixed cases.

    `cancelled` and budget are trusted caller controls, never candidate fields.
    The budget is checked between bounded deterministic cases; no untrusted code,
    model request, network action or replayed external side effect is executed.
    """
    from backend.learning import resolve_rules
    from backend.skill_release import current_source_fingerprint

    if type(max_seconds) not in (int, float) or not 0 < max_seconds <= 5:
        raise ValueError("evaluation_budget_invalid")
    if cancelled is not None and not callable(cancelled):
        raise ValueError("trusted_cancellation_callback_required")
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("evaluation_time_timezone_required")
    source_before = current_source_fingerprint(ROOT)
    # The budget bounds the deterministic cases below. Hashing the source tree is disk I/O with
    # its own file-count and size limits; counting it here blocked whole evaluations with
    # evaluation_budget_exhausted whenever the disk was busy (seen on Windows, 19 Nov 2026).
    started = time.monotonic()
    cases = []
    baseline = deepcopy(baseline_rules)
    proposed = deepcopy(candidate)
    def record(cid, passed, reason=None, status=None):
        cases.append({"id": cid, "status": status or ("passed" if passed else "failed"),
                      "critical": True, "reason": reason})
    try:
        if not isinstance(baseline, list) or len(baseline) > 100:
            raise ValueError("baseline_rule_limit")
        if len(json.dumps([proposed, baseline], ensure_ascii=False)) > 100000:
            raise ValueError("evaluation_payload_limit")
        _schema(proposed)
        for row in baseline:
            _schema(row)
        if proposed["id"] in {row["id"] for row in baseline}:
            raise ValueError("candidate_already_in_baseline")
        record("schema_boundary", True)
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        record("schema_boundary", False, str(exc) if isinstance(exc, ValueError) else "invalid_candidate")

    if cases[0]["status"] == "passed":
        conv = proposed.get("conversation_id") or "evaluation-conversation"
        task = proposed["applicability"]["task"]
        question = {"any": "اشرح الفكرة", "comparison": "قارن الخيارين", "translation": "ترجم العبارة", "math": "احسب 2+2"}[task]
        def resolve(rows, q=question, conversation=conv, selected_task=task):
            return resolve_rules(deepcopy(rows), q, conversation, task=selected_task, at=now)
        merged = baseline + [proposed]
        before = resolve(baseline)
        target_key = _key(proposed)
        def candidate_applies():
            return proposed["id"] in _ids(resolve(merged))
        def original_behavior():
            expected = {r["id"] for r in before["rules"] if _key(r) != target_key}
            return expected <= _ids(resolve(merged))
        def task_boundary():
            tasks = ("any", "comparison", "translation", "math")
            return all((proposed["id"] in _ids(resolve(merged, selected_task=t))) == (task == "any" or t == task) for t in tasks)
        def conversation_boundary():
            result = resolve(merged, conversation="evaluation-other-conversation")
            return (proposed["id"] in _ids(result)) == (proposed["scope"] == "project")
        def current_override():
            phrases = {"language": "اكتب بالعربية", "tone": "اكتب بأسلوب رسمي", "brevity": "اكتب جوابًا مختصرًا"}
            kind = proposed["rule"]["kind"]
            if kind == "terminology":
                # Explicit language should not erase an unrelated dictionary rule.
                return proposed["id"] in _ids(resolve(merged, q="اكتب بالعربية"))
            return proposed["id"] not in _ids(resolve(merged, q=phrases[kind]))
        def expiration():
            expired = deepcopy(proposed)
            expired["applicability"]["expires_at"] = now.isoformat()
            return proposed["id"] not in _ids(resolve(baseline + [expired]))
        def precedence():
            project = deepcopy(proposed)
            project.update(id="evaluation-project-rule", scope="project", conversation_id=None)
            conversation = deepcopy(proposed)
            conversation.update(id="evaluation-conversation-rule", scope="conversation", conversation_id=conv)
            selected = _ids(resolve([conversation, project]))
            reverse_selected = _ids(resolve([project, conversation]))
            return selected == reverse_selected == {conversation["id"]}
        def rollback():
            resolve(merged)
            return resolve(baseline) == before
        def objective():
            from backend.check_worker import Arithmetic
            _schema(proposed)
            rule = proposed["rule"]
            if rule["kind"] == "terminology":
                # Numeric substitutions are factual edits, not terminology.
                import re
                if any(re.fullmatch(r"[\d\s.+*/=()^−-]+", rule[k]) for k in ("source", "target")):
                    return False
            resolve(merged, q="احسب 2+2", selected_task="math")
            return Arithmetic("2+2").result()["exact"] == "4"
        def readonly():
            resolve(merged)
            return candidate == proposed and baseline_rules == baseline
        checks = (candidate_applies, original_behavior, task_boundary, conversation_boundary,
                  current_override, expiration, precedence, rollback, objective, readonly)
        for cid, check in zip(REQUIRED_CASE_IDS[1:], checks):
            if cancelled is not None and cancelled():
                record(cid, False, "evaluation_cancelled", "blocked")
            elif time.monotonic() - started >= max_seconds:
                record(cid, False, "evaluation_budget_exhausted", "blocked")
            else:
                try:
                    record(cid, check())
                except (ValueError, TypeError, KeyError, IndexError):
                    record(cid, False, "resolver_contract_failed")
    else:
        for cid in REQUIRED_CASE_IDS[1:]:
            record(cid, False, "candidate_schema_rejected", "blocked")
    source_after = current_source_fingerprint(ROOT)
    changed = source_before != source_after
    if changed:
        # Preserve the fixed case contract so the store can retain blocked
        # evidence rather than reject the explanatory report as malformed.
        cases[-1] = {"id": "read_only_replay", "status": "blocked", "critical": True,
                     "reason": "source_changed_during_evaluation"}
    statuses = {case["status"] for case in cases}
    status = "failed" if "failed" in statuses else "blocked" if "blocked" in statuses else "passed"
    return {"version": VERSION, "status": status, "cases": cases,
            "criteria_sha256": _digest(CRITERIA), "source_fingerprint": source_before,
            "source_changed_during_run": changed,
            "candidate_sha256": _digest(candidate), "baseline_sha256": _digest(baseline_rules),
            "environment": {"python": platform.python_version(), "platform": platform.platform()},
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "scope": "deterministic_rule_resolver_contract", "model_calls": 0,
            "external_effects_replayed": 0, "training_started": False,
            "limits": ["not_a_semantic_model_quality_evaluation", "live_authorization_checked_by_storage_layer",
                       "rollback_restores_resolver_input_not_external_effects"]}
