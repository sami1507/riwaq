"""Actual authenticated HTTP + SQLite + exact arithmetic, no provider mocks.

Public fixtures test bounded engineering contracts, not clinical certification,
general language understanding or independently held-out medical correctness.
"""
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import app as service, db, local_auth, medical_education

CASES=json.loads((Path(__file__).parent/'fixtures/medical_acceptance.json').read_text(encoding='utf8'))['cases']

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY',str(tmp_path/'absent.json'))
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as value:
        assert value.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
        yield value

def base(client):
    return '/api/projects/'+client.get('/api/projects').json()[0]['id']+'/medical'

def explain(client,body,path=None):
    response=client.post((path or base(client))+'/explain',json=body)
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='no-store'
    return response.json()

@pytest.mark.parametrize('case',CASES,ids=lambda row:row['id'])
def test_reference_engineering_contract(client,case):
    result=explain(client,case['input'])
    assert result['review_state']==case['expected_state']
    for key,value in case.get('expected_result',{}).items():assert result['result'][key]==value
    for field in case.get('expected_missing',[]):assert field in result['missing_fields']
    refs={row['source']['id'] for row in result['evidence_records']}
    assert set(case.get('expected_sources',[]))<=refs
    assert result['summary'] and not result['clinical_review']['qualified_review_completed']
    assert not any(result['privacy'].values())
    for evidence in result['evidence_records']:
        assert evidence['source']['status']=='read'
        assert evidence['source']['accessed_at']=='2026-11-17'
        assert evidence['freshness']=='reference_reviewed_at_development_not_live_at_answer'
        assert not evidence['clinical_applicability_certified']

@pytest.mark.parametrize('language',['ar','en'])
def test_levels_preserve_answer_and_add_real_depth(client,language):
    outputs=[explain(client,{'topic':'symptom_diagnosis','language':language,'level':level}) for level in ['simple','detailed','technical']]
    assert len({r['summary'] for r in outputs})==1
    assert len(outputs[0]['steps'])<len(outputs[1]['steps'])<len(outputs[2]['steps'])
    assert len(outputs[0]['definitions'])<len(outputs[1]['definitions'])<len(outputs[2]['definitions'])
    for value in outputs:assert not value['missing_fields']
    optional=explain(client,{'topic':'symptom_diagnosis','language':language,'optional_understanding_check':True})
    assert optional['summary']==outputs[0]['summary'] and optional['understanding_check']

def test_urgent_priority_over_topic_and_optional_lesson(client):
    result=explain(client,{'topic':'mass_conversion','quantity':{'value':'500','from_unit':'mcg','to_unit':'mg'},'severe_persistent_chest_pain':True,'shortness_of_breath':True,'optional_understanding_check':True})
    assert result['review_state']=='urgent_help' and not result['result']
    assert not result['missing_fields'] and 'understanding_check' not in result
    assert not any(c.isdigit() for c in result['summary'])
    assert 'الآن' in result['summary']

@pytest.mark.parametrize('value,source,target,expected',[
    ('٥٠٠','mcg','mg','1/2'),('٠٫٥','mg','mcg','500'),('0','mg','g','0'),
    ('1','g','mcg','1000000'),('900719925474099','mg','g','900719925474099/1000')])
def test_exact_units_arabic_zero_and_large_input(client,value,source,target,expected):
    result=explain(client,{'topic':'mass_conversion','quantity':{'value':value,'from_unit':source,'to_unit':target}})
    assert result['result']['exact']==expected
    assert result['result']['decimal_exact']
    assert {item['kind'] for item in result['verification']}=={'existing_bounded_arithmetic','inverse_unit_check'}

def test_mass_volume_missing_concentration(client):
    result=explain(client,{'topic':'mass_conversion','quantity':{'value':'5','from_unit':'mg','to_unit':'mL'}})
    assert result['review_state']=='needs_data'
    assert result['missing_fields']==['verified_concentration'] and not result['result']

@pytest.mark.parametrize('value',['__import__("os")','1,000','1,5','1e99','1'*33,500,True])
def test_reject_ambiguous_unsafe_unbounded_numeric_input(client,value):
    response=client.post(base(client)+'/explain',json={'topic':'mass_conversion','quantity':{'value':value,'from_unit':'mg','to_unit':'g'}})
    assert response.status_code==422

@pytest.mark.parametrize('extra',[{'persist':True},{'allow_training':True},{'actor':'admin'},{'tenant':'other'},{'verified':True}])
def test_client_cannot_authorize_persistence_or_authority(client,extra):
    assert client.post(base(client)+'/explain',json={'topic':'symptom_diagnosis',**extra}).status_code==422

def test_lab_preserves_report_units_zero_and_correction(client):
    source={'name':'Synthetic','value':'10','unit':'u','reference_low':'0','reference_high':'5','reference_unit':'u','sample_date':'2026-11-16'}
    above=explain(client,{'topic':'lab_result','lab':source})
    source['value']='0'
    within=explain(client,{'topic':'lab_result','lab':source})
    assert above['result']['position']=='above' and within['result']['position']=='within'
    assert above['limitations']==within['limitations']
    assert within['result']['report']['value']=='0'
    assert within['result']['reference_origin']=='user_supplied_not_independently_verified'
    source['reference_unit']='different'
    mismatch=explain(client,{'topic':'lab_result','lab':source})
    assert mismatch['review_state']=='needs_data' and not mismatch['result']
    source['reading_uncertain']=True
    assert explain(client,{'topic':'lab_result','lab':source})['missing_fields']==['confirmed_reading']

def test_lab_reversed_range_and_bad_date_rejected(client):
    for source in [{'reference_low':'10','reference_high':'0'},{'sample_date':'2026-02-30'}]:
        assert client.post(base(client)+'/explain',json={'topic':'lab_result','lab':source}).status_code==422

def test_no_raw_image_or_live_warning_claim(client):
    result=explain(client,{'topic':'unclear_report','image_kind':'raw_medical_image'})
    assert result['review_state']=='unsupported' and not result['result']
    warning=explain(client,{'topic':'current_drug_warning','language':'en'})
    assert warning['review_state']=='current_evidence_unavailable' and not warning['evidence_records']
    assert 'not been checked' in warning['summary']

def test_no_content_persistence_or_automatic_learning(client):
    case=next(row for row in CASES if row['id']=='MED-012')
    with db.get_db() as conn:before='\n'.join(conn.iterdump())
    assert 'PRIVATE_MEDICAL_SENTINEL_61732' not in before
    result=explain(client,case['input'])
    with db.get_db() as conn:
        after='\n'.join(conn.iterdump())
        for table in ['messages','memories','learning_feedback','learning_candidates','learning_events']:
            assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0
    assert 'PRIVATE_MEDICAL_SENTINEL_61732' not in after
    assert not any(result['privacy'].values())

def test_source_metadata_cannot_certify_itself(client):
    body={'topic':'source_contexts','source_contexts':[{'label':'A','verified':True},{'label':'B'}]}
    assert client.post(base(client)+'/explain',json=body).status_code==422

def test_blank_fields_and_unknown_context_are_not_information(client):
    assert client.post(base(client)+'/explain',json={'topic':'lab_result','lab':{'unit':'   '}}).status_code==422
    result=explain(client,{'topic':'source_contexts','source_contexts':[{'label':'A'},{'label':'B'}]})
    assert len(result['missing_fields'])==6 and not result['result']

def test_unknown_or_other_owned_project_hidden(client):
    assert client.get('/api/projects/not-known/medical/capabilities').status_code==404
    pid=client.post('/api/projects',json={'name':'Other synthetic owner'}).json()['id']
    with db.get_db() as conn:conn.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?',('different-owner',pid))
    response=client.post(f'/api/projects/{pid}/medical/explain',json={'topic':'symptom_diagnosis'})
    assert response.status_code==404 and 'different-owner' not in response.text

def test_revoke_before_delivery_hides_result(client,monkeypatch):
    path=base(client);pid=path.split('/')[3];real=medical_education.explain
    def revoke(request):
        result=real(request)
        with db.get_db() as conn:conn.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?',('revoked-owner',pid))
        return result
    monkeypatch.setattr(medical_education,'explain',revoke)
    response=client.post(path+'/explain',json={'topic':'mass_conversion','quantity':{'value':'500','from_unit':'mcg','to_unit':'mg'}})
    assert response.status_code==404 and '0.5' not in response.text

def test_signed_out_cannot_access_medical_paths(client):
    path=base(client)
    assert client.post('/api/auth/logout').status_code==200
    assert client.get(path+'/capabilities').status_code==401
    assert client.post(path+'/explain',json={'topic':'symptom_diagnosis'}).status_code==401

def test_capabilities_are_honest_and_sources_not_live(client):
    response=client.get(base(client)+'/capabilities');assert response.status_code==200
    result=response.json()
    assert not result['clinical_use_approved'] and not result['qualified_medical_review'] and not result['live_guidance_verified']
    assert result['network_requests']==0 and result['storage']=='ephemeral response only'
    assert len(result['topics'])==10
    nice=next(row for row in result['sources'] if row['id']=='nice-ng197')
    assert nice['status']=='unavailable' and nice['supports']==[]
