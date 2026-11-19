"""Local release evidence issued only by the fixed-profile operator runner.

No HTTP signing endpoint. Model text, uploaded reports and a word PASS are not
evidence. HMAC proves this installation issued a record, not publisher identity
or independent security certification. Live-provider approval is not inferred.
"""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib,hmac,json,platform,sys
from pathlib import Path
from backend.skill_registry import RegistryError,canonical_json,digest

SCHEMA='riwaq-release-evidence-1'
PROFILES=frozenset({'batch05-fast'})
SOURCE_DIRS=('backend','frontend/src','frontend/scripts','config','skills','tests','scripts','evaluation','docs/skills')
SOURCE_FILES=('requirements.txt','requirements.lock.txt','frontend/package.json','frontend/package-lock.json','pytest.ini')

def current_source_fingerprint(root):
    root=Path(root).resolve();paths=set()
    for name in SOURCE_DIRS:
        folder=root/name
        if folder.is_dir():
            paths.update(p for p in folder.rglob('*') if p.is_file() and not any(x in p.parts for x in ('__pycache__','node_modules','.pytest_cache')) and p.suffix not in {'.pyc','.pyo'})
    paths.update(root/n for n in SOURCE_FILES if (root/n).is_file())
    if len(paths)>10000:raise RegistryError('release_source_file_limit')
    records=[];total=0
    for path in sorted(paths):
        if path.is_symlink() or not path.resolve().is_relative_to(root):raise RegistryError('release_source_link_not_supported')
        if path.stat().st_size>10*1024*1024:raise RegistryError('release_source_file_too_large')
        raw=path.read_bytes();total+=len(raw)
        if total>100*1024*1024:raise RegistryError('release_source_limit')
        records.append({'path':path.relative_to(root).as_posix(),'sha256':hashlib.sha256(raw).hexdigest()})
    return digest(records)

def _key(signing_key):
    if not isinstance(signing_key,bytes) or len(signing_key)<32:raise RegistryError('release_signing_key_unavailable')
    return hmac.new(signing_key,b'riwaq-fixed-runner-attestation-v1',hashlib.sha256).digest()

def _report(report):
    if not isinstance(report,dict) or report.get('schema_version')!='riwaq-evaluation-1' or report.get('profile') not in PROFILES:raise RegistryError('unsupported_release_report')
    cases=report.get('cases')
    if not isinstance(cases,list) or not 1<=len(cases)<=10000:raise RegistryError('release_cases_missing')
    ids=set();counts={s:0 for s in ['passed','failed','skipped','blocked']}
    for case in cases:
        if not isinstance(case,dict) or not isinstance(case.get('id'),str) or not 1<=len(case['id'])<=250 or case['id'] in ids or type(case.get('critical')) is not bool or case.get('status') not in counts:raise RegistryError('invalid_release_case')
        ids.add(case['id']);counts[case['status']]+=1
    if counts!=report.get('totals'):raise RegistryError('release_totals_mismatch')
    execution=report.get('execution',{})
    if execution.get('kind')!='local_contract' or type(execution.get('exit_code')) is not int:raise RegistryError('release_execution_missing')
    commands=execution.get('commands')
    if not isinstance(commands,list) or not 1<=len(commands)<=12 or any(not isinstance(cmd,list) or not cmd or any(not isinstance(arg,str) or len(arg)>1000 for arg in cmd) for cmd in commands):raise RegistryError('release_commands_missing')
    if not any(len(cmd)>3 and cmd[1:3]==['-m','pytest'] for cmd in commands):raise RegistryError('release_test_process_missing')
    if not any(c.get('split')=='holdout' for c in cases):raise RegistryError('release_holdout_missing')
    dataset=report.get('dataset',{})
    if not isinstance(dataset,dict) or not isinstance(dataset.get('version'),str) or not isinstance(dataset.get('sha256'),str) or len(dataset['sha256'])!=64:raise RegistryError('release_dataset_missing')
    env=report.get('environment',{})
    if not isinstance(env,dict) or not isinstance(env.get('python'),str) or not isinstance(env.get('platform'),str):raise RegistryError('release_environment_missing')
    return counts

def seal_runner_report(report,root,signing_key):
    """Trusted CLI calls after its own fixed subprocesses; never sign UI input."""
    _report(report);current=current_source_fingerprint(root)
    if report.get('source_fingerprint')!=current:raise RegistryError('source_changed_during_release_run')
    payload={'report':report,'source_fingerprint':current,'issued_at':datetime.now(timezone.utc).isoformat(),'issuer':'local-fixed-profile-runner'}
    return {'schema_version':SCHEMA,'payload':payload,'signature':hmac.new(_key(signing_key),canonical_json(payload).encode(),hashlib.sha256).hexdigest()}

def evaluate_release(evidence,root,signing_key,required_profile='batch05-fast'):
    reasons=[];scope='unverified';counts=None
    try:
        if not isinstance(evidence,dict) or set(evidence)!={'schema_version','payload','signature'} or evidence['schema_version']!=SCHEMA:raise RegistryError('unsigned_or_unsupported_evidence')
        payload=evidence['payload'];signature=evidence['signature']
        if not isinstance(payload,dict) or not isinstance(signature,str) or len(canonical_json(evidence))>4000000:raise RegistryError('invalid_release_evidence')
        expected=hmac.new(_key(signing_key),canonical_json(payload).encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,signature):raise RegistryError('evidence_issuer_not_verified')
        if payload.get('issuer')!='local-fixed-profile-runner':raise RegistryError('unknown_evidence_issuer')
        report=payload.get('report');counts=_report(report)
        if required_profile not in PROFILES or report['profile']!=required_profile:raise RegistryError('release_profile_mismatch')
        if payload.get('source_fingerprint')!=current_source_fingerprint(root) or report['source_fingerprint']!=payload['source_fingerprint']:raise RegistryError('release_source_stale')
        if report['environment']['python']!=platform.python_version():raise RegistryError('release_environment_changed')
        if report['environment']['platform']!=platform.platform():raise RegistryError('release_environment_changed')
        if report['execution']['exit_code']!=0:reasons.append('test_process_failed')
        if any(c['critical'] and c['status']!='passed' for c in report['cases']):reasons.append('critical_case_not_passed')
        if counts['failed']:reasons.append('behavior_checks_failed')
        if counts['skipped'] or counts['blocked']:reasons.append('required_checks_not_verified')
        scope='local_contract'
    except (RegistryError,ValueError,KeyError,TypeError,OSError) as exc:
        reasons.append(str(exc) if isinstance(exc,RegistryError) else 'release_evidence_unavailable')
    return {'approved':not reasons,'state':'approved_local' if not reasons else 'blocked','reasons':list(dict.fromkeys(reasons)),
            'tested_scope':scope,'counts':counts,'live_provider_verified':False,'publish_authorized':False,'profile':required_profile}

def stored_decision(root,data_dir):
    """Read one operator-owned signed report; absence remains unverified."""
    path=Path(data_dir)/'skill-release'/'batch05-fast.json'
    if not path.exists():return {'approved':False,'state':'unverified','reasons':['release_evidence_missing'],'tested_scope':'unverified','publish_authorized':False,'live_provider_verified':False}
    try:
        from backend import local_auth
        if path.is_symlink() or path.parent.is_symlink() or path.stat().st_size>4000000:raise RegistryError('invalid_release_storage')
        return evaluate_release(json.loads(path.read_bytes()),root,local_auth._master_key(create=False))
    except (OSError,ValueError,RuntimeError):return {'approved':False,'state':'blocked','reasons':['release_evidence_unavailable'],'tested_scope':'unverified','publish_authorized':False,'live_provider_verified':False}
