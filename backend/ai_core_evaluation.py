"""Fresh, bounded evidence for the user's 100 AI-core scenarios.

The old contract suite supplies partial component evidence, never semantic
certification of a different input. No uploaded report, shell command, or model
claim can become a passing case. Full-scenario bindings must be reviewed here.
"""
from __future__ import annotations
import ast
import hashlib
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
STATUSES = ('passed', 'failed', 'partial', 'blocked', 'skipped', 'not_applicable')
IDS = tuple(f'AI-{group:02}-T{case:02}' for group in range(1, 21) for case in range(1, 6))
DATASET = ROOT / 'evaluation/ai_core/AI_CORE_EVAL_CASES.json'

# These are reviewed test selectors, not executable strings read from data.
# All legacy bindings cover a component contract. They do not execute the full
# user's scenario and therefore cannot turn its outcome into "passed".
_GROUPS = {
 1: [('test_skills', 'test_selected_file_auto_read_and_exact_slide_source_mapping'), ('test_skill_routing_refinement', 'test_read_only_cannot_override_with_explicit_writer'), ('test_skill_refinement_workflows', 'test_independent_output_survives_blocked_specialist'), ('test_skill_batch05_registry', 'test_conflict_policy_preserves_user_language_and_local_processing'), ('test_skill_routing_refinement', 'test_status_or_unbound_continue_does_not_process_selected_attachments')],
 2: [None, ('test_skill_runtime_security', 'test_untrusted_request_cannot_supply_actor_or_destination'), ('test_output_contracts', 'test_rejects_unsupported_refs_depth_and_contradictory_bounds'), ('test_skill_batch04_planning', 'test_relative_tomorrow_uses_explicit_reference_not_system_clock'), ('test_skill_batch04_planning', 'test_invalid_or_ambiguous_temporal_input_never_guesses')],
 3: [('test_skill_refinement_memory', 'test_same_named_projects_and_corrected_then_deleted_lineage'), ('test_skill_batch05_providers', 'test_history_constraints_and_correction_are_not_silently_dropped'), ('test_project_memory_versions', 'test_version_survives_separate_process_read_and_correction'), ('test_project_memory_versions', 'test_stale_generation_cannot_save_answer_after_memory_correction'), ('test_voice_sessions', 'test_no_implicit_sqlite_or_raw_audio_persistence')],
 4: [('test_knowledge', 'test_pdf_citation_uses_actual_page_and_scans_report_missing_ocr'), ('test_document_extract', 'test_tsv_word_positions_low_confidence_and_native_overlap'), ('test_document_extract', 'test_xlsx_raw_formula_cached_values_blanks_merges_no_execution'), ('test_skill_refinement_workflows', 'test_independent_output_survives_blocked_specialist'), ('test_document_extract', 'test_native_rtl_pdf_never_indexes_unreliable_raw_when_ocr_fails')],
 5: [('test_skill_batch03_data', 'test_cleaning_number_separators_and_ambiguous_dates'), ('test_skill_artifacts', 'test_spreadsheet_zero_missing_date_identifier_and_formula_are_real_types'), ('test_skill_batch03_data', 'test_explicit_cleaning_preserves_zero_ids_and_source'), ('test_document_extract', 'test_invalid_actual_types_rejected'), ('test_skill_batch03_data', 'test_csv_utf8_semicolon_quotes_newline_and_leading_zero')],
 6: [None, None, None, ('test_skill_batch04_planning', 'test_dependency_order_and_deadline_are_applied_before_allocation'), ('test_skill_batch03_data', 'test_exploration_full_and_groups_filters_no_causality')],
 7: [('test_reasoning', 'test_arithmetic_exact'), ('test_reasoning', 'test_arithmetic_exact'), ('test_reasoning', 'test_arithmetic_exact'), ('test_reasoning', 'test_arithmetic_rejects_unsafe_or_excessive'), ('test_reasoning', 'test_arithmetic_exact')],
 8: [('test_research_chat', 'test_current_facts_are_not_inferred_from_fetch_date'), ('test_research_security', 'test_historical_evidence_never_claims_current_verification'), None, ('test_skill_batch02_content', 'test_fetch_time_never_replaces_unknown_or_old_publication'), ('test_skill_batch02_content', 'test_fetch_time_never_replaces_unknown_or_old_publication')],
 9: [('test_research_answer', 'test_quote_matching_does_not_claim_semantic_entailment'), ('test_research_answer', 'test_valid_exact_quote_and_number_provenance'), ('test_facts', 'test_incomparable_dimensions_not_false_conflicts'), ('test_knowledge_chat', 'test_no_evidence_does_not_invoke_generator'), ('test_facts', 'test_conflicts_and_decimal_normalization')],
 10: [('test_skill_batch02_content', 'test_document_version_and_processing_change_invalidates_source'), ('test_skill_batch02_content', 'test_search_excludes_other_project_and_revoked_file_before_matching'), ('test_skill_batch02_content', 'test_read_preserves_multilingual_text_source_locations_and_versions'), ('test_skill_batch02_content', 'test_no_silent_complete_read_truncation_or_fabricated_page'), ('test_skill_runtime_security', 'test_source_deletion_purges_transitively_derived_runs')],
 11: [('test_skill_batch03_data', 'test_orders_reference_statistics_and_profile'), ('test_skill_batch03_data', 'test_join_reference_and_duplicate_preflight'), ('test_skill_batch03_data', 'test_orders_reference_statistics_and_profile'), ('test_skill_batch03_data', 'test_invalid_cleaning_keep_is_explicit_and_localized'), ('test_skill_batch03_data', 'test_product_chart_input_edit_refreshes_linked_native_cache')],
 12: [('test_skill_batch03_data', 'test_statistics_empty_single_constant_weights_and_numeric_text'), ('test_skill_batch03_data', 'test_exploration_full_and_groups_filters_no_causality'), ('test_skill_batch03_data', 'test_statistics_empty_single_constant_weights_and_numeric_text'), None, None],
 13: [('test_skill_batch03_data', 'test_native_chart_uses_real_range_and_preserves_zero_gap'), None, None, None, ('test_skill_runtime_security', 'test_corrupt_persisted_artifact_does_not_download_as_success')],
 14: [('test_coding_checks', 'test_pipeline_nonzero_does_not_apply'), ('test_coding_edits', 'test_two_file_apply_and_undo_preserve_existing_dirty_bytes'), ('test_coding_context', 'test_dependency_declarations_and_readme_not_installed_or_executed'), ('test_skill_batch05_contracts', 'test_actual_typescript_error_blocks_and_repair_passes'), ('test_coding_context', 'test_python_fault_size_and_packaged_failure_are_explicit')],
 15: [('test_skill_runtime_security', 'test_untrusted_request_cannot_supply_actor_or_destination'), ('test_skill_batch05_execution', 'test_same_operation_key_coalesces_concurrent_requests_but_new_key_is_new_intent'), ('test_skill_runtime_security', 'test_idempotency_reuses_persisted_result_and_rejects_changed_parameters'), ('test_skill_runtime_security', 'test_verification_without_server_run_is_blocked_not_verified'), ('test_isolated_worker', 'test_execution_failures_always_remove_owned_container')],
 16: [('test_skill_batch02_lifecycle', 'test_unknown_model_or_effect_is_not_replayed'), ('test_skill_batch02_lifecycle', 'test_cancel_preserves_completed_output_and_unknown_usage'), ('test_skill_batch02_lifecycle', 'test_checkpoint_is_reference_only_versioned_and_resumes_once'), None, ('test_skill_refinement_workflows', 'test_invalid_spreadsheet_does_not_erase_valid_document')],
 17: [('test_output_contracts', 'test_strict_json_types_and_canonical_citations'), ('test_skill_artifacts', 'test_docx_reopens_with_real_headings_tables_direction_and_original_text'), ('test_skill_batch03_data', 'test_formula_patch_recalculates_dependents_and_compare_value'), ('test_skill_runtime_security', 'test_other_project_or_owner_cannot_read_execute_or_download'), ('test_skills', 'test_translation_literals_reject_missing_duplicate_and_added_numbers')],
 18: [('test_voice_audio', 'test_silence_returns_no_text_without_loading_whisper_or_running_vad'), ('test_voice_sessions', 'test_unreviewed_transcription_is_not_saved_or_model_history'), ('test_document_vision', 'test_explicit_local_question_returns_separate_fields_and_source'), ('test_document_vision', 'test_strict_visual_answer_schema_does_not_accept_prose_or_extra_actions'), ('test_voice_sessions', 'test_save_confirmation_idempotency_original_edit_and_cascade')],
 19: [('test_skill_batch04_integration', 'test_source_content_is_not_a_send_grant_and_private_scope_is_checked'), ('test_skill_runtime_security', 'test_other_project_or_owner_cannot_read_execute_or_download'), ('test_skill_runtime_security', 'test_revocation_during_actual_artifact_build_blocks_result'), ('test_knowledge_fetch_hardening', 'test_request_counter_covers_redirects_and_prevents_excess_get'), ('test_document_extract', 'test_zip_traversal_and_macros_never_extract')],
 20: [('test_skill_batch05_providers', 'test_history_constraints_and_correction_are_not_silently_dropped'), ('test_skill_batch05_providers', 'test_fallback_rejects_cloud_or_lost_images'), ('test_skill_batch05_providers', 'test_retry_after_honored_and_no_call_after_cancellation'), ('test_reasoning', 'test_depth_is_respected_and_auto_is_cheap'), ('test_skill_batch05_execution', 'test_result_after_deadline_is_not_published_as_success')],
}
RELATED = {f'AI-{group:02}-T{index:02}': f'tests/{entry[0]}.py::{entry[1]}' for group, entries in _GROUPS.items() for index, entry in enumerate(entries, 1) if entry}
RELATED.update({
    'AI-01-T04': 'tests/test_skill_batch05_registry.py::test_conflicts_honor_user_language_and_processing_scope',
    'AI-14-T04': 'tests/test_skill_batch05_contracts.py::test_actual_typescript_detects_seeded_type_error_then_accepts_fix',
})
# Exact new fixture bindings. A full deterministic-case pass is still labelled
# component evidence, not a complete conversational/product release acceptance.
EXACT = {}
for cid, name, complete in [
    ('AI-05-T01', 'csv_decimal_comma_quoted_semicolon_and_newline_preserve_records', True),
    ('AI-05-T02', 'structured_zero_null_empty_remain_distinct', False),
    ('AI-05-T03', 'explicit_arabic_quantity_does_not_normalize_text_identifier', True),
    ('AI-05-T04', 'csv_binary_or_archive_disguised_as_text_is_rejected', True),
    ('AI-05-T05', 'csv_decimal_comma_quoted_semicolon_and_newline_preserve_records', True),
    ('AI-11-T01', 'reference_orders_statistics_and_left_join_keep_zero_missing_and_unmatched', True),
    ('AI-11-T02', 'duplicate_reference_join_rejects_inflated_total_and_explicit_many_many_reports_it', True),
    ('AI-11-T03', 'reference_orders_statistics_and_left_join_keep_zero_missing_and_unmatched', True),
    ('AI-11-T04', 'invalid_amount_is_reported_not_silently_zeroed', True),
    ('AI-11-T05', 'corrected_orders_recompute_statistics_table_chart_and_saved_workbook', False),
    ('AI-12-T01', 'weighted_group_means_are_26_not_unweighted_50', True),
    ('AI-12-T03', 'outlier_is_retained_and_mean_median_computed_independently', True),
    ('AI-13-T01', 'arabic_chart_keeps_zero_category_and_has_explicit_zero_baseline', False),
    ('AI-13-T02', 'chart_thousands_display_scales_axis_not_source_values', False),
    ('AI-13-T03', 'close_bar_values_use_honest_zero_baseline', False),
    ('AI-17-T03', 'saved_formula_updates_after_input_change_without_replacing_formula', False),
]:
    EXACT[cid] = {'node': 'tests/test_ai_core_data.py::test_' + name,
                  'full_criterion': complete, 'kind': 'component',
                  'limit': 'Explicit typed inputs and deterministic local data engine; no model interpretation, native Office visual acceptance, or automatic dependent rerun.'}
for cid, name, complete in [
    ('AI-01-T01', 'single_attachment_dialect_and_quoted_instructions_remain_data', False),
    ('AI-01-T02', 'readonly_still_blocks_explicit_writing_skill', False),
    ('AI-01-T03', 'all_four_outputs_are_retained_and_missing_outputs_not_completed', False),
    ('AI-01-T04', 'language_exception_and_product_names_are_captured_without_rewriting_input', False),
    ('AI-01-T05', 'status_question_reuses_previous_task_without_new_execution', False),
    ('AI-02-T01', 'resource_identity_is_bound_to_order_not_label', False),
    ('AI-02-T02', 'resource_identity_is_bound_to_order_not_label', False),
    ('AI-02-T03', 'contradictory_word_limits_reject_before_task_or_tool', True),
]:
    EXACT[cid] = {'node': 'tests/test_ai_core_request.py::test_' + name,
                  'full_criterion': complete, 'kind': 'api' if cid in {'AI-01-T02', 'AI-01-T03', 'AI-01-T05', 'AI-02-T03'} else 'component',
                  'limit': 'Request extraction and guarded task admission; no independent language-quality verdict or completed four-output task.'}
for cid, name, complete in [
    ('AI-09-T01', 'unrelated_quote_with_same_number_is_rejected', False),
    ('AI-09-T02', 'cited_twenty_cannot_support_two_hundred_elsewhere', False),
    ('AI-10-T03', 'budget_coverage_preserves_each_candidate_document_before_second_excerpt', False),
    ('AI-10-T04', 'explicit_full_aggregate_research_has_zero_model_calls', True),
]:
    EXACT[cid] = {'node': 'tests/test_ai_core_evidence.py::test_' + name,
                  'full_criterion': complete, 'kind': 'mock' if cid == 'AI-10-T04' else 'component',
                  'limit': 'Deterministic quote/numeric provenance and coverage guard; no unrestricted semantic entailment or live research/model evaluation.'}
for cid, module, name, complete, kind in [
    ('AI-08-T03', 'evidence_api', 'matching_content_is_not_three_independent_confirmations', False, 'api'),
    ('AI-08-T05', 'evidence_api', 'fetch_date_does_not_become_publication_or_event_date', False, 'api'),
    ('AI-09-T02', 'grounded_chat', 'ordinary_grounded_chat_validates_claim_before_delta_and_storage', False, 'mock'),
    ('AI-10-T01', 'evidence_api', 'stale_public_version_stays_historical_and_current_retrieval_uses_new', True, 'api'),
    ('AI-10-T02', 'evidence_api', 'cross_project_same_filename_never_returns_other_evidence', False, 'api'),
    ('AI-10-T04', 'grounded_chat', 'ordinary_grounded_aggregate_is_rejected_before_model_or_saved_answer', True, 'mock'),
    ('AI-10-T05', 'evidence_api', 'deleted_or_revoked_source_cannot_be_read_from_citation', False, 'api'),
    ('AI-19-T03', 'evidence', 'research_revocation_after_model_blocks_answer_publication', False, 'mock'),
    ('AI-20-T01', 'evidence', 'same_validator_preserves_authorized_conversation_constraints', False, 'mock'),
]:
    EXACT[cid] = {'node': f'tests/test_ai_core_{module}.py::test_' + name,
                  'full_criterion': complete, 'kind': kind,
                  'limit': 'Synthetic source identities and actual local storage/API where declared; controlled provider output is simulated, no live semantic quality or all derivative formats certified.'}
for cid in ('AI-16-T01', 'AI-20-T05'):
    EXACT[cid] = {'node': 'tests/test_ai_core_recovery.py::test_deadline_keeps_committed_output_and_resume_does_not_rebuild_it',
                  'full_criterion': False, 'kind': 'api',
                  'limit': 'Real SQLite/artifact creation/readback/resume with an injected deadline. This tests a committed first output; not every external loss-of-response window or monetary budget.'}
EXACT['AI-03-T03'] = {'node': 'tests/test_skill_refinement_memory.py::test_project_memory_survives_app_reopen_without_enabling_personal',
    'full_criterion': False, 'kind': 'api',
    'limit': 'Actual application lifespan reopen and newly paired HTTP client read the same synthetic memory/version. Separate-process database read is also in the profile; conversational decision recall and a listening-service process restart remain unproved.'}
EXACT['AI-01-T01'] = {'node': 'tests/test_ai_core_request.py::test_single_arabic_attachment_five_exact_source_bullets_and_reopen',
    'full_criterion': True, 'kind': 'api',
    'limit': 'One synthetic Arabic attachment with five short source sentences, resolved reference, five exact extractive bullets and reopened result. No model call or general abstractive summarization quality claim.'}
for cid, name in [
    ('AI-07-T01', 'sequential_percentages_use_the_new_value_as_second_base'),
    ('AI-07-T02', 'length_addition_converts_units_before_calculation'),
    ('AI-07-T03', 'percentage_points_are_distinct_from_relative_percentage'),
    ('AI-07-T04', 'division_by_zero_has_no_invented_numeric_value'),
    ('AI-07-T05', 'price_change_uses_original_price_denominator'),
]:
    EXACT[cid] = {'node': 'tests/test_ai_core_reasoning.py::test_' + name,
        'full_criterion': True, 'kind': 'component',
        'limit': 'Bounded explicitly recognized problem grammar and existing exact arithmetic engine; covers declared Arabic/English forms and units, not unrestricted natural-language reasoning or model weights.'}
for index in range(1, 6):
    cid = f'AI-07-T{index:02}'
    EXACT[cid] = {'node': 'tests/test_ai_core_problem_api.py::' + ('test_actual_undefined_quantity_has_no_numeric_replacement' if index == 4 else 'test_actual_http_problem_result_and_saved_receipt'),
        'parameter_case_id': cid, 'full_criterion': True, 'kind': 'api',
        'limit': 'Authenticated HTTP, fixed subprocess arithmetic and reopened SQLite receipt in opt-in problem mode. Narrow AR/EN grammar and declared units; no model invocation or unrestricted language reasoning.'}
EXACT['AI-09-T02']['supporting_nodes'] = [
    'tests/test_ai_core_evidence.py::test_known_unit_cannot_be_swapped_even_in_inference',
    'tests/test_ai_core_evidence.py::test_known_unit_cannot_be_borrowed_from_another_number_or_uncited_sentence',
    'tests/test_ai_core_grounded_chat.py::test_ordinary_grounded_rejects_known_unit_swap_before_stream_and_storage',
]
EXACT['AI-02-T01'] = {
    'node': 'tests/test_ai_core_resource_binding.py::test_second_file_summary_consumes_only_bound_file_and_preserves_original_request',
    'supporting_nodes': [
        'tests/test_ai_core_resource_binding.py::test_second_file_summary_export_contains_only_selected_numbers',
        'tests/test_ai_core_resource_binding.py::test_second_file_scope_survives_explicit_checkpoint_resume',
        'tests/test_ai_core_resource_binding.py::test_explicit_artifact_payload_cannot_override_resolved_source',
    ],
    'full_criterion': False, 'kind': 'api',
    'limit': 'Actual authenticated summary/read, Word export and checkpoint resume bind the selected second file. This does not prove arbitrary document editing from conversational references; unsupported conflicting payloads reject.'}
EXACT['AI-19-T03']['supporting_nodes'] = [
    'tests/test_ai_core_resource_binding.py::test_selected_file_change_or_revocation_withholds_readback_and_replay',
    'tests/test_ai_core_resource_binding.py::test_selected_file_mutation_during_summary_blocks_publication',
]
EXACT['AI-19-T03']['supporting_kind'] = 'api'
EXACT['AI-15-T01'] = {
    'node': RELATED['AI-15-T01'], 'full_criterion': False, 'kind': 'api',
    'supporting_nodes': [
        'tests/test_ai_core_engine_version.py::test_data_result_wire_declares_actual_engine_version',
        'tests/test_ai_core_engine_version.py::test_old_missing_or_unrecognized_data_engine_cannot_publish_artifacts',
        'tests/test_ai_core_engine_version.py::test_current_data_engine_result_is_returned_after_wire_validation',
        'tests/test_ai_core_engine_version.py::test_documents_protocol_does_not_require_data_engine_version',
    ], 'supporting_kind': 'component',
    'limit': 'Trusted request identity and parent validation of actual declared data-engine wire versions. Controlled worker results exercise compatibility rejection locally; real old/current Docker probes are separate opt-in evidence, not part of this profile.'}


# Continuation evidence remains scoped: no model-quality or native Office claim.
for cid,node,limit in [
    ('AI-12-T04','test_three_months_do_not_support_precise_twelve_month_forecast',
     'Explicit forecast-review contract rejects unsupported precision from three periods; no trained model, accuracy estimate or unrestricted natural-language routing.'),
    ('AI-12-T05','test_late_feature_is_excluded_and_chronological_plan_uses_available_features',
     'Declared feature availability is checked and eligible-only chronological tables are prepared; hidden transformations and fitted-model reevaluation remain unproved.'),
    ('AI-13-T04','test_observed_forecast_native_series_are_separate_and_keep_zero',
     'OOXML series/source/date bindings and supplied scenario ranges, not an independently fitted forecast or native Excel visual acceptance.'),
]:
    EXACT[cid]={'node':'tests/test_ai_core_forecast.py::'+node,'full_criterion':False,'kind':'component','limit':limit}
EXACT['AI-16-T04']={'node':'tests/test_ai_core_report_revision.py::test_revision_preserves_real_calculation_without_recomputing',
    'supporting_nodes':['tests/test_ai_core_report_revision.py::test_saved_report_language_revision_reuses_content_and_original_artifact',
                        'tests/test_ai_core_report_revision.py::test_parent_mutation_during_revision_prevents_late_file_and_readback'],
    'full_criterion':False,'kind':'api',
    'limit':'Authenticated saved-result revision with real deterministic calculation, editable DOCX and no repeated analysis. Transport/prose translation are mocked in this profile; actual Docker/Chrome/model runs are separate. Scope is bounded narrative and data tables, not all derived formats or arbitrary ongoing task steering.'}
EXACT['AI-09-T02']['supporting_nodes'] += ['tests/test_grounded_repair.py::test_ordinary_api_repairs_before_storing_answer_and_records_both_attempts',
    'tests/test_grounded_repair.py::test_reference_only_recovery_never_promotes_rejected_claim_to_approved_answer']
for cid,node,limit in [
    ('AI-04-T02','test_real_receipt_ocr_preserves_decimal_or_review_required_raw_text','Actual installed OCR on two synthetic receipt images; raw ambiguous text and review requirement retained. No conversational amount interpretation or universal OCR accuracy.'),
    ('AI-04-T05','test_real_arabic_columns_order_is_explicitly_unverified','Actual PDFium/OCR extraction flags unverified RTL order; observed identifier OCR error remains unresolved. No footnote acceptance.'),
    ('AI-17-T02','test_arabic_word_contains_editable_table_and_mixed_text_not_an_image','Actual editable Arabic table and mixed text readback; native Word renderer unavailable, so visual layout is not certified.'),
]:
    EXACT[cid]={'node':'tests/test_ai_core_visual.py::'+node,'full_criterion':False,'kind':'component','limit':limit}


def load_cases(path=DATASET):
    raw = Path(path).read_bytes()
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('ai_dataset_size_limit')
    data = json.loads(raw)
    cases = data.get('cases')
    if not isinstance(cases, list) or [c.get('id') if isinstance(c, dict) else None for c in cases] != list(IDS):
        raise ValueError('ai_dataset_requires_exact_100_ordered_ids')
    for case in cases:
        for field in ('title', 'category', 'capability'):
            if not isinstance(case.get(field), str) or not case[field]:
                raise ValueError('ai_dataset_missing_metadata')
        for field in ('input', 'resources', 'judge', 'environment', 'reference'):
            if not isinstance(case.get(field), dict) or not case[field]:
                raise ValueError('ai_dataset_missing_contract')
        for field in ('expected', 'forbidden'):
            if not isinstance(case.get(field), list) or not case[field] or any(not isinstance(x, str) or not x.strip() for x in case[field]):
                raise ValueError('ai_dataset_missing_criterion')
        if type(case.get('critical')) is not bool:
            raise ValueError('ai_dataset_invalid_criticality')
    return data, hashlib.sha256(raw).hexdigest()


def test_exists(node, root=ROOT):
    if not re.fullmatch(r'tests/test_[a-z0-9_]+\.py::test_[a-z0-9_]+', node):
        return False
    path, name = node.split('::')
    try:
        tree = ast.parse((Path(root) / path).read_text(encoding='utf-8-sig'))
    except (OSError, SyntaxError):
        return False
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name for n in tree.body)


def approved_nodes(root=ROOT):
    """Only reviewed local selectors can enter the subprocess argv."""
    nodes = [*RELATED.values(), *(item['node'] for item in EXACT.values())]
    nodes.extend(node for item in EXACT.values() for node in item.get('supporting_nodes', []))
    return sorted({node for node in nodes if test_exists(node, root)})


def profile_selectors(existing=(), root=ROOT):
    """Reuse the existing CI invocation, collecting a covered file only once."""
    files = list(existing)
    for name in ('request', 'resource_binding', 'data', 'reasoning', 'problem_api', 'evidence', 'evidence_api', 'grounded_chat', 'recovery', 'evaluation','report_revision','forecast','visual'):
        value = f'tests/test_ai_core_{name}.py'
        if (Path(root) / value).is_file() and value not in files:
            files.append(value)
    if 'tests/test_grounded_repair.py' not in files:files.append('tests/test_grounded_repair.py')
    # The existing local CI profile covers the new math path as regression
    # checks. The sixty-case acceptance report remains a separate measurement.
    for name in ('api','discrete','wording','symbolic','boundary_review','symbolic_review','evaluation','request'):
        value=f'tests/test_math_{name}.py'
        if (Path(root)/value).is_file() and value not in files:files.append(value)
    covered = {value for value in files if '::' not in value}
    return files + [node for node in approved_nodes(root) if node.split('::')[0] not in covered and node not in files]


def read_junit(path):
    """No assertion/error body is copied into the user-facing evidence table."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        return {}
    try:
        raw = path.read_bytes()
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            return {}
        tree = ET.fromstring(raw)
    except (OSError, ET.ParseError):
        return {}
    results = {}
    for item in tree.iter('testcase'):
        module = item.get('classname', '')
        name = item.get('name', '')
        if not re.fullmatch(r'(?:tests\.)?test_[a-z0-9_]+', module):
            continue
        base = name.split('[', 1)[0]
        if not re.fullmatch(r'test_[a-z0-9_]+', base):
            continue
        node = 'tests/' + module.removeprefix('tests.') + '.py::' + base
        status = 'failed' if item.find('failure') is not None or item.find('error') is not None else 'skipped' if item.find('skipped') is not None else 'passed'
        label = re.search(r'\[(AI-[0-9]{2}-T[0-9]{2})\]$', name)
        results.setdefault(node, []).append({'status': status, 'parameterized': '[' in name,
                                            'case_id': label.group(1) if label else None})
    return results


def assess_case(case, observed, *, execution_ok=True):
    """Related regressions establish partial support, never complete scenarios."""
    exact = EXACT.get(case['id'])
    node = exact['node'] if exact else RELATED.get(case['id'])
    rows = observed.get(node, []) if node else []
    if exact and exact.get('parameter_case_id'):
        rows = [row for row in rows if row.get('case_id') == exact['parameter_case_id']]
    groups = [(node, rows)] if node else []
    groups.extend((support, observed.get(support, [])) for support in (exact or {}).get('supporting_nodes', []))
    all_rows = [row for _, group in groups for row in group]
    complete = bool(groups) and all(group for _, group in groups)
    if any(row['status'] == 'failed' for row in all_rows):
        status, reason = 'failed', 'related_runtime_contract_failed'
    elif not execution_ok or not complete:
        status, reason = 'blocked', 'fresh_execution_missing_or_incomplete'
    elif any(row['status'] == 'skipped' for row in all_rows):
        status, reason = 'skipped', 'required_component_test_skipped'
    elif exact and exact['full_criterion']:
        status, reason = 'passed', 'exact_deterministic_criterion_verified_within_component_scope'
    else:
        status, reason = 'partial', 'component_regression_passed_full_user_scenario_not_proved'
    return {'id': case['id'], 'title': case['title'], 'critical': case['critical'], 'status': status,
        'reason_codes': [reason], 'evidence': [{'node': selected, 'cases': len(group), 'status': 'failed' if any(row['status'] == 'failed' for row in group) else 'blocked' if not group else 'skipped' if any(row['status'] == 'skipped' for row in group) else 'passed', 'kind': exact.get('supporting_kind', exact['kind']) if exact and selected != node else exact['kind'] if exact else 'component_or_mock_api', 'scope': exact['limit'] if exact else 'related existing component/API contract; provider responses may be simulated'} for selected, group in groups],
        'unverified_criteria': [] if status == 'passed' else list(case['expected']),
        'declared_fixture_executed': bool(complete and execution_ok),
        'exact_user_scenario_executed': bool(exact and exact['full_criterion'] and complete and execution_ok),
        'semantic_review': 'not_run', 'live_model': 'not_run', 'native_desktop': 'not_run',
        'reference': case['reference']}


def totals(cases):
    if any(row.get('status') not in STATUSES for row in cases):
        raise ValueError('invalid_ai_result_status')
    return {status: sum(row['status'] == status for row in cases) for status in STATUSES}


def independent_sample(root=ROOT):
    root = Path(root)
    path = root / 'evaluation/ai_core/independent-sample.json'
    raw = path.read_bytes()
    freeze = json.loads(path.with_name('independent-sample.freeze.json').read_text(encoding='utf-8'))
    if hashlib.sha256(raw).hexdigest() != freeze['dataset_sha256']:
        raise ValueError('independent_sample_changed_after_freeze')
    sample = json.loads(raw)
    from backend.check_worker import Arithmetic
    from backend.output_contracts import OutputContract
    contract = OutputContract.model_validate({'schema': {'type': 'object', 'properties': {'count': {'type': 'integer'}}, 'required': ['count'], 'additionalProperties': False}})
    results = []
    for case in sample['cases']:
        try:
            if case['executor'] == 'arithmetic':
                ok = Arithmetic(case['input']).result()['exact'] == case['exact']
            elif case['executor'] == 'arithmetic_rejection':
                ok = False
                try: Arithmetic(case['input']).result()
                except ValueError: ok = True
            elif case['executor'] == 'structured_integer':
                ok = contract.check(case['input'], 0)['schema_valid'] is case['valid']
            else:
                raise ValueError('unapproved_independent_executor')
        except (ValueError, KeyError, TypeError):
            ok = False
        results.append({'id': case['id'], 'status': 'passed' if ok else 'failed', 'scope': 'deterministic component only'})
    return {'version': sample['version'], 'sha256': freeze['dataset_sha256'], 'cases': results, 'totals': totals(results), 'heldout': False, 'model': None}
