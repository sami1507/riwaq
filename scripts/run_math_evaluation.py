"""Versioned math acceptance evidence. Never imported by the product runtime.

The known 60 cases are measured twice: a typed request checks the mathematical
tool contract; the declared concise text checks parsing. A successful structured
request does not by itself certify the user's natural-language interpretation.
"""
from __future__ import annotations
import argparse
import asyncio
import ast
from collections import Counter
from datetime import datetime,timezone
from fractions import Fraction
import hashlib
import importlib.util
import json
import platform
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
DATASET=ROOT/'evaluation/math/MATH_REASONING_EVALS.json'
STATUSES=('passed','failed','partial','blocked','skipped','not_applicable')

def dump(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def load_cases():
    data=json.loads(DATASET.read_bytes())
    if data.get('schema_version')!='riwaq-math-evals-1' or len(data.get('cases',[]))!=60:
        raise ValueError('invalid_math_dataset')
    if [c['id'] for c in data['cases']]!=[f'MATH-{i:03d}' for i in range(1,61)]:
        raise ValueError('invalid_math_case_ids')
    return data

def new_output(value):
    p=Path(value).absolute()
    for part in (p,*p.parents):
        if part.is_symlink() or getattr(part,'is_junction',lambda:False)():raise ValueError('linked_output')
    if not p.resolve().is_relative_to((ROOT/'reports').resolve()) or p.exists():
        raise ValueError('new_report_directory_required')
    p.mkdir(parents=True)
    return p

def totals(rows):
    counts=Counter(r['status'] for r in rows)
    if set(counts)-set(STATUSES):raise ValueError('unknown_status')
    return {k:counts[k] for k in STATUSES}

def exact_equal(actual,expected):
    """Compare bounded rational wire values, never eval/sympify arbitrary text."""
    if type(actual) not in (str,int) or type(expected) not in (str,int):return False
    if isinstance(actual,str) and len(actual)>256:return False
    try:return Fraction(actual)==Fraction(expected)
    except (ValueError,ZeroDivisionError,TypeError):return actual==expected

def algebra(text):
    """Independent, bounded output-expression reader. No Python execution.

    This verifier does not call the runtime parser. It accepts a narrow Python
    expression AST and builds mathematical objects explicitly. It is not used
    to infer domain conditions or completeness from sampled identities.
    """
    import sympy as s
    if type(text) is not str or not 0<len(text)<=1024:raise ValueError('invalid_expression_wire')
    tree=ast.parse(text.replace('^','**'),mode='eval')
    if len(list(ast.walk(tree)))>256:raise ValueError('expression_wire_limit')
    names={n.id for n in ast.walk(tree) if isinstance(n,ast.Name)}
    functions={'sin':s.sin,'cos':s.cos,'exp':s.exp,'log':s.log,'ln':s.log,'sqrt':s.sqrt,'Abs':s.Abs,'abs':s.Abs}
    constants={'pi':s.pi,'E':s.E,'e':s.E,'I':s.I,'oo':s.oo}
    symbols={n:s.Symbol(n) for n in names-functions.keys()-constants.keys() if n.isidentifier() and not n.startswith('_') and len(n)<=16}
    def visit(node,depth=0):
        if depth>20:raise ValueError('expression_wire_depth')
        if isinstance(node,ast.Expression):return visit(node.body,depth+1)
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):
            raw=ast.get_source_segment(text.replace('^','**'),node)
            if raw is None or len(raw)>80:raise ValueError('expression_literal_limit')
            return s.Rational(raw)
        if isinstance(node,ast.Name) and node.id in constants:return constants[node.id]
        if isinstance(node,ast.Name) and node.id in symbols:return symbols[node.id]
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            v=visit(node.operand,depth+1);return v if isinstance(node.op,ast.UAdd) else -v
        if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Pow)):
            a,b=visit(node.left,depth+1),visit(node.right,depth+1)
            if isinstance(node.op,ast.Add):return a+b
            if isinstance(node.op,ast.Sub):return a-b
            if isinstance(node.op,ast.Mult):return a*b
            if isinstance(node.op,ast.Div):return a/b
            if not b.is_number or abs(b)>64:raise ValueError('expression_exponent_limit')
            return a**b
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in functions and len(node.args)==1 and not node.keywords:
            return functions[node.func.id](visit(node.args[0],depth+1))
        raise ValueError('unsupported_expression_wire')
    return visit(tree)

def equivalent(actual,expected):
    if exact_equal(actual,expected):return True
    if type(actual) is not str or type(expected) is not str:return False
    try:
        import sympy as s
        return s.simplify(algebra(actual)-algebra(expected))==0
    except (ValueError,TypeError,SyntaxError,ZeroDivisionError,NotImplementedError):return False

def same_solutions(actual,expected):
    if type(actual) is not list or len(actual)!=len(expected):return False
    unused=list(actual)
    for e in expected:
        found=None
        for i,a in enumerate(unused):
            if isinstance(e,dict):
                match=type(a) is dict and set(a)==set(e) and all(equivalent(a[k],v) for k,v in e.items())
            else:match=equivalent(a,e)
            if match:found=i;break
        if found is None:return False
        unused.pop(found)
    return True

def judge_payload(request,expected,payload):
    """Return explicit criterion failures, accepting equivalent mathematics."""
    errors=[]
    def require(ok,code):
        if not ok:errors.append(code)
    if type(payload) is not dict:return ['missing_result']
    if payload.get('execution_status') not in ('completed','partial'):return ['execution_not_completed']
    if type(payload.get('result')) is not dict:return ['invalid_result_shape']
    result=payload['result'];operation=request['operation']
    if payload.get('legacy'):
        calculations=payload['legacy'].get('results',[])
        successes=[c for c in calculations if c.get('status')=='ok']
        if successes:
            last=successes[-1];result={'value':last['result']['exact'],'unit':last.get('unit')}
            if len(successes)>1:result['steps']=[{'base':'100' if i==0 else c['result']['exact'],'value':c['result']['exact']} for i,c in enumerate(successes)]
    if expected.get('problem_status'):
        require(payload.get('problem_status')==expected['problem_status'],'problem_state')
    if expected.get('problem_status')=='ambiguous':
        require(bool(payload.get('conditions') or payload.get('explanation') or result.get('interpretations')),'missing_ambiguity_explanation')
        return errors
    if expected.get('problem_status') not in ('insufficient_data',):
        require(bool(payload.get('verification')),'missing_verification')
    if operation in ('exact','round','percent_changes','unit_sum','evaluate','bayes','dice_sum','combinations','binomial','weighted_mean') and 'value' in expected:
        require(exact_equal(result.get('value'),expected['value']),'exact_value')
        if operation in ('exact','evaluate'):require(payload.get('result_kind')=='exact','exactness_kind')
        if operation=='round':
            require(result.get('rounding')=='ROUND_HALF_UP' and result.get('places')==request['places'],'rounding_policy')
        if operation=='unit_sum':require(result.get('unit')==expected.get('unit',request.get('output_unit')),'output_unit')
        if operation=='percent_changes' and 'intermediate' in expected:
            steps=result.get('steps',[])
            require(len(steps)==2 and exact_equal(steps[0].get('value'),expected['intermediate']),'percentage_intermediate')
            if not payload.get('legacy'):require(len(steps)==2 and exact_equal(steps[1].get('base'),expected['intermediate']),'second_percentage_base')
        if operation=='bayes' and 'denominator' in expected:require(exact_equal(result.get('evidence_probability'),expected['denominator']),'bayes_denominator')
        if operation=='dice_sum':
            require(exact_equal(result.get('favourable'),expected['favorable']) and exact_equal(result.get('sample_space'),expected['outcomes']),'sample_space')
    elif operation=='unit_sum':
        require(set(result.get('incompatible_dimensions',[]))=={'length','time'},'dimension_rejection')
        require('value' not in result,'no_numeric_replacement')
    elif operation=='solve':
        if 'solutions' in expected:
            require(same_solutions(result.get('solutions'),expected['solutions']),'solution_set')
            require(payload.get('completeness')=='proved_in_domain','solution_completeness')
            if not expected['solutions']:require(payload.get('problem_status')=='no_solution','empty_solution_state')
            if expected.get('excluded') or expected.get('domain'):require(bool(payload.get('conditions')),'original_domain_conditions')
        elif 'nonzero_solution' in expected:
            cases=result.get('cases',[])
            require(payload.get('result_kind')=='conditional' and equivalent(result.get('coefficient'),'a') and equivalent(result.get('constant'),'-1'),'parameter_case_model')
            require(any(c.get('condition')=='Ne(a, 0)' and same_solutions(c.get('solutions'),['1/a']) for c in cases),'nonzero_parameter_branch')
            require(any('Eq(a, 0)' in c.get('condition','') and c.get('problem_status')=='no_solution' and c.get('solutions')==[] for c in cases),'zero_parameter_branch')
        elif 'family' in expected:
            families=result.get('families',[])
            require(payload.get('completeness')=='proved_in_domain' and payload.get('result_kind')=='parametric','periodic_completeness')
            require(any(f.get('parameter_domain')=='integer' and equivalent(f.get('expression'),'pi*'+f.get('parameter','invalid')) for f in families),'integer_periodic_family')
    elif operation=='inequality':
        actual=result.get('intervals',[])
        require(len(actual)==len(expected['intervals']),'interval_count')
        for a,e in zip(actual,expected['intervals']):
            require(equivalent(a.get('left'),e[0]) and equivalent(a.get('right'),e[1]) and a.get('left_open') is e[2] and a.get('right_open') is e[3],'interval_endpoints')
        require(bool(payload.get('conditions')),'pole_condition')
    elif operation=='system':
        if 'solutions' in expected:require(same_solutions(result.get('solutions'),expected['solutions']),'system_solution_set')
        elif expected.get('problem_status')=='inconsistent':
            require(result.get('solutions')==[] and result.get('rank')<result.get('augmented_rank'),'rank_inconsistency')
        else:
            import sympy as s
            solutions=result.get('solutions',[])
            require(len(solutions)==1 and payload.get('result_kind')=='parametric','parametric_system')
            if len(solutions)==1:
                x,y=algebra(solutions[0]['x']),algebra(solutions[0]['y']);params=x.free_symbols|y.free_symbols
                require(len(params)==1 and s.simplify(x+y-2)==0,'family_identity')
                if len(params)==1:
                    t=next(iter(params));require(s.diff(x,t)!=0 or s.diff(y,t)!=0,'nonconstant_family')
            require(result.get('rank')==result.get('augmented_rank')==1,'family_rank')
        require(payload.get('completeness')=='proved_in_domain','system_completeness')
    elif operation=='matrix_inverse':
        require(exact_equal(result.get('determinant'),'0') and result.get('inverse') is None and payload.get('problem_status')=='no_solution','singular_matrix')
    elif operation=='eigen':
        import sympy as s
        matrix=s.Matrix([[algebra(v) for v in row] for row in request['matrix']]);pairs=result.get('eigenpairs',[])
        require(same_solutions([p.get('value') for p in pairs],expected['eigenvalues']),'eigenvalues')
        for pair in pairs:
            require(bool(pair.get('vectors')),'missing_eigenvector')
            for vector in pair.get('vectors',[]):
                v=s.Matrix([algebra(n) for n in vector]);require(v.rows==matrix.cols and v!=s.zeros(matrix.cols,1) and matrix*v==algebra(pair['value'])*v,'eigen_equation')
    elif operation=='diophantine':
        import sympy as s
        solutions=result.get('solutions',[]);require(len(solutions)==1 and result.get('parameter_domain')=='integer','integer_family')
        if len(solutions)==1:
            x,y=algebra(solutions[0]['x']),algebra(solutions[0]['y']);t=s.Symbol(result.get('parameter','invalid'))
            require(s.simplify(2*x+3*y-7)==0,'diophantine_identity')
            require((s.diff(x,t),s.diff(y,t)) in ((3,-2),(-3,2)),'primitive_solution_step')
            require(x.subs(t,0).is_integer and y.subs(t,0).is_integer,'integer_base_solution')
        require(payload.get('completeness')=='proved_in_domain','integer_completeness')
    elif operation=='optimize':
        require(exact_equal(result.get('value'),expected['value']) and same_solutions([result.get('point')],[expected['point']]),'optimum')
        require(result.get('optimality')=='global' and payload.get('completeness')=='proved_in_domain','global_optimality_evidence')
    elif operation=='entailment':
        if expected.get('problem_status')=='inconsistent':
            require(result.get('consistency')=='UNSAT' and result.get('reliable_real_world_conclusion') is False,'inconsistency_first')
        else:
            require(result.get('consistency')=='SAT' and result.get('entails') is expected['entailed'],'entailment')
            if not expected['entailed']:
                require(result.get('counterexample')==expected['countermodel'],'checked_countermodel')
    elif operation=='quantifier_order':
        require(result.get('forall_x_exists_y_greater') is True and result.get('exists_y_forall_x_greater') is False,'quantifier_order')
        require(bool(payload.get('explanation')),'constructive_argument')
    elif operation=='existential_import':
        require(result.get('entailed') is False and result.get('countermodel',{}).get('A')==[],'empty_set_countermodel')
    elif operation=='polynomial_even':
        require(result.get('always_even') is True and result.get('residues_mod_2')==[0,0],'all_integer_parity_classes')
        require(payload.get('completeness')=='proved_in_domain','parity_proof_scope')
    elif operation=='prime_counterexample':
        require(exact_equal(result.get('n'),expected['n']) and exact_equal(result.get('value'),expected['value']),'counterexample_value')
        factors=result.get('factors',[])
        require(len(factors)==2 and all(Fraction(n)>1 for n in factors) and Fraction(factors[0])*Fraction(factors[1])==Fraction(expected['value']),'factor_certificate')
    elif operation=='ordering':
        require([result.get('order')]==expected['orders'] and result.get('unique') is True and result.get('solution_count')==1,'unique_order')
    elif operation=='differentiate':
        require(equivalent(result.get('expression'),expected['derivative']),'symbolic_derivative')
    elif operation=='derivative_at':
        require(result.get('differentiable') is False and exact_equal(result.get('left'),expected['left']) and exact_equal(result.get('right'),expected['right']),'one_sided_derivatives')
    elif operation=='integrate':
        if 'value' in expected:require(exact_equal(result.get('value'),expected['value']) and result.get('converges') is True,'definite_integral')
        elif expected.get('convergent') is False:
            require(result.get('converges') is False and payload.get('problem_status')=='undefined','ordinary_improper_divergence')
            require('value' not in result,'principal_value_not_ordinary_integral')
        else:
            import sympy as s
            expr=algebra(result.get('expression',''));x=s.Symbol(request.get('variable','x'))
            require(s.simplify(s.diff(expr,x)-algebra(request['expression']))==0,'antiderivative_verification')
            require(bool(result.get('constant')) and bool(result.get('constant_scope')) and bool(payload.get('conditions')),'constant_and_disconnected_domain')
    elif operation=='limit':require(exact_equal(result.get('value'),expected['value']),'two_sided_limit')
    elif operation=='ode_linear':
        import sympy as s
        expr=algebra(result.get('expression',''));x=s.Symbol('x')
        require(s.simplify(s.diff(expr,x)-algebra(request['coefficient'])*expr)==0,'ode_residual')
        require(s.simplify(expr.subs(x,algebra(request['initial']['point']))-algebra(request['initial']['value']))==0,'initial_condition')
    elif operation=='root':
        import mpmath as mp
        with mp.workdps(70):
            val=mp.mpf(result.get('value','nan'));reference=mp.findroot(lambda x:mp.cos(x)-x,(0,1));tolerance=mp.mpf(expected['x_error'])
            require(mp.isfinite(val) and abs(val-reference)<=tolerance,'root_variable_accuracy')
        lo,hi=(Fraction(v) for v in result.get('interval',[]));bound=Fraction(result['error_bound'])
        require(lo<hi and 0<=lo<=hi<=1 and (hi-lo)/2<=bound<=Fraction(expected['x_error']),'root_error_bound')
        require(result.get('unique') is True and result.get('iterations',0)>0,'root_scope_and_stop')
    elif operation=='continuity_root':
        require(result.get('continuous') is False and result.get('root_existence_proved') is False,'discontinuity_not_root')
        require(payload.get('problem_status')=='no_solution' and bool(payload.get('conditions')),'pole_and_empty_roots')
    elif operation=='verify_root':
        require(exact_equal(result.get('residual'),expected['residual']) and exact_equal(result.get('root_error'),expected['root_error']) and result.get('accepted') is False,'residual_vs_variable_error')
    elif operation=='statistics':
        for key,value in expected.items():
            require(result.get(key)==value if type(value) is int else exact_equal(result.get(key),value),'statistics_'+key)
    elif operation=='probability':
        require(exact_equal(result.get('union'),expected['union']) and exact_equal(result.get('conditional_a_given_b'),expected['conditional']),'probability_values')
    elif operation=='rectangle':require(same_solutions(result.get('sides'),expected['dimensions']),'positive_rectangle_dimensions')
    elif operation=='speed':
        require(equivalent(result.get('expression'),expected['relation']) and payload.get('result_kind')=='conditional' and result.get('missing')=='duration_hours','missing_duration_relation')
        require(any('t>0' in c for c in payload.get('conditions',[])) and 'value' not in result,'positive_duration_no_invention')
    else:errors.append('missing_criterion_binding')
    return errors

def judge_case(case,payloads):
    if len(payloads)!=len(case['requests']):return ['missing_case_outputs']
    errors=[]
    for i,(request,payload) in enumerate(zip(case['requests'],payloads)):
        expected=case['expected']
        if case['id']=='MATH-008':expected={'value':expected['values'][i]}
        if case['id']=='MATH-013':expected={'solutions':expected['solutions'][i]}
        try:errors.extend(f'output_{i}:{e}' for e in judge_payload(request,expected,payload))
        except (ValueError,KeyError,TypeError,ZeroDivisionError,IndexError,SyntaxError) as error:
            errors.append(f'output_{i}:invalid_wire_{type(error).__name__}')
    return errors

def baseline(output):
    """Import fixed snapshotted modules, not whatever is being edited today."""
    before=ROOT/'reports/math-reasoning-20261116/baseline/backend'
    modules={}
    for name in ('check_worker','reasoning_problem'):
        spec=importlib.util.spec_from_file_location('math_baseline_'+name,before/(name+'.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);modules[name]=module
    data=load_cases();rows=[]
    scalar_ids={'MATH-001':'value','MATH-002':'value','MATH-003':'value','MATH-004':'final_value','MATH-006':'total_length'}
    for case in data['cases']:
        started=time.monotonic()
        try:
            result=modules['reasoning_problem'].solve_problem(case['input_text'],lambda s:modules['check_worker'].Arithmetic(s).result())
        except Exception as exc:
            row={'id':case['id'],'status':'blocked','reason':str(exc),'result':None}
        else:
            wanted=scalar_ids.get(case['id']);items={r['id']:r for r in result['results']}
            matches=wanted in items and items[wanted]['status']=='ok' and exact_equal(items[wanted]['result']['exact'],case['expected']['value'])
            if case['id']=='MATH-006':matches=matches and items[wanted]['unit']=='m'
            row={'id':case['id'],'status':'passed' if matches else 'partial','reason':'Exact supported concise-input arithmetic.' if matches else 'No full criterion judge for legacy result.','result':result}
        try:
            original=modules['reasoning_problem'].solve_problem(case['source_text'],lambda s:modules['check_worker'].Arithmetic(s).result())
        except Exception as exc:
            row['original_text_status']='blocked';row['original_result']=None;row['original_error']=str(exc)
        else:
            row['original_result']=original;row['original_text_status']='partial'
        row['seconds']=round(time.monotonic()-started,6);rows.append(row)
    report={'schema_version':'riwaq-math-baseline-1','generated_at':datetime.now(timezone.utc).isoformat(),
      'dataset_sha256':digest(DATASET),'python':platform.python_version(),'platform':platform.platform(),
      'source_files':{str((before/(n+'.py')).relative_to(ROOT)):digest(before/(n+'.py')) for n in modules},
      'cases':rows,'totals':totals(rows),'original_text_totals':totals([{'status':r['original_text_status']} for r in rows]),'scope':'Legacy module component baseline on explicit concise input_text and original source_text separately; not live chat, no model, no UI. Unsupported forms are blocked, not correct-rejection passes.'}
    dump(output/'baseline.json',report)
    return report

async def call_worker(text):
    from backend.reasoning import checker
    from backend.reasoning_math import validate_math_result
    started=time.monotonic()
    try:
        response=await checker('solve_math',text,timeout=30)
        if response.get('status')!='ok':
            return {'status':'blocked','seconds':round(time.monotonic()-started,6),'response':response,'errors':[response.get('error','worker_unavailable')]}
        payload=response.get('result')
        validate_math_result(payload,text)
        if payload.get('input_sha256')!=hashlib.sha256(text.encode()).hexdigest() or payload.get('process_limited') is not True:
            raise ValueError('worker_binding_or_limits_missing')
        return {'status':'executed','seconds':round(time.monotonic()-started,6),'response':response,'errors':[]}
    except (TimeoutError,OSError,ValueError,KeyError,TypeError) as exc:
        return {'status':'blocked','seconds':round(time.monotonic()-started,6),'response':None,'errors':[type(exc).__name__+': '+str(exc)[:240]]}

def from_call(call):
    if call['status']!='executed':return None
    return call['response']['result']

def payloads_from_text(call,count):
    payload=from_call(call)
    if payload is None:return []
    nested=payload.get('result',{}).get('results')
    if count>1 and type(nested) is list:return nested
    return [payload]

def combine_status(*statuses):
    if 'failed' in statuses:return 'failed'
    if statuses and all(s in ('passed','not_applicable') for s in statuses):return 'passed'
    if 'passed' in statuses:return 'partial'
    return 'blocked'

async def run_case(case):
    if case['id']=='MATH-040':
        from backend.math_discrete import normalize_solver_outcome
        values={s:normalize_solver_outcome(s) for s in ('UNKNOWN','TIMEOUT')}
        valid=values['UNKNOWN']['problem_status']=='unresolved' and values['UNKNOWN']['completeness']=='not_proved' and values['TIMEOUT']['execution_status']=='limits_exceeded' and values['TIMEOUT']['problem_status']=='unresolved'
        return {'id':case['id'],'status':'passed' if valid else 'failed','contract_status':'passed' if valid else 'failed','text_status':'not_applicable','original_text_status':'not_applicable',
          'scope':'Explicit local trusted-adapter simulation; no actual SAT/SMT provider or mathematical infeasibility proof.',
          'contract_calls':[],'text_call':None,'original_text_call':None,'adapter_evidence':values,'errors':[] if valid else ['unknown_or_timeout_misclassified']}
    calls=[]
    for request in case['requests']:
        calls.append(await call_worker(json.dumps(request,ensure_ascii=False,separators=(',',':'))))
    outputs=[from_call(c) for c in calls]
    errors=judge_case(case,outputs) if all(v is not None for v in outputs) else []
    contract_status='blocked' if any(v is None for v in outputs) else 'failed' if errors else 'passed'
    text_call=await call_worker(case['input_text'])
    text_outputs=payloads_from_text(text_call,len(case['requests']))
    text_errors=judge_case(case,text_outputs) if text_call['status']=='executed' else []
    text_status='blocked' if text_call['status']!='executed' else 'failed' if text_errors else 'passed'
    original_call=await call_worker(case['source_text'])
    original_outputs=payloads_from_text(original_call,len(case['requests']))
    original_errors=judge_case(case,original_outputs) if original_call['status']=='executed' else []
    original_status='blocked' if original_call['status']!='executed' else 'failed' if original_errors else 'passed'
    return {'id':case['id'],'status':combine_status(contract_status,text_status,original_status),'contract_status':contract_status,'text_status':text_status,'original_text_status':original_status,
      'scope':'Actual fixed local checker subprocess, typed tool inputs and declared concise text; no model, API persistence or visual UI test in this runner.',
      'contract_calls':calls,'text_call':text_call,'original_text_call':original_call,'errors':errors,'text_errors':text_errors,'original_text_errors':original_errors}

async def independent_sample():
    path=ROOT/'evaluation/math/independent-sample.json'
    frozen=ROOT/'evaluation/math/independent-sample.freeze.json'
    if digest(path)!=json.loads(frozen.read_bytes())['sha256']:raise ValueError('independent_fixture_changed')
    rows=[]
    for case in json.loads(path.read_bytes())['cases']:
        call=await call_worker(json.dumps(case['request'],ensure_ascii=False,separators=(',',':')))
        output=from_call(call)
        try:errors=judge_payload(case['request'],case['expected'],output) if output is not None else []
        except (ValueError,KeyError,TypeError,ZeroDivisionError,IndexError,SyntaxError) as e:errors=[type(e).__name__]
        rows.append({'id':case['id'],'status':'blocked' if output is None else 'failed' if errors else 'passed','call':call,'errors':errors})
    return {'sha256':digest(path),'cases':rows,'totals':totals(rows),
      'scope':'Frozen additional engineering variants; developers can see them; not a blind or family-disjoint generalization benchmark.'}

def markdown(report):
    lines=['# نتائج الحالات الرياضية الستين','',
      'هذه نتائج تشغيل العامل الحسابي الفعلي على مدخلات اصطناعية؛ لا تثبت وحدها الحفظ عبر API أو عرض الواجهة أو فهم الصور أو برهانًا رسميًا.',
      'العقد المهيكل وتفسير النص المختصر مقاسان منفصلين. نجاح الأول مع تعذر الثاني يبقى جزئيًا.',
      '',f"بصمة المصدر: `{report['source_fingerprint']}`. تغير أثناء التشغيل: `{report['source_changed_during_run']}`.",
      'الأعداد: `'+json.dumps(report['totals'],ensure_ascii=False)+'`؛ المجموع 60.',
      'العقد الرياضي: `'+json.dumps(report['contract_totals'],ensure_ascii=False)+'`.',
      '', '| الحالة | النتيجة | العقد الرياضي | النص المختصر | النص الأصلي | أخطاء/حدود |','|---|---|---|---|---|---|']
    for case in report['cases']:
        errors=case.get('errors',[])+case.get('text_errors',[])+case.get('original_text_errors',[])
        if case.get('text_call') and case['text_call']['status']=='blocked':errors+=case['text_call']['errors']
        if case.get('original_text_call') and case['original_text_call']['status']=='blocked':errors+=case['original_text_call']['errors']
        if case['id']=='MATH-040':errors=['محاكاة عقد UNKNOWN/TIMEOUT فقط؛ لا موصل SAT/SMT حي.']
        cells=[case['id'],case['status'],case['contract_status'],case['text_status'],case['original_text_status'],'؛ '.join(errors) or 'ضمن النطاق المسجل فقط']
        lines.append('| '+' | '.join(str(v).replace('|','\\|').replace('\n',' ') for v in cells)+' |')
    lines+=['','[النتائج الخام والأزمنة والإصدارات](MATH_REASONING_RESULTS.json)','','العينة الإضافية: `'+json.dumps(report['independent']['totals'])+'`. ليست عينة عمياء أو ضمان تعميم.']
    return '\n'.join(lines)+'\n'

async def run(output):
    from backend.skill_release import current_source_fingerprint
    import importlib.metadata as metadata
    data=load_cases();source=current_source_fingerprint(ROOT);started=time.monotonic();rows=[]
    for case in data['cases']:
        row=await run_case(case);rows.append(row)
        print(case['id'],row['status'],flush=True)
    sample=await independent_sample()
    changed=current_source_fingerprint(ROOT)!=source
    report={'schema_version':'riwaq-math-results-1','generated_at':datetime.now(timezone.utc).isoformat(),
      'dataset_sha256':digest(DATASET),'source_fingerprint':source,'source_changed_during_run':changed,
      'configuration':{'python':platform.python_version(),'platform':platform.platform(),'sympy':metadata.version('sympy'),'mpmath':metadata.version('mpmath'),'model':None,'provider':None,'checker_timeout_seconds':30,'network':'none'},
      'cases':rows,'totals':totals(rows),'contract_totals':totals([{'status':r['contract_status']} for r in rows]),
      'text_totals':totals([{'status':r['text_status']} for r in rows]),'original_text_totals':totals([{'status':r['original_text_status']} for r in rows]),'independent':sample,'seconds':round(time.monotonic()-started,3),
      'critical_failures':[r['id'] for r in rows if r['status']=='failed'],
      'status':'failed' if any(r['status']=='failed' for r in rows) or sample['totals']['failed'] else 'blocked' if changed else 'passed' if all(r['status']=='passed' for r in rows) else 'partial',
      'release_approved':False,'production_effects':False,'historical_evidence_imported':False,
      'scope':'Synthetic math worker acceptance only. No model training, general intelligence comparison, native visual rendering or arbitrary NLP certification.'}
    dump(output/'MATH_REASONING_RESULTS.json',report)
    (output/'MATH_REASONING_RESULTS.md').write_text(markdown(report),encoding='utf-8')
    dump(output/'manifest.json',{'source_fingerprint':source,'dataset_sha256':digest(DATASET),'files':[{'path':p.name,'sha256':digest(p)} for p in sorted(output.iterdir()) if p.is_file()]})
    return report

def exit_code(report,*,is_baseline=False):
    if report.get('status')=='failed' or report['totals']['failed']:return 1
    if report.get('status')=='passed' or is_baseline and report['totals']['passed']==60:return 0
    return 2

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--baseline',action='store_true')
    args=parser.parse_args();output=new_output(args.output)
    if args.baseline:report=baseline(output)
    else:report=asyncio.run(run(output))
    print(json.dumps({'totals':report['totals'],'output':str(output)},ensure_ascii=False))
    return exit_code(report,is_baseline=args.baseline)

if __name__=='__main__':raise SystemExit(main())
