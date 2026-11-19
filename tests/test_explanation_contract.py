"""Backend explanation contracts only; no browser, screen or human comprehension.

Uses original public EXPL events stored outside runtime. UI evidence is separate.
No mocks, network, model, storage mutation or engine-file edits are involved.
"""
import json
from fractions import Fraction
from pathlib import Path
import pytest
import sympy as s
from backend.reasoning_math import solve_math,validate_math_result
from backend.math_expression import relation,symbols,MathInputError
from backend.medical_schema import Explain
from backend.medical_education import explain

FIXTURE=json.loads((Path(__file__).parent/'fixtures/explanation_acceptance.json').read_text(encoding='utf8'))
CASES={case['id']:case for case in FIXTURE['cases']}

def math(value):
    text=json.dumps(value) if isinstance(value,dict) else value
    return validate_math_result(solve_math(text),text)

@pytest.mark.parametrize('language',['ar','en'])
def test_EXPL_001_level_changes_depth_without_changing_concept(language):
    records=[explain(Explain(**CASES['EXPL-001']['input'],language=language,level=level)) for level in ['simple','detailed','technical']]
    assert len({row['summary'] for row in records})==1
    assert len(records[0]['steps'])<len(records[1]['steps'])<len(records[2]['steps'])
    assert len(records[0]['definitions'])<len(records[1]['definitions'])<len(records[2]['definitions'])
    assert all(row['review_state']=='educational_source_supported' for row in records)
    assert all(not row['clinical_review']['clinical_use_approved'] for row in records)

def test_EXPL_002_steps_are_equivalent_and_original_is_checked():
    case=CASES['EXPL-002'];record=math(case['input']);data=record['result']
    assert data['solutions']==[case['expected']['solution']]
    syms=symbols(['x']);x=syms['x']
    for step in data['teaching_steps']:
        equation,_=relation(step['equation'],syms,'real')
        assert s.solveset(equation.lhs-equation.rhs,x,domain=s.S.Reals)==s.FiniteSet(4)
        assert step['reason_ar'] and step['reason_en']
    assert any('نطرح 3' in row['reason_ar'] for row in data['teaching_steps'])
    assert any('نقسم الطرفين على 2' in row['reason_ar'] for row in data['teaching_steps'])
    assert data['substitution']['left']==data['substitution']['right']==case['expected']['original_substitution']
    assert data['substitution']['verified'] is True

def test_EXPL_003_alternative_available_but_unscoped_followup_not_claimed():
    record=math(CASES['EXPL-002']['input']);data=record['result']
    assert data['teaching']['alternative_ar'] not in [row['reason_ar'] for row in data['teaching_steps']]
    assert 'تشبيه' in data['teaching']['alternative_ar']
    example=data['teaching_example'];again=math(example['request'])
    assert again['result']['solutions']==[example['solution']]
    assert example['substitution']['left']==example['substitution']['right']
    assert example['solution']!=data['solutions'][0]
    # A stored alternative exists for the UI. This engine does not resolve a
    # conversational follow-up by pretending the missing referent is known.
    with pytest.raises(MathInputError):math(CASES['EXPL-003']['input'])

@pytest.mark.parametrize('language',['ar','en'])
def test_EXPL_004_optional_check_does_not_gate_the_answer(language):
    request=CASES['EXPL-004']['input']
    optional=explain(Explain(**request,language=language))
    ordinary=explain(Explain(**{**request,'optional_understanding_check':False},language=language))
    assert optional['summary']==ordinary['summary'] and optional['summary']
    assert optional['definitions']==ordinary['definitions']
    assert optional['understanding_check'] and 'understanding_check' not in ordinary
    assert not any(optional['privacy'].values())
    assert not optional['missing_fields']

def test_EXPL_005_exact_absolute_relative_and_population_conditions():
    case=CASES['EXPL-005'];record=math(case['input'])
    for key,value in case['expected'].items():assert record['result'][key]==value
    assert record['result']['after']=='1/1000' # risk did not disappear
    assert record['result']['population_scope']=='hypothetical'
    assert any('not an estimate of personal risk' in row for row in record['conditions'])

def test_EXPL_006_points_do_not_cross_zero_and_are_computed():
    case=CASES['EXPL-006'];record=math(case['input']);data=record['result']
    assert data['excluded']==case['expected']['excluded'] and len(data['series'])==case['expected']['branches']
    assert record['result_kind']=='approximate'
    for branch in data['series']:
        points=branch['points'];xs=[Fraction(point['x']) for point in points]
        assert len(points)>1 and (all(x<0 for x in xs) or all(x>0 for x in xs))
        assert all(abs(Fraction(p['x'])*Fraction(p['y'])-1)<Fraction(1,10**13) for p in points)
    # This verifies data segmentation, not the SVG or exported rendering.

def test_EXPL_007_ambiguity_preserves_both_groupings_and_domain_guard():
    case=CASES['EXPL-007'];record=math(case['input'])
    assert record['problem_status']==case['expected']['problem_status']
    assert record['result']['readings']==case['expected']['readings']
    assert record['execution_status']=='partial' and record['completeness']=='not_proved'
    assert record['original_text']=='1/2x'
    assert any('denominator' in value and 'nonzero' in value for value in record['conditions'])
    assert record['explanation']

def test_EXPL_008_levels_preserve_the_same_domain_solution_and_constraints():
    case=CASES['EXPL-008']
    records=[math({**case['input'],'explanation_level':level}) for level in ['simple','detailed','technical']]
    for record in records:
        assert record['request']['domain']=='real'
        assert record['conditions']==case['expected']['conditions']
        assert record['result']['solutions']==case['expected']['solutions']
        assert record['result_kind']=='exact' and record['result']['substitution']['verified']
        assert 'teaching_example' not in record['result'] # cannot discard the constraint in a related example
    assert records[0]['result']==records[1]['result']==records[2]['result']

def test_zero_baseline_relative_risk_is_null_with_reason_not_a_number():
    record=math({'operation':'risk_change','before':'0','after':'1','denominator':'1000','period':'year','scope':'hypothetical'})
    assert record['result']['relative_percent'] is None
    assert record['result']['relative_reduction'] is None
    assert record['result']['absolute_reduction']=='-1/1000'
    assert any('undefined' in condition and 'zero' in condition for condition in record['conditions'])

def test_negative_divisor_inequality_has_inequality_reason_and_correct_direction():
    record=math('حل -2x > 6');data=record['result']
    assert data['set']=='Interval.open(-oo, -3)'
    assert '<' in data['teaching_steps'][-1]['equation']
    assert any('سالب' in step['reason_ar'] for step in data['teaching_steps'])
