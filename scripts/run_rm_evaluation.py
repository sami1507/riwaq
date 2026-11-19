"""Twenty disclosed acceptance cases on the real fixed worker; no clinical gate."""
import argparse,asyncio,hashlib,json,sys,time,platform
from datetime import datetime, timezone
from importlib.metadata import version
from fractions import Fraction
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend import reasoning,reasoning_math


def judge(identifier,output):
    r=output['result'];status=output['problem_status']
    def equal(a,b):assert Fraction(a)==Fraction(b),(a,b)
    if identifier=='RM-001':equal(r['value'],'3/10');assert r['decimal']=='0.3' and r['decimal_exact'] is True
    elif identifier=='RM-002':
        if 'calculations' in r:equal(r['calculations'][-1]['exact'],'391/4')
        else:equal(r['value'],'391/4')
    elif identifier=='RM-003':equal(r['value'],20);assert r['unit']=='m/s'
    elif identifier=='RM-004':assert status=='no_solution' and r['solutions']==[] and output['conditions']
    elif identifier=='RM-005':assert r['solutions']==['2'] and output['conditions']
    elif identifier=='RM-006':assert len(r['cases'])==3 and {c['problem_status'] for c in r['cases']}=={'solved','no_solution'} and r['variable']=='x'
    elif identifier=='RM-007':assert r['intervals']==[{'left':'-oo','right':'-3','left_open':True,'right_open':True}]
    elif identifier=='RM-008':assert r['results'][0]['problem_status']=='no_solution' and set(r['results'][1]['result']['solutions'])=={'-I','I'}
    elif identifier=='RM-009':assert r['families']==[{'expression':'pi*k','parameter':'k','parameter_domain':'integer'}]
    elif identifier=='RM-010':
        from scripts.run_math_evaluation import algebra
        row=r['solutions'][0];assert algebra(row['x'])+algebra(row['y'])==2 and r['parameters']
    elif identifier=='RM-011':assert r['differentiable'] is False and r['left']=='-1' and r['right']=='1'
    elif identifier=='RM-012':assert r['converges'] is False and r['principal_value']=='0' and status=='undefined'
    elif identifier=='RM-013':
        from scripts.run_math_evaluation import algebra
        import sympy as s
        f=algebra(r['expression']);x=next(iter(f.free_symbols));assert s.simplify(s.diff(f,x)-f)==0 and f.subs(x,0)==3
    elif identifier=='RM-014':assert r['attained'] is False and r['bound']=='0' and r['bound_kind']=='infimum' and r['point'] is None
    elif identifier=='RM-015':assert r['entails'] is False and r['counterexample'] is not None
    elif identifier=='RM-016':assert r['counterexample']==2 and r['universal_claim_refuted'] is True
    elif identifier=='RM-017':equal(r['value'],'2/13');equal(r['true_positive_count'],90);equal(r['false_positive_count'],495);assert r['population_scope']=='hypothetical'
    elif identifier=='RM-018':equal(r['sum'],30);equal(r['mean'],10);assert r['known']==3 and r['missing']==1
    elif identifier=='RM-019':equal(r['residual'],'99/1000000000000');equal(r['root_error'],99);assert r['is_exact_root'] is False
    else:raise AssertionError('unknown_case')


async def run(output):
    output.mkdir(parents=True,exist_ok=False)
    cases=json.loads((ROOT/'evaluation/math/RM_EVALS.json').read_bytes())['cases'];rows=[]
    files=['backend/reasoning_math.py','backend/math_wording.py','backend/math_symbolic.py','backend/math_discrete.py','backend/math_expression.py','backend/check_worker.py']
    fingerprint=lambda:{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files}
    before=fingerprint()
    for case in cases:
        start=time.monotonic();calls=[];errors=[]
        for text in [case['text']]+([case['limit_text']] if 'limit_text' in case else []):
            actual=await reasoning.checker('solve_math',text,timeout=35);calls.append(actual)
            try:
                if case['id']=='RM-020':assert actual.get('status')=='error' and actual.get('result',{}).get('problem_status')!='no_solution'
                else:
                    assert actual['status']=='ok',actual
                    reasoning_math.validate_math_result(actual['result'],text);judge(case['id'],actual['result'])
            except (AssertionError,KeyError,ValueError,TypeError,StopIteration) as exc:errors.append(str(exc)[:400] or type(exc).__name__)
        rows.append({'id':case['id'],'status':'failed' if errors else 'passed','seconds':time.monotonic()-start,'calls':calls,'errors':errors})
        print(case['id'],rows[-1]['status'],flush=True)
    report={'scope':'Real fixed checker process, disclosed original prompt grammar only; not a clinical, model, UI, or general language-quality evaluation. RM020 uses two actual rejected inputs.',
            'generated_at':datetime.now(timezone.utc).isoformat(),
            'configuration':{'python':platform.python_version(),'platform':platform.platform(),'sympy':version('sympy'),'mpmath':version('mpmath'),'model':None,'provider':None,'timeout_seconds':35},
            'dataset_sha256':hashlib.sha256((ROOT/'evaluation/math/RM_EVALS.json').read_bytes()).hexdigest(),
            'evaluator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'cases':rows,'source_sha256':before,'source_unchanged':fingerprint()==before,'totals':{s:sum(r['status']==s for r in rows) for s in ('passed','failed')}}
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if report['totals']['passed']==20 and report['source_unchanged'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();raise SystemExit(asyncio.run(run(args.output)))
