"""Small, explicit AR/EN math-problem grammar; arithmetic stays in Arithmetic.

This module grants no authority and performs no I/O. It does not extract numbers
from arbitrary prose, solve general natural-language questions, or evaluate code.
"""
from __future__ import annotations

import re

VERSION = 'problem-v1'
NUMBER = r'(?:[0-9]{1,40}(?:\.[0-9]{1,24})?|\.[0-9]{1,24})'
DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٪×÷−', '01234567890123456789.%*/-')
LENGTH_UNITS = {
    'm': ('m', '1'), 'meter': ('m', '1'), 'meters': ('m', '1'), 'metre': ('m', '1'), 'metres': ('m', '1'), 'متر': ('m', '1'), 'أمتار': ('m', '1'),
    'km': ('km', '1000'), 'kilometer': ('km', '1000'), 'kilometers': ('km', '1000'), 'kilometre': ('km', '1000'), 'kilometres': ('km', '1000'), 'كيلومتر': ('km', '1000'), 'كيلومترات': ('km', '1000'),
    'cm': ('cm', '(1/100)'), 'centimeter': ('cm', '(1/100)'), 'centimeters': ('cm', '(1/100)'), 'سنتيمتر': ('cm', '(1/100)'),
    'mm': ('mm', '(1/1000)'), 'millimeter': ('mm', '(1/1000)'), 'millimeters': ('mm', '(1/1000)'), 'مليمتر': ('mm', '(1/1000)'),
}


class ProblemError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def parse_problem(text):
    if type(text) is not str or not 1 <= len(text.strip()) <= 512 or any(ord(c) < 32 and c not in '\t\r\n' for c in text):
        raise ProblemError('invalid_problem_text')
    normalized = re.sub(r'\s+', ' ', text.translate(DIGITS).strip()).rstrip('؟?').strip()
    # Sentence-final punctuation is allowed, but arbitrary trailing requests or
    # source instructions never become part of a supported mathematical clause.
    if normalized.endswith('.'):
        normalized = normalized[:-1].rstrip()
    calculations, terms = [], []

    def add(identifier, expression, unit):
        if len(expression) > 512:
            raise ProblemError('problem_expression_limit')
        calculations.append({'id': identifier, 'expression': expression, 'unit': unit})

    # A percentage change applies to the value at that step, only when the
    # second base is explicit. No assumption about an unstated original base.
    sequential = re.fullmatch(
        rf'(?:قيمة\s*|سعر\s*)?(?P<initial>{NUMBER})\s*(?:ترتفع|تزيد|تزداد)\s*(?:بنسبة\s*)?(?P<rise>{NUMBER})\s*%\s*ثم\s*(?:تنخفض|تنقص)\s*(?:بنسبة\s*)?(?P<fall>{NUMBER})\s*%\s*من\s*(?:القيمة\s*)?الجديدة', normalized)
    if sequential is None:
        sequential = re.fullmatch(rf'(?:a value of\s+|a price of\s+)?(?P<initial>{NUMBER})\s+increases by\s+(?P<rise>{NUMBER})\s*%\s+then decreases by\s+(?P<fall>{NUMBER})\s*%\s+of the new value', normalized, re.I)
    if sequential:
        a, up, down = (sequential[name] for name in ('initial', 'rise', 'fall'))
        terms = [{'id': 'initial', 'value': a}, {'id': 'increase_percent', 'value': up}, {'id': 'decrease_percent', 'value': down}]
        add('after_increase', f'({a})*(1+({up})/100)', 'quantity')
        add('final_value', f'({a})*(1+({up})/100)*(1-({down})/100)', 'quantity')
        kind = 'sequential_percentages'
    else:
        units = '|'.join(re.escape(unit) for unit in sorted(LENGTH_UNITS, key=len, reverse=True))
        length = re.fullmatch(rf'(?P<a>{NUMBER})\s*(?P<ua>{units})\s*\+\s*(?P<b>{NUMBER})\s*(?P<ub>{units})', normalized, re.I)
        points = re.fullmatch(rf'(?:من\s*)?(?P<a>{NUMBER})\s*%\s*(?:إلى|الى)\s*(?P<b>{NUMBER})\s*%(?:\s*[؛;،,]\s*نقاط مئوية وتغير نسبي)?', normalized)
        if points is None:
            points = re.fullmatch(rf'(?:from\s+)?(?P<a>{NUMBER})\s*%\s+to\s+(?P<b>{NUMBER})\s*%(?:\s*[;,]\s*percentage points and relative change)?', normalized, re.I)
        price = re.fullmatch(rf'سعر\s*(?P<a>{NUMBER})\s*(?:إلى|الى)\s*(?P<b>{NUMBER})', normalized)
        if price is None:
            price = re.fullmatch(rf'price\s+(?:from\s+)?(?P<a>{NUMBER})\s+to\s+(?P<b>{NUMBER})', normalized, re.I)
        if length:
            ua, fa = LENGTH_UNITS[length['ua'].lower()]
            ub, fb = LENGTH_UNITS[length['ub'].lower()]
            terms = [{'value': length['a'], 'unit': ua, 'to_meters_factor': fa}, {'value': length['b'], 'unit': ub, 'to_meters_factor': fb}]
            add('total_length', f"({length['a']})*{fa}+({length['b']})*{fb}", 'm')
            kind = 'length_sum'
        elif points or price:
            match = points or price
            a, b = match['a'], match['b']
            terms = [{'id': 'original', 'value': a}, {'id': 'new', 'value': b}]
            add('percentage_points' if points else 'absolute_change', f'({b})-({a})', 'percentage_points' if points else 'quantity')
            add('relative_change', f'(({b})-({a}))/({a})*100', 'percent')
            kind = 'percentage_comparison' if points else 'price_change'
        elif re.fullmatch(r'[0-9.\s()+*/-]+', normalized) and re.search(r'[+*/-]', normalized):
            add('value', normalized, 'scalar')
            kind = 'arithmetic_expression'
        else:
            raise ProblemError('unsupported_or_ambiguous_problem')
    return {'version': VERSION, 'kind': kind, 'input_terms': terms, 'calculations': calculations,
            'scope': 'Exact recognized grammar and stated length conversion factors only; no arbitrary natural-language interpretation or source inference.'}


def solve_problem(text, calculate):
    """The trusted caller supplies the existing fixed Arithmetic evaluator."""
    if not callable(calculate):
        raise ProblemError('arithmetic_evaluator_required')
    problem = parse_problem(text)
    results = []
    for item in problem['calculations']:
        try:
            result = calculate(item['expression'])
        except ValueError as exc:
            code = 'undefined_division_by_zero' if str(exc) == 'Division by zero' else 'arithmetic_rejected'
            results.append({**item, 'status': 'error', 'error_code': code})
        else:
            if type(result) is not dict or result.get('expression') != item['expression'] or type(result.get('decimal_exact')) is not bool or type(result.get('exact')) is not str or type(result.get('decimal')) is not str:
                raise ProblemError('invalid_arithmetic_receipt')
            results.append({**item, 'status': 'ok', 'result': result})
    successes = sum(item['status'] == 'ok' for item in results)
    return {**problem, 'status': 'ok' if successes == len(results) else 'partial' if successes else 'error', 'results': results}


def render_problem(problem, language='ar'):
    if language not in ('ar', 'en') or type(problem) is not dict or problem.get('version') != VERSION or not isinstance(problem.get('results'), list):
        raise ProblemError('invalid_problem_result')
    labels = {'after_increase': ('بعد الزيادة', 'After increase'), 'final_value': ('القيمة النهائية', 'Final value'),
              'total_length': ('الطول الكلي', 'Total length'), 'percentage_points': ('فرق النقاط المئوية', 'Percentage-point difference'),
              'absolute_change': ('التغير المطلق', 'Absolute change'), 'relative_change': ('التغير النسبي من الأصل', 'Relative change from original'), 'value': ('النتيجة', 'Result')}
    units = {'m': ('متر', 'm'), 'percent': ('%', '%'), 'percentage_points': ('نقطة مئوية', 'percentage points'), 'quantity': ('', ''), 'scalar': ('', '')}
    index = 0 if language == 'ar' else 1
    lines = []
    for item in problem['results']:
        if item.get('id') not in labels or item.get('unit') not in units or type(item.get('expression')) is not str:
            raise ProblemError('invalid_problem_result')
        label = labels[item['id']][index]
        if item.get('status') == 'ok':
            result = item['result']; value = result['decimal'] if result['decimal_exact'] else result['exact']
            if not re.fullmatch(r'-?[0-9]+(?:[./][0-9]+)?', value):
                raise ProblemError('invalid_problem_result')
            displayed = f"{value} {units[item['unit']][index]}".strip()
            lines.append(f"{label}: **{displayed}**")
        elif item.get('error_code') == 'undefined_division_by_zero':
            lines.append(label + (': غير معرف — المقام صفر.' if language == 'ar' else ': undefined — denominator is zero.'))
        else:
            lines.append(label + (': تعذر الحساب ضمن الحدود.' if language == 'ar' else ': calculation was rejected within the limits.'))
        lines.append('```text\n' + item['expression'] + '\n```')
    lines.append('حُسبت الصيغة المعترف بها محليًا؛ لا يشمل ذلك تفسير المسائل الحرة أو التحقق من بيانات مصادر خارجية.' if language == 'ar' else 'Computed the recognized form locally; arbitrary problem interpretation and external source data were not verified.')
    return '\n\n'.join(lines)
