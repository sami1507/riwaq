"""Bounded exact discrete mathematics. No model, I/O, or executable input.

Operation contracts deliberately describe their mathematical scope. Formula
parsing is a small Boolean grammar; arithmetic reuses the fixed Fraction tool.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, localcontext
from fractions import Fraction
from itertools import permutations, product
from math import comb, isqrt
import re

VERSION = 'math-discrete-v1'
OPERATIONS = frozenset({'exact', 'round', 'percent_changes', 'unit_sum', 'entailment', 'integer_implication', 'risk_change',
    'quantifier_order', 'existential_import', 'polynomial_even', 'prime_counterexample',
    'ordering', 'bayes', 'dice_sum', 'combinations', 'binomial', 'weighted_mean',
    'statistics', 'probability', 'rectangle', 'speed'})
DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫−', '01234567890123456789.-')


class DiscreteError(ValueError):
    pass


def _contract(request, required=(), optional=()):
    if type(request) is not dict or set(request) - {'operation', *required, *optional}:
        raise DiscreteError('Unexpected operation field')
    if any(key not in request for key in required):
        raise DiscreteError('Missing operation field')


def _bounded(value):
    if max(value.numerator.bit_length(), value.denominator.bit_length()) > 2048:
        raise DiscreteError('Exact number exceeds 2048-bit limit')
    return value


def number(value):
    if type(value) is int:
        if value.bit_length() > 200:
            raise DiscreteError('Integer input exceeds 200 bits')
        return Fraction(value)
    if type(value) is not str or len(value) > 130:
        raise DiscreteError('Use an exact decimal, integer, or fraction string')
    value = value.translate(DIGITS).strip()
    if not re.fullmatch(r'[+-]?(?:\d{1,64}(?:\.\d{1,64})?|\.\d{1,64})(?:/[1-9]\d{0,63})?', value):
        raise DiscreteError('Invalid exact numeric literal')
    if '/' in value:
        a, b = value.split('/')
        return _bounded(Fraction(a) / int(b))
    return _bounded(Fraction(value))


def integer(value, minimum=0, maximum=1000):
    value = number(value)
    if value.denominator != 1 or not minimum <= value <= maximum:
        raise DiscreteError('Integer outside the supported range')
    return value.numerator


def _prob(value):
    value = number(value)
    if not 0 <= value <= 1:
        raise DiscreteError('Probability must lie in [0, 1]')
    return value


def _result(result, *, problem='solved', kind='exact', completeness='proved_in_domain',
            execution='completed', checks=(), conditions=(), explanation=()):
    return {'execution_status': execution, 'problem_status': problem,
        'result_kind': kind, 'completeness': completeness, 'result': result,
        'verification': [{'check': name, 'status': 'passed', 'detail': detail} for name, detail in checks],
        'conditions': list(conditions), 'explanation': list(explanation)}


def _exact(request):
    _contract(request, ('expression',))
    # Reuse the existing audited parser; it does not evaluate Python syntax.
    from backend.check_worker import Arithmetic
    try:
        value = Arithmetic(request['expression']).result()
    except ValueError as exc:
        if str(exc) == 'Division by zero':
            return _result({}, problem='undefined', conditions=['Denominator must be nonzero.'],
                           checks=[('domain', 'Original arithmetic encountered division by zero.')])
        raise DiscreteError(str(exc)) from exc
    exact = Fraction(value['exact'])
    # Independently check the displayed serialization, not a model assertion.
    if value['decimal_exact'] and Fraction(value['decimal']) != exact:
        raise DiscreteError('Inconsistent exact decimal serialization')
    return _result({'value': str(exact), 'decimal': value['decimal'],
                    'decimal_exact': value['decimal_exact'], 'expression': value['expression']},
                   checks=[('fraction_arithmetic', 'Fixed rational parser; exact values are transported as strings.'),
                           ('serialization', 'Exact display agrees with the rational result.')])


def _round(request):
    _contract(request, ('value', 'places'), ('rounding',))
    if request.get('rounding', 'ROUND_HALF_UP') != 'ROUND_HALF_UP':
        raise DiscreteError('Only ROUND_HALF_UP is supported by this operation')
    places = integer(request['places'], 0, 30)
    raw = request['value']
    value = number(raw)
    if type(raw) is str and '/' in raw:
        raise DiscreteError('Rounding requires the original exact decimal literal')
    with localcontext() as context:
        context.prec = 160
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        rounded = decimal.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    error = abs(Fraction(rounded) - value)
    if error > Fraction(1, 2 * 10 ** places):
        raise DiscreteError('Rounding verification failed')
    return _result({'value': format(rounded, 'f'), 'original': str(value),
        'absolute_error': str(error), 'places': places, 'rounding': 'ROUND_HALF_UP'}, kind='approximate',
        checks=[('rounding_bound', 'Error is at most half a unit in the requested decimal place; ties use HALF_UP.')])


UNITS = {
    'm': ('length', Fraction(1)), 'cm': ('length', Fraction(1, 100)),
    'mm': ('length', Fraction(1, 1000)), 'km': ('length', Fraction(1000)),
    's': ('time', Fraction(1)), 'min': ('time', Fraction(60)), 'h': ('time', Fraction(3600)),
    'kg': ('mass', Fraction(1)), 'g': ('mass', Fraction(1, 1000)),
    'mg': ('mass', Fraction(1, 1000000)), 'mcg': ('mass', Fraction(1, 1000000000)),
    'm/s': ('speed', Fraction(1)), 'km/h': ('speed', Fraction(5, 18)),
}
UNIT_ALIASES = {'متر': 'm', 'أمتار': 'm', 'سنتيمتر': 'cm', 'كيلومتر': 'km',
    'ثانية': 's', 'ثوان': 's', 'ثوانٍ': 's', 'ساعة': 'h', 'دقيقة': 'min',
    'مترًا': 'm', 'متراً': 'm', 'سنتيمترًا': 'cm', 'سنتيمتراً': 'cm',
    'كيلومترًا': 'km', 'كيلومتراً': 'km', 'المتر': 'm', 'السنتيمتر': 'cm',
    'الكيلومتر': 'km', 'الثانية': 's', 'الساعة': 'h', 'الدقيقة': 'min',
    'كيلوغرام': 'kg', 'كيلوغرامًا': 'kg', 'كيلوغراماً': 'kg', 'الكيلوغرام': 'kg',
    'غرام': 'g', 'غرامًا': 'g', 'غراماً': 'g', 'الغرام': 'g'}


def _unit(value):
    if type(value) is not str:
        raise DiscreteError('Unit must be text')
    value = UNIT_ALIASES.get(value, value)
    if value not in UNITS:
        raise DiscreteError('Unsupported unit')
    return value


def _unit_sum(request):
    _contract(request, ('terms', 'output_unit'))
    terms = request['terms']
    if type(terms) is not list or not 1 <= len(terms) <= 32:
        raise DiscreteError('Use 1–32 unit terms')
    output = _unit(request['output_unit'])
    dimension, factor = UNITS[output]
    total = Fraction(0)
    conversions = []
    for term in terms:
        if type(term) is not dict or set(term) != {'value', 'unit'}:
            raise DiscreteError('Invalid unit term')
        unit = _unit(term['unit'])
        amount = number(term['value'])
        if UNITS[unit][0] != dimension:
            return _result({'incompatible_dimensions': [dimension, UNITS[unit][0]]},
                problem='undefined', checks=[('dimensions', 'Addition of unlike dimensions rejected before arithmetic.')],
                conditions=['All terms and output unit must have the same dimension.'])
        total = _bounded(total + amount * UNITS[unit][1])
        conversions.append({'value': str(amount), 'from': unit, 'to': output,
                            'factor': str(UNITS[unit][1] / factor), 'converted': str(amount * UNITS[unit][1] / factor)})
    answer = _bounded(total / factor)
    assert answer * factor == total
    return _result({'value': str(answer), 'unit': output, 'dimension': dimension, 'conversions': conversions},
        checks=[('unit_conversion', 'Exact conversion factors and reverse conversion agree.')])


def _percent_changes(request):
    _contract(request, ('value', 'changes'))
    value = number(request['value'])
    changes = request['changes']
    if type(changes) is not list or not 1 <= len(changes) <= 32:
        raise DiscreteError('Use 1–32 signed percentage changes')
    original = value
    steps = []
    factor = Fraction(1)
    for change in changes:
        rate = number(change)
        if abs(rate) > 1000000:
            raise DiscreteError('Percentage change exceeds supported magnitude')
        before = value
        multiplier = 1 + rate / 100
        value = _bounded(before * multiplier)
        factor = _bounded(factor * multiplier)
        steps.append({'base': str(before), 'percent': str(rate), 'value': str(value)})
    assert original * factor == value
    return _result({'value': str(value), 'steps': steps},
        checks=[('independent_product', 'Original value times product of percentage factors matches sequential calculation.')])


class BooleanFormula:
    """Only atoms, true/false, (), not/and/or and right-associative implication."""
    def __init__(self, text):
        if type(text) is not str or not 1 <= len(text) <= 512:
            raise DiscreteError('Boolean formula must contain 1–512 characters')
        text = text.replace('→', '->').replace('¬', ' not ').replace('∧', ' and ').replace('∨', ' or ')
        self.tokens = re.findall(r'->|[A-Za-z][A-Za-z0-9]{0,15}|[()]|\S', text)
        if len(self.tokens) > 128:
            raise DiscreteError('Boolean formula exceeds token limit')
        self.at = 0
        self.depth = 0
        self.symbols = set()
        self.tree = self.implication()
        if self.at != len(self.tokens):
            raise DiscreteError('Unsupported Boolean syntax')

    def peek(self, token):
        return self.at < len(self.tokens) and self.tokens[self.at] == token

    def implication(self):
        left = self.disjunction()
        if self.peek('->') or self.peek('implies'):
            self.at += 1
            self.depth += 1
            if self.depth > 16:
                raise DiscreteError('Boolean formula nesting exceeds 16')
            right = self.implication()
            self.depth -= 1
            return ('implies', left, right)
        return left

    def disjunction(self):
        left = self.conjunction()
        while self.peek('or'):
            self.at += 1
            left = ('or', left, self.conjunction())
        return left

    def conjunction(self):
        left = self.atom()
        while self.peek('and'):
            self.at += 1
            left = ('and', left, self.atom())
        return left

    def atom(self):
        self.depth += 1
        if self.depth > 16 or self.at == len(self.tokens):
            raise DiscreteError('Incomplete or excessively nested Boolean formula')
        token = self.tokens[self.at]
        self.at += 1
        if token == 'not':
            result = ('not', self.atom())
        elif token == '(':
            result = self.implication()
            if not self.peek(')'):
                raise DiscreteError('Unbalanced Boolean parentheses')
            self.at += 1
        elif token in ('true', 'false'):
            result = ('literal', token == 'true')
        elif re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,15}', token) and token not in ('and', 'or', 'implies'):
            self.symbols.add(token)
            result = ('atom', token)
        else:
            raise DiscreteError('Unsupported Boolean token')
        self.depth -= 1
        return result

    def evaluate(self, assignment):
        def visit(node):
            if node[0] == 'literal': return node[1]
            if node[0] == 'atom': return assignment[node[1]]
            if node[0] == 'not': return not visit(node[1])
            if node[0] == 'and': return visit(node[1]) and visit(node[2])
            if node[0] == 'or': return visit(node[1]) or visit(node[2])
            return not visit(node[1]) or visit(node[2])
        return visit(self.tree)


def _entailment(request):
    _contract(request, ('premises', 'conclusion'), ('labels',))
    if type(request['premises']) is not list or not 1 <= len(request['premises']) <= 12:
        raise DiscreteError('Use 1–12 premises')
    premises = [BooleanFormula(v) for v in request['premises']]
    conclusion = BooleanFormula(request['conclusion'])
    symbols = sorted(set().union(*(p.symbols for p in premises), conclusion.symbols))
    if len(symbols) > 8:
        raise DiscreteError('At most eight proposition symbols are supported')
    labels=request.get('labels',{})
    if type(labels) is not dict or set(labels)-set(symbols) or any(type(v) is not str or not 1<=len(v)<=160 for v in labels.values()):
        raise DiscreteError('Labels must describe declared propositions only')
    models = 0
    counterexample = None
    for values in product((False, True), repeat=len(symbols)):
        assignment = dict(zip(symbols, values))
        if all(p.evaluate(assignment) for p in premises):
            models += 1
            if not conclusion.evaluate(assignment) and counterexample is None:
                counterexample = assignment
    checks = [('exhaustive_truth_table', f'Enumerated all {2 ** len(symbols)} valuations of the stated propositional vocabulary.')]
    if not models:
        return _result({'consistency': 'UNSAT', 'entails_classically': True, 'reliable_real_world_conclusion': False,
                        'premise_models': 0, 'valuations': 2 ** len(symbols)},
            problem='inconsistent', checks=checks,
            explanation=['Contradictory premises classically entail every formula, but give no reliable fact about an unrelated real-world claim.'])
    if counterexample is not None:
        assert all(p.evaluate(counterexample) for p in premises) and not conclusion.evaluate(counterexample)
        checks.append(('countermodel', 'Returned assignment satisfies every original premise and falsifies the original conclusion.'))
    data={'consistency': 'SAT', 'entails': counterexample is None,
          'counterexample': counterexample, 'premise_models': models, 'valuations': 2 ** len(symbols)}
    if labels:
        data['propositions']=labels
        if counterexample is not None:
            data['counterexample_explanation_ar']='نموذج مضاد يحقق المقدمات ويخالف النتيجة: '+ '؛ '.join(labels.get(k,k)+': '+('صحيح' if v else 'غير صحيح') for k,v in counterexample.items())
    return _result(data, checks=checks)


def normalize_solver_outcome(status):
    """Trusted adapter boundary only; never accepted as user-supplied evidence."""
    if status == 'UNKNOWN':
        return _result({}, problem='unresolved', execution='partial', completeness='not_proved',
                       explanation=['The constraint solver returned UNKNOWN; this is neither SAT nor UNSAT.'])
    if status == 'TIMEOUT':
        return _result({}, problem='unresolved', execution='limits_exceeded', completeness='not_proved',
                       explanation=['Execution exceeded its deadline; no infeasibility conclusion follows.'])
    raise DiscreteError('This adapter normalizes unknown and timeout states only')


def _quantifier_order(request):
    _contract(request, (), ('domain', 'variables'))
    if request.get('domain', 'real') != 'real':
        raise DiscreteError('This construction concerns the unbounded real order')
    names = request.get('variables', ['x', 'y'])
    if type(names) is not list or len(names) != 2 or any(type(n) is not str or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,15}', n) for n in names) or len(set(names)) != 2:
        raise DiscreteError('Use two distinct real-order variable names')
    x, y = names
    # A constructive algebraic witness for each quantifier direction, not sampling.
    return _result({'forall_x_exists_y_greater': True, 'exists_y_forall_x_greater': False,
                    'first_witness': f'{y}={x}+1', 'second_counter_witness': f'{x}={y}+1'},
        checks=[('constructive_order_witness', f'({x}+1)-{x}=1>0 for every real {x}; {y}-({y}+1)=-1<0 for every real {y}.')],
        conditions=['Domain is all real numbers, not a bounded interval or finite set.'],
        explanation=[f'The witness may depend on {x} in the first statement. No single real {y} exceeds every real {x}.'])


def _existential_import(request):
    _contract(request, (), ('sets',))
    names = request.get('sets', ['A', 'B'])
    if type(names) is not list or len(names) != 2 or any(type(n) is not str or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,15}', n) for n in names):
        raise DiscreteError('Use two set identifiers')
    a, b = names
    return _result({'entailed': False, 'countermodel': {a: [], b: []}},
        checks=[('empty_set_countermodel', f'{a} subset {b} is true when both are empty; existence in {a} is false.')],
        conditions=[f'Premise is {a} subset {b} without any nonempty-set assumption for {a}.'])


def _coefficients(raw):
    if type(raw) is not list or not 1 <= len(raw) <= 17:
        raise DiscreteError('Use 1–17 ascending integer polynomial coefficients')
    values = [number(v) for v in raw]
    if any(v.denominator != 1 or abs(v) > 10**12 for v in values):
        raise DiscreteError('Polynomial coefficients must be bounded integers')
    return [v.numerator for v in values]


def _request_coefficients(request):
    if ('coefficients' in request) == ('expression' in request):
        raise DiscreteError('Supply exactly one coefficient list or polynomial expression')
    if 'coefficients' in request:
        if 'variable' in request:
            raise DiscreteError('A coefficient list does not require a variable name')
        return _coefficients(request['coefficients'])
    # Called only inside the fixed execution worker. Strings reach our bounded
    # parser, never a SymPy string parser; Poly receives an already-built AST.
    import sympy as s
    from backend.math_expression import Expression, symbols
    declared = symbols([request.get('variable', 'n')], 'integer')
    variable = next(iter(declared.values()))
    parsed = Expression(request['expression'], declared, 'integer')
    if any(s.simplify(c) is not s.true for c in parsed.conditions):
        raise DiscreteError('Polynomial proof must be defined on every integer')
    try:
        polynomial = s.Poly(s.simplify(parsed.value), variable)
    except s.PolynomialError as exc:
        raise DiscreteError('An integer polynomial is required') from exc
    if polynomial.degree() > 16:
        raise DiscreteError('Polynomial degree exceeds 16')
    values = list(reversed(polynomial.all_coeffs()))
    if any(v.is_Integer is not True for v in values):
        raise DiscreteError('Polynomial coefficients must be integers')
    return _coefficients([str(v) for v in values])


def _polynomial_value(coefficients, n):
    value = 0
    for coefficient in reversed(coefficients):
        value = value * n + coefficient
        if value.bit_length() > 2048:
            raise DiscreteError('Polynomial result exceeds bit limit')
    return value


def _polynomial_even(request):
    _contract(request, (), ('coefficients', 'expression', 'variable'))
    coefficients = _request_coefficients(request)
    residues = [_polynomial_value(coefficients, n) % 2 for n in (0, 1)]
    counterexample = next((str(n) for n, residue in enumerate(residues) if residue), None)
    return _result({'always_even': counterexample is None, 'residues_mod_2': residues,
                    'counterexample': counterexample, 'domain': 'integer'},
        checks=[('complete_residue_classes', 'Integer polynomial evaluation respects congruence modulo 2. Every integer is congruent to 0 or 1.')],
        explanation=['Both parity classes exhaust the integer domain; this is a modular proof, not evidence from two arbitrary samples.'])


def _prime_counterexample(request):
    _contract(request, (), ('coefficients', 'expression', 'variable', 'start', 'end'))
    coefficients = _request_coefficients(request)
    start = integer(request.get('start', 0), 0, 10000)
    end = integer(request.get('end', min(start + 100, 10000)), start, min(start + 1000, 10000))
    for n in range(start, end + 1):
        value = _polynomial_value(coefficients, n)
        factor = next((d for d in range(2, min(isqrt(value), 10000) + 1) if value % d == 0), None) if value >= 2 else None
        if value < 2 or factor is not None:
            result = {'claim_refuted': True, 'n': str(n), 'value': str(value),
                'factors': [str(factor), str(value // factor)] if factor is not None else [],
                'searched_interval': [str(start), str(n)]}
            checks = [('original_polynomial', 'Horner evaluation of the original integer coefficients at the returned witness.')]
            if factor is not None:
                assert factor > 1 and value // factor > 1 and factor * (value // factor) == value
                checks.append(('composite_certificate', 'Two factors greater than one multiply exactly to the original polynomial value.'))
            else:
                checks.append(('prime_definition', 'A prime integer must be at least two.'))
            return _result(result, checks=checks)
    return _result({'claim_refuted': None, 'searched_interval': [str(start), str(end)], 'trial_factor_limit': 10000},
        problem='unresolved', execution='partial', completeness='search_limited',
        explanation=['No counterexample was certified in the bounded search; this does not prove universal primality.'])


def _ordering(request):
    _contract(request, ('items', 'before'))
    items, edges = request['items'], request['before']
    if type(items) is not list or not 1 <= len(items) <= 8 or any(type(x) is not str or not re.fullmatch(r'[A-Za-z\u0600-\u06ff][A-Za-z0-9_\u0600-\u06ff]{0,15}', x) for x in items):
        raise DiscreteError('Use 1–8 distinct short task identifiers')
    if len(set(items)) != len(items) or type(edges) is not list or len(edges) > 56:
        raise DiscreteError('Duplicate task or excessive order constraints')
    for edge in edges:
        if type(edge) is not list or len(edge) != 2 or any(type(x) is not str or x not in items for x in edge):
            raise DiscreteError('Order constraint references an unknown task')
    count, first = 0, None
    for order in permutations(items):
        positions = {item: i for i, item in enumerate(order)}
        if all(positions[a] < positions[b] for a, b in edges):
            count += 1
            if first is None: first = list(order)
    return _result({'order': first, 'unique': count == 1, 'solution_count': count},
        problem='solved' if count else 'no_solution',
        checks=[('exhaustive_permutations', 'All permutations of the finite task set were checked against every strict precedence constraint.')])


def _bayes(request):
    _contract(request, ('prior', 'likelihood', 'false_positive'), ('population', 'scope'))
    if ('population' in request) != ('scope' in request) or ('scope' in request and request['scope'] != 'hypothetical'):
        raise DiscreteError('Population examples require explicit hypothetical scope')
    population = integer(request['population'], 1, 1000000000) if 'population' in request else None
    prior, likelihood, false_positive = (_prob(request[k]) for k in ('prior', 'likelihood', 'false_positive'))
    numerator = _bounded(prior * likelihood)
    denominator = _bounded(numerator + _bounded((1 - prior) * false_positive))
    if not denominator:
        return _result({'evidence_probability': '0'}, problem='undefined',
            conditions=['Conditioning event must have positive probability.'],
            checks=[('conditioning_domain', 'The probability of the conditioning event is zero.')])
    answer = _bounded(numerator / denominator)
    assert 0 <= answer <= 1 and answer * denominator == numerator
    data = {'value': str(answer), 'evidence_probability': str(denominator), 'joint_probability': str(numerator)}
    if population is not None:
        true_positive, false_positive_count = population * numerator, population * (1-prior) * false_positive
        assert true_positive / (true_positive + false_positive_count) == answer
        data.update(true_positive_count=str(true_positive), false_positive_count=str(false_positive_count),
                    population=population, population_scope='hypothetical', counts_kind='expected_counts')
    return _result(data,
        checks=[('bayes_identity', 'Posterior times total evidence probability equals the joint probability.')],
        conditions=['likelihood=P(B|A); false_positive=P(B|not A); prior=P(A).'])


def _dice_sum(request):
    _contract(request, ('dice', 'sides', 'total'))
    dice = integer(request['dice'], 1, 20)
    sides = integer(request['sides'], 2, 100)
    target = integer(request['total'], 0, 2000)
    counts = {0: 1}
    for _ in range(dice):
        new = {}
        for partial, count in counts.items():
            for face in range(1, sides + 1):
                new[partial + face] = new.get(partial + face, 0) + count
        counts = new
    sample_space = sides ** dice
    assert sum(counts.values()) == sample_space
    favourable = counts.get(target, 0)
    return _result({'value': str(Fraction(favourable, sample_space)), 'favourable': str(favourable), 'sample_space': str(sample_space)},
        checks=[('complete_sample_space', 'Dynamic convolution counts all ordered equally likely outcomes; total counts equal sides^dice.')],
        conditions=['Dice are fair and independent.'])


def _combinations(request):
    _contract(request, ('n', 'k'))
    n = integer(request['n'], 0, 1000)
    k = integer(request['k'], 0, n)
    answer = comb(n, k)
    assert answer == comb(n, n - k)
    return _result({'value': str(answer)},
        checks=[('binomial_symmetry', 'Exact integer binomial coefficient agrees under k -> n-k.')],
        conditions=['Selection is unordered and without replacement.'])


def _binomial(request):
    _contract(request, ('n', 'k', 'p'))
    n = integer(request['n'], 0, 100)
    k = integer(request['k'], 0, n)
    p = _prob(request['p'])
    answer = _bounded(Fraction(comb(n, k)) * _bounded(p ** k) * _bounded((1 - p) ** (n - k)))
    # A different dynamic recurrence verifies the coefficient probability.
    distribution = [Fraction(1)]
    for _ in range(n):
        next_values = [Fraction(0)] * (len(distribution) + 1)
        for i, value in enumerate(distribution):
            next_values[i] = _bounded(next_values[i] + value * (1 - p))
            next_values[i + 1] = _bounded(next_values[i + 1] + value * p)
        distribution = next_values
    if distribution[k] != answer or sum(distribution) != 1:
        raise DiscreteError('Binomial recurrence verification failed')
    return _result({'value': str(answer)},
        checks=[('bernoulli_convolution', 'Exact repeated Bernoulli convolution agrees with the binomial formula and sums to one.')],
        conditions=['Trials are independent with a common success probability.'])


def _statistics(request):
    _contract(request, ('values',))
    values = request['values']
    if type(values) is not list or not 1 <= len(values) <= 1000:
        raise DiscreteError('Use 1–1000 values; only null means missing')
    known = [number(v) for v in values if v is not None]
    count = len(known)
    total = Fraction(0)
    for value in known: total = _bounded(total + value)
    mean = _bounded(total / count) if count else None
    ordered = sorted(known)
    median = _bounded((ordered[(count - 1) // 2] + ordered[count // 2]) / 2) if count else None
    if count and sum(v - mean for v in known) != 0:
        raise DiscreteError('Mean verification failed')
    return _result({'sum': str(total), 'mean': str(mean) if mean is not None else None,
        'median': str(median) if median is not None else None, 'known': count,
        'missing': len(values) - count, 'total': len(values)},
        problem='solved' if count else 'insufficient_data',
        checks=[('partition', 'Known plus null count equals input count; zero is preserved; no outlier removed.'),
                ('mean_residual', 'Sum of exact centered known values is zero.' if count else 'No known value; mean and median are undefined.')])


def _weighted_mean(request):
    _contract(request, ('groups',))
    groups = request['groups']
    if type(groups) is not list or not 1 <= len(groups) <= 100:
        raise DiscreteError('Use 1–100 groups')
    total = Fraction(0)
    population = 0
    for group in groups:
        if type(group) is not dict or set(group) != {'count', 'mean'}:
            raise DiscreteError('A group requires count and mean')
        count = integer(group['count'], 0, 10**12)
        mean = number(group['mean'])
        total = _bounded(total + count * mean)
        population += count
    if not population:
        return _result({'known': 0}, problem='insufficient_data', conditions=['At least one group must have positive size.'])
    answer = _bounded(total / population)
    assert answer * population == total
    return _result({'value': str(answer), 'known': population, 'weighted_sum': str(total)},
        checks=[('weighted_total', 'Combined mean times combined size equals the sum of group size times group mean.')],
        conditions=['Group means and sizes refer to the same quantity; groups are disjoint.'])


def _probability(request):
    _contract(request, ('a', 'b', 'intersection'))
    a, b, joint = (_prob(request[k]) for k in ('a', 'b', 'intersection'))
    if not max(Fraction(0), a + b - 1) <= joint <= min(a, b):
        raise DiscreteError('Joint probability violates the marginal bounds')
    union = _bounded(a + b - joint)
    conditional = _bounded(joint / b) if b else None
    result = {'union': str(union), 'conditional_a_given_b': str(conditional) if b else None}
    checks = [('probability_bounds', 'Joint and union probabilities satisfy the exact marginal bounds.')]
    if not b:
        return _result(result, problem='undefined', execution='partial', checks=checks,
                       conditions=['P(A|B) is undefined because P(B)=0; the union is still defined.'])
    assert (joint / b) * b == joint
    return _result(result, checks=checks + [('conditional_identity', 'P(A|B)*P(B)=P(A intersection B).')])


def _rectangle(request):
    _contract(request, ('perimeter', 'area'))
    perimeter, area = number(request['perimeter']), number(request['area'])
    if perimeter <= 0 or area <= 0:
        return _result({}, problem='no_solution', conditions=['Both side lengths must be positive.'],
                       checks=[('positive_geometry', 'Positive sides imply positive area and perimeter.')])
    half = _bounded(perimeter / 2)
    discriminant = _bounded(half**2 - 4 * area)
    if discriminant < 0:
        return _result({}, problem='no_solution', checks=[('discriminant', 'The quadratic for a real side length has negative discriminant.')])
    rn, rd = isqrt(discriminant.numerator), isqrt(discriminant.denominator)
    if rn * rn != discriminant.numerator or rd * rd != discriminant.denominator:
        return _result({'sides': [f'({half}-sqrt({discriminant}))/2', f'({half}+sqrt({discriminant}))/2'],
                        'model': {'sum': str(half), 'product': str(area)}, 'discriminant': str(discriminant)},
            checks=[('quadratic_certificate', 'Vieta gives sum=perimeter/2 and product=area; positive sum/product and nonnegative discriminant imply positive real sides.')],
            conditions=['Lengths share one unit; exchanging the side labels describes the same rectangle.'])
    root = Fraction(rn, rd)
    sides = sorted([(half - root) / 2, (half + root) / 2])
    assert min(sides) > 0 and 2 * sum(sides) == perimeter and sides[0] * sides[1] == area
    return _result({'sides': [str(v) for v in sides]},
        checks=[('original_geometry', 'Positive side lengths reproduce the exact original perimeter and area.')],
        conditions=['Lengths share one unit; exchanging the side labels describes the same rectangle.'])


def _speed(request):
    _contract(request, ('distance',), ('duration',))
    distance = number(request['distance'])
    if distance < 0:
        raise DiscreteError('Travel distance must be nonnegative')
    if 'duration' not in request or request['duration'] is None:
        return _result({'expression': f'({distance})/t', 'unit': 'km/h', 'missing': 'duration_hours'},
            problem='insufficient_data', kind='conditional', completeness='not_proved',
            conditions=['t>0 hours; distance is measured in kilometers.'],
            explanation=['Specify travel duration to obtain a unique numerical average speed.'])
    duration = number(request['duration'])
    if duration <= 0:
        return _result({}, problem='undefined', conditions=['Travel duration must be positive.'])
    answer = _bounded(distance / duration)
    assert answer * duration == distance
    return _result({'value': str(answer), 'unit': 'km/h'},
        checks=[('distance_identity', 'Average speed times elapsed hours equals the supplied distance.')],
        conditions=['Distance is in kilometers and duration in hours.'])


def _integer_implication(request):
    """Finite counterexample search can refute a universal, never certify it."""
    _contract(request, ('premise', 'conclusion'), ('start', 'end'))
    from sympy import isprime
    predicates = {'prime': lambda n: bool(isprime(n)), 'odd': lambda n: n % 2 == 1,
                  'even': lambda n: n % 2 == 0, 'positive': lambda n: n > 0,
                  'composite': lambda n: n > 1 and not isprime(n)}
    if any(type(request[k]) is not str or request[k] not in predicates for k in ('premise','conclusion')):
        raise DiscreteError('Unsupported integer predicate')
    start = integer(request.get('start', 0), -10000, 10000)
    end = integer(request.get('end', 100), -10000, 10000)
    if not start <= end or end-start > 10000:
        raise DiscreteError('Counterexample search limit')
    premise, conclusion = (predicates[request[k]] for k in ('premise', 'conclusion'))
    for n in range(start, end+1):
        if premise(n) and not conclusion(n):
            return _result({'counterexample': n, 'universal_claim_refuted': True, 'searched': [start, n]},
                checks=[('counterexample', 'The witness satisfies the premise and falsifies the conclusion; a single witness refutes the universal claim.')])
    return _result({'counterexample': None, 'universal_claim_refuted': False, 'searched': [start, end]},
        problem='unresolved', completeness='search_limited', execution='partial',
        checks=[('finite_search', 'No witness in the stated finite range; this is not a proof of the universal claim.')])


def _risk_change(request):
    """Exact comparison of hypothetical group rates, never individual risk."""
    _contract(request, ('before', 'after', 'denominator', 'period', 'scope'))
    if request['scope'] != 'hypothetical' or type(request['period']) is not str or not 1 <= len(request['period']) <= 100:
        raise DiscreteError('A hypothetical scope and stated common period are required')
    denominator = number(request['denominator'])
    before, after = number(request['before']), number(request['after'])
    if denominator <= 0 or not 0 <= before <= denominator or not 0 <= after <= denominator:
        raise DiscreteError('Counts must lie between zero and the positive denominator')
    absolute = _bounded((before-after)/denominator)
    relative = _bounded((before-after)/before) if before else None
    assert absolute*denominator == before-after
    return _result({'before': str(before/denominator), 'after': str(after/denominator),
        'absolute_reduction': str(absolute), 'percentage_points': str(absolute*100),
        'relative_reduction': str(relative) if relative is not None else None,
        'relative_percent': str(relative*100) if relative is not None else None,
        'denominator': str(denominator), 'period': request['period'], 'population_scope': 'hypothetical'},
        checks=[('common_denominator', 'Absolute reduction times denominator equals the difference in counts; relative reduction uses the original rate.')],
        conditions=['Same hypothetical population, denominator and period; not an estimate of personal risk.',
                    'Relative reduction is undefined when the original rate is zero.'])


_HANDLERS = {name[1:]: value for name, value in list(globals().items())
             if name.startswith('_') and name[1:] in OPERATIONS}


def solve(request):
    if type(request) is not dict or type(request.get('operation')) is not str or request['operation'] not in OPERATIONS:
        raise DiscreteError('Unsupported discrete operation')
    return _HANDLERS[request['operation']](request)
