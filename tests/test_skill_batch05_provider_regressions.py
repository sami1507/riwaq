"""Same acceptance assertions against the saved baseline or current source."""
import asyncio,importlib.util,json,os
from pathlib import Path
import httpx,pytest


def selected(name):
    if os.environ.get('RIWAQ_BATCH05_PROVIDER_BEFORE')=='1':
        path=Path('reports/skills-batch-05-20261116/baseline/backend')/(name+'.py')
        spec=importlib.util.spec_from_file_location('before_'+name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    return importlib.import_module('backend.'+name)


def test_false_string_cannot_mean_completed():
    with pytest.raises(ValueError):selected('providers').decode_frame('ollama',{'done':'false'})


def test_early_constraint_is_preserved_or_request_explicitly_rejected():
    grounding=selected('grounding')
    history=[{'role':'user','status':'complete','content':'EARLY_REQUIRED_CONSTRAINT '+('x'*800)},
             {'role':'assistant','status':'complete','content':'y'*800},
             {'role':'user','status':'complete','content':'latest correction'}]
    try:messages,_=grounding.compose({'instructions':''},[],history,'continue',[],{'context_size':2048,'max_tokens':512},False)
    except ValueError as exc:
        assert 'history exceeds' in str(exc)
        return
    assert 'EARLY_REQUIRED_CONSTRAINT' in json.dumps(messages)


def test_invalid_structured_value_is_not_delivered_to_consumer(monkeypatch):
    provider=selected('providers');real=httpx.AsyncClient;seen=[]
    def respond(request):
        if request.url.path=='/api/show':return httpx.Response(200,json={'capabilities':['completion']})
        return httpx.Response(200,text=json.dumps({'message':{'content':'{"value":500}'},'done':True})+'\n')
    monkeypatch.setattr(provider.httpx,'AsyncClient',lambda **kwargs:real(transport=httpx.MockTransport(respond),**kwargs))
    cfg={'context_size':4096,'max_tokens':64,'output_schema':{'type':'object','properties':{'value':{'type':'integer','maximum':10}},'required':['value'],'additionalProperties':False}}
    async def scenario():
        try:
            async for packet in provider.stream('ollama','chat',[{'role':'user','content':'synthetic'}],cfg):seen.append(packet)
        except ValueError:pass
    asyncio.run(scenario())
    assert not seen
