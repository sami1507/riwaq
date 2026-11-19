"""Fixed local Batch05 contract profile; no deployment, installs or arbitrary commands.

The signer is called only after this process executes the declared tests. A
caller cannot submit a JSON report to this CLI and get it approved.
"""
from __future__ import annotations
import argparse,hashlib,json,os,platform,subprocess,sys,tempfile,time
from datetime import datetime,timezone
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def junit_cases(path):
    if not path.is_file():return [{'id':'pytest:report-missing','split':'contract','critical':True,'status':'blocked','reason_codes':['junit_missing']}]
    root=ET.parse(path).getroot();cases=[]
    for item in root.iter('testcase'):
        status='failed' if item.find('failure') is not None or item.find('error') is not None else 'skipped' if item.find('skipped') is not None else 'passed'
        cases.append({'id':'pytest:'+item.get('classname','')+'.'+item.get('name',''),'split':'contract','critical':True,'status':status,'reason_codes':[] if status=='passed' else ['pytest_'+status]})
    if not cases:return [{'id':'pytest:no-cases','split':'contract','critical':True,'status':'blocked','reason_codes':['no_tests_collected']}]
    return cases

def invoke(argv,cwd,env,timeout,log):
    started=time.monotonic()
    try:
        process=subprocess.run(argv,cwd=cwd,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,shell=False)
        # Tests use synthetic data. The public report does not copy assertions
        # or exception text; this operator-local log is never served by API.
        log.write_text(process.stdout+process.stderr,encoding='utf-8')
        return {'command':argv,'exit_code':process.returncode,'seconds':round(time.monotonic()-started,6),'status':'passed' if process.returncode==0 else 'failed'}
    except subprocess.TimeoutExpired:
        log.write_text('Fixed profile exceeded its subprocess deadline. No successful result is claimed.\n',encoding='utf-8')
        return {'command':argv,'exit_code':124,'seconds':round(time.monotonic()-started,6),'status':'blocked','reason_code':'profile_timeout'}
    except OSError:
        return {'command':argv,'exit_code':127,'seconds':round(time.monotonic()-started,6),'status':'blocked','reason_code':'runtime_unavailable'}

def install_evidence(evidence):
    from backend.db import data_dir
    from backend.local_auth import _master_key,_private
    from backend.skill_release import evaluate_release
    decision=evaluate_release(evidence,ROOT,_master_key(create=False))
    if not decision['approved']:raise ValueError('Unapproved evidence cannot be installed')
    base=data_dir().absolute()
    for part in (base,*base.parents):
        if part.is_symlink() or getattr(part,'is_junction',lambda:False)():raise ValueError('Linked evidence storage is not supported')
    folder=base/'skill-release'
    if folder.is_symlink() or getattr(folder,'is_junction',lambda:False)():raise ValueError('Linked evidence storage is not supported')
    folder.mkdir(exist_ok=True);_private(folder,directory=True)
    target=folder/'batch05-fast.json'
    if target.exists() and (not target.is_file() or target.stat().st_nlink!=1 or target.is_symlink()):raise ValueError('Evidence pointer is not a regular file')
    encoded=(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n').encode()
    archive=folder/(hashlib.sha256(encoded).hexdigest()+'.json')
    if not archive.exists():
        with archive.open('xb') as handle:handle.write(encoded);handle.flush();os.fsync(handle.fileno())
        _private(archive)
    elif archive.is_symlink() or archive.read_bytes()!=encoded:raise ValueError('Evidence archive conflict')
    fd,name=tempfile.mkstemp(prefix='evidence-',suffix='.tmp',dir=folder)
    temporary=Path(name)
    try:
        with os.fdopen(fd,'wb') as handle:handle.write(encoded);handle.flush();os.fsync(handle.fileno())
        _private(temporary);os.replace(temporary,target)
    finally:
        if temporary.exists() and temporary.resolve().parent==folder.resolve():temporary.unlink()
    return {'installed':True,'profile':'batch05-fast','scope':'local_contract','published':False}

def run(output,*,ci=False,seal=True,install=False,ai_core=False):
    from backend.skill_evaluation import load_dataset,run_routing,totals
    from backend.skill_release import current_source_fingerprint,seal_runner_report
    if install and not seal:raise ValueError('Installing requires trusted runner sealing')
    output=Path(output).resolve()
    if output.exists():raise ValueError('Evaluation output directory must be new')
    output.mkdir(parents=True)
    source=current_source_fingerprint(ROOT)
    data,sha=load_dataset(ROOT/'evaluation/batch05/dataset.json')
    cases=run_routing(data);commands=[]
    from backend.environment_audit import audit_environment
    configuration=audit_environment(ROOT)
    (output/'environment-audit.json').write_text(json.dumps(configuration,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    cases.append({'id':'environment:startup-and-client-boundary','split':'contract','critical':True,'status':'failed' if configuration['state']=='blocked' else 'passed','reason_codes':['configuration_blocked'] if configuration['state']=='blocked' else []})
    files=sorted(str(p.relative_to(ROOT)).replace('\\','/') for p in (ROOT/'tests').glob('test_skill_batch05_*.py'))
    if not files:raise ValueError('Fixed contract suite is missing')
    files+=['tests/test_skills.py','tests/test_skill_batch02_lifecycle.py','tests/test_skill_batch02_review.py',
            'tests/test_skill_refinement_review.py','tests/test_skill_batch04_integration.py']
    # Feedback gates are application contracts, not a separate competing CI.
    files+=['tests/test_learning_core.py','tests/test_learning_api.py','tests/test_learning_verification.py',
            'tests/test_learning_evaluation.py','tests/test_learning_data.py','tests/test_learning_review.py']
    if ai_core:
        from backend.ai_core_evaluation import load_cases as load_ai_cases,profile_selectors
        ai_dataset,ai_hash=load_ai_cases()
        files=profile_selectors(files)
    env={k:v for k,v in os.environ.items() if not k.startswith('RIWAQ_') and not any(s in k.upper() for s in ('API_KEY','PASSWORD','SECRET','ACCESS_TOKEN'))}
    env['PYTHONUTF8']='1';env['PYTHONDONTWRITEBYTECODE']='1'
    with tempfile.TemporaryDirectory(prefix='riwaq-batch05-ci-') as data_dir:
        env['RIWAQ_DATA_DIR']=data_dir
        command=[sys.executable,'-m','pytest',*files,'-q','--junitxml='+str(output/'pytest.xml')]
        result=invoke(command,ROOT,env,360 if ai_core else 240,output/'pytest.log');commands.append(result)
        pytest_result=result
        try:cases.extend(junit_cases(output/'pytest.xml'))
        except (ET.ParseError,OSError):cases.append({'id':'pytest:invalid-report','split':'contract','critical':True,'status':'blocked','reason_codes':['junit_invalid']})
        report={'schema_version':'riwaq-evaluation-1','profile':'batch05-fast','source_fingerprint':source,'dataset':{'version':data['version'],'sha256':sha},'environment':{'python':platform.python_version(),'platform':platform.platform(),'provider':'none','model':None},'cases':cases,'totals':totals(cases),'generated_at':datetime.now(timezone.utc).isoformat(),'execution':{'kind':'local_contract','commands':[command],'exit_code':result['exit_code']},'semantic_coverage':{'executed':False,'unexecuted_cases':[c['id'] for c in data['cases'] if c['runner']=='human-review'],'reason':'Independent semantic review is a separate profile; not a successful model evaluation.'}}
        (output/'evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        if seal:
            from backend.local_auth import _master_key
            # Existing protected operator identity. Sealing does not activate a
            # skill or bypass runtime authorization; the current-source gate
            # rejects a failure, stale report or a missing required test.
            evidence=seal_runner_report(report,ROOT,_master_key(create=False))
            (output/'release-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        if ci:
            import shutil
            node=shutil.which('node') or 'node'
            commands.append(invoke([node,'node_modules/typescript/bin/tsc','-b','--pretty','false'],ROOT/'frontend',env,120,output/'typescript.log'))
            if commands[-1]['exit_code']==0:commands.append(invoke([node,'node_modules/vite/bin/vite.js','build'],ROOT/'frontend',env,120,output/'build.log'))
    changed=current_source_fingerprint(ROOT)!=source
    result={'profile':'local-ci' if ci else 'batch05-fast','source_fingerprint':source,'source_changed_during_run':changed,'steps':commands,'totals':report['totals'],'live_services':{'status':'not_run','reason':'No authorized provider test credentials or production operations in this profile'},'status':'passed' if not changed and all(r['status']=='passed' for r in commands) and not any(report['totals'][k] for k in ('failed','skipped','blocked')) else 'blocked' if changed or any(r['status']=='blocked' for r in commands) else 'failed','deployed':False}
    if ai_core:
        from scripts.run_ai_core_evaluation import finish_report,configuration
        ai_result=finish_report(output,ai_dataset,ai_hash,source,configuration(files,profile='ai-core-with-local-ci-v1',timeout=360),pytest_result)
        result['ai_core']={'status':ai_result['status'],'totals':ai_result['totals'],'report':'AI_CORE_EVAL_RESULTS.json','release_approved':False}
        if ai_result['status']=='failed':result['status']='failed'
        elif result['status']=='passed' and ai_result['status']!='passed':result['status']=ai_result['status']
    result['evidence_installation']=install_evidence(evidence) if install and result['status']=='passed' else {'installed':False,'reason':'not_requested' if not install else 'required_profile_not_passed'}
    (output/'ci.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);p.add_argument('--ci',action='store_true');p.add_argument('--no-seal',action='store_true',help='Run measurement without touching the operator protected key');p.add_argument('--install-evidence',action='store_true',help='After all requested checks pass, atomically install current local evidence in this initialized test/operator data directory');p.add_argument('--ai-core',action='store_true',help='Include fixed AI-core contracts in the same pytest invocation and report all100 cases separately; partial evidence is not approval')
    args=p.parse_args()
    try:result=run(args.output,ci=args.ci,seal=not args.no_seal,install=args.install_evidence,ai_core=args.ai_core)
    except (ValueError,OSError):p.exit(2,'Fixed evaluation refused: check new output directory, protected identity and current source.\n')
    print(json.dumps(result,ensure_ascii=False));return 0 if result['status']=='passed' else 2 if result['status']=='partial' else 1
if __name__=='__main__':raise SystemExit(main())
