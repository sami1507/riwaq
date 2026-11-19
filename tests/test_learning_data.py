"""Synthetic witnesses for the offline training-data permission boundary."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import copy
import json

import pytest

from training.learning_data import (DataApproval, comparison_context_sha256,
                                    deletion_impact, record_sha256, validate_dataset, validate_examples)

NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


def fixture():
    rows = {
        "train": [{"id": "t1", "kind": "sft", "messages": [
            {"role": "user", "content": "احسب مجموع ثلاثة وخمسة"},
            {"role": "assistant", "content": "ثمانية"}]}],
        "validation": [{"id": "v1", "kind": "dpo", "prompt": [
            {"role": "user", "content": "Explain why a square has four equal sides."}],
            "chosen": "The defining sides are equal.", "rejected": "It has five sides."}],
        "test": [{"id": "e1", "kind": "sft", "messages": [
            {"role": "user", "content": "Name a unit used to measure duration."},
            {"role": "assistant", "content": "second"}]}],
    }
    approvals = {}
    for split, records in rows.items():
        for row in records:
            approvals[row["id"]] = DataApproval(
                record_sha256(row), "owner-a", "project-a", row["kind"],
                f"family-{split}", (f"source-{split}",), "rights:synthetic-author",
                "consent:approved-training", "review:synthetic-test-not-human",
                NOW - timedelta(days=1), NOW + timedelta(days=1), allowed_uses=("training",),
                target_verdict="correct", comparison_verdict="same_information_validated",
                comparison_context_sha256=comparison_context_sha256(row))
    return rows, approvals


def run(rows, approvals, **kwargs):
    return validate_dataset(rows, approvals=approvals, owner_id="owner-a",
                            project_id="project-a", revoked_sources=frozenset(), now=NOW, **kwargs)


def codes(result):
    return {error["code"] for error in result["errors"]}


def approve_changed(rows, approvals, split):
    row = rows[split][0]
    approvals[row["id"]] = replace(approvals[row["id"]], record_sha256=record_sha256(row))


def test_reviewed_sft_dpo_pass_preflight_without_launching_or_authorizing_training():
    rows, approvals = fixture()
    result = run(rows, approvals)
    assert result["status"] == "passed"
    assert result["counts"] == {"train": 1, "validation": 1, "test": 1}
    assert result["training_started"] is False
    assert result["training_authorized"] is False
    assert "ثمانية" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("field", ["verified", "training_permission", "redaction_reviewed", "tests", "commands"])
def test_uploaded_authority_flags_and_executable_material_cannot_self_approve(field):
    rows, approvals = fixture()
    rows["train"][0][field] = True
    approve_changed(rows, approvals, "train")
    assert "invalid_record_fields" in codes(run(rows, approvals))


def test_uploaded_approval_dict_is_not_trusted_receipt():
    rows, approvals = fixture()
    approvals["t1"] = vars(approvals["t1"])
    assert "trusted_approval_missing" in codes(run(rows, approvals))


@pytest.mark.parametrize("field,value", [("owner_id", "owner-b"), ("project_id", "project-b")])
def test_receipt_from_other_scope_blocks(field, value):
    rows, approvals = fixture()
    approvals["t1"] = replace(approvals["t1"], **{field: value})
    assert "approval_scope_mismatch" in codes(run(rows, approvals))


@pytest.mark.parametrize("mutation", ["target", "prompt", "reverse_dpo"])
def test_changed_text_or_reversed_preference_requires_new_review(mutation):
    rows, approvals = fixture()
    if mutation == "reverse_dpo":
        row = rows["validation"][0]
        row["chosen"], row["rejected"] = row["rejected"], row["chosen"]
    else:
        rows["train"][0]["messages"][1 if mutation == "target" else 0]["content"] += " changed"
    assert "approval_payload_mismatch" in codes(run(rows, approvals))


@pytest.mark.parametrize("change,expected", [
    ({"active": False}, "approval_revoked"),
    ({"expires_at": NOW}, "approval_time_invalid"),
    ({"reviewed_at": NOW + timedelta(seconds=1)}, "approval_time_invalid"),
    ({"reviewed_at": NOW.replace(tzinfo=None)}, "approval_time_invalid"),
    ({"rights_reference": ""}, "review_evidence_missing"),
    ({"review_reference": ""}, "review_evidence_missing"),
    ({"consent_reference": ""}, "review_evidence_missing"),
    ({"source_ids": ()}, "source_lineage_missing"),
])
def test_review_requires_current_consent_rights_lineage_and_target_review(change, expected):
    rows, approvals = fixture()
    approvals["t1"] = replace(approvals["t1"], **change)
    assert expected in codes(run(rows, approvals))


def test_family_and_source_split_leakage_are_separate_blocks():
    rows, approvals = fixture()
    approvals["e1"] = replace(approvals["e1"], family_id=approvals["t1"].family_id,
                              source_ids=approvals["v1"].source_ids)
    assert {"cross_split_family_leakage", "cross_split_source_leakage"} <= codes(run(rows, approvals))


def test_same_prompt_with_changed_answer_does_not_escape_leak_detection():
    rows, approvals = fixture()
    rows["test"][0]["messages"][0]["content"] = rows["train"][0]["messages"][0]["content"]
    approve_changed(rows, approvals, "test")
    assert "duplicate_prompt" in codes(run(rows, approvals))


def test_number_variants_are_not_independent_test_families():
    rows, approvals = fixture()
    rows["train"][0]["messages"][0]["content"] = "Compute the difference between 152 and 31."
    rows["test"][0]["messages"][0]["content"] = "Compute the difference between 804 and 20!"
    approve_changed(rows, approvals, "train")
    approve_changed(rows, approvals, "test")
    assert "cross_split_template_leakage" in codes(run(rows, approvals))


def test_long_near_duplicate_is_flagged_without_claiming_semantic_independence():
    rows, approvals = fixture()
    words = "This is an intentionally long source prompt about alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega"
    rows["train"][0]["messages"][0]["content"] = words
    rows["test"][0]["messages"][0]["content"] = words + " please"
    approve_changed(rows, approvals, "train")
    approve_changed(rows, approvals, "test")
    result = run(rows, approvals)
    assert "cross_split_near_duplicate" in codes(result)
    assert "lexical_similarity_not_semantic_independence" in result["limits"]


def test_deletion_quarantines_descendants_and_does_not_claim_weight_unlearning():
    rows, approvals = fixture()
    approvals["t1"] = replace(approvals["t1"], source_ids=("original-source", "derived-source"))
    revoked = frozenset({"original-source"})
    result = validate_dataset(rows, approvals=approvals, owner_id="owner-a", project_id="project-a",
                              revoked_sources=revoked, now=NOW)
    assert "source_deleted_or_consent_revoked" in codes(result)
    impact = deletion_impact(approvals, revoked)
    assert impact["affected_record_ids"] == ["t1"]
    assert impact["weights_unlearned"] is False


def test_dpo_targets_cannot_be_equal_after_whitespace_and_case_normalization():
    rows, approvals = fixture()
    row = rows["validation"][0]
    row["rejected"] = "  " + row["chosen"].upper() + "  "
    approve_changed(rows, approvals, "validation")
    assert "indistinguishable_preference_pair" in codes(run(rows, approvals))


def test_answer_only_permission_is_not_training_permission():
    rows, approvals = fixture()
    approvals["t1"] = replace(approvals["t1"], allowed_uses=("answer",))
    assert "training_consent_missing" in codes(run(rows, approvals))


@pytest.mark.parametrize("verdict", ["unverified", "incorrect", "style_preferred_but_wrong"])
def test_friendly_style_or_generated_target_does_not_outvote_objective_correctness(verdict):
    rows, approvals = fixture()
    approvals["v1"] = replace(approvals["v1"], target_verdict=verdict)
    assert "target_unverified_or_incorrect" in codes(run(rows, approvals))


@pytest.mark.parametrize("change", [
    {"comparison_verdict": "different_information"},
    {"comparison_verdict": "unresolved"},
    {"comparison_context_sha256": "f" * 64},
])
def test_dpo_comparability_needs_trusted_exact_context_review(change):
    rows, approvals = fixture()
    approvals["v1"] = replace(approvals["v1"], **change)
    assert "dpo_comparison_unverified" in codes(run(rows, approvals))


def test_withdrawal_identifies_trained_versions_without_claiming_weight_erasure():
    _, approvals = fixture()
    result = deletion_impact(approvals, frozenset({"source-train"}), trained_versions={
        "old-adapter-v1": ("source-train", "other"), "unrelated-v2": ("source-test",)})
    assert result["affected_model_versions"] == ["old-adapter-v1"]
    assert result["weights_unlearned"] is False


@pytest.mark.parametrize("case", ["missing_split", "empty_split", "too_many", "non_json"])
def test_dataset_boundaries_fail_before_processing(case):
    rows, approvals = fixture()
    if case == "missing_split": rows.pop("test")
    elif case == "empty_split": rows["test"] = []
    elif case == "too_many": rows["train"] *= 501
    else: rows["train"][0]["not_json"] = object()
    with pytest.raises(ValueError): run(rows, approvals)


def test_records_and_approvals_are_never_mutated():
    rows, approvals = fixture()
    before = copy.deepcopy(rows)
    run(rows, approvals)
    assert rows == before


def test_single_approved_export_is_a_draft_not_training_ready():
    rows, approvals = fixture()
    report = validate_examples(rows["train"], approvals=approvals, owner_id="owner-a",
                                project_id="project-a", revoked_sources=frozenset(), now=NOW)
    assert report["status"] == "passed"
    assert report["scope"] == "draft_examples"
    assert report["record_ids"] == ["t1"]
    assert report["training_ready"] is False
    assert report["training_authorized"] is False
    assert "ثمانية" not in json.dumps(report, ensure_ascii=False)


def test_draft_export_keeps_same_consent_and_hash_checks_as_dataset():
    rows, approvals = fixture()
    approvals["t1"] = replace(approvals["t1"], allowed_uses=("answer",))
    rows["train"][0]["messages"][-1]["content"] = "changed"
    report = validate_examples(rows["train"], approvals=approvals, owner_id="owner-a",
                                project_id="project-a", revoked_sources=frozenset(), now=NOW)
    assert {"training_consent_missing", "approval_payload_mismatch"} <= codes(report)


def test_legacy_cli_is_structural_only_and_does_not_self_authorize(tmp_path):
    from training.validate_data import validate
    paths = []
    for split in ("train", "validation", "test"):
        row = {"id": split, "source_group": split, "license": "unverified-claim",
               "training_permission": True, "permission_reference": "uploaded-claim",
               "redaction_reviewed": True, "messages": [
                   {"role": "user", "content": "Synthetic prompt " + split},
                   {"role": "assistant", "content": "Synthetic target " + split}]}
        path = tmp_path / (split + ".jsonl")
        path.write_text(json.dumps(row) + "\n")
        paths.append(path)
    result = validate(paths)
    assert result["status"] == "structural_only"
    assert result["requires_trusted_review"] is True
    assert result["training_authorized"] is False
