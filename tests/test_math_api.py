"""Synthetic authenticated HTTP + real limited math worker; no model calls."""
import asyncio
import copy
import json
import pytest
import psutil
from fastapi.testclient import TestClient
from backend import app as service, reasoning
from backend.reasoning_math import parse_math


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    def forbidden(*args,**kwargs):pytest.fail('Math reached a model, secret or retrieval')
    for name in ('validate_provider','stream','secret'):monkeypatch.setattr(service.providers,name,forbidden)
    monkeypatch.setattr(service.knowledge,'retrieve',forbidden)
    monkeypatch.setattr(service.public_knowledge,'retrieve_public',forbidden)
    service.ACTIVE.clear()
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as c:
        from backend.local_auth import issue_pairing_code
        assert c.post('/api/auth/login',json={'pairing_code':issue_pairing_code()}).status_code==200
        yield c
    assert not service.ACTIVE


def chat(client):
    pid=client.get('/api/projects').json()[0]['id']
    return client.post('/api/conversations',json={'project_id':pid}).json()['id']


def calculate(client,cid,text,**options):
    return client.post(f'/api/conversations/{cid}/generate',json={'answer_mode':'problem','content':text,**options})


def saved(response):
    assert response.status_code==200,response.text
    packets=[json.loads(row) for row in response.text.splitlines() if row]
    assert packets[-1]['type']=='done',packets
    message=packets[-1]['message']
    assert message['status']=='complete' and message['model']=='riwaq-math-v1'
    receipt=message['run']
    assert receipt['model_calls']==0 and receipt['tool_calls']==1
    assert receipt['actual_input_tokens']==receipt['actual_output_tokens']==0
    check=next(c for c in receipt['checks'] if c['kind']=='solve_math')
    data=json.loads(check['details'])
    assert data['process_limited'] and data['memory_limit_mb']==512 and data['cpu_limit_seconds']==20
    assert data['engines']=={'sympy':'1.14.0','mpmath':'1.3.0'}
    return message,data


@pytest.mark.parametrize('text,status,solutions',[
    ('حل ٢س + ٣ = ١١','solved',['4']),
    ('حل (x^2-1)/(x-1)=2','no_solution',[]),
    ('حل sqrt(x+1)=x-1','solved',['3']),
    ('solve x^2=4; where: x<0','solved',['-2']),
    ('solve x^2+1=0; domain: real','no_solution',[]),
    ('solve x^2+1=0; domain: complex','solved',['-I','I']),
])
def test_actual_math_chat_saved_with_original_domain(client,text,status,solutions):
    cid=chat(client);message,data=saved(calculate(client,cid,text))
    assert data['problem_status']==status
    assert sorted(data['result']['solutions'])==sorted(solutions)
    assert data['original_text']==text and data['request']==parse_math(text)
    messages=client.get(f'/api/conversations/{cid}').json()['messages']
    assert messages[-1]==message and messages[0]['content']==text
    assert data['verification']


def test_big_integer_json_never_passes_binary_float(client):
    text=json.dumps({'operation':'exact','expression':'9007199254740993+1'})
    message,data=saved(calculate(client,chat(client),text))
    assert data['result']['value']=='9007199254740994'
    assert '9007199254740994' in message['content']


def test_ambiguous_grouping_is_reviewable_not_a_guessed_solution(client):
    message,data=saved(calculate(client,chat(client),'1/2x'))
    assert data['problem_status']=='ambiguous' and data['execution_status']=='partial'
    assert len(data['result']['readings'])==2 and message['run']['remaining']


@pytest.mark.parametrize('text',[
    '10 m + 3 s',
    'اجمع 10 أمتار و3 ثوانٍ ككمية واحدة.',
    '{"operation":"unit_sum","terms":[{"value":"10","unit":"kg"},{"value":"3","unit":"s"}],"output_unit":"kg"}',
])
def test_incompatible_dimensions_rejected_before_chat_save(client,text):
    cid=chat(client);response=calculate(client,cid,text)
    assert response.status_code==422,response.text
    assert 'incompatible unit dimensions' in response.json()['detail']
    assert client.get(f'/api/conversations/{cid}').json()['messages']==[]


def test_edit_domain_keeps_original_and_recomputes_new_branch(client):
    cid=chat(client);first,data=saved(calculate(client,cid,'solve x^2+1=0; domain: real'))
    original=client.get(f'/api/conversations/{cid}').json()['messages'][0]
    second,data=saved(calculate(client,cid,'solve x^2+1=0; domain: complex',edit_message_id=original['id']))
    assert second['id']!=first['id'] and data['problem_status']=='solved'
    assert first['content']!=second['content']


@pytest.mark.parametrize('text',[
    '{"operation":"exact","expression":"1+1","verified":true}',
    '{"operation":"exact","operation":"solve","expression":"1+1"}',
    '{"operation":"round","value":2.675,"places":2}',
    'evaluate __import__("os").system("whoami")',
    'evaluate open("outside-secret.txt").read()',
    'solve x^999999=1',
    'solve '+('('*35)+'x'+(')'*35)+'=1',
])
def test_untrusted_or_excessive_input_never_saves_success(client,text):
    cid=chat(client);response=calculate(client,cid,text)
    assert response.status_code==422,response.text
    assert client.get(f'/api/conversations/{cid}').json()['messages']==[]


@pytest.mark.parametrize('field,value',[
    ('input_sha256','0'*64),('process_limited',False),('memory_limit_mb',0),
    ('original_text','different'),('request',{'operation':'exact','expression':'99'}),
    ('engines',{'sympy':'0','mpmath':'1.3.0'}),('problem_status','verified'),
])
def test_corrupted_worker_evidence_is_rejected_before_save(client,monkeypatch,field,value):
    actual=reasoning.checker
    async def corrupted(*args,**kwargs):
        output=copy.deepcopy(await actual(*args,**kwargs));assert output['status']=='ok',output
        output['result'][field]=value;return output
    monkeypatch.setattr(reasoning,'checker',corrupted)
    cid=chat(client);response=calculate(client,cid,'solve x=7')
    assert response.status_code==503,response.text
    assert client.get(f'/api/conversations/{cid}').json()['messages']==[]


@pytest.mark.parametrize('kind,status',[('timeout',504),('unavailable',503)])
def test_simulated_engine_failure_is_not_no_solution(client,monkeypatch,kind,status):
    async def failed(*args,**kwargs):
        if kind=='timeout':raise TimeoutError('synthetic deadline')
        return {'status':'unavailable','error':'synthetic missing engine'}
    monkeypatch.setattr(reasoning,'checker',failed)
    cid=chat(client);response=calculate(client,cid,'solve x=7')
    assert response.status_code==status
    assert client.get(f'/api/conversations/{cid}').json()['messages']==[]


@pytest.mark.asyncio
async def test_actual_worker_cancel_and_timeout_leave_no_process(monkeypatch):
    actual=asyncio.create_subprocess_exec;started=asyncio.Event();children=[]
    async def capture(*args,**kwargs):
        child=await actual(*args,**kwargs);children.append(child);started.set();return child
    monkeypatch.setattr(asyncio,'create_subprocess_exec',capture)
    task=asyncio.create_task(reasoning.checker('solve_math','integrate sin(x)^20',timeout=30))
    await asyncio.wait_for(started.wait(),3);task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert all(p.returncode is not None and not psutil.pid_exists(p.pid) for p in children)
    with pytest.raises(TimeoutError):await reasoning.checker('solve_math','solve x=1',timeout=.00001)
    assert all(p.returncode is not None and not psutil.pid_exists(p.pid) for p in children)
