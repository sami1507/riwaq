"""Counterexamples discovered independently of the published sixty cases."""
import sympy as s

from backend.math_symbolic import solve
from backend.math_expression import relation, symbols


def test_endpoint_root_does_not_certify_all_roots_without_uniqueness():
    result = solve({'operation': 'root', 'expression': 'x*(x-1)',
                    'bounds': ['0', '2'], 'tolerance': '0.000001'})
    assert result['problem_status'] == 'solved' and result['result']['value'] == '0'
    assert result['completeness'] != 'proved_in_domain'
    assert result['result'].get('unique') is not True


def test_sqrt_derivative_conditions_exclude_zero():
    result = solve({'operation': 'differentiate', 'expression': 'sqrt(x)'})
    assert result['problem_status'] == 'solved'
    declared = symbols(['x'])
    conditions = [relation(c, declared)[0] for c in result['conditions']]
    assert any(s.simplify(c.subs(declared['x'], 0)) is s.false for c in conditions)
    assert all(s.simplify(c.subs(declared['x'], 4)) is s.true for c in conditions)


def test_equal_positive_infinite_limits_are_not_reported_as_disagreeing():
    result = solve({'operation': 'limit', 'expression': '1/x^2', 'point': '0'})
    assert not any('do not agree' in row['detail'] for row in result['verification'])
    data = result['result']
    assert data.get('value') == 'oo' or (data.get('diverges') is True and data.get('direction') == 'oo' and data.get('left') == data.get('right') == 'oo')


def test_continuity_check_recognizes_original_endpoint_root():
    result = solve({'operation': 'continuity_root', 'expression': 'x', 'bounds': ['0', '1']})
    assert result['result']['continuous'] is True
    assert result['result']['root_existence_proved'] is True


def test_integer_evaluation_does_not_certify_fraction_as_integer_result():
    result = solve({'operation': 'evaluate', 'expression': '1/2', 'domain': 'integer'})
    assert result['problem_status'] == 'undefined'
    assert 'value' not in result['result']
