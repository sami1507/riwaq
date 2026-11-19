"""Acceptance and independent variants for bounded exact mathematics."""
from fractions import Fraction
import pytest

from backend.math_discrete import DiscreteError, solve, normalize_solver_outcome


@pytest.mark.parametrize('expression,expected', [
    ('0.1 + 0.2', '3/10'), ('9007199254740993 + 1', '9007199254740994'),
    ('3/7 + 2/5', '29/35'), ('١/٣ + ١/٦', '1/2'),
], ids=['MATH-001', 'MATH-002', 'MATH-003', 'arabic-fraction-variant'])
def test_exact_arithmetic_and_transport(expression, expected):
    data = solve({'operation': 'exact', 'expression': expression})
    assert data['result']['value'] == expected and type(data['result']['value']) is str
    assert data['execution_status'] == 'completed' and data['result_kind'] == 'exact'


def test_sequential_percentage_bases():
    data = solve({'operation': 'percent_changes', 'value': '100', 'changes': ['20', '-20']})
    assert data['result']['value'] == '96'
    assert data['result']['steps'][1]['base'] == '120'
    assert solve({'operation': 'percent_changes', 'value': '250', 'changes': ['10', '-10']})['result']['value'] == '495/2'


@pytest.mark.parametrize('value,expected', [('2.675', '2.68'), ('-2.675', '-2.68'), ('2.6749', '2.67')])
def test_decimal_rounding_uses_original_literal(value, expected):
    result = solve({'operation': 'round', 'value': value, 'places': 2})
    assert result['result']['value'] == expected
    assert result['result_kind'] == 'approximate'


def test_units_exact_and_incompatible_dimensions():
    request = {'operation': 'unit_sum', 'terms': [{'value': '150', 'unit': 'cm'}, {'value': '2', 'unit': 'm'}], 'output_unit': 'm'}
    assert solve(request)['result']['value'] == '7/2'
    request['terms'] = [{'value': '10', 'unit': 'm'}, {'value': '3', 'unit': 's'}]
    data = solve(request)
    assert data['problem_status'] == 'undefined' and 'value' not in data['result']
    request['terms'] = [{'value': '30', 'unit': 'min'}, {'value': '1', 'unit': 'h'}]
    request['output_unit'] = 's'
    assert solve(request)['result']['value'] == '5400'


@pytest.mark.parametrize('premises,conclusion,entails,counterexample', [
    (['P->Q', 'P'], 'Q', True, None),
    (['P->Q', 'Q'], 'P', False, {'P': False, 'Q': True}),
    (['P→Q', '¬Q'], '¬P', True, None),
    (['Rain implies Wet', 'not Rain'], 'not Wet', False, {'Rain': False, 'Wet': True}),
    (['(A or B) and not A'], 'B', True, None),
], ids=['MATH-031', 'MATH-032', 'MATH-033', 'denying-antecedent-renamed', 'disjunctive-syllogism'])
def test_entailment_exhaustive_and_countermodels(premises, conclusion, entails, counterexample):
    data = solve({'operation': 'entailment', 'premises': premises, 'conclusion': conclusion})
    assert data['result']['consistency'] == 'SAT'
    assert data['result']['entails'] is entails
    assert data['result']['counterexample'] == counterexample
    assert data['completeness'] == 'proved_in_domain'


def test_inconsistent_premises_do_not_report_real_world_conclusion():
    data = solve({'operation': 'entailment', 'premises': ['P', 'not P'], 'conclusion': 'R'})
    assert data['problem_status'] == 'inconsistent'
    assert data['result']['consistency'] == 'UNSAT'
    assert data['result']['entails_classically'] is True
    assert data['result']['reliable_real_world_conclusion'] is False
    assert 'entails' not in data['result']


def test_quantifier_order_constructions_and_empty_set_countermodel():
    data = solve({'operation': 'quantifier_order', 'domain': 'real'})
    assert data['result']['forall_x_exists_y_greater'] is True
    assert data['result']['exists_y_forall_x_greater'] is False
    assert data['result']['first_witness'] == 'y=x+1'
    empty = solve({'operation': 'existential_import'})
    assert empty['result']['entailed'] is False and empty['result']['countermodel']['A'] == []
    with pytest.raises(DiscreteError):
        solve({'operation': 'quantifier_order', 'domain': 'bounded'})


def test_entailment_renaming_order_and_no_atoms_preserve_semantics():
    first = solve({'operation': 'entailment', 'premises': ['A->B', 'A'], 'conclusion': 'B'})
    renamed = solve({'operation': 'entailment', 'premises': ['X', 'X->Y'], 'conclusion': 'Y'})
    assert first['result'] == renamed['result']
    tautology = solve({'operation': 'entailment', 'premises': ['true'], 'conclusion': 'false'})
    assert tautology['result']['entails'] is False and tautology['result']['counterexample'] == {}
    inconsistent = solve({'operation': 'entailment', 'premises': ['false'], 'conclusion': 'true'})
    assert inconsistent['problem_status'] == 'inconsistent'


def test_polynomial_parity_proves_all_integers_and_detects_counterexample():
    result = solve({'operation': 'polynomial_even', 'coefficients': [0, 1, 1]})
    assert result['result']['always_even'] is True
    assert result['result']['residues_mod_2'] == [0, 0]
    assert result['completeness'] == 'proved_in_domain'
    assert solve({'operation': 'polynomial_even', 'coefficients': [1, 1, 1]})['result']['counterexample'] == '0'
    assert solve({'operation': 'polynomial_even', 'coefficients': [0, -1, 1]})['result']['always_even'] is True


def test_prime_claim_certified_counterexample_and_unresolved_search():
    request = {'operation': 'prime_counterexample', 'coefficients': [41, 1, 1], 'start': 0, 'end': 40}
    data = solve(request)
    assert data['result']['n'] == '40' and data['result']['value'] == '1681'
    a, b = map(int, data['result']['factors'])
    assert a * b == 1681 and min(a, b) > 1
    request['end'] = 39
    limited = solve(request)
    assert limited['problem_status'] == 'unresolved' and limited['completeness'] == 'search_limited'
    assert limited['result']['claim_refuted'] is None


def test_ordering_uniqueness_cycle_and_renamed_items():
    request = {'operation': 'ordering', 'items': ['C', 'A', 'B'], 'before': [['A', 'B'], ['B', 'C']]}
    data = solve(request)
    assert data['result'] == {'order': ['A', 'B', 'C'], 'unique': True, 'solution_count': 1}
    request['before'].append(['C', 'A'])
    assert solve(request)['problem_status'] == 'no_solution'
    request['before'] = [['A', 'B']]
    assert solve(request)['result']['solution_count'] == 3


def test_unknown_and_timeout_are_not_unsat_and_are_not_user_operations():
    for status, execution in [('UNKNOWN', 'partial'), ('TIMEOUT', 'limits_exceeded')]:
        data = normalize_solver_outcome(status)
        assert data['problem_status'] == 'unresolved' and data['execution_status'] == execution
        assert data['completeness'] == 'not_proved' and not data['verification']
    with pytest.raises(DiscreteError): solve({'operation': 'UNKNOWN'})
    with pytest.raises(DiscreteError): normalize_solver_outcome('UNSAT')


def test_bayes_original_denominator_and_impossible_condition():
    request = {'operation': 'bayes', 'prior': '0.01', 'likelihood': '0.99', 'false_positive': '0.05'}
    data = solve(request)
    assert data['result']['value'] == '1/6'
    assert Fraction(data['result']['evidence_probability']) == Fraction('0.0594')
    request.update(likelihood='0', false_positive='0')
    assert solve(request)['problem_status'] == 'undefined'


def test_finite_probability_combinations_and_binomial():
    dice = solve({'operation': 'dice_sum', 'dice': 2, 'sides': 6, 'total': 7})
    assert dice['result'] == {'value': '1/6', 'favourable': '6', 'sample_space': '36'}
    assert solve({'operation': 'combinations', 'n': 10, 'k': 3})['result']['value'] == '120'
    assert solve({'operation': 'binomial', 'n': 3, 'k': 2, 'p': '1/2'})['result']['value'] == '3/8'
    assert solve({'operation': 'binomial', 'n': 0, 'k': 0, 'p': '0'})['result']['value'] == '1'
    assert solve({'operation': 'dice_sum', 'dice': 1, 'sides': 4, 'total': 10})['result']['value'] == '0'


def test_statistics_keep_zero_missing_and_outlier_and_weighted_denominator():
    data = solve({'operation': 'weighted_mean', 'groups': [{'count': 10, 'mean': '80'}, {'count': 90, 'mean': '20'}]})
    assert data['result']['value'] == '26' and data['result']['known'] == 100
    data = solve({'operation': 'statistics', 'values': ['1', '2', '3', '100']})
    assert data['result']['mean'] == '53/2' and data['result']['median'] == '5/2'
    data = solve({'operation': 'statistics', 'values': ['0', '10', '20', None]})
    assert data['result'] == {'sum': '30', 'mean': '10', 'median': '10', 'known': 3, 'missing': 1, 'total': 4}
    data = solve({'operation': 'statistics', 'values': [None]})
    assert data['result']['mean'] is None and data['problem_status'] == 'insufficient_data'


def test_probability_marginals_union_and_zero_condition():
    request = {'operation': 'probability', 'a': '0.6', 'b': '0.5', 'intersection': '0.2'}
    assert solve(request)['result'] == {'union': '9/10', 'conditional_a_given_b': '2/5'}
    request['intersection'] = '0'
    with pytest.raises(DiscreteError): solve(request)
    request.update(a='0.5', b='0', intersection='0')
    data = solve(request)
    assert data['execution_status'] == 'partial' and data['result']['union'] == '1/2'
    assert data['result']['conditional_a_given_b'] is None


def test_geometry_rechecks_model_and_missing_time_stays_conditional():
    data = solve({'operation': 'rectangle', 'perimeter': '30', 'area': '54'})
    assert data['result']['sides'] == ['6', '9']
    assert solve({'operation': 'rectangle', 'perimeter': '4', 'area': '5'})['problem_status'] == 'no_solution'
    radical = solve({'operation': 'rectangle', 'perimeter': '8', 'area': '1'})
    assert radical['result']['discriminant'] == '12' and radical['result_kind'] == 'exact'
    speed = solve({'operation': 'speed', 'distance': '120'})
    assert speed['problem_status'] == 'insufficient_data' and speed['result_kind'] == 'conditional'
    assert speed['result']['expression'] == '(120)/t' and 'value' not in speed['result']
    assert solve({'operation': 'speed', 'distance': '120', 'duration': '2.5'})['result']['value'] == '48'


@pytest.mark.parametrize('payload', [
    {'operation': 'statistics', 'values': [True]},
    {'operation': 'statistics', 'values': [0.1]},
    {'operation': 'statistics', 'values': ['']},
    {'operation': 'statistics', 'values': ['abc']},
    {'operation': 'statistics', 'values': ['NaN']},
    {'operation': 'exact', 'expression': '__import__("os").system("whoami")'},
    {'operation': 'entailment', 'premises': ['P.__class__'], 'conclusion': 'Q'},
    {'operation': 'entailment', 'premises': ['not ' * 20 + 'P'], 'conclusion': 'Q'},
    {'operation': 'entailment', 'premises': ['P and Q and R and S and T and U and V and W and X'], 'conclusion': 'Q'},
    {'operation': 'entailment', 'premises': ['P'], 'conclusion': 'Q', 'verified': True},
    {'operation': 'round', 'value': '2.675', 'places': True},
    {'operation': 'round', 'value': '2.675', 'places': 2, 'rounding': 'unknown'},
    {'operation': 'combinations', 'n': 10**9, 'k': 3},
    {'operation': 'prime_counterexample', 'coefficients': [41, 1, 1], 'end': 10001},
    {'operation': 'ordering', 'items': ['A', 'A'], 'before': []},
    {'operation': 'ordering', 'items': ['A'], 'before': [['A', 'B']]},
    {'operation': 'bayes', 'prior': '1.1', 'likelihood': '0.5', 'false_positive': '0.1'},
    {'operation': 'binomial', 'n': 100, 'k': 50, 'p': '1/' + '9' * 64},
    {'operation': 'statistics', 'values': ['0'] * 1001},
    {'operation': 'percent_changes', 'value': '10', 'changes': ['1'] * 33},
])
def test_contract_limits_and_untrusted_content_rejected(payload):
    with pytest.raises((ValueError, TypeError)):
        solve(payload)
