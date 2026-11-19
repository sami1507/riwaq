"""Bounded repair orchestration, simulated generation and actual API/SQLite."""
import asyncio,json,time
import pytest
from backend import research_answer as answer
from backend.research_schema import ResearchOptions
from test_ai_core_grounded_chat import client,source_chat,response,payload

TEXT='The measured amount is 20 units. Another shelf contains 200 items.'
BAD=json.dumps({'claims':[{'text':'The measured amount is 200 units.','kind':'source_statement','references':[{'source':1,'quote':'The measured amount is 20 units.'}]}],'gaps':[]})
GOOD=json.dumps({'claims':[{'text':'The measured amount is 20 units.','kind':'source_statement','references':[{'source':1,'quote':'The measured amount is 20 units.'}]}],'gaps':[]})
CFG={'context_size':8192,'max_tokens':1024,'_evidence_repair_limit':1}


def run(monkeypatch,outputs,*,cfg=None,check=None,messages=None):
    calls=[];packets=[];receipt={}
    async def model(provider,model,context,config):
        calls.append((context,config));yield {'content':outputs[min(len(calls)-1,len(outputs)-1)]};yield {'finish_reason':'stop','metrics':{'eval_count':25}}
    monkeypatch.setattr(answer.providers,'stream',model)
    async def allowed():pass
    async def scenario():
        async for packet in answer.stream('synthetic','Correct the measured amount.',[{'text':TEXT}],ResearchOptions(scopes=['files'],response_language='en'),cfg or CFG,receipt,check or allowed,context_messages=messages):packets.append(packet)
    asyncio.run(scenario())
    return calls,packets,receipt


def test_invalid_numeric_claim_gets_one_validated_repair_before_publication(monkeypatch):
    calls,packets,receipt=run(monkeypatch,[BAD,GOOD])
    assert len(calls)==2 and receipt['model_calls']==2 and receipt['status']=='answered'
    assert receipt['validation_attempts'][0]['status']=='rejected' and receipt['validation_attempts'][1]['status']=='accepted'
    assert 'number_without_evidence_provenance' in receipt['validation_attempts'][0]['errors']
    assert all('The measured amount is 200 units.' not in p.get('content','') for p in packets)
    assert packets[-1]['content']=='The measured amount is 20 units. [S1]'
    assert BAD not in json.dumps(calls[1][0])
    assert sum(call[1]['max_tokens'] for call in calls)<=CFG['max_tokens']


def test_repair_stops_after_two_invalid_answers_and_preserves_failure_provenance(monkeypatch):
    calls,packets,receipt=run(monkeypatch,[BAD])
    assert len(calls)==2 and receipt['status']=='partial'
    assert receipt['evidence_recovery']['correction_completed'] is False
    assert len(receipt['validation_attempts'])==2 and all(a['status']=='rejected' for a in receipt['validation_attempts'])
    assert not receipt['claims'] and 'not an accepted answer' in packets[-1]['content']


def test_valid_first_answer_does_not_spend_repair_budget(monkeypatch):
    calls,packets,receipt=run(monkeypatch,[GOOD])
    assert len(calls)==1 and receipt['model_calls']==1


def test_explicit_research_default_remains_one_call(monkeypatch):
    calls,packets,receipt=run(monkeypatch,[BAD,GOOD],cfg={k:v for k,v in CFG.items() if k!='_evidence_repair_limit'})
    assert len(calls)==1 and receipt['status']=='insufficient_evidence'


@pytest.mark.parametrize('failure',[PermissionError,asyncio.CancelledError,TimeoutError])
def test_repair_rechecks_authorization_cancellation_and_deadline(monkeypatch,failure):
    calls=[];checks=[]
    async def model(*args):
        calls.append(1);yield {'content':BAD};yield {'finish_reason':'stop'}
    monkeypatch.setattr(answer.providers,'stream',model)
    async def allowed():
        checks.append(1)
        if len(checks)==3:raise failure()
    async def scenario():
        async for packet in answer.stream('synthetic','Correct amount',[{'text':TEXT}],ResearchOptions(scopes=['files']),CFG,{},allowed):
            assert not packet.get('content')
    with pytest.raises(failure):asyncio.run(scenario())
    assert len(calls)==1


def test_repair_refuses_to_drop_history_when_total_input_work_budget_is_exhausted(monkeypatch):
    history=[{'role':'system','content':'Keep exact identifiers.'},{'role':'user','content':'Earlier explicit constraint '+('a'*3500)},{'role':'user','content':'Correct the measured amount from '+TEXT}]
    calls,packets,receipt=run(monkeypatch,[BAD,GOOD],messages=history)
    assert len(calls)==1 and receipt['repair']['blocked_reason']=='input_work_budget_exhausted'
    assert calls[0][0][1:]==history[1:]


def test_repair_keeps_all_authorized_history_and_source_identifiers(monkeypatch):
    history=[{'role':'system','content':'Do not change SKU-Z9.'},{'role':'user','content':'Answer in English.'},{'role':'assistant','content':'Acknowledged.'},{'role':'user','content':'Correct the amount. '+TEXT}]
    calls,packets,receipt=run(monkeypatch,[BAD,GOOD],messages=history)
    assert len(calls)==2 and calls[1][0][1:]==history[1:]
    assert 'Do not change SKU-Z9.' in calls[1][0][0]['content']


def test_ordinary_api_repairs_before_storing_answer_and_records_both_attempts(client,monkeypatch):
    _,chat,file=source_chat(client);calls=[]
    async def model(*args):
        calls.append(1);yield {'content':BAD if len(calls)==1 else GOOD};yield {'finish_reason':'stop'}
    monkeypatch.setattr(answer.providers,'stream',model)
    packets,message=response(client,chat,payload(file,'A draft says the measured amount is 200 units. Correct it against the file.'))
    assert len(calls)==2 and message['research']['model_calls']==2
    assert message['content']=='The measured amount is 20 units. [S1]'
    saved=client.get('/api/conversations/'+chat).json()['messages'][-1]
    assert saved['research']['validation_attempts']==message['research']['validation_attempts']
    assert all('The measured amount is 200 units.' not in p.get('content','') for p in packets)


def test_repair_contract_does_not_accept_another_inference_or_unverified_gap(monkeypatch):
    inference=json.loads(GOOD);inference['claims'][0]['kind']='inference'
    calls,packets,receipt=run(monkeypatch,[BAD,json.dumps(inference)])
    assert len(calls)==2 and receipt['status']=='partial'
    assert receipt['evidence_recovery']['correction_completed'] is False
    assert receipt['validation_attempts'][-1]['errors']==['invalid_answer_schema']


def test_invalid_provider_json_can_be_repaired_without_echoing_raw_content(monkeypatch):
    from backend.output_contracts import OutputValidationError
    calls=[];receipt={}
    async def model(*args):
        calls.append(args)
        if len(calls)==1:raise OutputValidationError('structured_output_invalid_json')
        yield {'content':GOOD};yield {'finish_reason':'stop'}
    monkeypatch.setattr(answer.providers,'stream',model)
    async def allowed():pass
    async def scenario():return [p async for p in answer.stream('synthetic','Correct amount',[{'text':TEXT}],ResearchOptions(scopes=['files']),CFG,receipt,allowed)]
    packets=asyncio.run(scenario())
    assert len(calls)==2 and receipt['status']=='answered' and '20' in packets[-1]['content']
    assert receipt['validation_attempts'][0]['errors']==['invalid_answer_schema']


def test_generation_attempt_limit_blocks_hidden_transport_retry_and_fallback(monkeypatch):
    import httpx
    from test_skill_batch05_providers import mock_provider,default_reply,CONFIG
    from backend import providers
    calls=[]
    def respond(request):
        if request.url.path=='/api/chat':calls.append(json.loads(request.content)['model']);return httpx.Response(503)
        return default_reply(request)
    mock_provider(monkeypatch,respond)
    async def scenario():return [p async for p in providers.stream('ollama','broken',[{'role':'user','content':'Synthetic'}],CONFIG|{'_generation_attempt_limit':1,'local_fallback_models':['other']})]
    with pytest.raises(ValueError,match='provider_generation_unavailable'):asyncio.run(scenario())
    assert calls==['broken']


def test_deadline_expiring_after_first_generation_never_dispatches_repair(monkeypatch):
    from unittest.mock import Mock
    clock=[10.0];calls=[]
    monkeypatch.setattr(answer,'time',Mock(monotonic=lambda:clock[0]))
    async def model(*args):
        calls.append(1);yield {'content':BAD};clock[0]=21;yield {'finish_reason':'stop'}
    monkeypatch.setattr(answer.providers,'stream',model)
    async def allowed():pass
    async def scenario():return [p async for p in answer.stream('synthetic','Correct amount',[{'text':TEXT}],ResearchOptions(scopes=['files']),CFG|{'_deadline':20},{},allowed)]
    with pytest.raises(TimeoutError):asyncio.run(scenario())
    assert len(calls)==1


def test_small_output_budget_does_not_enable_an_extra_call(monkeypatch):
    calls,packets,receipt=run(monkeypatch,[BAD,GOOD],cfg=CFG|{'max_tokens':128})
    assert len(calls)==1 and receipt['repair']['blocked_reason']=='output_budget_too_small'


def test_reference_only_recovery_never_promotes_rejected_claim_to_approved_answer(monkeypatch):
    calls,packets,receipt=run(monkeypatch,[BAD])
    assert receipt['status']=='partial' and receipt['validation']['status']=='rejected'
    assert 'number_without_evidence_provenance' in receipt['validation']['errors']
    assert receipt['claims']==[] and receipt['evidence_recovery']['quotes']==[{'source':1,'quote':'The measured amount is 20 units.'}]
    assert 'reference-only, not an accepted answer' in packets[-1]['content']
    assert '200 units' not in packets[-1]['content']


@pytest.mark.parametrize('replacement',[{'source':2,'quote':'The measured amount is 20 units.'},{'source':1,'quote':'Invented source quote with 500 units.'}])
def test_unbound_references_cannot_enter_quote_only_recovery(monkeypatch,replacement):
    bad=json.loads(BAD);bad['claims'][0]['references']=[replacement]
    calls,packets,receipt=run(monkeypatch,[json.dumps(bad)])
    assert receipt['status']=='insufficient_evidence' and not receipt.get('evidence_recovery')


def test_reference_only_recovery_rechecks_acl_after_last_model_result(monkeypatch):
    checks=[];seen=[]
    async def model(*args):yield {'content':BAD};yield {'finish_reason':'stop'}
    monkeypatch.setattr(answer.providers,'stream',model)
    async def allowed():
        checks.append(1)
        if len(checks)==5:raise PermissionError('Synthetic source revoked before reference recovery')
    async def scenario():
        async for packet in answer.stream('synthetic','Correct amount',[{'text':TEXT}],ResearchOptions(scopes=['files']),CFG,{},allowed):seen.append(packet)
    with pytest.raises(PermissionError):asyncio.run(scenario())
    assert all('content' not in packet for packet in seen)
