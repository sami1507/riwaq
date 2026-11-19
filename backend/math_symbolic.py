"""Bounded exact symbolic operations over the reviewed mathematical grammar.

Called only inside the fixed, resource-limited checker. This is not a general
Python environment, a natural-language solver, or a formal proof assistant.
"""
from __future__ import annotations

import itertools
import math
import sympy as s
from sympy.calculus.util import continuous_domain, function_range
from sympy.solvers.solveset import NonlinearError
from backend.math_expression import Expression, MathInputError, normalize, numeric, relation, symbols

OPERATIONS = {'evaluate', 'solve', 'inequality', 'system', 'matrix_inverse', 'eigen',
              'diophantine', 'optimize', 'differentiate', 'derivative_at', 'integrate',
              'limit', 'ode_linear', 'root', 'verify_root', 'continuity_root', 'plot'}
COMMON = {'operation', 'domain', 'variable', 'variables', 'explanation_level'}
FIELDS = {
    'evaluate': {'expression'}, 'solve': {'expression', 'constraints'},
    'inequality': {'expression', 'constraints'}, 'system': {'equations', 'constraints'},
    'matrix_inverse': {'matrix'}, 'eigen': {'matrix'}, 'diophantine': {'expression'},
    'optimize': {'expression', 'constraints', 'sense'}, 'differentiate': {'expression'},
    'derivative_at': {'expression', 'point'}, 'integrate': {'expression', 'bounds', 'constraints'},
    'limit': {'expression', 'point'}, 'ode_linear': {'coefficient', 'initial'},
    'root': {'expression', 'bounds', 'tolerance'}, 'verify_root': {'expression', 'point', 'tolerance'},
    'continuity_root': {'expression', 'bounds'},
    'plot': {'expression', 'bounds', 'samples'},
}


def show(value):
    text = s.sstr(value)
    if len(text) > 6000: raise MathInputError('result_length_limit')
    return text


def finite_decimal(value):
    """Exact terminating base-ten display, without binary floats or rounding."""
    if not value.is_Rational: return None
    denominator=int(value.q);twos=fives=0
    while denominator%2==0: denominator//=2;twos+=1
    while denominator%5==0: denominator//=5;fives+=1
    places=max(twos,fives)
    if denominator!=1 or places>100: return None
    scaled=abs(int(value.p))*(2**(places-twos))*(5**(places-fives))
    digits=str(scaled).rjust(places+1,'0')
    text=digits[:-places]+'.'+digits[-places:] if places else digits
    return ('-' if value<0 else '')+text


def check(name, detail, status='passed'):
    return {'check': name, 'status': status, 'detail': detail}


def result(data=None, *, status='solved', kind='exact', complete='proved_in_domain',
           checks=(), conditions=(), explanation=(), execution='completed'):
    return {'execution_status': execution, 'problem_status': status, 'result_kind': kind,
            'completeness': complete, 'result': data or {}, 'verification': list(checks),
            'conditions': [c if isinstance(c,str) else (show(c.lhs)+' '+c.rel_op+' '+show(c.rhs)) if getattr(c,'is_Relational',False) else show(c) for c in conditions],
            'explanation': list(explanation)}


def unresolved(reason, conditions=()):
    return result({'reason': reason}, status='unresolved', complete='not_proved',
                  execution='partial', conditions=conditions)


def condition_set(conditions, x, domain):
    allowed = domain
    for c in conditions:
        c = s.simplify(c)
        if c is s.true: continue
        if c is s.false: return s.EmptySet
        if c.free_symbols - {x}: raise NotImplementedError('parameter_domain_condition')
        if isinstance(c, s.Unequality):
            allowed = allowed - s.solveset(c.lhs - c.rhs, x, domain=domain)
        elif isinstance(c, s.Equality):
            allowed = allowed.intersect(s.solveset(c.lhs - c.rhs, x, domain=domain))
        else:
            if domain != s.S.Reals: raise NotImplementedError('ordered_complex_constraint')
            allowed = allowed.intersect(s.solve_univariate_inequality(c, x, relational=False))
    return allowed


def truth(c, substitution=None):
    value = s.simplify(c.subs(substitution or {}, simultaneous=True))
    return value is s.true


def constraints(request, syms, domain):
    rows = request.get('constraints', [])
    if type(rows) is not list or len(rows) > 12: raise MathInputError('constraint_limit')
    values, guards = [], []
    for text in rows:
        item, extra = relation(text, syms, domain); values.append(item); guards.extend(extra)
    return values + guards


def equation(text, syms, domain):
    eq, guards = relation(text, syms, domain)
    if not isinstance(eq, s.Equality): raise MathInputError('equation_required')
    return eq, guards


def equation_solve(request, syms, x, domain):
    eq, guards = equation(request['expression'], syms, request.get('domain', 'real'))
    guards += constraints(request, syms, request.get('domain', 'real'))
    expr = eq.lhs - eq.rhs
    # A general scalar linear equation with parameters must retain the zero
    # coefficient branch. No division by a possibly zero parameter is hidden.
    if expr.free_symbols - {x}:
        if domain == s.S.Integers: return unresolved('integer_parameter_case_split_not_supported', guards)
        try: poly = s.Poly(expr, x)
        except s.PolynomialError: return unresolved('parameterized_nonpolynomial_equation', guards)
        if poly.degree() != 1 or guards:
            return unresolved('parameterized_equation_outside_linear_scope', guards)
        a, b = poly.all_coeffs()
        cases = [{'condition': show(s.Ne(a, 0, evaluate=False)), 'solutions': [show(-b / a)], 'problem_status':'solved'}]
        all_condition=s.simplify(s.And(s.Eq(a,0,evaluate=False),s.Eq(b,0,evaluate=False)))
        none_condition=s.simplify(s.And(s.Eq(a,0,evaluate=False),s.Ne(b,0,evaluate=False)))
        if all_condition is not s.false: cases.append({'condition':show(all_condition),'set':show(domain),'problem_status':'solved'})
        if none_condition is not s.false: cases.append({'condition':show(none_condition),'solutions':[],'problem_status':'no_solution'})
        return result({'cases': cases, 'variable': str(x),'coefficient':show(a),'constant':show(b)}, kind='conditional',
                      checks=[check('linear_case_split', 'a*x+b=0: nonzero coefficient and both zero-coefficient branches retained.')])
    allowed = condition_set(guards, x, domain)
    solved = s.solveset(expr, x, domain=allowed)
    if solved.has(s.ConditionSet): return unresolved('symbolic_solution_remains_conditional', guards)
    if solved is s.EmptySet:
        return result({'solutions': [], 'set': 'EmptySet', 'variable': str(x)}, status='no_solution',
                      conditions=guards, checks=[check('original_domain', 'Solveset intersected with all original denominator, root, logarithm and explicit constraints.')])
    if isinstance(solved, s.FiniteSet):
        verified, rejected = [], []
        for candidate in solved:
            if s.simplify(expr.subs(x, candidate)) == 0 and all(truth(c, {x: candidate}) for c in guards):
                verified.append(show(candidate))
            else: rejected.append(show(candidate))
        if rejected: return unresolved('candidate_verification_inconclusive', guards)
        data = {'solutions': verified, 'set': show(solved), 'variable': str(x)}
        if len(verified) == 1 and eq.lhs.is_polynomial(x) and eq.rhs.is_polynomial(x) and s.Poly(expr, x).degree() == 1:
            candidate=next(iter(solved))
            data.update(linear_teaching(eq, x, candidate))
            if not guards:
                # A separate exercise shifts the RHS by the nonzero linear
                # coefficient. It is computed and independently substituted,
                # never presented as another solution of the original task.
                coefficient=s.expand(expr).coeff(x)
                example=s.Eq(eq.lhs,eq.rhs+coefficient,evaluate=False)
                example_solution=s.solveset(example.lhs-example.rhs,x,domain=domain)
                if example_solution==s.FiniteSet(candidate+1):
                    data['teaching_example']={'request':{'operation':'solve','expression':show(example.lhs)+' = '+show(example.rhs),
                        'variable':str(x),'domain':request.get('domain','real')},'solution':show(candidate+1),
                        **linear_teaching(example,x,candidate+1), 'conditions': [], 'result_kind':'exact'}
        return result(data, conditions=guards,
                      checks=[check('substitute_original', 'Every returned candidate satisfies the original equation and all saved domain restrictions.'),
                              check('solution_set', 'SymPy solveset returned a finite set in the stated domain; substitution checks validity, not completeness by itself.')])
    data={'set': show(solved), 'variable': str(x)}
    simple=s.simplify(expr)
    if domain==s.S.Reals and simple.func==s.sin and not guards:
        arg=simple.args[0]
        if arg.is_polynomial(x) and s.Poly(arg,x).degree()==1:
            a,b=s.Poly(arg,x).all_coeffs();k=s.Symbol('k',integer=True)
            family=(k*s.pi-b)/a
            if s.simplify(simple.subs(x,family))==0:
                data['families']=[{'expression':show(family),'parameter':'k','parameter_domain':'integer'}]
    return result(data, kind='parametric', conditions=guards,
                  checks=[check('symbolic_solution_set', 'Solveset returned the complete symbolic set in the declared domain, including periodic families.')])


def linear_teaching(eq, x, candidate):
    """Exact operations on both sides; explanatory text never generates values."""
    left, right = s.expand(eq.lhs), s.expand(eq.rhs)
    steps = []
    def append(ar, en):
        steps.append({'equation': show(left)+' = '+show(right), 'reason_ar': ar, 'reason_en': en})
    append('المعادلة الأصلية؛ نبحث عن قيمة المجهول التي تحقق المساواة.',
           'The original equation asks for a value of the unknown that makes both sides equal.')
    coefficient_right = right.coeff(x)
    if coefficient_right != 0:
        left, right = s.expand(left-coefficient_right*x), s.expand(right-coefficient_right*x)
        append('نطرح '+show(coefficient_right*x)+' من الطرفين؛ إجراء العملية نفسها يحافظ على المساواة.',
               'Subtract '+show(coefficient_right*x)+' from both sides; the same operation preserves equality.')
    constant = left.subs(x, 0)
    if constant != 0:
        left, right = s.expand(left-constant), s.expand(right-constant)
        append('نطرح '+show(constant)+' من الطرفين لإلغاء الحد الثابت والمحافظة على المساواة.',
               'Subtract '+show(constant)+' from both sides to remove the constant while preserving equality.')
    coefficient = left.coeff(x)
    if coefficient == 0: raise MathInputError('linear_teaching_zero_divisor')
    left, right = s.simplify(left/coefficient), s.simplify(right/coefficient)
    if left != x or s.simplify(right-candidate) != 0: raise MathInputError('linear_teaching_verification_failed')
    append('نقسم الطرفين على '+show(coefficient)+'، وهو غير صفري؛ القسمة تعكس الضرب.',
           'Divide both sides by the nonzero coefficient '+show(coefficient)+'; division reverses multiplication.')
    l, r = s.simplify(eq.lhs.subs(x,candidate)), s.simplify(eq.rhs.subs(x,candidate))
    if l != r: raise MathInputError('linear_teaching_substitution_failed')
    return {'teaching_steps': steps,
        'substitution': {'expression': show(eq.lhs)+' = '+show(eq.rhs), 'variable': str(x),
                         'value': show(candidate), 'left': show(l), 'right': show(r), 'verified': True},
        'teaching': {'variable': str(x), 'meaning_ar': 'المجهول: العدد الذي نبحث عنه.', 'meaning_en': 'The unknown is the number we seek.',
            'alternative_ar': 'تخيّل المساواة ككفتي ميزان: إزالة المقدار نفسه من الكفتين تحافظ على التوازن. هذا تشبيه للعملية، والتحقق هو التعويض في المعادلة الأصلية.',
            'alternative_en': 'Think of equality as two balanced scales: removing the same amount from each preserves balance. This illustrates the operation; substitution in the original equation verifies the answer.'}}


def system_solve(request, syms, domain):
    rows = request.get('equations')
    if type(rows) is not list or not 1 <= len(rows) <= 6: raise MathInputError('equation_count_limit')
    equations, guards = [], constraints(request, syms, domain)
    for text in rows:
        eq, extra = equation(text, syms, domain); equations.append(eq.lhs - eq.rhs); guards.extend(extra)
    variables = list(syms.values())
    try:
        matrix, constants = s.linear_eq_to_matrix(equations, variables)
    except (s.PolynomialError, NonlinearError):
        matrix = None
    if matrix is not None:
        rank, augmented = matrix.rank(), matrix.row_join(constants).rank()
        if rank < augmented:
            return result({'solutions': [], 'rank': rank, 'augmented_rank': augmented}, status='inconsistent',
                          checks=[check('rank_inconsistency', 'rank(A) < rank([A|b]); no solution.')])
        solved = s.linsolve((matrix, constants), variables)
        complete = 'proved_in_domain'
    else:
        # Polynomial systems only: nonlinsolve's transcendental conditions and
        # complex branches are not silently treated as complete real solutions.
        if any(not e.is_polynomial(*variables) for e in equations): return unresolved('nonpolynomial_system_not_supported', guards)
        if any(s.Poly(e, *variables).total_degree() > 4 for e in equations): raise MathInputError('system_degree_limit')
        solved = s.nonlinsolve(equations, variables); complete = 'proved_in_domain'
        rank = augmented = None
    if not isinstance(solved, s.FiniteSet): return unresolved('system_solution_set_unresolved', guards)
    output, inconclusive = [], False
    for row in solved:
        mapping = dict(zip(variables, row))
        if domain == 'real' and any(v.is_real is False for v in row): continue
        if domain == 'integer' and any(v.is_integer is False for v in row): continue
        if domain == 'integer' and any(v.is_integer is not True for v in row): inconclusive = True; continue
        if any(v.is_real is None for v in row) and domain == 'real': inconclusive = True; continue
        if not all(s.simplify(e.subs(mapping, simultaneous=True)) == 0 for e in equations): inconclusive = True; continue
        if not all(truth(c, mapping) for c in guards): inconclusive = True; continue
        output.append({str(v): show(a) for v, a in mapping.items()})
    if inconclusive: return unresolved('system_candidate_or_domain_inconclusive', guards)
    free = sorted({str(v) for row in solved for item in row for v in item.free_symbols})
    return result({'solutions': output, 'parameters': free, 'rank': rank, 'augmented_rank': augmented},
                  status='solved' if output else 'no_solution', kind='parametric' if free else 'exact', complete=complete,
                  conditions=guards, checks=[check('substitute_system', 'Every tuple was substituted simultaneously into every original equation and constraint.'),
                                            check('system_method', 'Exact rank/linsolve for linear systems; polynomial nonlinsolve for bounded nonlinear systems.')])


def matrix_operation(request):
    domain=request.get('domain','real')
    if domain=='integer':raise MathInputError('matrix_domain_real_or_complex_required')
    rows = request.get('matrix')
    if type(rows) is not list or not 1 <= len(rows) <= 5 or any(type(r) is not list or len(r) != len(rows) for r in rows):
        raise MathInputError('square_matrix_dimension_limit')
    matrix = s.Matrix([[numeric(v, domain=='real') for v in row] for row in rows])
    if request['operation'] == 'matrix_inverse':
        det = s.simplify(matrix.det())
        if det == 0:
            return result({'determinant': '0', 'inverse': None, 'rank': matrix.rank()}, status='no_solution',
                          checks=[check('determinant', 'The exact determinant is zero; the square matrix is singular.')])
        inverse = matrix.inv()
        if s.simplify(matrix * inverse) != s.eye(matrix.rows): return unresolved('inverse_identity_check_failed')
        return result({'determinant': show(det), 'inverse': [[show(v) for v in row] for row in inverse.tolist()]},
                      checks=[check('matrix_product', 'A * inverse = identity exactly.')])
    entries = []
    for value, multiplicity, vectors in matrix.eigenvects():
        if domain=='real' and value.is_real is False:continue
        if domain=='real' and value.is_real is not True:return unresolved('real_eigenvalue_membership_unresolved')
        if any(s.simplify(matrix * vector - value * vector) != s.zeros(matrix.rows, 1) or vector == s.zeros(matrix.rows, 1) for vector in vectors):
            return unresolved('eigenvector_check_failed')
        entries.append({'value': show(value), 'multiplicity': int(multiplicity), 'vectors': [[show(v) for v in vec] for vec in vectors]})
    return result({'eigenpairs': entries,'domain':domain}, status='solved' if entries else 'no_solution',
                  checks=[check('eigen_equation', 'Each nonzero vector satisfies A*v=lambda*v exactly; eigenvalues are filtered by the declared real or complex domain.')])


def diophantine(request, syms):
    if request.get('domain') != 'integer' or len(syms) != 2: raise MathInputError('two_integer_variables_required')
    variables = list(syms.values()); eq, guards = equation(request['expression'], syms, 'integer')
    expr = s.expand(eq.lhs - eq.rhs)
    if guards or not expr.is_polynomial(*variables) or s.Poly(expr, *variables).total_degree() != 1:
        raise MathInputError('linear_integer_equation_required')
    a, b = (expr.coeff(v) for v in variables); c = -expr.subs(dict.fromkeys(variables, 0))
    if not all(v.is_Integer for v in (a, b, c)) or a == b == 0: raise MathInputError('integer_coefficients_required')
    gcd = s.gcd(a, b)
    if c % gcd:
        return result({'solutions': []}, status='no_solution', checks=[check('gcd_divisibility', 'gcd(a,b) does not divide the right hand side.')])
    u, v, g = s.gcdex(a, b); t = s.Symbol('t', integer=True)
    mapping = {variables[0]: u * c / g + b / g * t, variables[1]: v * c / g - a / g * t}
    if s.simplify(expr.subs(mapping, simultaneous=True)) != 0: return unresolved('diophantine_substitution_failed')
    return result({'solutions': [{str(k): show(v) for k, v in mapping.items()}], 'parameter': 't', 'parameter_domain': 'integer'}, kind='parametric',
                  checks=[check('bezout_and_gcd', 'Bezout gives one solution; coprime reduced coefficients give the complete integer family.'),
                          check('substitute_original', 'The parametric tuple satisfies the original equation identically.')])


def optimize(request, syms, domain):
    sense = request.get('sense', 'min')
    if sense not in ('min', 'max') or domain == 'complex': raise MathInputError('invalid_optimization_domain_or_sense')
    expr = Expression(request['expression'], syms, domain)
    guards = expr.conditions + constraints(request, syms, domain)
    variables = list(syms.values())
    if not expr.value.is_polynomial(*variables): return unresolved('optimization_polynomial_scope', guards)
    poly = s.Poly(expr.value, *variables)
    if len(variables) == 1 and poly.degree() <= 1 and domain == 'real':
        x = variables[0]
        allowed = condition_set(guards, x, s.S.Reals)
        if allowed is s.EmptySet:
            return result({'solutions': []}, status='inconsistent', conditions=guards,
                          checks=[check('empty_feasible_domain','The original constraints have no common point.')])
        image = function_range(expr.value, x, allowed)
        bound = image.inf if sense == 'min' else image.sup
        attained = image.contains(bound) is s.true
        point = None
        if attained:
            candidates = s.solveset(expr.value-bound, x, domain=allowed)
            if isinstance(candidates, s.FiniteSet) and candidates:
                point = next(iter(candidates))
            else: return unresolved('constant_objective_representative_not_selected', guards)
            if not truth(allowed.contains(point)) or s.simplify(expr.value.subs(x,point)-bound) != 0:
                return unresolved('optimization_attainment_check_failed', guards)
        return result({'bound': show(bound), 'bound_kind': 'infimum' if sense=='min' else 'supremum',
            'attained': attained, 'point': {str(x):show(point)} if point is not None else None,
            'value': show(bound) if attained else None, 'range': show(image), 'optimality':'global'},
            conditions=guards, checks=[check('affine_range','Computed the exact range on the original feasible set; endpoint membership distinguishes an attained extremum from a bound.')])
    if len(variables) == 1 and poly.degree() == 2 and not guards and domain == 'real':
        a, b, c = poly.all_coeffs(); x = variables[0]
        if (sense == 'min' and a > 0) or (sense == 'max' and a < 0):
            point = -b / (2 * a); value = s.simplify(expr.value.subs(x, point))
            identity = s.simplify(expr.value - value - a * (x - point) ** 2)
            return result({'value': show(value), 'point': {str(x): show(point)}, 'optimality': 'global'},
                          checks=[check('completed_square', f'f - optimum = {show(a)}*({show(x-point)})^2; identity residual {show(identity)}.')])
        return result({'attainment': 'unbounded'}, status='no_solution', checks=[check('quadratic_sign', 'The requested objective is unbounded.')])
    if len(variables) > 2 or poly.total_degree() > 1 or any(not (c.lhs-c.rhs).is_polynomial(*variables) or s.Poly(c.lhs-c.rhs, *variables).total_degree() > 1 for c in guards):
        return unresolved('optimization_supports_linear_two_variables_or_unconstrained_scalar_quadratic', guards)
    if any(isinstance(c, (s.StrictLessThan, s.StrictGreaterThan, s.Unequality)) for c in guards):
        return unresolved('strict_open_feasible_set_not_certified', guards)
    from sympy.solvers.simplex import lpmin, lpmax, InfeasibleLPError, UnboundedLPError
    try:
        objective, mapping = (lpmin if sense == 'min' else lpmax)(expr.value, guards)
        if domain == 'integer':
            # Exhaustive finite lattice enumeration uses separately proven LP
            # coordinate bounds. Rounding a real optimum is never accepted.
            ranges = []
            for variable in variables:
                lo = int(s.ceiling(lpmin(variable, guards)[0])); hi = int(s.floor(lpmax(variable, guards)[0]))
                if hi - lo > 10000: raise MathInputError('integer_search_limit')
                ranges.append(range(lo, hi + 1))
            if math.prod(map(len, ranges)) > 10000: raise MathInputError('integer_search_limit')
            candidates = []
            for row in itertools.product(*ranges):
                m = dict(zip(variables, row))
                if all(truth(c, m) for c in guards): candidates.append((s.simplify(expr.value.subs(m)), m))
            if not candidates: return result({'solutions': []}, status='no_solution', checks=[check('finite_integer_enumeration', 'No feasible point in the proven integer bounding box.')])
            objective, mapping = (min if sense == 'min' else max)(candidates, key=lambda a: a[0])
        if not all(truth(c, mapping) for c in guards) or s.simplify(expr.value.subs(mapping) - objective) != 0:
            return unresolved('optimization_primal_check_failed', guards)
        data={'value': show(objective), 'point': {str(k): show(v) for k,v in mapping.items()}, 'optimality': 'global'}
        checks=[check('constraints_and_objective', 'Independently substituted the returned point into every constraint and the objective.'),
                check('optimality_method', 'Exact simplex optimum.' if domain == 'real' else 'Exhaustive integer enumeration within exact simplex coordinate bounds.')]
        if domain == 'real':
            certificate=linear_bound_certificate(expr.value,guards,variables,mapping,objective,sense)
            if certificate is None: return unresolved('independent_linear_optimality_certificate_unavailable',guards)
            data['bound_certificate']=certificate
            checks.append(check('dual_bound_identity','Nonnegative multiples of the original constraints produce the objective bound, attained at the verified point.'))
        return result(data,
                      conditions=guards, checks=[check('constraints_and_objective', 'Independently substituted the returned point into every constraint and the objective.'),
                                                *checks[1:]])
    except InfeasibleLPError:
        return result({'solutions': []}, status='inconsistent', conditions=guards, checks=[check('linear_feasibility', 'Exact simplex reported infeasible constraints.')])
    except UnboundedLPError:
        return unresolved('unbounded_objective_or_integer_bounding_box', guards)


def linear_bound_certificate(objective, constraints, variables, point, optimum, sense):
    transformed=objective if sense=='max' else -objective
    constant=transformed.subs(dict.fromkeys(variables,0));target=s.Matrix([transformed.coeff(v) for v in variables])
    rows=[]
    for c in constraints:
        if isinstance(c,s.Equality):expressions=[c.lhs-c.rhs,c.rhs-c.lhs]
        elif isinstance(c,s.LessThan):expressions=[c.lhs-c.rhs]
        elif isinstance(c,s.GreaterThan):expressions=[c.rhs-c.lhs]
        else:return None
        for expression in expressions:
            expression=s.expand(expression)
            if s.simplify(expression.subs(point))!=0:continue
            coefficients=[expression.coeff(v) for v in variables]
            bound=-expression.subs(dict.fromkeys(variables,0))
            rows.append((coefficients,bound,expression))
    for count in range(1,min(len(variables),len(rows))+1):
        for chosen in itertools.combinations(rows,count):
            matrix=s.Matrix.hstack(*[s.Matrix(r[0]) for r in chosen]);weights=s.linsolve((matrix,target))
            if not isinstance(weights,s.FiniteSet) or len(weights)!=1:continue
            weight=list(weights)[0]
            if any(w.free_symbols or w.is_nonnegative is not True for w in weight):continue
            value=s.simplify(constant+sum(w*r[1] for w,r in zip(weight,chosen)))
            if value!=(optimum if sense=='max' else -optimum):continue
            return {'sense':sense,'weights':[show(w) for w in weight],'constraints':[show(r[2])+' <= 0' for r in chosen],'bound':show(optimum)}
    return None


def derivative_rule(f,x):
    if f==x:return s.S.One
    if not f.has(x):return s.S.Zero
    if f.func==s.Add:return s.Add(*[derivative_rule(a,x) for a in f.args])
    if f.func==s.Mul:
        return s.Add(*[derivative_rule(a,x)*s.Mul(*(f.args[:i]+f.args[i+1:])) for i,a in enumerate(f.args)])
    if f.func==s.Pow and not f.args[1].has(x):
        base,power=f.args;return power*base**(power-1)*derivative_rule(base,x)
    arg=f.args[0];inner=derivative_rule(arg,x)
    rules={s.sin:lambda:s.cos(arg),s.cos:lambda:-s.sin(arg),s.tan:lambda:1/s.cos(arg)**2,
           s.exp:lambda:s.exp(arg),s.log:lambda:1/arg,s.Abs:lambda:s.sign(arg)}
    if f.func in rules:return rules[f.func]()*inner
    raise NotImplementedError('derivative_rule_not_supported')


def two_sided_domain(f, guards, x, point):
    allowed=continuous_domain(f,x,s.S.Reals).intersect(condition_set(guards,x,s.S.Reals))
    left=allowed.intersect(s.Interval.open(point-1,point))
    right=allowed.intersect(s.Interval.open(point,point+1))
    return left.closure.contains(point) is s.true and right.closure.contains(point) is s.true


def calculus(request, syms, x, domain):
    if domain != 'real': raise MathInputError('calculus_real_domain_required')
    op = request['operation']
    if op == 'ode_linear':
        a = numeric(request['coefficient']); initial = request.get('initial')
        if type(initial) is not dict or set(initial) != {'point', 'value'}: raise MathInputError('initial_point_and_value_required')
        p, v = numeric(initial['point']), numeric(initial['value'])
        y = v*s.exp(a*(x-p))
        if s.simplify(s.diff(y,x)-a*y) != 0 or s.simplify(y.subs(x,p)-v) != 0: return unresolved('ode_verification_failed')
        return result({'expression': show(y)}, conditions=['x in Reals'],
                      checks=[check('ode_and_initial', 'y prime - a*y = 0 and y(initial point) = initial value exactly.')])
    parsed = Expression(request['expression'], syms, domain); f = parsed.value
    if f.free_symbols - {x}: raise MathInputError('single_variable_calculus_required')
    guards = parsed.conditions + (constraints(request,syms,domain) if op=='integrate' else [])
    if op == 'differentiate':
        derivative = s.diff(f, x)
        if derivative.has(s.Derivative): return unresolved('unevaluated_derivative', guards)
        manual=derivative_rule(f,x)
        if s.simplify(derivative-manual)!=0:return unresolved('independent_derivative_rule_check_failed',guards)
        # At a root of an absolute-value argument the generic chain rule needs a
        # separate point check. Do not silently define the derivative as zero.
        guards=guards+[s.Ne(a.args[0],0,evaluate=False) for a in f.atoms(s.Abs)]
        denominator=s.denom(s.together(derivative))
        if denominator!=1:guards.append(s.Ne(denominator,0,evaluate=False))
        data={'expression':show(derivative)}
        try:
            derivative_domain=continuous_domain(derivative,x,s.S.Reals).intersect(condition_set(guards,x,s.S.Reals))
            data['domain']=show(derivative_domain)
        except NotImplementedError:
            data['domain']='Original conditions and the displayed derivative denominator restrictions; corner candidates require derivative_at.'
        return result(data, conditions=guards,
                      checks=[check('independent_derivative_rules', 'Compared SymPy differentiation with explicit sum/product/power and allowed-function chain rules; excluded absolute-value corner candidates need derivative_at.')])
    if op == 'derivative_at':
        p = numeric(request['point'])
        if any(not truth(c, {x:p}) for c in guards): return result(status='undefined', conditions=guards)
        if not two_sided_domain(f,guards,x,p):return result({'reason':'two_sided_real_neighborhood_missing'},status='undefined',conditions=guards,
                                                          checks=[check('real_neighborhood','The original real domain does not approach this point from both sides.')])
        delta = s.Symbol('h', real=True)
        quotient = (f.subs(x,p+delta)-f.subs(x,p))/delta
        left, right = s.limit(quotient,delta,0,dir='-'),s.limit(quotient,delta,0,dir='+')
        equal = s.simplify(left-right)==0 and left.is_finite is True
        return result({'differentiable': equal, 'left': show(left), 'right': show(right), 'value': show(left) if equal else None},
                      status='solved' if equal else 'undefined', conditions=guards,
                      checks=[check('difference_quotient_limits', 'Computed the left and right limits of the original difference quotient.')])
    if op == 'limit':
        p = numeric(request['point'])
        if not two_sided_domain(f,guards,x,p):return result({'reason':'two_sided_real_neighborhood_missing'},status='undefined',conditions=guards,
                                                          checks=[check('real_neighborhood','The original real domain does not approach this point from both sides; no complex continuation substitutes for a real two-sided limit.')])
        left=s.limit(f,x,p,dir='-');right=s.limit(f,x,p,dir='+')
        if left.has(s.Limit) or right.has(s.Limit): return unresolved('unevaluated_limit',guards)
        if left==right and left in (s.oo,-s.oo):
            return result({'left':show(left),'right':show(right),'diverges':True,'direction':show(left)},status='undefined',
                          checks=[check('two_sided_divergence','Both one-sided limits diverge in the same direction; there is no finite real limit.')])
        if left!=right and s.simplify(left-right) != 0:
            return result({'left':show(left),'right':show(right)},status='undefined',checks=[check('two_sided_limit','The one-sided limits do not agree.')])
        return result({'value':show(left)},checks=[check('two_sided_limit','Computed matching left and right limits; the function value at the point is separate.')])
    if op == 'integrate':
        original_domain=continuous_domain(f,x,s.S.Reals).intersect(condition_set(guards,x,s.S.Reals))
        F=s.integrate(f,x)
        if F.has(s.Integral): return unresolved('unevaluated_integral',guards)
        if s.simplify(s.diff(F,x)-f) != 0:return unresolved('antiderivative_verification_failed',guards)
        if 'bounds' not in request:
            return result({'expression':show(F)+' + C','constant':'C','constant_scope':'independent on each connected interval of the original domain','domain':show(original_domain)},conditions=guards,
                          checks=[check('differentiate_antiderivative','Derivative of the returned antiderivative equals the original integrand on each interval of its domain.')])
        a,b=bound_pair(request['bounds'])
        singular=s.Interval(a,b)-original_domain
        split=list(singular) if isinstance(singular,s.FiniteSet) else []
        if singular is not s.EmptySet and not isinstance(singular,s.FiniteSet):return unresolved('nonisolated_integrand_domain_gap',guards)
        points=sorted(set([a,b]+split));total=s.S.Zero;divergent=False
        for lo,hi in zip(points,points[1:]):
            lower=s.limit(F,x,lo,dir='+');upper=s.limit(F,x,hi,dir='-')
            if lower.is_finite is not True or upper.is_finite is not True:divergent=True;break
            total+=upper-lower
        if divergent:
            pv=s.Integral(f,(x,a,b)).principal_value()
            return result({'converges':False,'principal_value':show(pv) if not pv.has(s.Integral) else None},status='undefined',conditions=guards,
                          checks=[check('improper_integral_parts','At least one ordinary one-sided improper integral diverges; a principal value is a separate concept.')])
        return result({'value':show(s.simplify(total)),'converges':True,
                       'integration_kind':'ordinary_improper' if split else 'proper',
                       'excluded_points':[show(v) for v in split]},conditions=guards,
                      checks=[check('differentiate_antiderivative','Antiderivative differentiated back to the original integrand.'),check('endpoint_limits','Checked each one-sided endpoint limit separately.')])
    raise MathInputError('unsupported_calculus_operation')


def bound_pair(value):
    if type(value) is not list or len(value)!=2:raise MathInputError('two_bounds_required')
    a,b=map(numeric,value)
    if not a<b:raise MathInputError('increasing_bounds_required')
    return a,b


def interval_value(expr, x, at):
    import mpmath as mp
    iv=mp.iv
    if expr==x:return iv.mpf(int(at.p))/iv.mpf(int(at.q))
    if expr.is_Rational:return iv.mpf(int(expr.p))/iv.mpf(int(expr.q))
    if expr==s.pi:return iv.pi
    if expr==s.E:return iv.exp(1)
    args=[interval_value(a,x,at) for a in expr.args]
    if expr.func==s.Add:return sum(args,iv.mpf(0))
    if expr.func==s.Mul:
        value=iv.mpf(1)
        for a in args:value*=a
        return value
    if expr.func==s.Pow:return args[0]**args[1]
    functions={s.sin:iv.sin,s.cos:iv.cos,s.exp:iv.exp,s.log:iv.ln,s.Abs:abs}
    if expr.func in functions:return functions[expr.func](*args)
    raise MathInputError('interval_function_not_supported')


def numeric_operation(request, syms, x):
    parsed=Expression(request['expression'],syms,'real');f=parsed.value
    if f.free_symbols-{x}:raise MathInputError('single_numeric_variable_required')
    guards=parsed.conditions
    if request['operation']=='verify_root':
        candidate=numeric(request['point']);tol=numeric(request['tolerance']) if 'tolerance' in request else None
        if tol is not None and (tol<=0 or tol<s.Rational(1,10**30) or tol>1):raise MathInputError('tolerance_range')
        if not all(truth(c,{x:candidate}) for c in guards):return result({'accepted':False},status='undefined',conditions=guards)
        roots=s.solveset(f,x,domain=condition_set(guards,x,s.S.Reals))
        residual=s.Abs(s.simplify(f.subs(x,candidate)))
        if not isinstance(roots,s.FiniteSet) or not roots:return unresolved('root_distance_requires_complete_finite_exact_root_set',guards)
        error=s.Min(*[s.Abs(candidate-r) for r in roots]);accepted=bool(error<=tol) if tol is not None else error==0
        return result({'candidate':show(candidate),'residual':show(residual),'root_error':show(error),'tolerance':show(tol) if tol is not None else None,
                       'accepted':accepted,'is_exact_root':error==0},
                      checks=[check('root_distance','Compared distance to the exact root set separately from the residual.', 'passed' if accepted or tol is None else 'failed')])
    a,b=bound_pair(request.get('bounds'));interval=s.Interval(a,b)
    if not a.is_Rational or not b.is_Rational or max(abs(a),abs(b))>10**6:
        raise MathInputError('numeric_bounds_require_rationals_within_one_million')
    allowed=continuous_domain(f,x,s.S.Reals).intersect(condition_set(guards,x,s.S.Reals))
    if not interval.is_subset(allowed):
        roots=s.solveset(f,x,domain=allowed.intersect(interval))
        return result({'continuous':False,'root_existence_proved':False,'roots':show(roots)},status='no_solution' if roots is s.EmptySet else 'unresolved',
                      complete='proved_in_domain' if roots is s.EmptySet else 'not_proved',conditions=guards,
                      checks=[check('continuity','The interval crosses an excluded point; sign change cannot establish root existence.')])
    if request['operation']=='continuity_root':
        endpoint_values=[s.simplify(f.subs(x,v)) for v in (a,b)]
        return result({'continuous':True,'root_existence_proved':any(v==0 for v in endpoint_values) or (endpoint_values[0]*endpoint_values[1]).is_negative is True},
                      checks=[check('continuity_and_endpoint_signs','Continuity was checked on the complete closed interval.')])
    tol=numeric(request.get('tolerance','0.000000000001'))
    if not tol.is_Rational or tol<=0 or tol<s.Rational(1,10**30) or tol>1:raise MathInputError('tolerance_range')
    import mpmath as mp
    mp.iv.dps=60
    def sign(at):
        value=interval_value(f,x,at)
        if value.a>0:return 1
        if value.b<0:return -1
        if s.simplify(f.subs(x,at))==0:return 0
        raise MathInputError('interval_sign_inconclusive')
    sa,sb=sign(a),sign(b)
    if sa==0 or sb==0:
        point=a if sa==0 else b
        return result({'value':show(point),'error_bound':'0','interval':[show(point),show(point)],'iterations':0,'unique':False},
                      complete='search_limited',checks=[check('exact_endpoint_root','Original expression is exactly zero at this endpoint. Other roots in the search interval have not been excluded.')])
    if sa==sb:return unresolved('opposite_endpoint_signs_required',guards)
    iterations=0
    while b-a>tol and iterations<256:
        midpoint=(a+b)/2;sm=sign(midpoint);iterations+=1
        if sm==0:a=b=midpoint;break
        if sm==sa:a=midpoint;sa=sm
        else:b=midpoint;sb=sm
    if b-a>tol:return result({'interval':[show(a),show(b)],'iterations':iterations},execution='limits_exceeded',status='unresolved',complete='not_proved')
    midpoint=(a+b)/2
    try:
        derivative_range=function_range(s.diff(f,x),x,interval)
        unique=derivative_range.is_subset(s.Interval.open(-s.oo,0)) or derivative_range.is_subset(s.Interval.open(0,s.oo))
    except (NotImplementedError,ValueError):unique=False
    digits=max(0,math.ceil(-math.log10(float(tol))))
    # Decimal places (not significant digits) determine an explicit absolute
    # display error. The displayed midpoint is approximate, not a claim that
    # every printed digit is a digit of the true root.
    scale=10**digits;rounded=s.Rational(s.floor(midpoint*scale+s.Rational(1,2)),scale)
    value=(f'{int(abs(rounded)*scale)//scale}.{int(abs(rounded)*scale)%scale:0{digits}d}' if digits else str(int(abs(rounded))))
    if rounded<0:value='-'+value
    error=(b-a)/2+s.Rational(1,2*scale)
    return result({'value':value,'exact_midpoint':show(midpoint),'error_bound':show(error),
                   'interval':[show(a),show(b)],'iterations':iterations,'unique':bool(unique),'tolerance':show(tol),
                   'residual':show(s.N(f.subs(x,midpoint),25)),'method':'rational bisection with outward-rounded interval sign checks'},
                  kind='approximate',complete='proved_in_domain' if unique else 'search_limited',conditions=guards,
                  checks=[check('continuous_bracket','Checked original continuity and interval signs using mpmath interval arithmetic at 60 decimal places.'),
                          check('variable_error_bound','The half-width plus decimal display allowance bounds error in x, separately from the residual.'),
                          check('uniqueness','Derivative range excludes zero on the entire original interval.' if unique else 'Uniqueness was not established.','passed' if unique else 'not_proved')])


def function_plot(request, syms, x):
    parsed = Expression(request['expression'], syms, 'real')
    if parsed.value.free_symbols-{x}: raise MathInputError('single_plot_variable_required')
    low, high = bound_pair(request.get('bounds'))
    if not low.is_Rational or not high.is_Rational or max(abs(low),abs(high))>10**6:
        raise MathInputError('plot_bounds_require_rationals_within_one_million')
    samples = request.get('samples', 60)
    if type(samples) is not int or not 4 <= samples <= 100: raise MathInputError('plot_sample_limit')
    window = s.Interval(low, high)
    allowed = continuous_domain(parsed.value,x,s.S.Reals).intersect(condition_set(parsed.conditions,x,s.S.Reals)).intersect(window)
    pieces = list(allowed.args) if isinstance(allowed,s.Union) else [allowed]
    if not pieces or len(pieces)>8 or any(not isinstance(p,s.Interval) for p in pieces):
        return unresolved('plot_requires_at_most_eight_continuous_intervals',parsed.conditions)
    count = samples//len(pieces)
    if count<2: raise MathInputError('plot_samples_per_interval_limit')
    series=[]
    for piece in pieces:
        # Open endpoints are excluded by exact rational subdivision. Each
        # series remains within a separately verified continuous interval.
        left_open, right_open = int(bool(piece.left_open)), int(bool(piece.right_open))
        positions=[s.Rational(i+left_open,count-1+left_open+right_open) for i in range(count)]
        points=[]
        for position in positions:
            at=piece.start+(piece.end-piece.start)*position
            value=s.simplify(parsed.value.subs(x,at))
            if allowed.contains(at) is not s.true or value.is_real is not True or value.is_finite is not True:
                return unresolved('plot_point_domain_or_value_unverified',parsed.conditions)
            points.append({'x':show(s.N(at,16)),'y':show(s.N(value,16))})
        series.append({'interval':show(piece),'points':points})
    return result({'series':series,'expression':request['expression'],'variable':str(x),
                   'bounds':[show(low),show(high)],'domain':show(allowed),'excluded':show(window-allowed),
                   'samples':sum(len(item['points']) for item in series),'coordinate_precision':'16 significant decimal digits'},
                  kind='approximate',complete='search_limited',conditions=parsed.conditions,
                  checks=[check('continuous_segments','Each series lies in a separately computed continuous domain interval; excluded points are never joined.'),
                          check('computed_coordinates','Every sample was substituted into the expression at an exact point and checked for domain membership and finiteness.')],
                  explanation=['Finite sampled coordinates illustrate the function in the stated window; they do not prove all behavior between samples.'])


def solve(request):
    if type(request) is not dict or request.get('operation') not in OPERATIONS:raise MathInputError('unsupported_symbolic_operation')
    op=request['operation']
    if set(request)-(COMMON|FIELDS[op]):raise MathInputError('unexpected_math_field')
    if 'explanation_level' in request and request['explanation_level'] not in ('simple','detailed','technical'):
        raise MathInputError('unsupported_explanation_level')
    domain=request.get('domain','real');variable=normalize(request.get('variable',request.get('variables',['x'])[0] if type(request.get('variables',['x'])) is list and request.get('variables',['x']) else 'x'))
    syms=symbols(request.get('variables',[variable]),domain)
    if variable not in syms:raise MathInputError('primary_variable_not_declared')
    x=syms[variable];space={'real':s.S.Reals,'complex':s.S.Complexes,'integer':s.S.Integers}[domain]
    try:
        if op=='evaluate':
            parsed=Expression(request['expression'],syms,domain)
            if parsed.value.free_symbols:raise MathInputError('numeric_expression_required')
            if not all(truth(c) for c in parsed.conditions):return result(status='undefined',conditions=parsed.conditions)
            value=s.simplify(parsed.value)
            if value.has(s.zoo,s.nan,s.oo,-s.oo) or (domain!='complex' and value.is_real is False):return result(status='undefined',conditions=parsed.conditions)
            if domain=='integer' and value.is_integer is not True:return result({'reason':'result_not_in_declared_integer_domain'},status='undefined',conditions=parsed.conditions,
                                                                                checks=[check('integer_domain','The exact value is not an integer in the declared domain.')])
            data={'value':show(value)}
            if '.' in parsed.normalized:
                decimal=finite_decimal(value)
                if decimal is not None: data.update(decimal=decimal,decimal_exact=True)
            return result(data,checks=[check('exact_expression','Computed from exact Rational literals and symbolic constants; no binary float transport.')])
        if op=='solve':return equation_solve(request,syms,x,space)
        if op=='inequality':
            if domain!='real':raise MathInputError('real_inequality_required')
            rel,guards=relation(request['expression'],syms,domain);guards+=constraints(request,syms,domain)
            solved=condition_set([rel]+guards,x,space)
            pieces=solved.args if isinstance(solved,s.Union) else [solved]
            intervals=[{'left':show(piece.start),'right':show(piece.end),'left_open':bool(piece.left_open),'right_open':bool(piece.right_open)} for piece in pieces if isinstance(piece,s.Interval)]
            data={'set':show(solved),'intervals':intervals}
            delta=s.expand(rel.lhs-rel.rhs)
            if isinstance(rel,(s.StrictLessThan,s.LessThan,s.StrictGreaterThan,s.GreaterThan)) and not delta.free_symbols-{x} and delta.is_polynomial(x) and s.Poly(delta,x).degree()==1:
                coefficient=delta.coeff(x);threshold=s.simplify(-delta.subs(x,0)/coefficient)
                sign=rel.rel_op if coefficient>0 else {'<':'>','>':'<','<=':'>=','>=':'<='}[rel.rel_op]
                data['teaching_steps']=[{'equation':show(x)+' '+sign+' '+show(threshold),
                    'reason_ar':('نقلب اتجاه المتباينة عند القسمة على المعامل السالب ' if coefficient<0 else 'يبقى اتجاه المتباينة عند القسمة على المعامل الموجب ')+show(coefficient)+'.',
                    'reason_en':('Reverse the inequality when dividing by the negative coefficient ' if coefficient<0 else 'Keep the inequality direction when dividing by the positive coefficient ')+show(coefficient)+'.'}]
            return result(data,status='no_solution' if solved is s.EmptySet else 'solved',conditions=guards,
                          checks=[check('inequality_sign_domains','Solved all sign intervals while retaining poles and strict endpoints.')])
        if op=='system':return system_solve(request,syms,domain)
        if op in ('matrix_inverse','eigen'):return matrix_operation(request)
        if op=='diophantine':return diophantine(request,syms)
        if op=='optimize':return optimize(request,syms,domain)
        if op=='plot':
            if domain!='real':raise MathInputError('plot_real_domain_required')
            return function_plot(request,syms,x)
        if op in ('differentiate','derivative_at','integrate','limit','ode_linear'):return calculus(request,syms,x,domain)
        if op in ('root','verify_root','continuity_root'):
            if domain!='real':raise MathInputError('numeric_real_domain_required')
            return numeric_operation(request,syms,x)
    except KeyError as exc:raise MathInputError('required_math_field_missing') from exc
    except NotImplementedError as exc:return unresolved(type(exc).__name__+': '+str(exc)[:180])
    raise MathInputError('unsupported_symbolic_operation')
