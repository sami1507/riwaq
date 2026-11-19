"""Evidence integrity, not a second implementation of mathematical algorithms."""
import copy
import json
from pathlib import Path
import pytest
from scripts import run_math_evaluation as evaluation


def test_exactly_sixty_user_cases_and_required_evidence_fields():
    data=evaluation.load_cases()
    assert data['case_count']==60 and data['known_development_cases'] is True
    assert data['human_blind_review'] is False
    for case in data['cases']:
        assert case['input_text'] and case['source_text'] and case['domain'] and case['expected']
        assert case['acceptance'] and case['forbidden'] and case['judgment']
        assert case['scope'] and case['source'] and case['reference_review']
        assert all(type(r) is dict and type(r.get('operation')) is str for r in case['requests'])
    assert [c['group'] for c in data['cases']]==[n for n in range(1,7) for _ in range(10)]


@pytest.mark.parametrize('a,b,expected',[
    ('0.3','3/10',True),('9007199254740994','9007199254740994',True),
    ('9007199254740992','9007199254740994',False),(True,'1',False),
    (0.3,'3/10',False),('0','',False),('1/0','0',False),
    ('__import__("os")','0',False),('1'*257,'1',False),
])
def test_exact_wire_comparison_preserves_types_and_big_integers(a,b,expected):
    assert evaluation.exact_equal(a,b) is expected


def test_missing_results_are_not_successes():
    assert evaluation.totals([])==dict.fromkeys(evaluation.STATUSES,0)
    assert evaluation.totals([{'status':'blocked'},{'status':'partial'}])['passed']==0
    with pytest.raises(ValueError,match='unknown_status'):
        evaluation.totals([{'status':'unknown'}])


def test_reference_values_are_not_imported_by_runtime():
    # Scan runtime Python only; scripts/tests may legitimately read fixtures.
    for path in (evaluation.ROOT/'backend').glob('*.py'):
        text=path.read_text(encoding='utf-8-sig')
        assert 'MATH_REASONING_EVALS.json' not in text, path
        assert 'independent-frozen.json' not in text, path
        assert 'evaluation/math' not in text, path


def test_output_path_never_overwrites_or_leaves_reports(tmp_path):
    with pytest.raises(ValueError,match='new_report_directory_required'):
        evaluation.new_output(tmp_path/'outside')
    with pytest.raises(ValueError,match='new_report_directory_required'):
        evaluation.new_output(evaluation.ROOT/'reports')


def packet(result,problem='solved',kind='exact',completeness='proved_in_domain'):
    return {'execution_status':'completed','problem_status':problem,'result_kind':kind,
      'completeness':completeness,'result':result,'verification':[{'check':'test','status':'passed','detail':'synthetic fixture'}],
      'conditions':[],'explanation':[]}


def test_judge_detects_lost_integer_precision_even_with_success_claim():
    case=evaluation.load_cases()['cases'][1]
    assert not evaluation.judge_case(case,[packet({'value':'9007199254740994'})])
    assert evaluation.judge_case(case,[packet({'value':'9007199254740992'})])
    assert evaluation.judge_case(case,[packet({'value':9007199254740994.0})])


def test_judge_detects_lost_solution_and_unproved_completeness():
    case=evaluation.load_cases()['cases'][14]
    assert not evaluation.judge_case(case,[packet({'solutions':['2','0']})])
    assert evaluation.judge_case(case,[packet({'solutions':['2']})])
    assert evaluation.judge_case(case,[packet({'solutions':['0','2']},completeness='not_proved')])


def test_judge_accepts_symbolic_equivalence_but_not_untrusted_code():
    assert evaluation.equivalent('x**2*(x*cos(x)+3*sin(x))','3*x^2*sin(x)+x^3*cos(x)')
    assert not evaluation.equivalent('__import__("os").getcwd()','0')
    assert not evaluation.equivalent('x.__class__','0')
    assert not evaluation.equivalent('2**99999999','0')


def test_judge_does_not_count_small_residual_as_root_accuracy():
    case=evaluation.load_cases()['cases'][49]
    correct=packet({'residual':'0.000001','root_error':'0.001','accepted':False})
    assert not evaluation.judge_case(case,[correct])
    incorrect=copy.deepcopy(correct);incorrect['result']['accepted']=True
    assert evaluation.judge_case(case,[incorrect])


def test_partial_and_missing_text_never_become_end_to_end_passes():
    assert evaluation.combine_status('passed','blocked','blocked')=='partial'
    assert evaluation.combine_status('passed','passed','blocked')=='partial'
    assert evaluation.combine_status('passed','passed','failed')=='failed'
    assert evaluation.combine_status('blocked','blocked','blocked')=='blocked'
    assert evaluation.combine_status('passed','not_applicable','not_applicable')=='passed'


def test_extra_result_and_missing_result_are_not_ignored():
    case=evaluation.load_cases()['cases'][7]
    assert evaluation.judge_case(case,[packet({'value':'-4'})])==['missing_case_outputs']
    assert not evaluation.judge_case(case,[packet({'value':'-4'}),packet({'value':'4'})])
    assert evaluation.judge_case(case,[packet({'value':'-4'}),packet({'value':'4'}),packet({'value':'9'})])==['missing_case_outputs']


def test_independent_sample_is_frozen_visible_and_not_claimed_family_disjoint():
    path=evaluation.ROOT/'evaluation/math/independent-sample.json'
    freeze=json.loads((path.parent/'independent-sample.freeze.json').read_bytes())
    assert evaluation.digest(path)==freeze['sha256']
    assert freeze['case_count']==12 and freeze['blind'] is False and freeze['family_disjoint'] is False
    assert len(json.loads(path.read_bytes())['cases'])==12


def test_independent_failure_and_source_change_cannot_exit_successfully():
    counts={'passed':60,'failed':0}
    assert evaluation.exit_code({'status':'failed','totals':counts})==1
    assert evaluation.exit_code({'status':'blocked','totals':counts})==2
    assert evaluation.exit_code({'status':'partial','totals':counts})==2
    assert evaluation.exit_code({'status':'passed','totals':counts})==0
