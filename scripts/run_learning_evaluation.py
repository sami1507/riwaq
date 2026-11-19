"""Fresh local evidence for forty public learning acceptance scenarios.

Runs only reviewed pytest modules. Fixtures and candidates supply no test names,
commands, expected pass flags, or authority. Full-scenario gaps remain visible.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.skill_release import current_source_fingerprint

DATASET = ROOT / "evaluation/learning/LEARNING_EVAL_CASES.json"
SAMPLE = ROOT / "evaluation/learning/independent-sample.json"
STATUSES = ("passed", "failed", "partial", "blocked", "skipped", "not_applicable")
MODULES = ("tests/test_learning_core.py", "tests/test_learning_api.py",
           "tests/test_learning_verification.py", "tests/test_learning_evaluation.py",
           "tests/test_learning_data.py", "tests/test_learning_review.py")


def binding(module, test, full=False, level="component", remaining=""):
    return {"module": module, "test": test, "full_criterion": full,
            "level": level, "remaining": remaining}


# Reviewed coverage, never loaded from candidate/user data. A passing related
# test with full_criterion=False remains partial. API/store entries are added
# only after inspecting the actual assertions.
BINDINGS = {
    "LEARN-003": [binding("test_learning_verification", "test_style_and_new_details_are_not_factual_errors", remaining="Semantic response rewriting and downstream facts are not graded here.")],
    "LEARN-004": [binding("test_learning_verification", "test_revised_input_without_source_is_requirement_change", remaining="Recomputation proves revised math input; arbitrary new document requirements are not executed.")],
    "LEARN-006": [binding("test_learning_verification", "test_actual_fixed_worker_statistics_and_domain_are_preserved", level="actual_fixed_worker", remaining="Scalar recomputation retains zero; all dependent saved charts/files are not rebuilt.")],
    "LEARN-007": [binding("test_learning_verification", "test_actual_fixed_worker_disputes_false_assertion_and_recomputes_new_input", level="actual_fixed_worker", remaining="Exact arithmetic is verified; feedback-to-candidate rejection is tested separately at API.")],
    "LEARN-008": [binding("test_learning_verification", "test_source_quote_only_certifies_occurrence_not_truth_or_authority", remaining="Quote occurrence is checked; temporal fact-version adoption/as-of answering is not added.")],
    "LEARN-014": [binding("test_learning_evaluation", "test_seeded_current_instruction_override_failure_blocks_gate", remaining="Resolver override is tested; persistent next-session defaults are tested separately.")],
    "LEARN-016": [binding("test_learning_verification", "test_extraction_cause_requires_recorded_span_and_original_correct_math", level="contract_simulation", remaining="Recorded span diagnosis is simulated; no new OCR image extraction or full artifact regeneration.")],
    "LEARN-017": [binding("test_learning_verification", "test_operational_checker_failures_never_create_bad_reasoning_sample", level="contract_simulation", remaining="Failure classification and no false training label; provider retry path is unchanged.")],
    "LEARN-021": [binding("test_learning_verification", "test_source_injection_is_data_without_executable_permission", level="contract_simulation", remaining="Source content is not authority; complete quarantined feedback flow tested separately.")],
    "LEARN-026": [binding("test_learning_evaluation", "test_seeded_neighbor_regression_blocks_gate", True, remaining="Bounded deterministic resolver cases only; no semantic model benchmark.")],
    "LEARN-027": [binding("test_learning_data", "test_friendly_style_or_generated_target_does_not_outvote_objective_correctness", True, remaining="Trusted correctness verdict gate, not automatic unrestricted factual grading."),
                  binding("test_learning_evaluation", "test_correctness_failure_is_not_hidden_by_other_passing_style_cases", True)],
    "LEARN-028": [binding("test_learning_data", "test_family_and_source_split_leakage_are_separate_blocks", True),
                  binding("test_learning_data", "test_long_near_duplicate_is_flagged_without_claiming_semantic_independence", True)],
    "LEARN-029": [binding("test_learning_evaluation", "test_readonly_shadow_never_runs_a_command_or_external_request", True, remaining="Only pure rendering-rule replay is supported; external workflows are not replayed.")],
    "LEARN-030": [binding("test_learning_evaluation", "test_rollback_checks_actual_resolver_state_instead_of_textual_success", remaining="Rollback gate is checked; no automatic measured post-release threshold monitor.")],
    "LEARN-031": [binding("test_learning_data", "test_answer_only_permission_is_not_training_permission", remaining="Offline preflight boundary; actual export/storage consent paths tested separately.")],
    "LEARN-032": [binding("test_learning_data", "test_uploaded_approval_dict_is_not_trusted_receipt", remaining="Trusted receipt boundary and SFT draft validity; actual feedback export tested separately.")],
    "LEARN-033": [binding("test_learning_data", "test_dpo_comparability_needs_trusted_exact_context_review", True),
                  binding("test_learning_data", "test_changed_text_or_reversed_preference_requires_new_review", True,
                          remaining="Offline DPO preflight; no DPO optimization or model quality claim.")],
    "LEARN-034": [binding("test_learning_data", "test_single_approved_export_is_a_draft_not_training_ready", remaining="A draft is not a dataset or training job. Historical local QLoRA environment exists; no new job/dependency check is claimed.")],
    "LEARN-035": [binding("test_learning_data", "test_withdrawal_identifies_trained_versions_without_claiming_weight_erasure", remaining="Offline lineage identifies affected model versions; removable persisted descendants tested separately.")],
    "LEARN-038": [binding("test_learning_evaluation", "test_cancel_preserves_partial_evidence_and_blocks_activation", True),
                  binding("test_learning_evaluation", "test_time_budget_stops_without_claiming_missing_case_success", True,
                          remaining="Between-case budget for bounded local resolver evaluation; zero provider/model cost.")],
    "LEARN-039": [binding("test_learning_evaluation", "test_candidate_cannot_supply_tests_commands_or_gate_authority", True),
                  binding("test_learning_evaluation", "test_criteria_are_bound_and_no_candidate_setting_can_change_them", True)],
}

# These bindings were reviewed against the actual SQLite/API assertions. Passing
# is scoped to that exercised contract, never an inferred model-quality verdict.
BINDINGS.update({
    "LEARN-001": [binding("test_learning_api", "test_feedback_unknown_and_network_replay_no_invented_truth", True, "authenticated_api_sqlite")],
    "LEARN-002": [binding("test_learning_core", "test_weak_signal_never_directly_adopts_rule", True, "sqlite_lifecycle",
                          "Negative/regenerate/abandon events stay weak signals; no implicit user-behavior collection is claimed.")],
    "LEARN-005": [binding("test_learning_core", "test_parallel_same_key_and_cas_have_one_winner", True, "concurrent_sqlite"),
                  binding("test_learning_api", "test_feedback_unknown_and_network_replay_no_invented_truth", True, "authenticated_api_sqlite")],
    "LEARN-007": [binding("test_learning_api", "test_wrong_correction_is_disputed_with_actual_worker", True, "authenticated_api_actual_math_worker",
                          "Ordinary exact arithmetic and blocked false-rule adoption; no arbitrary factual or model-style grading.")],
    "LEARN-009": [binding("test_learning_evaluation", "test_valid_scoped_rendering_rules_pass_fixed_contracts", remaining="Formal-tone selection is tested; semantic output preference quality is not graded."),
                  binding("test_learning_core", "test_project_memory_is_existing_versioned_record_and_rollback_disables", level="sqlite_lifecycle")],
    "LEARN-010": [binding("test_learning_core", "test_current_target_mutation_blocks_and_redacts_read_and_replay", level="sqlite_lifecycle",
                          remaining="Exact old answer versions are protected; this test does not reproduce every editable-file lineage.")],
    "LEARN-011": [binding("test_learning_core", "test_conversation_lesson_uses_no_memory_and_current_instruction_wins", True, "sqlite_lifecycle"),
                  binding("test_learning_review", "test_conversation_only_feedback_cannot_migrate_to_another_conversation", True, "sqlite_review"),
                  binding("test_learning_core", "test_default_consent_does_not_export_and_withdrawal_marks_weights_unchanged", True, "sqlite_lifecycle")],
    "LEARN-013": [binding("test_learning_core", "test_identical_term_has_independent_project_meanings", True, "two_project_sqlite",
                          "Two synthetic projects under the local owner; not an invented hosted multi-tenant service.")],
    "LEARN-014": [binding("test_learning_evaluation", "test_fixed_boundaries_cover_scoped_and_task_specific_rules", True),
                  binding("test_learning_evaluation", "test_seeded_current_instruction_override_failure_blocks_gate", True)],
    "LEARN-015": [binding("test_learning_core", "test_deleted_feedback_tombstone_prevents_stale_activation", True, "sqlite_lifecycle"),
                  binding("test_learning_api", "test_revoked_lesson_cannot_finish_an_inflight_answer", True, "authenticated_api_with_provider_stub",
                          "Actual storage and stream completion guard; generation is an explicitly labelled provider stub.")],
    "LEARN-018": [binding("test_learning_review", "test_source_change_after_verification_before_commit_is_rejected", level="sqlite_review",
                          remaining="Stale verification cannot commit; automatic stale search-index repair is not implemented in this slice.")],
    "LEARN-020": [binding("test_learning_api", "test_storage_failure_is_not_success", level="authenticated_api_sqlite_fault",
                          remaining="API returns503 and preserves empty store; UI failure presentation is separate browser evidence.")],
    "LEARN-021": [binding("test_learning_verification", "test_source_injection_is_data_without_executable_permission", True, "contract_simulation"),
                  binding("test_learning_core", "test_typed_rule_has_no_factual_or_authority_payload", True, "schema_boundary",
                          "Fixed source-verification and typed-rule boundary; no arbitrary source-driven action executor.")],
    "LEARN-022": [binding("test_learning_api", "test_wrong_correction_is_disputed_with_actual_worker", level="authenticated_api_actual_math_worker",
                          remaining="Checked arithmetic cannot be overruled; repeated same-source influence has no separate aggregation experiment.")],
    "LEARN-023": [binding("test_learning_verification", "test_source_quote_only_certifies_occurrence_not_truth_or_authority", level="contract_simulation",
                          remaining="Quote occurrence does not settle truth; multi-reviewer conflict adjudication/quarantine is not implemented.")],
    "LEARN-024": [binding("test_learning_api", "test_ownership_authority_and_server_only_reports", True, "authenticated_api_sqlite"),
                  binding("test_learning_review", "test_stored_verified_status_cannot_replace_nested_target_binding", True, "sqlite_review")],
    "LEARN-025": [binding("test_learning_api", "test_ownership_authority_and_server_only_reports", True, "authenticated_api_sqlite",
                          "Foreign owner blocked from records, context retrieval and training export; local owner/project model, not hosted multi-tenancy."),
                  binding("test_learning_core", "test_unauthorized_project_and_revoked_session_cannot_read", True, "sqlite_lifecycle")],
    "LEARN-031": [binding("test_learning_api", "test_actual_training_draft_requires_consent_and_verifier_then_withdraws", True, "authenticated_api_actual_math_worker"),
                  binding("test_learning_data", "test_answer_only_permission_is_not_training_permission", True)],
    "LEARN-032": [binding("test_learning_api", "test_actual_training_draft_requires_consent_and_verifier_then_withdraws", True, "authenticated_api_actual_math_worker"),
                  binding("test_learning_data", "test_uploaded_approval_dict_is_not_trusted_receipt", True),
                  binding("test_learning_data", "test_friendly_style_or_generated_target_does_not_outvote_objective_correctness", True,
                          remaining="One authorized verified SFT draft and rejected unverified targets; not training-ready data or trained weights.")],
    "LEARN-035": [binding("test_learning_api", "test_actual_training_draft_requires_consent_and_verifier_then_withdraws", True, "authenticated_api_actual_math_worker"),
                  binding("test_learning_core", "test_default_consent_does_not_export_and_withdrawal_marks_weights_unchanged", True, "sqlite_lifecycle"),
                  binding("test_learning_data", "test_withdrawal_identifies_trained_versions_without_claiming_weight_erasure", True,
                          remaining="Synthetic registered model lineage and removable descendants; existing trained weights are explicitly unchanged.")],
    "LEARN-036": [binding("test_learning_api", "test_candidate_edit_requires_retest_and_detects_old_version", level="authenticated_api_sqlite",
                          remaining="Stale update conflicts; true parallel updates of the candidate itself need a separate witness."),
                  binding("test_learning_core", "test_parallel_same_key_and_cas_have_one_winner", level="concurrent_sqlite")],
    "LEARN-037": [binding("test_learning_core", "test_idempotency_is_one_event_and_changed_payload_conflicts", True, "persisted_commit_lost_ack_simulation",
                          "Real SQLite commit followed by replay; no external training job is supported or created.")],
    "LEARN-040": [binding("test_learning_api", "test_adopt_context_explicit_override_and_rollback_through_chat", level="authenticated_api_sqlite_provider_stub",
                          remaining="Actual lifecycle and chat context contract; browser interaction and generated-answer semantic improvement are separate and not inferred.")],
})


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_binding():
    return {"application": current_source_fingerprint(ROOT),
            "training_preflight": sha(ROOT / "training/learning_data.py"),
            "legacy_training_validator": sha(ROOT / "training/validate_data.py")}


def load_dataset():
    value = json.loads(DATASET.read_text(encoding="utf-8"))
    expected = [f"LEARN-{i:03}" for i in range(1, 41)]
    if [row.get("id") for row in value.get("cases", [])] != expected:
        raise ValueError("learning_dataset_must_contain_exactly_forty_ids")
    if value.get("claims_holdout") is not False:
        raise ValueError("public_cases_are_not_holdout")
    return value


def read_junit(path):
    found = {}
    if not path.exists():
        return found
    for row in ET.parse(path).getroot().iter("testcase"):
        module = row.get("classname", "").split(".")[-1]
        name = row.get("name", "").split("[")[0]
        status = "failed" if row.find("failure") is not None or row.find("error") is not None else "skipped" if row.find("skipped") is not None else "passed"
        found.setdefault((module, name), []).append(status)
    return found


def assess_case(case, results):
    links = BINDINGS.get(case["id"], [])
    evidence = []
    for link in links:
        observed = results.get((link["module"], link["test"]), [])
        state = "blocked" if not observed else "failed" if "failed" in observed else "skipped" if "skipped" in observed else "passed"
        evidence.append(dict(link, status=state, parameter_cases=len(observed),
                             node=f'tests/{link["module"]}.py::{link["test"]}'))
    states = {entry["status"] for entry in evidence}
    if "failed" in states:
        status = "failed"
    elif not links or "blocked" in states or "skipped" in states:
        status = "partial" if "passed" in states else "blocked"
    else:
        status = "passed" if all(entry["full_criterion"] for entry in evidence) else "partial"
    remaining = list(dict.fromkeys(entry["remaining"] for entry in evidence if entry["remaining"]))
    if not links:
        remaining = ["No reviewed executable full-scenario binding in this run; capability is not inferred from a related test."]
    if case["id"] == "LEARN-012":
        remaining = ["Personal memory write/injection remains blocked by the existing phase-8 security gate; project persistence is not personal-memory acceptance."]
    return {"id": case["id"], "status": status, "input_event": case["input_event"],
            "acceptance": case["acceptance"], "evidence": evidence, "remaining": remaining}


def independent_sample():
    from backend.learning import resolve_rules
    freeze = json.loads(SAMPLE.with_name("independent-sample.freeze.json").read_text())
    if sha(SAMPLE) != freeze["sha256"]:
        return {"status": "blocked", "reason": "frozen_sample_changed", "cases": []}
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    cases = []
    for case in sample["cases"]:
        started = time.monotonic()
        try:
            result = resolve_rules([case["rule"]], case["question"], case["conversation_id"],
                                   task=case["task"], at=datetime.fromisoformat(case["at"]))
            selected = case["rule"]["id"] in {rule["id"] for rule in result["rules"]}
            status = "passed" if selected == case["expected_selected"] else "failed"
            reason = None
        except (ValueError, TypeError, KeyError):
            status, reason = "failed", "resolver_contract_failure"
        cases.append({"id": case["id"], "status": status, "reason": reason,
                      "seconds": round(time.monotonic() - started, 6)})
    return {"status": "passed" if all(row["status"] == "passed" for row in cases) else "failed",
            "sha256": freeze["sha256"], "frozen_at": freeze["frozen_at"],
            "scope": "separate_engineering_samples_not_blinded_human_evaluation",
            "cases": cases}


def write_markdown(report, path):
    labels = {"passed": "ناجح", "failed": "فاشل", "partial": "جزئي", "blocked": "متعذر", "skipped": "متخطى", "not_applicable": "غير منطبق"}
    text = ["# نتائج التعلم والتغذية الراجعة — أربعون حالة", "",
            "هذه حالات قبول معلنة. النجاح مقيد بنطاق الفحص المذكور؛ الجزئي لا يعني اكتمال المسار.", "",
            f'الحالة العامة: `{report["status"]}`. النتائج: `{json.dumps(report["totals"], ensure_ascii=False)}`.',
            f'بصمة التطبيق: `{report["source"]["application"]}`. تغير المصدر أثناء التشغيل: `{report["source_changed_during_run"]}`.',
            "لم يُنفذ تدريب أو تنزيل أو نشر. تجربة QLoRA التاريخية ورفض مرشحها محفوظان.", "",
            "| الحالة | النتيجة | الدليل | النطاق أو العائق |", "|---|---|---|---|"]
    for row in report["cases"]:
        nodes = "; ".join("`" + item["node"] + "`" for item in row["evidence"]) or "—"
        limits = "; ".join(row["remaining"]) or "Within the reviewed local test scope."
        text.append(f'| {row["id"]} | {labels[row["status"]]} | {nodes} | {limits.replace("|", "/")} |')
    text.extend(["", "النتائج الخام ونسخ البيئة وأمر الاختبار محفوظة في `LEARNING_RESULTS.json`؛ مخرجات pytest في `pytest.txt` و`tests.xml`.",
                 "العينة الإضافية منفصلة هندسيًا ومجمدة ببصمة، وليست اختبارًا بشريًا معمى أو ضمان تعميم.", ""])
    path.write_text("\n".join(text), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--development", action="store_true",
                        help="Do not open the frozen additional samples while implementation is changing.")
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    allowed = (ROOT / "reports/learning-20261116/evaluation").resolve()
    if not output.is_relative_to(allowed) or output == allowed or output.exists():
        raise ValueError("new_learning_report_subdirectory_required")
    if any(parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()) for parent in (output, *output.parents)):
        raise ValueError("report_link_not_allowed")
    output.mkdir(parents=True)
    dataset = load_dataset()
    before = source_binding()
    start = time.monotonic()
    selected = [name for name in MODULES if (ROOT / name).is_file()]
    command = [sys.executable, "-m", "pytest", "-q", *selected, "--junitxml", str(output / "tests.xml")]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    timed_out = False
    with (output / "pytest.txt").open("w", encoding="utf-8") as handle:
        try:
            process = subprocess.run(command, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                     timeout=240, check=False)
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            timed_out, returncode = True, None
    observed = read_junit(output / "tests.xml")
    cases = [assess_case(case, observed) for case in dataset["cases"]]
    extra = ({"status": "not_run", "reason": "protected_until_final_source_freeze", "cases": []}
             if args.development else independent_sample())
    after = source_binding()
    changed = before != after
    totals = {status: sum(row["status"] == status for row in cases) for status in STATUSES}
    status = "blocked" if changed or timed_out else "failed" if returncode != 0 or totals["failed"] or extra["status"] == "failed" else "partial" if totals["partial"] or totals["blocked"] or extra["status"] != "passed" else "passed"
    report = {"version": "learning-acceptance-report-1", "status": status,
              "at": datetime.now(timezone.utc).isoformat(), "source": before,
              "source_after": after, "source_changed_during_run": changed,
              "dataset_sha256": sha(DATASET), "cases": cases, "totals": totals,
              "independent_sample": extra, "execution": {"command": command, "returncode": returncode,
              "timed_out": timed_out, "seconds": round(time.monotonic() - start, 6),
              "python": platform.python_version(), "platform": platform.platform(),
              "selected_modules": selected, "missing_modules": [name for name in MODULES if name not in selected]},
              "training_started": False, "release_approved": False,
              "limits": ["No semantic model quality claim", "No personal-memory gate override",
                         "No new model weights or paid/provider requests", "API/worker simulations are labelled in their test scopes"]}
    (output / "LEARNING_RESULTS.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(report, output / "LEARNING_RESULTS.md")
    manifest = {p.name: sha(p) for p in output.iterdir() if p.is_file()}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"status": status, "totals": totals, "independent": extra["status"], "output": str(output)}, ensure_ascii=False))
    return 0 if status == "passed" else 1 if status == "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
