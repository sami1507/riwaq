"""Synthetic real-adapter contract mutations plus evaluation/CI failure detection."""
import copy,hashlib,importlib.util,json,sys,platform,os,shutil
from pathlib import Path
import pytest
from backend import skill_core,skill_evaluation,skill_transform_protocol as transform

ROOT=Path(__file__).resolve().parents[1]

def fixture():
    raw=b'Order,Value\nO1,10\nO2,0\n'
    source={'id':'fixture01','name':'orders.csv','version':1,'sha256':hashlib.sha256(raw).hexdigest(),'content':raw}
    payload=transform.encode_payload('data',{'operation':'read'},[source])
    artifact=transform.output_artifact(raw,'result.csv',{'rows':2})
    refs=[{'source_id':source['id'],'version':1,'sha256':source['sha256']}]
    output=transform.encode_result(payload,{'rows':2},[artifact],refs)
    return payload,json.loads(output)

@pytest.mark.parametrize('mutation',['bytes_type','sha_changed','reference_missing','extra_field','auth_error'])
def test_real_transform_decoder_rejects_broken_connector_contract(mutation):
    payload,envelope=fixture()
    if mutation=='bytes_type':envelope['artifacts'][0]['bytes']='24'
    elif mutation=='sha_changed':envelope['artifacts'][0]['sha256']='0'*64
    elif mutation=='reference_missing':envelope['source_refs'][0]['source_id']='not-authorized'
    elif mutation=='extra_field':envelope['additional_admin']=True
    else:envelope={'version':transform.VERSION,'request_sha256':hashlib.sha256(payload).hexdigest(),'success':False,'error_code':'authentication_required'}
    with pytest.raises(transform.TransformError):transform.decode_result(json.dumps(envelope).encode(),payload,{'state':'completed'})

def test_real_adapter_accepts_absent_optional_semantic_fields_and_checks_actual_bytes():
    payload,envelope=fixture();result=transform.decode_result(json.dumps(envelope).encode(),payload,{'state':'completed'})
    assert result.result=={'rows':2} and result.artifacts[0].content.endswith(b'O2,0\n')
    assert result.artifacts[0].sha256==hashlib.sha256(result.artifacts[0].content).hexdigest()
    assert result.artifacts[0].limitations==()

def test_contract_assertion_detects_seeded_adapter_acceptance_bug():
    payload,envelope=fixture();envelope['artifacts'][0]['bytes']='incorrect'
    def assertion(adapter):
        try:adapter(json.dumps(envelope).encode(),payload,{})
        except transform.TransformError:return
        raise AssertionError('Adapter accepted a non-integer byte count')
    with pytest.raises(AssertionError):assertion(lambda *_:object())
    assertion(transform.decode_result)

def test_dataset_cross_split_duplicate_is_rejected_without_auto_updating_expected(tmp_path):
    original=json.loads((ROOT/'evaluation/batch05/dataset.json').read_bytes())
    original['cases'][12]['input']='  '+original['cases'][0]['input']+'  '
    path=tmp_path/'duplicate.json';path.write_text(json.dumps(original),encoding='utf-8')
    with pytest.raises(ValueError,match='duplicate_or_cross_split_leakage'):skill_evaluation.load_dataset(path)

def test_dataset_split_and_seeded_router_regression(monkeypatch):
    dataset,_=skill_evaluation.load_dataset(ROOT/'evaluation/batch05/dataset.json')
    assert len([c for c in dataset['cases'] if c['split']=='holdout' and c['runner']=='routing'])==12
    with monkeypatch.context() as m:
        m.setattr(skill_core,'select_skills',lambda *_:['authorized-email-send'])
        failures=skill_evaluation.run_routing(dataset,split='holdout')
        assert skill_evaluation.totals(failures)['failed']==12
        assert any('required_skill_missing' in c['reason_codes'] for c in failures)
    result=skill_evaluation.run_routing(dataset,split='holdout')
    assert skill_evaluation.totals(result)=={'passed':12,'failed':0,'skipped':0,'blocked':0}

def runner():
    spec=importlib.util.spec_from_file_location('regression_runner',ROOT/'scripts/run_skill_regression.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def test_ci_junit_failure_skip_and_missing_are_not_passes(tmp_path):
    module=runner();path=tmp_path/'junit.xml'
    assert module.junit_cases(path)[0]['status']=='blocked'
    path.write_text('<testsuites><testsuite><testcase classname="contracts" name="pass"/><testcase classname="contracts" name="fail"><failure>synthetic</failure></testcase><testcase classname="live" name="unavailable"><skipped>missing approved service</skipped></testcase></testsuite></testsuites>',encoding='utf-8')
    cases=module.junit_cases(path)
    assert [c['status'] for c in cases]==['passed','failed','skipped']

def test_fixed_command_failure_and_missing_runtime_are_reported(tmp_path):
    module=runner()
    failed=module.invoke([sys.executable,'-c','raise SystemExit(7)'],ROOT,{},10,tmp_path/'fail.log')
    assert failed['exit_code']==7 and failed['status']=='failed'
    missing=module.invoke([str(tmp_path/'nonexistent-program')],ROOT,{},10,tmp_path/'missing.log')
    assert missing['status']=='blocked' and missing['reason_code']=='runtime_unavailable'

def test_install_evidence_is_explicit_atomic_and_rejects_failed_or_linked_pointer(tmp_path,monkeypatch):
    from backend import local_auth,skill_release
    module=runner();root=tmp_path/'source';root.mkdir();data=tmp_path/'operator';data.mkdir()
    monkeypatch.setattr(module,'ROOT',root);monkeypatch.setenv('RIWAQ_DATA_DIR',str(data))
    key=b'SYNTHETIC_SIGNING_KEY_FOR_TEST_123456789'
    monkeypatch.setattr(local_auth,'_master_key',lambda create=False:key)
    report={'schema_version':'riwaq-evaluation-1','profile':'batch05-fast','source_fingerprint':skill_release.current_source_fingerprint(root),'dataset':{'version':'synthetic','sha256':'1'*64},'environment':{'python':platform.python_version(),'platform':platform.platform()},'cases':[{'id':'pytest:synthetic','split':'contract','critical':True,'status':'passed'},{'id':'synthetic:holdout','split':'holdout','critical':True,'status':'passed'}],'totals':{'passed':2,'failed':0,'skipped':0,'blocked':0},'execution':{'kind':'local_contract','commands':[['python','-m','pytest','fixed-fixture.py']],'exit_code':0}}
    evidence=skill_release.seal_runner_report(report,root,key)
    assert module.install_evidence(evidence)['installed']
    pointer=data/'skill-release/batch05-fast.json';before=pointer.read_bytes()
    invalid=copy.deepcopy(evidence);invalid['payload']['report']['cases'][0]['status']='failed'
    with pytest.raises(ValueError):module.install_evidence(invalid)
    assert pointer.read_bytes()==before
    os.link(pointer,data/'alias.json')
    with pytest.raises(ValueError,match='regular file'):module.install_evidence(evidence)

def test_sealing_installation_cannot_be_requested_with_measurement_only(tmp_path):
    with pytest.raises(ValueError,match='requires trusted'):runner().run(tmp_path/'not-created',seal=False,install=True)
    assert not (tmp_path/'not-created').exists()

def test_actual_typescript_detects_seeded_type_error_then_accepts_fix(tmp_path):
    node=shutil.which('node');compiler=ROOT/'frontend/node_modules/typescript/bin/tsc'
    if not node or not compiler.is_file():pytest.skip('Existing Node/TypeScript runtime unavailable; this is not a pass')
    source=tmp_path/'example.ts';source.write_text('const amount: number = "wrong";\n',encoding='utf-8')
    command=[node,str(compiler),'--noEmit','--skipLibCheck','--strict','--target','ES2022',str(source)]
    first=runner().invoke(command,tmp_path,os.environ.copy(),30,tmp_path/'type-before.log')
    assert first['status']=='failed' and 'TS2322' in (tmp_path/'type-before.log').read_text('utf-8')
    source.write_text('const amount: number = 42;\n',encoding='utf-8')
    second=runner().invoke(command,tmp_path,os.environ.copy(),30,tmp_path/'type-after.log')
    assert second['status']=='passed'
