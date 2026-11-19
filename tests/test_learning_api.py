"""Real authenticated API/SQLite/math workers; provider capture is an explicit stub.

The stub measures context delivery and revocation, not model quality.
"""
import json
import sqlite3
import uuid
import pytest
from fastapi.testclient import TestClient
from backend import app as service, db, local_auth, learning, providers


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY',str(tmp_path/'absent.json'))
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as value:
        assert value.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
        yield value

def setup(client):
    pid=client.get('/api/projects').json()[0]['id']
    cid=client.post('/api/conversations',json={'project_id':pid}).json()['id']
    # Synthetic prior reply stored exactly as a conversation message.
    with db.get_db() as c:
        for role,text in [('user','2+2'),('assistant','4')]:
            mid=db.uid();c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',(mid,cid,role,text,'complete','fixture',db.now(),'[]'))
    base=f'/api/projects/{pid}/learning'
    ref=client.get(base+f'/targets/message/{mid}').json()
    return pid,cid,base,{k:ref[k] for k in ('kind','id','version')}

def feedback(client,base,cid,ref,**overrides):
    body={'idempotency_key':str(uuid.uuid4()),'target':ref,'signal':'preference','dimensions':['style'],
          'reason':'Use short replies for comparisons','correction':{'kind':'preference','text':'Use short replies for comparisons'},
          'scope':'project','purposes':['current_task','project_memory'],'conversation_id':cid}
    body.update(overrides)
    response=client.post(base+'/feedback',json=body)
    assert response.status_code==200,response.text
    return response.json(),body

def checked(client,base,row):
    response=client.post(base+f"/feedback/{row['id']}/verify",json={'expected_version':row['version']})
    assert response.status_code==200,response.text
    return response.json()

def adopt(client,base,cid,row,task='comparison'):
    response=client.post(base+f"/feedback/{row['id']}/candidates",json={
        'expected_version':row['version'],'scope':row['scope'],'conversation_id':cid,
        'rule':{'kind':'brevity','value':'short'},'applicability':{'task':task}})
    assert response.status_code==200,response.text
    candidate=response.json()
    response=client.post(base+f"/candidates/{candidate['id']}/evaluate",json={'expected_version':candidate['version']})
    assert response.status_code==200,response.text
    report=response.json();assert report['evaluation']['status']=='passed',report
    response=client.post(base+f"/candidates/{candidate['id']}/activate",json={
        'expected_version':candidate['version'],'evaluation_id':report['evaluation']['id']})
    assert response.status_code==200,response.text
    return candidate,response.json()

def test_feedback_unknown_and_network_replay_no_invented_truth(client):
    _,cid,base,ref=setup(client)
    row,body=feedback(client,base,cid,ref,signal='negative',reason='',correction=None,dimensions=['unspecified'])
    assert row['status']=='recorded' and not row['verification']
    replay=client.post(base+'/feedback',json=body)
    assert replay.status_code==200 and replay.json()['id']==row['id']
    assert len(client.get(base+'/feedback').json())==1
    assert client.post(base+f"/feedback/{row['id']}/verify",json={'expected_version':1}).status_code==422
    body['signal']='positive'
    assert client.post(base+'/feedback',json=body).status_code==409
    assert client.get(base+'/candidates').json()==[]

def test_wrong_correction_is_disputed_with_actual_worker(client):
    _,cid,base,ref=setup(client)
    row,_=feedback(client,base,cid,ref,signal='correction',correction={'kind':'math_answer','candidate_value':'5'})
    result=checked(client,base,row)
    assert result['status']=='disputed'
    proof=result['verification']
    assert proof['corrected_output']['value']=='4' and proof['worker_calls']==1
    assert not proof['weights_changed'] and not proof['external_effect_performed']
    response=client.post(base+f"/feedback/{row['id']}/candidates",json={
        'expected_version':result['version'],'rule':{'kind':'brevity','value':'short'},'applicability':{'task':'any'},'scope':'project'})
    assert response.status_code==409

def test_adopt_context_explicit_override_and_rollback_through_chat(client,monkeypatch):
    pid,cid,base,ref=setup(client)
    captured=[]
    async def preflight(*args):return None
    async def stream(provider,model,messages,cfg):
        captured.append(messages)
        yield {'content':'SYNTHETIC_REPLY_FOR_CONTEXT_CONTRACT'}
        yield {'finish_reason':'stop'}
    monkeypatch.setattr(providers,'preflight',preflight);monkeypatch.setattr(providers,'stream',stream)
    row,_=feedback(client,base,cid,ref);row=checked(client,base,row)
    _,release=adopt(client,base,cid,row)
    other=client.post('/api/conversations',json={'project_id':pid}).json()['id']
    def generate(text):
        response=client.post(f'/api/conversations/{other}/generate',json={'content':text,'use_files':False,'use_knowledge':False})
        assert response.status_code==200,response.text
        rows=[json.loads(v) for v in response.text.splitlines()]
        errors=[v for v in rows if v['type']=='error']
        assert not errors,errors
        return rows[-1]['message']
    answer=generate('Compare bicycles and trains')
    assert 'SCOPED_PRESENTATION_DEFAULTS=' in captured[-1][0]['content']
    assert '"brevity","value":"short"' in captured[-1][0]['content']
    with db.get_db() as c:
        from backend.context_lineage import read
        assert any(x['kind']=='learning_release' for x in read(c,answer['id']))
    generate('Compare bicycles and trains in detailed form')
    assert 'SCOPED_PRESENTATION_DEFAULTS=' not in captured[-1][0]['content']
    response=client.post(base+f"/releases/{release['id']}/rollback",json={'expected_version':release['version']})
    assert response.status_code==200,response.text
    generate('Compare bicycles and trains')
    assert 'SCOPED_PRESENTATION_DEFAULTS=' not in captured[-1][0]['content']
    assert all(m['content']!='SYNTHETIC_REPLY_FOR_CONTEXT_CONTRACT' for m in captured[-1])
    assert client.get('/api/memories?project_id='+pid).json()==[]

def test_ownership_authority_and_server_only_reports(client):
    pid,cid,base,ref=setup(client)
    row,body=feedback(client,base,cid,ref)
    for extra in ({'actor':'admin'},{'tenant':'elsewhere'},{'verified':True}):
        assert client.post(base+'/feedback',json=body|extra).status_code==422
    assert client.post(base+f"/feedback/{row['id']}/verify",json={'expected_version':1,'verification':{'status':'verified'}}).status_code==422
    with db.get_db() as c:c.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?',('another-owner',pid))
    for path in ('feedback','feedback/'+row['id'],'candidates','releases','status','context?conversation_id='+cid):
        response=client.get(base+'/'+path)
        assert response.status_code==404 and row['reason'] not in response.text
    for suffix,body in (('training-draft',{'expected_version':row['version'],'rights_confirmed':True}),
                        ('verify',{'expected_version':row['version']}),
                        ('withdraw-consent',{'expected_version':row['version'],'purposes':['training']})):
        response=client.post(base+f"/feedback/{row['id']}/"+suffix,json=body)
        assert response.status_code==404 and row['reason'] not in response.text

def test_storage_failure_is_not_success(client,monkeypatch):
    _,cid,base,ref=setup(client)
    def fail(*args,**kwargs):raise sqlite3.OperationalError('SYNTHETIC_PRIVATE_ERROR')
    monkeypatch.setattr(learning,'add_feedback',fail)
    response=client.post(base+'/feedback',json={'idempotency_key':'retry-same','target':ref,'signal':'negative','conversation_id':cid})
    assert response.status_code==503 and 'SYNTHETIC_PRIVATE_ERROR' not in response.text
    assert client.get(base+'/feedback').json()==[]

def test_candidate_edit_requires_retest_and_detects_old_version(client):
    _,cid,base,ref=setup(client)
    row,_=feedback(client,base,cid,ref);row=checked(client,base,row)
    result=client.post(base+f"/feedback/{row['id']}/candidates",json={'expected_version':row['version'],
        'rule':{'kind':'tone','value':'formal'},'applicability':{'task':'any'},'scope':'project'}).json()
    route=base+f"/candidates/{result['id']}"
    edit={'expected_version':result['version'],'rule':{'kind':'tone','value':'neutral'},'applicability':{'task':'comparison'}}
    assert client.put(route,json=edit).status_code==200
    assert client.put(route,json=edit).status_code==409
    assert client.get(route).json()['status']=='proposed'
    assert client.post(route+'/activate',json={'expected_version':2,'evaluation_id':'invented'}).status_code==409

def test_personal_memory_gate_is_not_bypassed(client):
    _,cid,base,ref=setup(client)
    response=client.post(base+'/feedback',json={'idempotency_key':'personal-attempt','target':ref,'signal':'remember',
        'scope':'personal','conversation_id':cid,'reason':'Store preference'})
    assert response.status_code==423
    assert client.get(base+'/status').json()['training']['model_changed'] is False

def test_portable_snapshot_cannot_flatten_scoped_lessons(client):
    pid,cid,base,ref=setup(client)
    row,_=feedback(client,base,cid,ref);row=checked(client,base,row)
    adopt(client,base,cid,row)
    assert len(client.get('/api/memories?project_id='+pid).json())==1
    saved=client.get(f'/api/projects/{pid}/snapshot')
    assert saved.status_code==200,saved.text
    assert saved.json()['memories']==[]

def test_actual_training_draft_requires_consent_and_verifier_then_withdraws(client):
    _,cid,base,ref=setup(client)
    row,_=feedback(client,base,cid,ref,signal='correction',correction={'kind':'math_answer','candidate_value':'4'},purposes=['current_task'])
    row=checked(client,base,row)
    route=base+f"/feedback/{row['id']}"
    assert client.post(route+'/training-draft',json={'expected_version':row['version'],'rights_confirmed':True}).status_code==403
    allowed,_=feedback(client,base,cid,ref,signal='correction',correction={'kind':'math_answer','candidate_value':'4'},purposes=['current_task','training'])
    allowed=checked(client,base,allowed);route=base+f"/feedback/{allowed['id']}"
    body={'expected_version':allowed['version'],'rights_confirmed':True}
    exported=client.post(route+'/training-draft',json=body)
    assert exported.status_code==200,exported.text
    data=exported.json()
    assert data['record']['messages']==[{'role':'user','content':'2+2'},{'role':'assistant','content':'4'}]
    assert not data['training_ready'] and not data['training_started'] and not data['model_changed']
    withdrawn=client.post(route+'/withdraw-consent',json={'expected_version':allowed['version'],'purposes':['training']})
    assert withdrawn.status_code==200,withdrawn.text
    body['expected_version']=withdrawn.json()['version']
    assert client.post(route+'/training-draft',json=body).status_code==403

def test_revoked_lesson_cannot_finish_an_inflight_answer(client,monkeypatch):
    pid,cid,base,ref=setup(client)
    row,_=feedback(client,base,cid,ref);row=checked(client,base,row)
    candidate,release=adopt(client,base,cid,row)
    async def preflight(*args):return None
    async def stream(*args):
        yield {'content':'Derived from revoked preference'}
        with db.get_db() as c:
            who=local_auth.current_principal()
            learning.rollback_release(c,learning.guard.Actor(who['owner_id'],who['session_id']),pid,release['id'],release['version'])
        yield {'finish_reason':'stop'}
    monkeypatch.setattr(providers,'preflight',preflight);monkeypatch.setattr(providers,'stream',stream)
    response=client.post(f'/api/conversations/{cid}/generate',json={'content':'Compare trains and bicycles','use_files':False})
    assert response.status_code==200
    final=client.get(f'/api/conversations/{cid}').json()['messages'][-1]
    assert final['status']=='error' and final['content']==''

def test_verified_value_enters_current_conversation_only_and_forgetting_removes_it(client,monkeypatch):
    pid,cid,base,ref=setup(client)
    row,_=feedback(client,base,cid,ref,scope='conversation',purposes=['current_task'],signal='correction',
        correction={'kind':'math_answer','candidate_value':'4'})
    row=checked(client,base,row)
    captured=[]
    async def preflight(*args):return None
    async def stream(provider,model,messages,cfg):
        captured.append(messages);yield {'content':'Synthetic follow-up'};yield {'finish_reason':'stop'}
    monkeypatch.setattr(providers,'preflight',preflight);monkeypatch.setattr(providers,'stream',stream)
    def ask(target):
        response=client.post(f'/api/conversations/{target}/generate',json={'content':'Explain the preceding result','use_files':False})
        assert response.status_code==200,response.text
        events=[json.loads(x) for x in response.text.splitlines()]
        assert events[-1]['message']['status']=='complete',events[-1]
        return events[-1]['message']
    first=ask(cid)
    assert 'VERIFIED_CONVERSATION_CORRECTIONS=' in captured[-1][0]['content']
    another=client.post('/api/conversations',json={'project_id':pid}).json()['id']
    ask(another)
    assert 'VERIFIED_CONVERSATION_CORRECTIONS=' not in captured[-1][0]['content']
    # A new branch is a new conversation scope. A copied old answer must not
    # indirectly transfer a scoped lesson into it.
    branched=client.post(f'/api/conversations/{cid}/generate',json={
        'content':'','retry_message_id':first['id'],'use_files':False})
    assert branched.status_code==200,branched.text
    assert 'VERIFIED_CONVERSATION_CORRECTIONS=' not in captured[-1][0]['content']
    assert client.delete(base+f"/feedback/{row['id']}?expected_version={row['version']}").status_code==200
    ask(cid)
    assert 'VERIFIED_CONVERSATION_CORRECTIONS=' not in captured[-1][0]['content']
    assert not client.get('/api/memories?project_id='+pid).json()
