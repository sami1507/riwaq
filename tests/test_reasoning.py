import asyncio
from contextlib import aclosing
from fractions import Fraction
import json
from pathlib import Path
import pytest
import httpx
from fastapi.testclient import TestClient
from backend import app as service, reasoning
from backend.check_worker import Arithmetic, analyze_python
from backend.db import get_db

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));service.ACTIVE.clear()
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as client:
        from backend.local_auth import issue_pairing_code
        assert client.post('/api/auth/login', json={'pairing_code': issue_pairing_code()}).status_code == 200
        yield client
    assert not service.ACTIVE

def chat(client):
    pid=client.get('/api/projects').json()[0]['id']
    return pid,client.post('/api/conversations',json={'project_id':pid}).json()['id']

def packets(response):
    assert response.status_code==200,response.text
    return [json.loads(line) for line in response.text.splitlines()]

def fake(monkeypatch,text='Answer'):
    async def validate(*args):pass
    async def stream(*args):
        yield {'content':text};yield {'finish_reason':'stop','metrics':{'eval_count':4,'prompt_eval_count':8}}
    monkeypatch.setattr(service.providers,'validate_provider',validate);monkeypatch.setattr(service.providers,'stream',stream)

@pytest.mark.parametrize('expression,value',[('0.1+0.2','3/10'),('(19.95*3)*1.17-10','120049/2000'),('1/3','1/3'),('-2*(-3+4)','-2'),('١٢٫٥ × ٢','25'),('1--2','3')])
def test_arithmetic_exact(expression,value):
    assert Arithmetic(expression).result()['exact']==value

@pytest.mark.parametrize('value',['__import__("os")','a+1','2**10','[1][0]','1/0','1 2','('+('1+'*140)+'1)', '(' * 17+'1'+')'*17])
def test_arithmetic_rejects_unsafe_or_excessive(value):
    with pytest.raises(ValueError):Arithmetic(value).result()

def test_rounding_is_from_fraction_not_rounded_decimal():
    expression='1/200-1/('+'1'+'0'*60+'*'+'1'+'0'*40+')'
    assert Arithmetic(expression).result()['rounded_2']=='0.00'
    assert Arithmetic('-0.005').result()['rounded_2']=='-0.01'
    assert Arithmetic('0.005').result()['rounded_2']=='0.01'

def test_display_precision_is_honest_and_large_exact_value_survives():
    value=Arithmetic('1+1/1267650600228229401496703205376').result()
    assert not value['decimal_exact'];assert Fraction(value['decimal'])!=Fraction(value['exact'])
    result=Arithmetic('9'*60+'*'+'9'*60).result()
    assert Fraction(result['exact'])==int('9'*60)**2
    assert result['rounded_2'].endswith('.00')

def test_static_analysis_never_runs_input(tmp_path):
    target=tmp_path/'must-not-exist'
    source=f'open({str(target)!r},"w").write("bad")\ndef f(xs=[]):\n    return xs.sort()'
    result=analyze_python(source)
    assert result['executed'] is False and not target.exists()
    assert {r['code'] for r in result['diagnostics']}=={'mutable_default','returned_mutator'}
    with pytest.raises(ValueError):analyze_python('\n'.join(['x=1']*257))

@pytest.mark.asyncio
async def test_real_fixed_worker_utf8_and_limits():
    result=await reasoning.checker('calculate','١٢٫٥ × ٢')
    assert result['status']=='ok',result
    assert result['result']['exact']=='25' and result['result']['process_limited']
    assert result['result']['memory_limit_mb']==256
    bad=await reasoning.checker('calculate','evil()');assert bad['status']=='error'
    parsed=await reasoning.checker('analyze_python','def f(xs=[]):\n    return xs.sort()')
    assert parsed['status']=='ok' and parsed['result']['executed'] is False

@pytest.mark.asyncio
async def test_real_worker_timeout_is_bounded():
    with pytest.raises(TimeoutError):await reasoning.checker('calculate','1+1',timeout=.00001)

def test_budget_prevents_extra_attempts():
    run=reasoning.Run('r','quick','hi');run.data['ceilings']={'model_calls':1,'tool_calls':1,'wall_seconds':45,'reserved_tokens':2048}
    run.tool()
    with pytest.raises(reasoning.BudgetExceeded):run.tool()
    run.model([{'content':'hi'}],{'max_tokens':10})
    with pytest.raises(reasoning.BudgetExceeded):run.model([{'content':'hi'}],{'max_tokens':10})
    run=reasoning.Run('r','quick','hi');run.data['ceilings']['reserved_tokens']=2
    with pytest.raises(reasoning.BudgetExceeded):run.model([{'content':'hello'}],{'max_tokens':10})
    assert run.data['model_calls']==0

def test_depth_is_respected_and_auto_is_cheap():
    assert reasoning.choose_depth('auto','hello',None,None)[0]=='quick'
    assert reasoning.choose_depth('auto','احسب',None,None)[0]=='analytical'
    assert reasoning.choose_depth('quick','hard',None,{} )[0]=='quick'
    assert reasoning.explicit_expression('احسب (19.95 * 3) * 1.17 - 10. أجب باختصار')=='(19.95 * 3) * 1.17 - 10'
    assert reasoning.explicit_expression('عندي12 ملفًا و3 كتب') is None

def test_receipt_save_reload_branch_snapshot(client,monkeypatch):
    fake(monkeypatch);pid,cid=chat(client)
    answer=packets(client.post(f'/api/conversations/{cid}/generate',json={'content':'hello','depth':'auto'}))[-1]['message']
    assert answer['run']['effective_depth']=='quick' and answer['run']['model_calls']==1
    assert client.get(f'/api/conversations/{cid}').json()['messages'][-1]==answer
    # Branch AFTER this answer so a historical receipt is copied.
    second=packets(client.post(f'/api/conversations/{cid}/generate',json={'content':'next','depth':'quick'}))[-1]['message']
    branch=packets(client.post(f'/api/conversations/{cid}/generate',json={'retry_message_id':second['id'],'depth':'quick'}))[0]['conversation_id']
    assert client.get(f'/api/conversations/{branch}').json()['messages'][1]['run']==answer['run']
    snap=client.get(f'/api/projects/{pid}/snapshot').json()
    restored=client.post('/api/projects/import',json={'snapshot':snap,'confirm':True});assert restored.status_code==201,restored.text
    chats=client.get('/api/conversations?project_id='+restored.json()['id']).json()
    messages=client.get('/api/conversations/'+chats[0]['id']).json()['messages']
    assert messages[1]['run']['origin']=='imported_snapshot'
    client.delete('/api/conversations/'+cid)
    with get_db() as conn:assert conn.execute('SELECT 1 FROM reasoning_runs WHERE message_id=?',(answer['id'],)).fetchone() is None

def test_quick_both_retrieval_scopes_does_not_spend_third_tool(client,monkeypatch):
    fake(monkeypatch,'fact [S1]');pid,cid=chat(client)
    client.post(f'/api/projects/{pid}/files',files={'file':('fact.txt',b'fact information','text/plain')})
    async def retrieve(*args,**kwargs):return []
    monkeypatch.setattr(service.public_knowledge,'retrieve_public',retrieve)
    answer=packets(client.post(f'/api/conversations/{cid}/generate',json={'content':'fact','depth':'quick','use_files':True,'use_knowledge':True}))[-1]['message']
    assert answer['status']=='complete' and answer['run']['tool_calls']==2

def test_private_source_revocation_blocks_second_dispatch(client,monkeypatch):
    fake(monkeypatch);pid,cid=chat(client);file=client.post(f'/api/projects/{pid}/files',files={'file':('fact.txt',b'fact information','text/plain')}).json()
    calls=[]
    async def stream(*args):
        calls.append(1);yield {'content':'fact [S999]'}
        with get_db() as conn:conn.execute('DELETE FROM knowledge_files WHERE id=?',(file['id'],))
        yield {'finish_reason':'stop'}
    monkeypatch.setattr(service.providers,'stream',stream)
    answer=packets(client.post(f'/api/conversations/{cid}/generate',json={'content':'fact','depth':'analytical','review':True,'use_files':True}))[-1]['message']
    assert len(calls)==1 and answer['status']=='error'

def test_paid_reasoning_fails_closed(client,monkeypatch):
    def secret(*args):raise AssertionError('No keys or network')
    monkeypatch.setattr(service.providers,'secret',secret);_,cid=chat(client)
    assert client.post(f'/api/conversations/{cid}/generate',json={'content':'hi','depth':'auto','provider':'openai'}).status_code==400

def test_foreign_chat_branch_rejected_before_copy(client,monkeypatch):
    fake(monkeypatch);_,cid=chat(client)
    assert client.post(f'/api/conversations/{cid}/generate',json={'depth':'quick','retry_message_id':'foreign'}).status_code==404

def test_interrupted_snapshot_has_no_running_stages(client,monkeypatch):
    fake(monkeypatch);pid,cid=chat(client)
    packets(client.post(f'/api/conversations/{cid}/generate',json={'content':'hi','depth':'quick'}))
    snap=client.get(f'/api/projects/{pid}/snapshot').json();message=snap['conversations'][0]['messages'][1]
    message['status']='streaming';message['run']['status']='running';message['run']['stages'][0]['status']='running'
    response=client.post('/api/projects/import',json={'snapshot':snap,'confirm':True});assert response.status_code==201
    cid=client.get('/api/conversations?project_id='+response.json()['id']).json()[0]['id']
    message=client.get('/api/conversations/'+cid).json()['messages'][1]
    assert message['status']=='interrupted' and message['run']['status']=='interrupted'
    assert all(s['status']!='running' for s in message['run']['stages'])

@pytest.mark.asyncio
async def test_preparation_stop_releases_reservation_without_saving(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));service.ACTIVE.clear();entered=asyncio.Event()
    async def validate(*args):entered.set();await asyncio.sleep(60)
    monkeypatch.setattr(service.providers,'validate_provider',validate)
    async with service.lifespan(service.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),base_url='http://127.0.0.1:8765',headers={'X-Riwaq-Client':'local'}) as client:
            from backend.local_auth import issue_pairing_code
            assert (await client.post('/api/auth/login',json={'pairing_code':issue_pairing_code()})).status_code == 200
            pid=(await client.get('/api/projects')).json()[0]['id'];cid=(await client.post('/api/conversations',json={'project_id':pid})).json()['id']
            task=asyncio.create_task(client.post(f'/api/conversations/{cid}/generate',json={'content':'cancel during prepare','depth':'quick'}))
            await asyncio.wait_for(entered.wait(),2)
            assert (await client.post(f'/api/conversations/{cid}/stop')).json()['stopped']
            response=await asyncio.wait_for(task,2);assert response.status_code==499
            assert not service.ACTIVE and not (await client.get(f'/api/conversations/{cid}')).json()['messages']

@pytest.mark.asyncio
async def test_response_finalizer_runs_even_if_send_fails_before_body():
    finalized=[]
    async def body():yield b'x'
    async def finalizer():finalized.append(True)
    async def fail(message):raise RuntimeError('delivery failed')
    response=service.ManagedStreamingResponse(body(),finalizer=finalizer)
    with pytest.raises(RuntimeError):await response({'type':'http','asgi':{'spec_version':'2.4'}},None,fail)
    assert finalized

@pytest.mark.asyncio
async def test_revision_keeps_original_when_factual_text_changes(monkeypatch):
    calls=[]
    async def stream(*args):
        calls.append(1);yield {'content':'fact [S99]' if len(calls)==1 else 'changed false fact [S1]'};yield {'finish_reason':'stop'}
    monkeypatch.setattr(reasoning.providers,'stream',stream)
    run=reasoning.Run('r','analytical','fact',review=True)
    results=[x async for x in run.stream('ollama','local',[{'role':'user','content':'fact'}],{'max_tokens':100,'context_size':8192},[{}])]
    assert len(calls)==2 and not any('replacement' in x for x in results)

@pytest.mark.asyncio
async def test_provider_generator_closed_when_outer_stream_closed(monkeypatch):
    closed=[]
    async def stream(*args):
        try:yield {'content':'part'};await asyncio.sleep(60)
        finally:closed.append(True)
    monkeypatch.setattr(reasoning.providers,'stream',stream)
    run=reasoning.Run('r','quick','hi');iterator=run.stream('ollama','local',[{'content':'hi'}],{'max_tokens':10,'context_size':8192},[])
    assert (await anext(iterator))['type']=='progress';assert (await anext(iterator))['content']=='part'
    await iterator.aclose();assert closed

@pytest.mark.parametrize('question',['calculate: 2*3e4','calculate: 2+3^2','احسب: 1+2%','Do not calculate: 2+3'])
def test_no_truncated_or_negated_expression(question):
    assert reasoning.explicit_expression(question) is None

def test_long_citation_does_not_break_save(client,monkeypatch):
    fake(monkeypatch,'text [S'+'9'*5000+']');_,cid=chat(client)
    result=packets(client.post(f'/api/conversations/{cid}/generate',json={'content':'hello','depth':'quick'}))[-1]['message']
    assert result['content']=='text [unverified citation]' and not service.ACTIVE


def test_explicit_request_options_survive_reload_and_snapshot(client,monkeypatch):
    fake(monkeypatch);pid,cid=chat(client)
    options={'calculation':{'expression':'1+2'},'review':True,'use_files':False,'use_knowledge':False}
    answer=packets(client.post(f'/api/conversations/{cid}/generate',json={'content':'Calculate supplied expression','depth':'analytical',**options}))[-1]['message']
    assert answer['run']['request_options']==options
    assert client.get(f'/api/conversations/{cid}').json()['messages'][-1]['run']['request_options']==options
    snap=client.get(f'/api/projects/{pid}/snapshot').json()
    assert snap['conversations'][0]['messages'][1]['run']['request_options']==options

@pytest.mark.asyncio
@pytest.mark.parametrize('body_number',[1,3])
async def test_delivery_failure_after_body_saves_cancelled(tmp_path,monkeypatch,body_number):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));service.ACTIVE.clear();fake(monkeypatch,'partial')
    async with service.lifespan(service.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),base_url='http://127.0.0.1:8765',headers={'X-Riwaq-Client':'local'}) as client:
            from backend.local_auth import issue_pairing_code
            assert (await client.post('/api/auth/login',json={'pairing_code':issue_pairing_code()})).status_code == 200
            pid=(await client.get('/api/projects')).json()[0]['id'];cid=(await client.post('/api/conversations',json={'project_id':pid})).json()['id']
            body=json.dumps({'content':'delivery failure','depth':'quick'}).encode();received=False;sent=0
            async def receive():
                nonlocal received
                if not received:received=True;return {'type':'http.request','body':body,'more_body':False}
                await asyncio.sleep(60)
            async def send(message):
                nonlocal sent
                if message['type']=='http.response.body':
                    sent+=1
                    if sent==body_number:raise RuntimeError('Synthetic delivery failure')
            scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1','method':'POST','scheme':'http','path':f'/api/conversations/{cid}/generate','raw_path':f'/api/conversations/{cid}/generate'.encode(),'query_string':b'','root_path':'','headers':[(b'cookie',('riwaq_owner_session='+client.cookies.get('riwaq_owner_session')).encode()),(b'host',b'127.0.0.1:8765'),(b'x-riwaq-client',b'local'),(b'content-type',b'application/json')],'client':('127.0.0.1',1234),'server':('127.0.0.1',8765)}
            with pytest.raises(Exception):await service.app(scope,receive,send)
            answer=(await client.get(f'/api/conversations/{cid}')).json()['messages'][-1]
            assert answer['status']=='cancelled' and answer['run']['status']=='cancelled' and not service.ACTIVE

@pytest.mark.parametrize('kind',['memory','cpu'])
def test_checker_memory_and_cpu_guards_are_enforced(kind):
    import subprocess,sys,time
    worker_dir=str(Path('backend').resolve())
    setup=f'import sys;sys.path.insert(0,{worker_dir!r});from check_worker import limits;handle=limits()\n'
    action='try:\n x=bytearray(400*1024*1024)\nexcept MemoryError:\n print("memory_limit_enforced")\nelse:\n raise RuntimeError("Memory limit not enforced")' if kind=='memory' else 'while True: pass'
    start=time.perf_counter();result=subprocess.run([sys.executable,'-I','-S','-c',setup+action],capture_output=True,timeout=7,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    assert time.perf_counter()-start<7
    if kind=='memory':assert result.returncode==0 and b'memory_limit_enforced' in result.stdout,result.stderr
    else:assert result.returncode!=0

@pytest.mark.parametrize('depth',[None,'quick'])
def test_retrieval_options_reload_retry_and_branch(client,monkeypatch,depth):
 from backend import context_lineage
 fake(monkeypatch,'fact [S1]');pid,cid=chat(client)
 assert client.post(f'/api/projects/{pid}/files',files={'file':('fact.txt',b'fact information','text/plain')}).status_code==201
 public_queries=[]
 async def retrieve(query,**kwargs):public_queries.append(query);return []
 monkeypatch.setattr(service.public_knowledge,'retrieve_public',retrieve)
 options={'use_files':True,'use_knowledge':True};events=packets(client.post(f'/api/conversations/{cid}/generate',json={'content':'fact',**options,**({'depth':depth} if depth else {})}));answer=events[-1]['message']
 assert events[0]['retrieval_options']==options and answer['retrieval_options']==options
 assert bool(answer.get('run'))==(depth is not None)
 if depth:assert all(answer['run']['request_options'][k] is v for k,v in options.items())
 loaded=client.get(f'/api/conversations/{cid}').json()['messages'];assert loaded[-1]==answer and loaded[0]['retrieval_options']==options
 second=packets(client.post(f'/api/conversations/{cid}/generate',json={'content':'next'}))[-1]['message'];branch=packets(client.post(f'/api/conversations/{cid}/generate',json={'retry_message_id':second['id']}))[0]['conversation_id'];copied=client.get(f'/api/conversations/{branch}').json()['messages']
 assert copied[0]['retrieval_options']==options and copied[1]['retrieval_options']==options and copied[1]['id']!=answer['id'] and len(public_queries)==1
 retried=packets(client.post(f'/api/conversations/{cid}/generate',json={'retry_message_id':answer['id']}))[-1]['message'];assert retried['retrieval_options']==options and retried['citations'] and len(public_queries)==2
 disabled={'use_files':False,'use_knowledge':False};overridden=packets(client.post(f'/api/conversations/{cid}/generate',json={'retry_message_id':answer['id'],**disabled}))[-1]['message'];assert overridden['retrieval_options']==disabled and overridden['citations']==[] and len(public_queries)==2
 client.delete(f'/api/conversations/{cid}')
 with get_db() as c:assert context_lineage.read_retrieval(c,answer['id']) is None

def test_snapshot_retry_preferences_do_not_restore_source_authority(client,monkeypatch):
 from backend import context_lineage
 fake(monkeypatch,'budget [S1]');pid,cid=chat(client)
 assert client.post(f'/api/projects/{pid}/files',files={'file':('budget.txt',b'The launch budget is 731 dollars.','text/plain')}).status_code==201
 options={'use_files':True,'use_knowledge':False};original=packets(client.post(f'/api/conversations/{cid}/generate',json={'content':'budget',**options}))[-1]['message']
 snapshot=client.get(f'/api/projects/{pid}/snapshot').json();assert all(m['retrieval_options']==options for m in snapshot['conversations'][0]['messages']) and original.get('run') is None
 bad=json.loads(json.dumps(snapshot));bad['conversations'][0]['messages'][-1]['retrieval_options']['use_files']='true';assert client.post('/api/projects/import',json={'snapshot':bad,'confirm':True}).status_code==422
 restored=client.post('/api/projects/import',json={'snapshot':snapshot,'confirm':True});assert restored.status_code==201;new_pid=restored.json()['id'];new_cid=client.get('/api/conversations',params={'project_id':new_pid}).json()[0]['id'];archived=client.get(f'/api/conversations/{new_cid}').json()['messages'];old=archived[-1]
 assert all(m['retrieval_options']==options and m.get('run') is None for m in archived)
 with get_db() as c:assert context_lineage.read(c,old['id'])==[context_lineage.BLOCKED]
 regenerated=packets(client.post(f'/api/conversations/{new_cid}/generate',json={'retry_message_id':old['id']}))[-1]['message'];assert regenerated['retrieval_options']==options and regenerated['citations'][0]['id']!=original['citations'][0]['id'] and regenerated['citations'][0]['project_id']==new_pid
 with get_db() as c:
  assert context_lineage.authorized(c,new_pid,context_lineage.read(c,regenerated['id']));c.execute('DELETE FROM knowledge_files WHERE project_id=?',(new_pid,))
 after=packets(client.post(f'/api/conversations/{new_cid}/generate',json={'retry_message_id':old['id']}))[-1]['message'];assert after['retrieval_options']==options and after['citations']==[] and 'No relevant source excerpts' in after['content']
 legacy=json.loads(json.dumps(snapshot))
 for m in legacy['conversations'][0]['messages']:m.pop('retrieval_options',None)
 r=client.post('/api/projects/import',json={'snapshot':legacy,'confirm':True});assert r.status_code==201;legacy_cid=client.get('/api/conversations',params={'project_id':r.json()['id']}).json()[0]['id'];assert all('retrieval_options' not in m for m in client.get(f'/api/conversations/{legacy_cid}').json()['messages'])
