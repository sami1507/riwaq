"""Authenticated real conversation API/SQLite with labelled simulated model output."""
import json
import pytest
from backend import app as service
from test_research_chat import client


def source_chat(client):
    project=client.get('/api/projects').json()[0]['id']
    chat=client.post('/api/conversations',json={'project_id':project}).json()['id']
    file=client.post(f'/api/projects/{project}/files',files={'file':('measured.txt',b'The measured amount is 20 units. Another shelf contains 200 items.','text/plain')})
    assert file.status_code==201
    return project,chat,file.json()['id']

def response(client,chat,body):
    result=client.post(f'/api/conversations/{chat}/generate',json=body)
    assert result.status_code==200,result.text
    packets=[json.loads(line) for line in result.text.splitlines()]
    return packets,packets[-1]['message']

def payload(file,content='What is the measured amount? Answer in English.'):
    # The existing text upload endpoint creates a legacy text index, not a
    # document renderer identity. This fixture project contains one file.
    return {'content':content,'use_files':True}

@pytest.mark.parametrize('amount,expected',[(20,'answered'),(200,'partial')])
def test_ordinary_grounded_chat_validates_claim_before_delta_and_storage(client,monkeypatch,amount,expected):
    _,chat,file=source_chat(client);calls=[]
    async def model(provider,model,messages,cfg):
        calls.append(messages)
        yield {'content':json.dumps({'claims':[{'text':f'The measured amount is {amount} units.','kind':'source_statement','references':[{'source':1,'quote':'The measured amount is 20 units.'}]}],'gaps':[]})}
        yield {'finish_reason':'stop'}
    monkeypatch.setattr(service.providers,'stream',model)
    packets,message=response(client,chat,payload(file))
    assert message['research']['mode']=='ordinary_grounded_conversation'
    assert message['research']['status']==expected and len(calls)==(1 if amount==20 else 2)
    assert len(message['research']['validation_attempts'])==len(calls)
    assert message['research']['validation']['semantic_support']=='not_certified'
    if amount==200:
        assert 'The measured amount is 200 units.' not in message['content']
        assert all('The measured amount is 200 units.' not in packet.get('content','') for packet in packets)
        assert message['research']['claims']==[]
        assert 'not an accepted answer' in message['content']
    else:assert message['content']=='The measured amount is 20 units. [S1]'
    saved=client.get('/api/conversations/'+chat).json()['messages'][-1]
    assert saved['content']==message['content'] and saved['research']==message['research']


def test_ordinary_grounded_aggregate_is_rejected_before_model_or_saved_answer(client,monkeypatch):
    _,chat,file=source_chat(client)
    async def forbidden(*args,**kwargs):
        raise AssertionError('Partial excerpts must not be aggregated')
        yield {}
    monkeypatch.setattr(service.providers,'stream',forbidden)
    result=client.post(f'/api/conversations/{chat}/generate',json=payload(file,'Calculate the total of all measured amounts in the entire table.'))
    assert result.status_code==413 and 'complete table' in result.text
    assert client.get('/api/conversations/'+chat).json()['messages']==[]


def test_grounded_explicit_output_contract_keeps_existing_typed_path(client,monkeypatch):
    _,chat,file=source_chat(client)
    async def model(provider,model,messages,cfg):
        yield {'content':'{"count":20}'}
        yield {'finish_reason':'stop'}
    monkeypatch.setattr(service.providers,'stream',model)
    body=payload(file,'Return JSON only with the measured amount as count.');body['output_contract']={'schema':{'type':'object','properties':{'count':{'type':'integer'}},'required':['count'],'additionalProperties':False}}
    _,message=response(client,chat,body)
    assert message['content']=='{"count":20}'
    assert not message.get('research')


def test_ordinary_no_source_keeps_existing_no_model_answer(client,monkeypatch):
    _,chat,file=source_chat(client)
    async def forbidden(*args,**kwargs):
        raise AssertionError('No evidence should not call generation')
        yield {}
    monkeypatch.setattr(service.providers,'stream',forbidden)
    _,message=response(client,chat,payload(file,'Where are quasars documented?'))
    assert not message['citations'] and message['research']['status']=='insufficient_evidence'


def test_ordinary_grounded_mixed_public_private_retains_scope_and_exact_source_ids(client,monkeypatch):
    from test_research_chat import setup,TEXT
    project,chat=setup(client)
    private='A range exercise uses 5 examples.'
    assert client.post(f'/api/projects/{project}/files',files={'file':('range-private.txt',private.encode(),'text/plain')}).status_code==201
    seen=[]
    async def model(provider,model,messages,cfg):
        raw=messages[-1]['content'].split('<untrusted_source_excerpts>\n',1)[1].split('\n</untrusted_source_excerpts>',1)[0]
        sources=json.loads(raw);seen.extend(sources)
        source_ids={s['text']:s['source'] for s in sources}
        assert private in source_ids and TEXT in source_ids
        claims=[{'text':'The stop value is excluded.','kind':'source_statement','references':[{'source':source_ids[TEXT],'quote':'The stop value is excluded.'}]},
                {'text':private,'kind':'source_statement','references':[{'source':source_ids[private],'quote':private}]}]
        yield {'content':json.dumps({'claims':claims,'gaps':[]})}
        yield {'finish_reason':'stop'}
    monkeypatch.setattr(service.providers,'stream',model)
    _,message=response(client,chat,{'content':'Explain range stop value and range exercise examples.','use_files':True,'use_knowledge':True})
    assert message['research']['status']=='answered' and len(seen)==2
    assert {c.get('scope','private') for c in message['citations']}=={'private','public'}
    assert message['research']['coverage']['candidate_resource_count']==2
    assert message['research']['coverage']['complete_source_read'] is False
    for index in range(len(message['citations'])):
        evidence=client.get(f"/api/projects/{project}/research/evidence/{message['id']}/{index}")
        assert evidence.status_code==200 and evidence.json()['excerpt'] in (private,TEXT)


def test_ordinary_grounded_rejects_known_unit_swap_before_stream_and_storage(client,monkeypatch):
    _,chat,file=source_chat(client)
    bad='The statement about 200 units refers to another shelf, not the measured amount directly.'
    async def model(*args,**kwargs):
        yield {'content':json.dumps({'claims':[
            {'text':'The measured amount is 20 units.','kind':'source_statement','references':[{'source':1,'quote':'The measured amount is 20 units.'}]},
            {'text':bad,'kind':'inference','references':[{'source':1,'quote':'Another shelf contains 200 items.'}]}],'gaps':[]})}
        yield {'finish_reason':'stop'}
    monkeypatch.setattr(service.providers,'stream',model)
    packets,message=response(client,chat,payload(file))
    assert message['research']['status']=='partial' and message['research']['claims']==[]
    assert message['research']['evidence_recovery']['correction_completed'] is False
    assert 'number_unit_without_evidence_provenance' in message['research']['validation_attempts'][0]['errors']
    assert message['research']['validation']['status']=='rejected'
    assert bad not in message['content'] and all(bad not in p.get('content','') for p in packets)
    saved=client.get('/api/conversations/'+chat).json()['messages'][-1]
    assert saved['content']==message['content'] and bad not in saved['content']
