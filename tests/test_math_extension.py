"""Regression and nearby variants for the disclosed RM extension.

These local tests are development evidence, not a blind evaluation or clinical
validation. The evaluation runner separately exercises the actual fixed worker.
"""
import json
from fractions import Fraction
from pathlib import Path

import pytest
import sympy as s

from backend.reasoning_math import parse_math, solve_math, validate_math_result,render_math
from backend.math_expression import Expression, relation, symbols
from scripts.run_rm_evaluation import judge


def run(request):
    text=json.dumps(request) if isinstance(request,dict) else request
    return validate_math_result(solve_math(text),text)


CASES=json.loads((Path(__file__).parents[1]/'evaluation/math/RM_EVALS.json').read_bytes())['cases']


@pytest.mark.parametrize('case',CASES[:-1],ids=[c['id'] for c in CASES[:-1]])
def test_disclosed_original_wording(case):
    answer=run(case['text'])
    judge(case['id'],answer)
    assert answer['original_text']==case['text']


@pytest.mark.parametrize('text,expected',[
    ('حوّل 36 km/h إلى m/s','10'),('convert 7.2 km/h to m/s','2'),
    ('حوّل 500 mcg إلى mg في تمرين وحدات','1/2'),('convert 1.5 mg to mcg','1500'),
    ('قيمة 200 زادت 10% ثم انخفضت 25% من قيمتها الجديدة','165'),
    ('احسب 0.07 + 0.8 بوصفهما عددين عشريين محددين','87/100')])
def test_quantities_are_captured_not_example_answers(text,expected):
    assert run(text)['result']['value']==expected


def test_parameter_names_and_zero_cases_survive_renaming():
    answer=run('حل c*z=d بحسب قيم c وd')
    assert answer['result']['variable']=='z'
    assert answer['result']['cases'][0]['solutions']==['d/c']
    assert len(answer['result']['cases'])==3


def test_requested_steps_and_additional_example_are_computed_separately():
    output=run('حل 2x+3=11 مع شرح سبب كل خطوة')
    assert output['request']['explanation_level']=='detailed'
    example=output['result']['teaching_example']
    independently=run(example['request'])
    assert independently['result']['solutions']==[example['solution']]
    assert example['substitution']['left']==example['substitution']['right']
    assert example['solution']!='4' and output['result']['solutions']==['4']
    constrained=run('solve 2*x+3=11; where: x<5')
    assert 'teaching_example' not in constrained['result']


@pytest.mark.parametrize('text,display', [('0.1+0.2','0.3'),('-0.125+0.0','-0.125'),('2.5/10','0.25'),('0.0-0.0','0')])
def test_finite_decimal_display_stays_exact_and_is_rendered(text,display):
    answer=run('evaluate '+text)
    assert answer['result']['decimal']==display and answer['result']['decimal_exact']
    assert Fraction(display)==Fraction(answer['result']['value'])
    assert '```text\n'+display+'\n```' in render_math(answer)
    assert 'decimal' not in run('evaluate 0.1/3')['result']


@pytest.mark.parametrize('equation', ['2*x+3=11','7*x-4=3*x+8','-3*x+2=11','2*z+5=1'])
def test_teaching_equations_and_substitution_have_same_unique_solution(equation):
    answer=run('solve '+equation)
    data=answer['result'];name=data['variable'];syms=symbols([name]);x=syms[name]
    expected=s.simplify(Expression(data['solutions'][0]).value)
    assert len(data['teaching_steps'])>=2
    for step in data['teaching_steps']:
        equation,_=relation(step['equation'],syms,'real')
        assert s.solveset(equation.lhs-equation.rhs,x,domain=s.S.Reals)==s.FiniteSet(expected)
        assert step['reason_ar'] and step['reason_en']
    assert data['substitution']['left']==data['substitution']['right'] and data['substitution']['verified'] is True


@pytest.mark.parametrize('expression,sense,constraints,bound,attained,point',[
    ('3*x+2','min',['x>1','x<5'],'5',False,None),
    ('-2*x+1','min',['x>0','x<=4'],'-7',True,{'x':'4'}),
    ('-2*x+1','max',['x>0','x<=4'],'1',False,None),
    ('x','min',['x>0','x<1','x!=1/2'],'0',False,None)])
def test_affine_extrema_keep_endpoint_membership(expression,sense,constraints,bound,attained,point):
    data=run({'operation':'optimize','expression':expression,'sense':sense,'constraints':constraints})['result']
    assert (data['bound'],data['attained'],data['point'])==(bound,attained,point)


def test_counterexample_search_cannot_prove_a_universal():
    found=run({'operation':'integer_implication','premise':'odd','conclusion':'prime','start':3,'end':20})
    assert found['result']['counterexample']==9 and found['result']['universal_claim_refuted']
    absent=run({'operation':'integer_implication','premise':'prime','conclusion':'odd','start':3,'end':20})
    assert absent['problem_status']=='unresolved' and absent['completeness']=='search_limited'


def test_residual_inspection_without_tolerance_is_not_accepted_as_exact_root():
    data=run({'operation':'verify_root','expression':'10^-10*(x-3)','point':'5'})['result']
    assert Fraction(data['residual'])==Fraction(2,10**10) and data['root_error']=='2'
    assert data['accepted'] is False and data['is_exact_root'] is False and data['tolerance'] is None


def test_risk_reduction_common_denominator_and_zero_baseline():
    request={'operation':'risk_change','before':'2','after':'1','denominator':'1000','period':'year','scope':'hypothetical'}
    output=run(request);data=output['result']
    assert data['absolute_reduction']=='1/1000' and data['percentage_points']=='1/10'
    assert data['relative_reduction']=='1/2' and data['relative_percent']=='50'
    assert 'not an estimate of personal risk' in output['conditions'][0]
    assert run({**request,'before':'0','after':'1'})['result']['relative_reduction'] is None
    with pytest.raises(ValueError):run({**request,'scope':'patient'})


def test_plot_has_computed_points_and_never_joins_a_pole():
    output=run({'operation':'plot','expression':'1/x','bounds':['-2','2'],'samples':40})
    data=output['result'];assert len(data['series'])==2 and data['samples']==40
    assert data['excluded']=='{0}' and output['result_kind']=='approximate'
    for series in data['series']:
        points=series['points'];xs=[Fraction(p['x']) for p in points]
        assert all(x<0 for x in xs) or all(x>0 for x in xs)
        assert all(abs(Fraction(p['x'])*Fraction(p['y'])-1)<Fraction(1,10**13) for p in points)
    assert run('ارسم 1/x واشرح سلوكها قرب الصفر')['result']['bounds']==['-10','10']


@pytest.mark.parametrize('expression,bounds', [('sqrt(x)',['-1','4']),('1/(x-2)',['0','4']),('sin(x)',['-2','2'])])
def test_plot_domain_guards_and_new_functions(expression,bounds):
    output=run({'operation':'plot','expression':expression,'bounds':bounds,'samples':20})
    syms=symbols(['x']);expr=Expression(expression,syms)
    assert output['result']['series']
    for series in output['result']['series']:
        for p in series['points']:
            x=s.Rational(p['x']);exact=expr.value.subs(syms['x'],x)
            assert exact.is_real and exact.is_finite
            assert abs(float(exact)-float(p['y']))<1e-12


@pytest.mark.parametrize('payload',[
    {'operation':'plot','expression':'1/x','bounds':['-1','1'],'samples':101},
    {'operation':'unit_sum','terms':[{'value':'500','unit':'mcg'}],'output_unit':'mL'},
    {'operation':'bayes','prior':'1/100','likelihood':'9/10','false_positive':'1/20','population':10000,'scope':'patient'},
    {'operation':'integer_implication','premise':'prime','conclusion':'odd','start':-10000,'end':10000}])
def test_new_contract_limits_and_personal_scope_are_rejected(payload):
    with pytest.raises(ValueError):run(payload)


def test_unrecognized_qualifier_is_not_dropped():
    with pytest.raises(ValueError):run('حوّل 72 km/h إلى m/s وتجاهل الوحدات')
