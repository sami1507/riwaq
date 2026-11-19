import json
from fractions import Fraction
import pytest
import sympy as s

from backend.math_expression import Expression, MathInputError, symbols
from backend.math_symbolic import solve


def run(operation, **kw):
    return solve({'operation': operation, **kw})


@pytest.mark.parametrize('text,expected', [('0.1+0.2','3/10'),('9007199254740993+1','9007199254740994'),
    ('3/7+2/5','29/35'),('-2^2','-4'),('(-2)^2','4'),('2^3^2','512'),('١/٣+٢/٣','1')])
def test_exact_literals_precedence_and_transport(text, expected):
    answer=run('evaluate', expression=text)
    assert answer['result']['value']==expected
    assert json.loads(json.dumps(answer))['result']['value']==expected


@pytest.mark.parametrize('text', ['__import__("os")','x.__class__','open(x)','[x for x in x]',
    'lambda x:x','sin.__globals__','1;2','x[0]','2^100','('*17+'1'+')'*17,'9'*65,
    '1/2x','1/2س','xy+1','x=1','nan','oo'])
def test_disallowed_inputs_do_not_reach_evaluation(text):
    with pytest.raises(MathInputError):Expression(text, symbols(['x']))


@pytest.mark.parametrize('equation,expected', [('٢س + ٣ = ١١',{'4'}),
    ('(x^2-1)/(x-1)=2',set()),('sqrt(x+1)=x-1',{'3'}),('x^2+1=0',set()),
    ('x*(x-2)=0',{'0','2'}),('ln(x-1)+ln(x+1)=ln(3)',{'2'}),('|2*x-1|=3',{'-1','2'})])
def test_original_equation_domain_and_all_finite_roots(equation,expected):
    answer=run('solve',expression=equation)
    assert answer['execution_status']=='completed'
    assert set(answer['result']['solutions'])==expected
    assert answer['problem_status']==('solved' if expected else 'no_solution')


def test_domain_change_and_negative_constraint():
    assert set(run('solve',expression='x^2+1=0',domain='complex')['result']['solutions'])=={'I','-I'}
    assert run('solve',expression='x^2=4',constraints=['x<0'])['result']['solutions']==['-2']
    assert run('solve',expression='x^2=2',domain='integer')['problem_status']=='no_solution'


def test_parameter_zero_branch_is_preserved():
    answer=run('solve',expression='a*x=1',variables=['x','a'])
    assert answer['result_kind']=='conditional'
    assert answer['result']['cases']==[
        {'condition':'Ne(a, 0)','solutions':['1/a'],'problem_status':'solved'},
        {'condition':'Eq(a, 0)','solutions':[],'problem_status':'no_solution'}]
    zero=run('solve',expression='a*x=0',variables=['x','a'])
    assert any(c.get('set')=='Reals' for c in zero['result']['cases'])


def test_rational_inequality_pole_and_strict_endpoints():
    answer=run('inequality',expression='(x-1)/(x+2)>0')
    assert answer['result']['intervals']==[
        {'left':'-oo','right':'-2','left_open':True,'right_open':True},
        {'left':'1','right':'oo','left_open':True,'right_open':True}]


def test_all_periodic_roots_not_a_finite_search():
    answer=run('solve',expression='sin(x)=0')
    assert answer['result']['families']==[{'expression':'pi*k','parameter':'k','parameter_domain':'integer'}]
    assert answer['completeness']=='proved_in_domain'


@pytest.mark.parametrize('equations,solutions,status', [
    (['x+y=3','x-y=1'],[{'x':'2','y':'1'}],'solved'),
    (['x+y=1','2*x+2*y=3'],[],'inconsistent'),
    (['x+y=1','x^2+y^2=1'],[{'x':'0','y':'1'},{'x':'1','y':'0'}],'solved')])
def test_systems_and_reordered_equations(equations,solutions,status):
    for rows in (equations,list(reversed(equations))):
        answer=run('system',equations=rows,variables=['x','y'])
        assert answer['problem_status']==status
        assert answer['result']['solutions']==solutions


def test_infinite_linear_family_renamed_symbols():
    answer=run('system',equations=['u+v=2','2*u+2*v=4'],variables=['u','v'])
    assert answer['result_kind']=='parametric'
    assert answer['result']['solutions']==[{'u':'2 - v','v':'v'}]
    assert answer['result']['rank']==answer['result']['augmented_rank']==1


def test_matrix_singularity_inverse_and_eigen_equations():
    assert run('matrix_inverse',matrix=[['1','2'],['2','4']])['result']['inverse'] is None
    assert run('matrix_inverse',matrix=[['1','2'],['3','4']])['result']['inverse']==[['-2','1'],['3/2','-1/2']]
    entries=run('eigen',matrix=[['2','1'],['1','2']])['result']['eigenpairs']
    matrix=s.Matrix([[2,1],[1,2]])
    assert {v['value'] for v in entries}=={'1','3'}
    for row in entries:
        for vector in row['vectors']:
            v=s.Matrix([s.Rational(n) for n in vector]);assert matrix*v==int(row['value'])*v
    rotation=[['0','-1'],['1','0']]
    assert run('eigen',matrix=rotation)['problem_status']=='no_solution'
    assert {p['value'] for p in run('eigen',matrix=rotation,domain='complex')['result']['eigenpairs']}=={'I','-I'}


def test_integer_parametric_family_is_complete():
    answer=run('diophantine',expression='2*x+3*y=7',variables=['x','y'],domain='integer')
    assert answer['result_kind']=='parametric'
    expected=answer['result']['solutions'][0]
    parsed={k:Expression(v,symbols(['t'],'integer'),'integer').value for k,v in expected.items()}
    assert s.simplify(2*parsed['x']+3*parsed['y']-7)==0
    # Necessary lattice stride is (3,-2); checking a few samples alone is not
    # considered proof of coverage.
    t=s.Symbol('t',integer=True)
    assert s.diff(parsed['x'],t)==3 and s.diff(parsed['y'],t)==-2
    assert run('diophantine',expression='4*x+6*y=7',variables=['x','y'],domain='integer')['problem_status']=='no_solution'


def test_optimization_independent_dual_certificate_and_integer_search():
    answer=run('optimize',expression='3*x+2*y',variables=['x','y'],sense='max',constraints=['x+y<=4','x<=2','x>=0','y>=0'])
    assert answer['result']['point']=={'x':'2','y':'2'}
    assert answer['result']['value']=='10'
    assert answer['result']['bound_certificate']['weights']==['2','1']
    integer=run('optimize',expression='x',sense='max',domain='integer',constraints=['2*x<=3','x>=0'])
    assert integer['result']['point']=={'x':'1'}
    quadratic=run('optimize',expression='(x-3)^2+2')
    assert quadratic['result']['value']=='2' and quadratic['result']['point']=={'x':'3'}
    # The affine open-domain extension now proves the bound and non-attainment.
    open_domain=run('optimize',expression='x',constraints=['x>0'])
    assert open_domain['problem_status']=='solved'
    assert open_domain['result']['bound']=='0' and open_domain['result']['attained'] is False
    assert open_domain['result']['point'] is None


def test_symbolic_derivative_and_absolute_corner():
    answer=run('differentiate',expression='x^3*sin(x)')
    x=s.Symbol('x',real=True)
    computed=Expression(answer['result']['expression'],{'x':x}).value
    assert s.simplify(computed-(3*x*x*s.sin(x)+x**3*s.cos(x)))==0
    corner=run('derivative_at',expression='abs(x)',point='0')
    assert corner['result']=={'differentiable':False,'left':'-1','right':'1','value':None}


def test_integral_exact_domain_and_improper_concept():
    assert run('integrate',expression='x^2',bounds=['0','1'])['result']['value']=='1/3'
    answer=run('integrate',expression='1/x^2')
    assert answer['result']['expression']=='-1/x + C'
    assert answer['conditions'] and 'each connected interval' in answer['result']['constant_scope']
    improper=run('integrate',expression='1/x',bounds=['-1','1'])
    assert improper['result']=={'converges':False,'principal_value':'0'}
    assert improper['problem_status']=='undefined'


def test_limit_and_ode_original_checks():
    assert run('limit',expression='sin(x)/x',point='0')['result']['value']=='1'
    answer=run('ode_linear',coefficient='2',initial={'point':'0','value':'3'})
    assert answer['result']['expression']=='3*exp(2*x)'
    changed=run('ode_linear',coefficient='-3',initial={'point':'1','value':'5'})
    assert changed['result']['expression']=='5*exp(3 - 3*x)'


def test_bracket_error_is_error_in_variable_not_only_residual():
    answer=run('root',expression='cos(x)-x',bounds=['0','1'],tolerance='0.000000000001')
    data=answer['result'];assert abs(float(data['value'])-.7390851332151607)<=1e-12
    assert Fraction(data['error_bound'])<=Fraction(1,10**12)
    assert data['unique'] is True and data['iterations']<=256
    midpoint=Fraction(data['exact_midpoint']);a,b=map(Fraction,data['interval']);assert a<=midpoint<=b
    assert run('continuity_root',expression='1/x',bounds=['-1','1'])['result']['continuous'] is False
    rejection=run('verify_root',expression='(x-1)^2',point='1.001',tolerance='0.000001')
    assert rejection['result']['residual']=='1/1000000'
    assert rejection['result']['root_error']=='1/1000'
    assert rejection['result']['accepted'] is False


@pytest.mark.parametrize('payload', [
    {'operation':'evaluate','expression':'1+1','admin':True},
    {'operation':'matrix_inverse','matrix':[['1','2']]},
    {'operation':'solve','expression':'x=1','variables':['x','x']},
    {'operation':'root','expression':'x','bounds':['1','0']},
    {'operation':'root','expression':'x','bounds':['-1','1'],'tolerance':'0'},
    {'operation':'eigen','matrix':[['1']]*6},
    {'operation':'evaluate','expression':'1','domain':'rational'},
    {'operation':'differentiate','expression':'x^y','variables':['x','y']},
])
def test_contract_bounds(payload):
    with pytest.raises(MathInputError):solve(payload)


def test_no_false_solution_from_unknown_or_undefined():
    assert run('solve',expression='x+sin(x)=1')['problem_status']=='unresolved'
    for expression in ['1/0','sqrt(-1)','ln(0)','0^0']:
        answer=run('evaluate',expression=expression)
        assert answer['problem_status']=='undefined' and 'value' not in answer['result']
    assert run('root',expression='x^2+1',bounds=['-1','1'])['problem_status']=='unresolved'


def test_real_limit_needs_two_sided_domain_and_derivative_has_corners():
    answer=run('limit',expression='sqrt(x)',point='0')
    assert answer['problem_status']=='undefined'
    assert answer['result']['reason']=='two_sided_real_neighborhood_missing'
    derivative=run('differentiate',expression='abs(x)')
    assert any('x != 0' in c for c in derivative['conditions'])


def test_numeric_error_contract_for_large_and_rational_bounds():
    with pytest.raises(MathInputError):run('root',expression='x-100000000',bounds=['0','200000000'])
    with pytest.raises(MathInputError):run('root',expression='x-1',bounds=['0','sqrt(2)'])
    answer=run('root',expression='x^2-2',bounds=['0','2'],tolerance='0.000000000000000001')
    assert Fraction(answer['result']['error_bound'])<=Fraction(1,10**18)
    assert abs(s.Rational(answer['result']['value'])-s.sqrt(2)).evalf(40)<s.Rational(answer['result']['error_bound'])


def test_real_odd_root_branch_and_integer_scalar_domain():
    assert run('evaluate',expression='(-8)^(1/3)')['result']['value']=='-2'
    assert run('evaluate',expression='(-8)^(2/3)')['result']['value']=='4'
    assert run('evaluate',expression='1/2',domain='integer')['problem_status']=='undefined'


@pytest.mark.parametrize('entry',[True,False,10**64])
def test_matrix_integer_contract_rejects_bool_and_oversize(entry):
    with pytest.raises(MathInputError):run('matrix_inverse',matrix=[[entry]])


def test_integral_explicit_domains_and_removable_holes_are_retained():
    indefinite=run('integrate',expression='1/x^2',constraints=['x!=0'])
    assert indefinite['problem_status']=='solved'
    assert indefinite['result']['expression']=='-1/x + C'
    polynomial=run('integrate',expression='x^2',constraints=['x!=0'])
    assert 'Union' in polynomial['result']['domain']
    assert polynomial['result']['constant_scope'].startswith('independent on each connected interval')
    definite=run('integrate',expression='x^2',constraints=['x!=0'],bounds=['-1','1'])
    assert definite['result']['value']=='2/3'
    assert definite['result']['integration_kind']=='ordinary_improper'
    assert definite['result']['excluded_points']==['0']
    gap=run('integrate',expression='x^2',constraints=['x>0'],bounds=['-1','1'])
    assert gap['problem_status']=='unresolved' and gap['completeness']=='not_proved'
