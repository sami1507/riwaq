"""Record synthetic, real HTTP outputs after running the medical pytest suite.

No patient data, pairing code, session cookie or database is exported.
Run from the repository root after tests/test_medical_education.py.
"""
import hashlib,json,os,platform,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
OUT=ROOT/'reports/medical-20261117'
suite=ET.parse(OUT/'pytest-medical.xml').find('testsuite')
fixture=json.loads((ROOT/'tests/fixtures/medical_acceptance.json').read_text(encoding='utf8'))
samples=[]
with tempfile.TemporaryDirectory(prefix='riwaq-medical-evidence-') as directory:
    os.environ['RIWAQ_DATA_DIR']=directory
    os.environ['RIWAQ_KNOWLEDGE_REGISTRY']=str(Path(directory)/'absent.json')
    from fastapi.testclient import TestClient
    from backend import app,local_auth
    with TestClient(app.app,headers={'X-Riwaq-Client':'local'}) as client:
        assert client.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
        project=client.get('/api/projects').json()[0]['id']
        base=f'/api/projects/{project}/medical'
        response=client.get(base+'/capabilities');assert response.status_code==200
        capabilities=response.json()
        inputs=[{'topic':'symptom_diagnosis','level':level,'language':language} for language in ['ar','en'] for level in ['simple','detailed','technical']]
        inputs.extend(row['input'] for row in fixture['cases'] if row['id'] in ['MED-002','MED-003','MED-004','MED-006','MED-008','MED-010'])
        for body in inputs:
            response=client.post(base+'/explain',json=body);assert response.status_code==200
            samples.append({'synthetic_input':body,'http_status':response.status_code,'actual_output':response.json()})
        assert client.post('/api/auth/logout').status_code==200

def outcome(test):
    return 'FAIL' if test.find('failure') is not None or test.find('error') is not None else 'SKIPPED' if test.find('skipped') is not None else 'PASS'

testcases=[{'name':test.attrib['name'],'status':outcome(test),'seconds':float(test.attrib['time'])} for test in suite.findall('testcase')]
files=['backend/medical_schema.py','backend/medical_education.py','backend/medical_api.py','config/medical_sources.json','tests/test_medical_education.py','tests/fixtures/medical_acceptance.json','backend/app.py','backend/guard.py','backend/check_worker.py']
record={
    'recorded_at':datetime.now(timezone.utc).isoformat(),
    'environment':{'os':platform.platform(),'python':platform.python_version(),'execution':'real local FastAPI TestClient, local-owner pairing, isolated SQLite and existing Arithmetic; no provider'},
    'commands':['.venv/Scripts/python.exe -m pytest tests/test_medical_education.py -q --junitxml=reports/medical-20261117/pytest-medical.xml','.venv/Scripts/python.exe tests/record_medical_evidence.py'],
    'tests':{'total':int(suite.attrib['tests']),'failures':int(suite.attrib['failures']),'errors':int(suite.attrib['errors']),'skipped':int(suite.attrib['skipped']),'seconds':float(suite.attrib['time']),'cases':testcases},
    'source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files},
    'capabilities_route_status':200,'sample_posts':len(samples),'model_calls':0,'external_network_calls_in_application_path':0,
    'clinical_review':{'qualified_review_completed':False,'clinical_use_approved':False,'blocker':'Qualified reviewer, reviewed scenario set and declared intended clinical use have not been supplied or performed.'},
    'acceptance_mapping':[{'id':row['id'],'engineering_test':'PASS' if any(t['name']==f"test_reference_engineering_contract[{row['id']}]" and t['status']=='PASS' for t in testcases) else 'NOT PASS','scope':row['requirement_scope'],'clinical_review':'NOT RUN'} for row in fixture['cases']],
    'limits':['Public fixtures are not held-out tests or evidence of LLM quality/generalization.','MED-008 source recommendation adjudication is partial; metadata alone is not evidence.','MED-007 clinical formula execution is unsupported; only the guard is tested.','MED-011 is general design education; no particular paper was reviewed.','MED-012 tests an ephemeral structured correction, not a shared medical knowledge learning loop.','Baseline measured absent HTTP routes, not medical accuracy. No before/after clinical percentage is claimed.']}
(OUT/'verification.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
(OUT/'samples.json').write_text(json.dumps({'disclosure':'Synthetic development examples, actual authenticated API responses; not patient care or clinical approval.','samples':samples},ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'tests':record['tests']['total'],'sample_posts':len(samples),'qualified_review_completed':False,'output_directory':str(OUT)},indent=2))
