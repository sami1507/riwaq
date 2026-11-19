"""Provider contracts and controlled clocks; no paid providers or real accounts."""
import asyncio,json,time
from decimal import Decimal
import httpx
import pytest
from backend import providers,provider_policy as policy,provider_runtime as runtime
from backend.grounding import compose
from backend.output_contracts import validated_output,OutputValidationError

CONFIG={'context_size':4096,'max_tokens':64,'mode':'local'}
SCHEMA={'type':'object','properties':{'value':{'type':'integer','minimum':0,'maximum':10}},'required':['value'],'additionalProperties':False}


def http_error(status,headers=None):
    r=httpx.Response(status,headers=headers,request=httpx.Request('GET','http://127.0.0.1:11434/test'))
    try:r.raise_for_status()
    except httpx.HTTPStatusError as e:return e


class Clock:
    def __init__(self):self.now=100.;self.waits=[];self.cancelled=False
    def __call__(self):return self.now
    async def sleep(self,seconds):self.now+=seconds;self.waits.append(seconds)


def mock_provider(monkeypatch,respond):
    real=httpx.AsyncClient
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kwargs:real(transport=httpx.MockTransport(respond),**kwargs))
    monkeypatch.setattr(providers,'LOCAL_ADMISSION',runtime.Admission(spacing=0))


def default_reply(request,*,capabilities=None):
    if request.url.path=='/api/show':return httpx.Response(200,json={'capabilities':capabilities or ['completion'],'model_info':{'llama.context_length':4096}})
    if request.url.path=='/api/tags':return httpx.Response(200,json={'models':[{'name':'chat','digest':'a'*64}]})
    if request.url.path=='/api/version':return httpx.Response(200,json={'version':'synthetic-1'})
    return httpx.Response(200,text=json.dumps({'message':{'content':'{"value":7}'},'done':True,'done_reason':'stop','eval_count':4})+'\n')


@pytest.mark.parametrize('changes,expected',[
    ({'capabilities':['completion']},False),({'capabilities':['completion','vision']},True),
    ({'capabilities':['completion','vision'],'remote_host':'https://invalid.test'},False)])
def test_capabilities_do_not_infer_vision_from_model_name(changes,expected):
    report=policy.capabilities('vision-sounding-name',changes)
    assert report['declared']['images'] is expected
    assert report['measured']['images']=='unverified'


@pytest.mark.parametrize('name',['../secret','https://model','model:cloud','user/model'])
def test_remote_or_unscoped_model_names_rejected(name):
    with pytest.raises(ValueError):policy.local_model_name(name)


def test_structured_boundaries_reject_without_consuming_or_leaking_values():
    executed=[]
    for text in ['{','{"value":3,"secret":"SYNTHETIC_SECRET_77"}','{"value":-1}','{"value":"0"}','{"value":NaN}','{"value":0,"value":1}']:
        with pytest.raises(OutputValidationError) as e:
            result=validated_output(text,SCHEMA)
            executed.append(result)
        assert 'SYNTHETIC_SECRET_77' not in str(e.value)
    assert not executed
    assert validated_output('{"value":0}',SCHEMA)=={'value':Decimal(0)}


def test_structured_relationship_and_authority_are_separate():
    schema={'type':'object','properties':{'start':{'type':'integer'},'end':{'type':'integer'},'resource':{'type':'string'}},'required':['start','end','resource'],'additionalProperties':False}
    with pytest.raises(OutputValidationError,match='relationship_failed'):validated_output('{"start":9,"end":2,"resource":"r"}',schema,ordered_fields=[('start','end')])
    with pytest.raises(OutputValidationError,match='resource_unavailable'):validated_output('{"start":0,"end":2,"resource":"other-project"}',schema,resource_fields=['resource'],authorize_resource=lambda key,val:val=='own')


def test_history_constraints_and_correction_are_not_silently_dropped():
    history=[{'role':'user','status':'complete','content':'Use English and produce Word AND CSV. Reference R1 version2.'},
             {'role':'assistant','status':'complete','content':'x'*1000},
             {'role':'user','status':'complete','content':'Correction: use Hebrew and preserve 001.'}]
    cfg={'context_size':4096,'max_tokens':64}
    messages,_=compose({'instructions':''},[],history,'Continue',[],cfg,False)
    text=json.dumps(messages)
    assert 'Word AND CSV' in text and 'Correction' in text and 'R1 version2' in text and '001' in text
    assert cfg['_context_budget']['exact_token_count'] is False
    # CORE stabilization (2026-11-17): earlier assistant replies may now be
    # abbreviated, declared in the receipt and to the model; user turns never.
    long_cfg={'context_size':4096,'max_tokens':64}
    messages,_=compose({'instructions':''},[],history*8,'Continue',[],long_cfg,False)
    assert [m['content'] for m in messages if m['role']=='user'][:-1]==[m['content'] for m in history*8 if m['role']=='user']
    compaction=long_cfg['_context_budget']['history_compaction']
    assert compaction['user_turns_preserved']==16 and compaction['affected_turns']
    with pytest.raises(ValueError,match='history exceeds'):
        compose({'instructions':''},[],history*40,'Continue',[],cfg,False)


def test_history_oversize_was_previous_silent_truncation_bug():
    history=[{'role':'user','status':'complete','content':'CRITICAL_EARLY_CONSTRAINT '+('x'*800)},
             {'role':'assistant','status':'complete','content':'y'*800},
             {'role':'user','status':'complete','content':'latest correction'}]
    with pytest.raises(ValueError,match='No earlier constraint'):
        compose({'instructions':''},[],history,'continue',[],{'context_size':2048,'max_tokens':512},False)


def test_retry_transient_then_success_with_controlled_clock():
    clock=Clock();calls=[];events=[]
    async def call():
        calls.append(clock())
        if len(calls)<3:raise http_error(503)
        return 'ok'
    assert asyncio.run(runtime.retry_call(call,deadline=105,clock=clock,sleep=clock.sleep,random_value=lambda:0,on_retry=events.append))=='ok'
    assert len(calls)==3 and len(events)==2 and sum(clock.waits)==pytest.approx(.3)
    assert all(set(e)=={'attempt','reason','wait_seconds'} for e in events)


@pytest.mark.parametrize('status',[400,401,403,404])
def test_fixed_or_authorization_error_is_never_retried(status):
    clock=Clock();calls=[]
    async def call():calls.append(1);raise http_error(status)
    with pytest.raises(httpx.HTTPError):asyncio.run(runtime.retry_call(call,deadline=110,clock=clock,sleep=clock.sleep))
    assert len(calls)==1 and not clock.waits


def test_retry_after_honored_and_no_call_after_cancellation():
    clock=Clock();calls=[]
    async def call():calls.append(1);raise http_error(429,{'retry-after':'3'})
    async def sleep(seconds):await clock.sleep(seconds);clock.cancelled=True
    with pytest.raises(asyncio.CancelledError):asyncio.run(runtime.retry_call(call,deadline=110,clock=clock,sleep=sleep,cancelled=lambda:clock.cancelled))
    assert len(calls)==1
    clock=Clock();calls=[]
    with pytest.raises(runtime.RuntimeLimit,match='retry_after_exceeds'):asyncio.run(runtime.retry_call(call,deadline=102,clock=clock,sleep=clock.sleep))
    assert len(calls)==1 and not clock.waits


def test_retry_after_http_date_and_unknown_effect():
    assert runtime.retry_after('Thu, 01 Jan 1970 00:01:00 GMT',wall=lambda:50)==10
    assert runtime.retry_after('garbage') is None
    assert not runtime.replay_allowed(httpx.ReadTimeout('synthetic'),replay='idempotent')
    assert not runtime.replay_allowed(http_error(503),replay='never')
    assert not runtime.replay_allowed(http_error(503),output_started=True)


def test_admission_independent_owner_and_cancelled_waiter():
    async def scenario():
        gate=runtime.Admission(capacity=2,per_owner=1,spacing=0);first=asyncio.Event();release=asyncio.Event();seen=[]
        async def occupy():
            async with gate.acquire('a',time.monotonic()+2):first.set();await release.wait()
        async def wait_a():
            async with gate.acquire('a',time.monotonic()+2):seen.append('a')
        busy=asyncio.create_task(occupy());await first.wait()
        waiting=asyncio.create_task(wait_a());await asyncio.sleep(0)
        async with gate.acquire('b',time.monotonic()+2):seen.append('b');assert gate.snapshot()['active']==2
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):await waiting
        release.set();await busy
        assert seen==['b'] and gate.snapshot()['active']==0 and gate.snapshot()['waiting']==0
    asyncio.run(scenario())


def test_cooldown_and_queue_deadline_without_burst():
    async def scenario():
        clock=Clock();gate=runtime.Admission(spacing=.2);gate.defer(2,clock)
        with pytest.raises(runtime.RuntimeLimit):
            async with gate.acquire('a',101,clock=clock,sleep=clock.sleep):raise AssertionError('expired request dispatched')
        assert gate.snapshot(clock)['waiting']==0
        async with gate.acquire('b',105,clock=clock,sleep=clock.sleep):assert clock()>=102
    asyncio.run(scenario())


@pytest.mark.parametrize('override,reason',[
    ({'output_started':True},'effect_or_output_may_exist'),({'outcome_unknown':True},'effect_or_output_may_exist'),
    ({'operation':'send'},'effect_or_output_may_exist'),({'remaining_attempts':0},'fallback_budget_exhausted'),
    ({'error_kind':'authorization_required'},'error_not_eligible')])
def test_fallback_constraints(override,reason):
    candidate=policy.capabilities('local2',{'capabilities':['completion']})
    args={'required':{'text'},'mode':'local','configured_models':['local2'],'used_models':['local1'],'error_kind':'temporarily_unavailable','remaining_seconds':1,'remaining_attempts':1,**override}
    assert policy.fallback_decision({'model':'local1'},candidate,**args)['reason']==reason


def test_fallback_rejects_cloud_or_lost_images():
    candidate=policy.capabilities('local2',{'capabilities':['completion']})
    args={'required':{'images'},'mode':'local','configured_models':['local2'],'used_models':[],'error_kind':'temporarily_unavailable','remaining_seconds':1,'remaining_attempts':1}
    assert not policy.fallback_decision({},candidate,**args)['allowed']
    assert policy.fallback_decision({},candidate|{'provider':'openai','processing':'remote'},**args)['reason']=='external_fallback_not_authorized'


def test_invalid_structured_stream_never_reaches_consumer(monkeypatch):
    def respond(request):
        if request.url.path=='/api/chat':return httpx.Response(200,text=json.dumps({'message':{'content':'{"value":500}'},'done':True})+'\n')
        return default_reply(request)
    mock_provider(monkeypatch,respond);packets=[]
    async def scenario():
        async for packet in providers.stream('ollama','chat',[{'role':'user','content':'synthetic'}],CONFIG|{'output_schema':SCHEMA}):packets.append(packet)
    with pytest.raises(OutputValidationError):asyncio.run(scenario())
    assert not packets


def test_valid_structured_stream_zero_and_final_receipt(monkeypatch):
    mock_provider(monkeypatch,default_reply)
    async def scenario():return [p async for p in providers.stream('ollama','chat',[{'role':'user','content':'synthetic'}],CONFIG|{'output_schema':SCHEMA})]
    packets=asyncio.run(scenario())
    assert packets[0]['content']=='{"value":7}' and packets[-1]['metrics']['provider_runtime']['selected_model']=='chat'


def test_capability_preflight_blocks_text_model_images_before_chat(monkeypatch):
    calls=[]
    def respond(request):calls.append(request.url.path);return default_reply(request)
    mock_provider(monkeypatch,respond)
    async def scenario():return [p async for p in providers.stream('ollama','chat',[{'role':'user','content':'synthetic','images':['AA==']}],CONFIG)]
    with pytest.raises(ValueError,match='capability_unavailable:images'):asyncio.run(scenario())
    assert '/api/chat' not in calls


def test_real_adapter_explicit_local_fallback_and_no_content_duplication(monkeypatch):
    calls=[]
    def respond(request):
        body=json.loads(request.content) if request.content else {}
        if request.url.path=='/api/chat':
            calls.append(body['model'])
            if body['model']=='broken':return httpx.Response(503)
        return default_reply(request)
    mock_provider(monkeypatch,respond)
    async def scenario():return [p async for p in providers.stream('ollama','broken',[{'role':'user','content':'synthetic'}],CONFIG|{'local_fallback_models':['chat']})]
    packets=asyncio.run(scenario())
    assert calls==['broken','broken','chat']
    assert ''.join(p.get('content','') for p in packets)=='{"value":7}'
    assert packets[-1]['metrics']['provider_runtime']['fallback_used']


def test_live_metadata_is_not_measured_success_and_probe_is_bound_to_version(monkeypatch):
    revision=['synthetic-1'];chat_calls=[]
    def respond(request):
        if request.url.path=='/api/version':return httpx.Response(200,json={'version':revision[0]})
        if request.url.path=='/api/chat':chat_calls.append(1)
        return default_reply(request)
    mock_provider(monkeypatch,respond);providers.MEASURED.clear()
    first=asyncio.run(providers.runtime_status('chat',CONFIG))
    assert first['status']=='limited' and first['measured']['text']=='unverified' and not chat_calls
    second=asyncio.run(providers.runtime_status('chat',CONFIG,probe=True))
    assert second['measured']['structured_output']=='passed_fixed_probe' and len(chat_calls)==1
    revision[0]='synthetic-2'
    third=asyncio.run(providers.runtime_status('chat',CONFIG))
    assert third['measured']['text']=='unverified' and len(chat_calls)==1


def test_generation_preflight_can_reach_explicit_local_fallback(monkeypatch):
    seen=[]
    def respond(request):
        body=json.loads(request.content) if request.content else {}
        if request.url.path=='/api/show':
            seen.append(body['model'])
            if body['model']=='broken':return httpx.Response(503)
        return default_reply(request)
    mock_provider(monkeypatch,respond)
    asyncio.run(providers.preflight('ollama','broken',CONFIG|{'local_fallback_models':['chat']}))
    assert seen.count('broken')==3 and seen[-1]=='chat'


def test_actual_generate_api_does_not_store_invalid_structured_text(tmp_path,monkeypatch):
    from backend import app as service
    from backend.local_auth import issue_pairing_code
    from fastapi.testclient import TestClient
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));service.ACTIVE.clear()
    calls=[]
    def respond(request):
        calls.append(request.url.path)
        if request.url.path=='/api/show':return httpx.Response(200,json={'capabilities':['completion'],'model_info':{'llama.context_length':16384}})
        if request.url.path=='/api/chat':return httpx.Response(200,text=json.dumps({'message':{'content':'{"value":500}'},'done':True,'done_reason':'stop'})+'\n')
        return default_reply(request)
    mock_provider(monkeypatch,respond)
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as client:
        assert client.post('/api/auth/login',json={'pairing_code':issue_pairing_code()}).status_code==200
        project=client.get('/api/projects').json()[0]['id']
        cid=client.post('/api/conversations',json={'project_id':project}).json()['id']
        response=client.post(f'/api/conversations/{cid}/generate',json={'content':'Synthetic JSON contract','output_contract':{'schema':SCHEMA}})
        assert response.status_code==200,response.text
        events=[json.loads(x) for x in response.text.splitlines()]
        saved=client.get('/api/conversations/'+cid).json()['messages'][-1]
        assert saved['content']=='' and saved['status']=='error'
        assert not any(event.get('type')=='delta' for event in events)
        assert '/api/chat' in calls


def test_partial_stream_failure_never_switches_model_or_repeats_output(monkeypatch):
    calls=[];packets=[]
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"message":{"content":"one partial"},"done":false}\n'
            raise httpx.ReadTimeout('synthetic failure after response')
    def respond(request):
        if request.url.path=='/api/chat':
            calls.append(json.loads(request.content)['model'])
            return httpx.Response(200,stream=BrokenStream())
        return default_reply(request)
    mock_provider(monkeypatch,respond)
    async def scenario():
        async for packet in providers.stream('ollama','chat',[{'role':'user','content':'synthetic'}],CONFIG|{'local_fallback_models':['other']}):packets.append(packet)
    with pytest.raises(httpx.ReadTimeout):asyncio.run(scenario())
    assert calls==['chat'] and packets==[{'content':'one partial'}]
    assert providers.LOCAL_ADMISSION.snapshot()['active']==0


def test_provider_diagnostic_values_do_not_enter_error_or_metric_fields():
    with pytest.raises(ValueError) as e:providers.decode_frame('ollama',{'error':'SYNTHETIC_SECRET_77'})
    assert 'SYNTHETIC_SECRET_77' not in str(e.value)
    packets,_=providers.decode_frame('ollama',{'done':True,'done_reason':'SYNTHETIC_SECRET_77'})
    assert packets[0]['finish_reason']=='unknown'


@pytest.mark.parametrize('frame',[{'done':'false'},{'done':True,'eval_count':'7'},{'message':{'content':{'secret':'not text'}}},[]])
def test_malformed_provider_frames_cannot_claim_completion(frame):
    with pytest.raises(ValueError):providers.decode_frame('ollama',frame)
