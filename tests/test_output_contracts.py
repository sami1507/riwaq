import json
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from backend import app as service,reasoning
from backend.output_contracts import OutputContract

def spec():return {'schema':{'type':'object','properties':{'value':{'type':['number','null']},'sources':{'type':'array','items':{'type':'string'},'maxItems':6}},'required':['value','sources'],'additionalProperties':False},'citation_field':'sources'}
def test_strict_json_types_and_canonical_citations():
 c=OutputContract.model_validate(spec())
 assert c.check('{"value":3.5,"sources":["[S1]"]}',1)['schema_valid']
 assert c.check('{"value":3.5,"sources":["[S1]"]}',1)['citation_format_valid']
 assert not c.check('{"value":"3.5","sources":["[S1]"]}',1)['schema_valid']
 assert c.check('{"value":"3.5","sources":["[S1]"]}',1)['citation_format_valid']
 assert not c.check('{"value":true,"sources":["[S1]"]}',1)['schema_valid']
 assert not c.check('{"value":3.5,"sources":["S1"]}',1)['citation_format_valid']
 assert c.check('{"value":null,"sources":[]}',0)['citation_format_valid']
 for text in ['{"value":1,"value":2,"sources":[]}','{"value":NaN,"sources":[]}','```json\n{}\n```','{"value":1,"sources":[],"extra":2}']:
  assert not c.check(text,1)['schema_valid']
 assert c.bind(2)['properties']['sources']['items']['enum']==['[S1]','[S2]']
 assert 'enum' not in c.schema_definition['properties']['sources']['items']

def test_rejects_unsupported_refs_depth_and_contradictory_bounds():
 for key,value in [('$ref','https://evil.invalid/schema'),('oneOf',[]),('pattern','.*')]:
  s=spec();s['schema']['properties']['value'][key]=value
  with pytest.raises(ValidationError):OutputContract.model_validate(s)
 s=spec();s['schema']['properties']['value']={'type':'number','minimum':10,'maximum':1}
 with pytest.raises(ValidationError):OutputContract.model_validate(s)
 s=spec();s['schema']['properties']['sources']['minItems']=1
 with pytest.raises(ValueError):OutputContract.model_validate(s).bind(0)
 s=spec();s['schema']['properties']['sources']['items']['enum']=['[S9]']
 assert OutputContract.model_validate(s).bind(1)['properties']['sources']['maxItems']==0
 s=spec();s['schema']['properties']['value']={'type':'array','items':{}}
 for i in range(20):s['schema']['properties']['value']={'type':'array','items':s['schema']['properties']['value']}
 with pytest.raises(ValidationError):OutputContract.model_validate(s)

def test_tool_observation_omits_process_internals():
 from backend.check_worker import Arithmetic
 x=Arithmetic('0.1+0.2').result()|{'memory_limit_mb':256,'process_limited':True}
 projected=reasoning.checker_context('calculate',x)
 assert projected['value_as_fraction']=='3/10' and projected['value_as_decimal']=='0.3'
 assert 'memory_limit_mb' not in projected and 'rounded_2' not in projected

@pytest.fixture
def client(tmp_path,monkeypatch):
 monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));service.ACTIVE.clear()
 with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as client:
     from backend.local_auth import issue_pairing_code
     assert client.post('/api/auth/login', json={'pairing_code': issue_pairing_code()}).status_code == 200
     yield client
 assert not service.ACTIVE

def test_contract_runs_without_depth_preserves_retry_and_does_not_rewrite(client,monkeypatch):
 async def validate(*a):pass
 seen=[]
 async def stream(provider,model,messages,config):
  seen.append((messages,config));yield {'content':'{"value":"wrong","sources":[]}'};yield {'finish_reason':'stop'}
 monkeypatch.setattr(service.providers,'validate_provider',validate);monkeypatch.setattr(service.providers,'stream',stream)
 pid=client.get('/api/projects').json()[0]['id'];cid=client.post('/api/conversations',json={'project_id':pid}).json()['id']
 response=client.post(f'/api/conversations/{cid}/generate',json={'content':'Answer in JSON','output_contract':spec()})
 assert response.status_code==200,response.text
 final=[json.loads(x) for x in response.text.splitlines()][-1]['message']
 assert final['content']=='{"value":"wrong","sources":[]}'
 assert next(x for x in final['run']['checks'] if x['kind']=='output_schema')['status']=='fail'
 assert final['run']['request_options']['output_contract']['schema']==OutputContract.model_validate(spec()).schema_definition
 assert seen[0][1]['output_schema']['properties']['sources']['maxItems']==0
 assert 'explicitly requests one JSON' in seen[0][0][0]['content']
 assert client.get(f'/api/conversations/{cid}').json()['messages'][-1]==final
 cid2=client.post('/api/conversations',json={'project_id':pid}).json()['id']
 client.post(f'/api/conversations/{cid2}/generate',json={'content':'hi'})
 assert 'output_schema' not in seen[-1][1]

def test_contract_cloud_rejected_without_dispatch(client,monkeypatch):
 called=[]
 async def validate(*a):called.append(a)
 monkeypatch.setattr(service.providers,'validate_provider',validate)
 pid=client.get('/api/projects').json()[0]['id'];cid=client.post('/api/conversations',json={'project_id':pid}).json()['id']
 response=client.post(f'/api/conversations/{cid}/generate',json={'content':'hi','provider':'openai','output_contract':spec()})
 assert response.status_code==400 and not called
