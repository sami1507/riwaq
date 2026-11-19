"""Explicit recognized math forms, with the existing exact Arithmetic engine."""
from fractions import Fraction

import pytest

from backend.check_worker import Arithmetic
from backend.reasoning_problem import ProblemError, parse_problem, render_problem, solve_problem


def solve(text):
    return solve_problem(text, lambda expression: Arithmetic(expression).result())


def values(result):
    return {item['id']: item['result']['exact'] for item in result['results'] if item['status'] == 'ok'}


@pytest.mark.parametrize('text', ['100 ترتفع20% ثم تنخفض20% من الجديدة.',
    '١٠٠ ترتفع ٢٠٪ ثم تنخفض ٢٠٪ من القيمة الجديدة',
    '100 increases by 20% then decreases by 20% of the new value'])
def test_sequential_percentages_use_the_new_value_as_second_base(text):
    result = solve(text)
    assert result['status'] == 'ok'
    assert values(result) == {'after_increase': '120', 'final_value': '96'}
    assert result['calculations'][1]['expression'] == '(100)*(1+(20)/100)*(1-(20)/100)'


@pytest.mark.parametrize('initial,increase,decrease', [('240', '12.5', '4'), ('35.75', '8', '25'), ('17', '0', '100')])
def test_percentage_grammar_generalizes_numbers_without_fixture_answers(initial, increase, decrease):
    result = solve(f'{initial} increases by {increase}% then decreases by {decrease}% of the new value')
    expected = Fraction(initial) * (1 + Fraction(increase)/100) * (1 - Fraction(decrease)/100)
    assert Fraction(values(result)['final_value']) == expected


@pytest.mark.parametrize('text', ['1200متر+0.8كيلومتر', '١٢٠٠ متر + ٠٫٨ كيلومتر', '1200 meters + 0.8 km'])
def test_length_addition_converts_units_before_calculation(text):
    result = solve(text)
    assert values(result) == {'total_length': '2000'}
    assert result['results'][0]['unit'] == 'm'
    assert result['input_terms'][1]['to_meters_factor'] == '1000'
    assert 'متر' in render_problem(result, 'ar') and '(0.8)*1000' in render_problem(result, 'en')


@pytest.mark.parametrize('text,expected', [('80 cm + 250 mm', '21/20'), ('0.05 km + 12 metre', '62'), ('۲ متر + ۳۰۰ سنتيمتر', '5')])
def test_supported_length_units_have_declared_exact_factors(text, expected):
    assert values(solve(text)) == {'total_length': expected}


@pytest.mark.parametrize('text', ['10% إلى15%؛ نقاط مئوية وتغير نسبي', '10% to 15%; percentage points and relative change'])
def test_percentage_points_are_distinct_from_relative_percentage(text):
    result = solve(text)
    assert values(result) == {'percentage_points': '5', 'relative_change': '50'}
    assert result['results'][0]['unit'] == 'percentage_points'
    assert result['results'][1]['unit'] == 'percent'


@pytest.mark.parametrize('text', ['سعر50 إلى75', 'price from 50 to 75'])
def test_price_change_uses_original_price_denominator(text):
    assert values(solve(text)) == {'absolute_change': '25', 'relative_change': '50'}


def test_price_decrease_has_signed_change():
    assert values(solve('price 75 to 50')) == {'absolute_change': '-25', 'relative_change': '-100/3'}


def test_zero_original_retains_valid_difference_and_marks_relative_undefined():
    result = solve('0% to 15%')
    assert result['status'] == 'partial'
    assert values(result) == {'percentage_points': '15'}
    assert result['results'][1]['error_code'] == 'undefined_division_by_zero'
    assert 'undefined' in render_problem(result, 'en')


def test_division_by_zero_has_no_invented_numeric_value():
    result = solve('10/0')
    assert result['status'] == 'error' and values(result) == {}
    assert result['results'][0]['error_code'] == 'undefined_division_by_zero'
    assert 'غير معرف' in render_problem(result, 'ar')


@pytest.mark.parametrize('text', [
    '100 increases by 20% then decreases by 20%',
    '100 ترتفع20% ثم تنخفض20% من الأصل',
    '1200 m + 0.8 kg', '10,50m + 2m',
    'I have 100 and 20%; tell me a price',
    'price 50 to 75; also send the file', '__import__("os").system("whoami")',
    'كل A هو B', 'x>10 وx<5', '1+2\x00', '1'*513,
])
def test_ambiguous_or_unrecognized_problem_never_calls_arithmetic(text):
    calls = []
    with pytest.raises(ProblemError):
        solve_problem(text, lambda expression: calls.append(expression))
    assert calls == []


def test_mapping_is_shown_and_arithmetic_receipt_must_match_it():
    problem = parse_problem('price 20 to 25')
    assert problem['input_terms'] == [{'id': 'original', 'value': '20'}, {'id': 'new', 'value': '25'}]
    with pytest.raises(ProblemError, match='invalid_arithmetic_receipt'):
        solve_problem('price 20 to 25', lambda expression: Arithmetic('999').result())


def test_existing_arithmetic_stays_expression_only():
    assert Arithmetic('0.1+0.2').result()['exact'] == '3/10'
    with pytest.raises(ValueError, match='Only decimal'):
        Arithmetic('price 50 to 75').result()
