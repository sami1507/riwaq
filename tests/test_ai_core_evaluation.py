"""Evaluation integrity: actual coverage cannot be promoted by report wording."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import pytest
from backend import ai_core_evaluation as evaluate


def test_exact_user_100_ids_and_reference_values_are_preserved():
    dataset, digest = evaluate.load_cases()
    assert len(dataset['cases']) == 100 and len(digest) == 64
    cases = {case['id']: case for case in dataset['cases']}
    assert cases['AI-07-T01']['resources']['calculation_reference']['exact'] == '96'
    assert cases['AI-11-T01']['resources']['tables']['orders']['rows'] == [['O1', 'C1', 10], ['O2', 'C1', 0], ['O3', 'C2', 20], ['O4', 'C9', None]]
    assert cases['AI-12-T01']['resources']['groups'] == [{'n': 10, 'mean': 80}, {'n': 90, 'mean': 20}]
    source = evaluate.ROOT / cases['AI-01-T01']['reference']['path']
    assert hashlib.sha256(source.read_bytes()).hexdigest() == cases['AI-01-T01']['reference']['sha256']


@pytest.mark.parametrize('mutation', ['duplicate', 'missing', 'reordered', 'empty_criterion'])
def test_missing_duplicate_or_weakened_case_is_rejected(tmp_path, mutation):
    data, _ = evaluate.load_cases()
    if mutation == 'duplicate': data['cases'][1] = deepcopy(data['cases'][0])
    if mutation == 'missing': data['cases'].pop()
    if mutation == 'reordered': data['cases'][0], data['cases'][1] = data['cases'][1], data['cases'][0]
    if mutation == 'empty_criterion': data['cases'][0]['expected'] = []
    path = tmp_path / 'cases.json'; path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError): evaluate.load_cases(path)


def test_related_contract_pass_is_partial_not_user_scenario_success():
    case = next(case for case in evaluate.load_cases()[0]['cases'] if case['id'] == 'AI-03-T01')
    node = evaluate.RELATED[case['id']]
    result = evaluate.assess_case(case, {node: [{'status': 'passed'}]})
    assert result['status'] == 'partial'
    assert result['unverified_criteria'] == case['expected']
    assert result['exact_user_scenario_executed'] is False and result['live_model'] == 'not_run'


def test_old_related_test_cannot_replace_missing_new_scenario_evidence():
    case = evaluate.load_cases()[0]['cases'][0]
    node = evaluate.RELATED[case['id']]
    result = evaluate.assess_case(case, {node: [{'status': 'passed'}]})
    assert result['status'] == 'blocked' and not result['exact_user_scenario_executed']


def test_exact_numeric_contract_has_explicit_component_scope_and_visual_stays_partial():
    cases = {case['id']: case for case in evaluate.load_cases()[0]['cases']}
    exact = cases['AI-11-T02']; visual = cases['AI-13-T01']
    for case, wanted in [(exact, 'passed'), (visual, 'partial')]:
        node = evaluate.EXACT[case['id']]['node']
        result = evaluate.assess_case(case, {node: [{'status': 'passed'}]})
        assert result['status'] == wanted and result['evidence'][0]['kind'] == 'component'
        assert result['native_desktop'] == 'not_run'


@pytest.mark.parametrize('states,execution_ok,wanted', [(['passed', 'failed'], True, 'failed'), (['skipped'], True, 'skipped'), ([], True, 'blocked'), (['passed'], False, 'blocked')])
def test_failure_missing_skip_and_incomplete_execution_cannot_be_passes(states, execution_ok, wanted):
    case = next(case for case in evaluate.load_cases()[0]['cases'] if case['id'] == 'AI-11-T02')
    node = evaluate.EXACT[case['id']]['node']
    result = evaluate.assess_case(case, {node: [{'status': state} for state in states]}, execution_ok=execution_ok)
    assert result['status'] == wanted
    assert sum(evaluate.totals([result]).values()) == 1


def test_junit_counts_all_parameter_cases_without_copying_sensitive_error(tmp_path):
    path = tmp_path / 'fresh.xml'
    path.write_text('<testsuites><testsuite><testcase classname="tests.test_ai_core_data" name="test_csv_binary_or_archive_disguised_as_text_is_rejected[one]"/><testcase classname="tests.test_ai_core_data" name="test_csv_binary_or_archive_disguised_as_text_is_rejected[two]"><failure>FAKE_SECRET_DO_NOT_COPY</failure></testcase></testsuite></testsuites>', encoding='utf-8')
    result = evaluate.read_junit(path)
    rows = result['tests/test_ai_core_data.py::test_csv_binary_or_archive_disguised_as_text_is_rejected']
    assert len(rows) == 2 and {row['status'] for row in rows} == {'passed', 'failed'}
    assert 'FAKE_SECRET' not in json.dumps(result)
    path.write_text('<!DOCTYPE a [<!ENTITY x SYSTEM "file:///private">]><testsuites/>', encoding='utf-8')
    assert evaluate.read_junit(path) == {}


def test_explicit_user_case_parameter_prevents_failure_attribution_to_other_case():
    cases = {case['id']: case for case in evaluate.load_cases()[0]['cases']}
    node = evaluate.EXACT['AI-07-T01']['node']
    observed = {node: [{'status': 'passed', 'case_id': 'AI-07-T01'}, {'status': 'failed', 'case_id': 'AI-07-T02'}]}
    assert evaluate.assess_case(cases['AI-07-T01'], observed)['status'] == 'passed'
    assert evaluate.assess_case(cases['AI-07-T02'], observed)['status'] == 'failed'
    assert evaluate.assess_case(cases['AI-07-T03'], observed)['status'] == 'blocked'


def test_fixed_selectors_exist_and_never_come_from_case_commands():
    assert all(evaluate.test_exists(node) for node in evaluate.RELATED.values())
    assert all(evaluate.test_exists(binding['node']) for binding in evaluate.EXACT.values())
    assert not evaluate.test_exists('tests/test_app.py::test_x; echo unsafe')
    assert not evaluate.test_exists('../private.py::test_secret')
    assert evaluate.approved_nodes() == sorted(set(evaluate.approved_nodes()))


def test_unit_binding_failure_blocks_case_despite_primary_numeric_contract_pass():
    case = next(case for case in evaluate.load_cases()[0]['cases'] if case['id'] == 'AI-09-T02')
    binding = evaluate.EXACT[case['id']]
    observed = {node: [{'status': 'passed'}] for node in [binding['node'], *binding['supporting_nodes']]}
    assert evaluate.assess_case(case, observed)['status'] == 'partial'
    observed[binding['supporting_nodes'][0]] = [{'status': 'failed'}]
    assert evaluate.assess_case(case, observed)['status'] == 'failed'
    observed.pop(binding['supporting_nodes'][0])
    assert evaluate.assess_case(case, observed)['status'] == 'blocked'


def test_existing_ci_includes_new_request_and_recovery_without_duplicate_collection():
    selectors = evaluate.profile_selectors(['tests/test_skills.py'])
    assert 'tests/test_skills.py' in selectors
    assert 'tests/test_ai_core_request.py' in selectors
    assert 'tests/test_ai_core_recovery.py' in selectors
    assert 'tests/test_ai_core_resource_binding.py' in selectors
    assert 'tests/test_ai_core_engine_version.py::test_old_missing_or_unrecognized_data_engine_cannot_publish_artifacts' in selectors
    assert 'tests/test_ai_core_engine_version.py' not in selectors
    assert not any('test_actual_old_image' in value for value in selectors)
    assert 'tests/test_ai_core_evaluation.py' in selectors
    assert len(selectors) == len(set(selectors))
    assert not any(node.startswith('tests/test_skills.py::') for node in selectors)
    assert not any(node.startswith('tests/test_ai_core_request.py::') for node in selectors)


def test_ci_reuses_one_process_and_partial_ai_evidence_cannot_install_approval(tmp_path, monkeypatch):
    from scripts import run_skill_regression as runner
    from backend import skill_release
    calls = []
    def fixed_process(argv, cwd, env, timeout, log):
        calls.append(argv)
        report = Path(next(arg.split('=', 1)[1] for arg in argv if arg.startswith('--junitxml=')))
        report.write_text('<testsuites><testsuite><testcase classname="tests.test_ai_core_request" name="test_contradictory_word_limits_reject_before_task_or_tool"/></testsuite></testsuites>', encoding='utf-8')
        log.write_text('Synthetic runner test; not a live execution report.\n', encoding='utf-8')
        return {'command': argv, 'status': 'passed', 'exit_code': 0, 'seconds': 0}
    monkeypatch.setattr(runner, 'invoke', fixed_process)
    monkeypatch.setattr(skill_release, 'current_source_fingerprint', lambda root: 'a' * 64)
    result = runner.run(tmp_path / 'fresh-ci', seal=False, ai_core=True)
    assert len(calls) == 1 and 'tests/test_ai_core_recovery.py' in calls[0]
    assert result['status'] == result['ai_core']['status'] == 'partial'
    assert sum(result['ai_core']['totals'].values()) == 100
    assert result['ai_core']['totals']['blocked'] > 0
    assert result['ai_core']['release_approved'] is False
    assert result['evidence_installation']['installed'] is False


def test_frozen_independent_sample_runs_and_modified_reference_is_rejected(tmp_path):
    result = evaluate.independent_sample()
    assert len(result['cases']) == 12 and result['totals']['passed'] == 12
    assert not result['heldout'] and result['model'] is None
    folder = tmp_path / 'evaluation/ai_core'; folder.mkdir(parents=True)
    for name in ('independent-sample.json', 'independent-sample.freeze.json'):
        (folder / name).write_bytes((evaluate.ROOT / 'evaluation/ai_core' / name).read_bytes())
    with (folder / 'independent-sample.json').open('a', encoding='utf-8') as handle: handle.write(' ')
    with pytest.raises(ValueError, match='changed_after_freeze'): evaluate.independent_sample(tmp_path)


def test_runner_cannot_reuse_old_output_or_write_outside_reports(tmp_path, monkeypatch):
    from scripts import run_ai_core_evaluation as runner
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    (tmp_path / 'reports/old').mkdir(parents=True)
    with pytest.raises(ValueError): runner.new_output(tmp_path / 'reports/old')
    with pytest.raises(ValueError): runner.new_output(tmp_path / 'elsewhere')
    assert runner.new_output(tmp_path / 'reports/new').is_dir()


def test_totals_keep_six_statuses_separate_and_reject_unknown():
    counts = evaluate.totals([{'status': status} for status in evaluate.STATUSES])
    assert counts == {status: 1 for status in evaluate.STATUSES}
    with pytest.raises(ValueError): evaluate.totals([{'status': 'ready'}])
