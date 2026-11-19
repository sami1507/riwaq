"""Synthetic API fixtures: ownership/transactions/history, separate from live model UI QA."""
import copy,hashlib,json
from authority_fixture import fixture_owner
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import db,knowledge,studio,snapshots
from backend.app import state

@pytest.fixture
def client(tmp_path,monkeypatch):
 monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));db.init_db()
 with db.get_db() as c:
  stamp=db.now()
  for pid in ['one','two']:
   c.execute('INSERT INTO projects VALUES(?,?,?,?)',(pid,pid,'',stamp))
   c.execute('INSERT INTO conversations VALUES(?,?,?,?,?,?)',('c'+pid,pid,'Chat',stamp,stamp,None))
 app=FastAPI();app.include_router(knowledge.router);app.include_router(studio.router);app.include_router(snapshots.router);app.add_api_route('/api/projects/{id}/state',state)
 with fixture_owner(), TestClient(app) as client:
  for pid,text in [('one','Budget is 731.'),('two','SECRET OTHER PROJECT')]:client.post(f'/api/projects/{pid}/files',files={'file':('facts.txt',text.encode())})
  with db.get_db() as c:
   references=[]
   for pid in ['one','two']:
    ch=dict(c.execute('SELECT * FROM knowledge_chunks WHERE project_id=?',(pid,)).fetchone());references.append({'id':ch['id'],'file_id':ch['file_id'],'text':'FORGED'})
   for mid,cid,role,status,text,cites in [('m1','cone','assistant','complete','Budget is 731 [S1]. Foreign [S2].',references),('m2','ctwo','assistant','complete','SECRET OTHER ANSWER',[]),('m3','cone','assistant','cancelled','Partial',[]),('m4','cone','user','complete','User question',[])]:
    c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',(mid,cid,role,text,status,'fixture-model',stamp,json.dumps(cites)))
  yield client

def term(client,**kw):
 r=client.post('/api/projects/one/glossary',json={'term':'budget','definition':'User definition',**kw});assert r.status_code==201,r.text;return r.json()
def decision(client,**kw):
 r=client.post('/api/projects/one/decisions',json={'title':'Storage','decision':'Use SQLite','rationale':'Local persistence',**kw});assert r.status_code==201,r.text;return r.json()
def patch(client,item,**kw):
 body={key:item[key] for key in ('title','decision','rationale','alternatives')}
 return client.patch('/api/projects/one/decisions/'+item['id'],json={**body,'expected_version':item['version'],'status':item['status'],**kw})
def counts():
 with db.get_db() as c:return {t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ['projects','events','studio_entries','studio_versions','studio_artifact_sources','artifacts','artifact_versions']}

def test_canonical_scoped_sources_and_exact_selection(client):
 assert client.post('/api/projects/one/glossary',json={'term':'x','definition':'y','source_message_id':'m2'}).status_code==404
 assert client.post('/api/projects/one/glossary',json={'term':'x','definition':'y','source_message_id':'m1','source_excerpt':'Invented'}).status_code==422
 saved=term(client,source_message_id='m1');p=saved['provenance']
 assert len(p['citations'])==1 and p['citations'][0]['text']=='Budget is 731.'
 assert p['excerpt']=='Budget is 731 [S1]. Foreign [unverified citation].'
 assert 'SECRET' not in json.dumps(saved) and 'FORGED' not in json.dumps(saved)
 assert p['answer_sha256']==hashlib.sha256(b'Budget is 731 [S1]. Foreign [S2].').hexdigest()

@pytest.mark.parametrize('mid',['m3','m4'])
def test_completed_assistant_required(client,mid):
 assert client.post('/api/projects/one/glossary',json={'term':'x','definition':'y','source_message_id':mid}).status_code==409
 assert client.post('/api/projects/one/answers/'+mid+'/artifact',json={'title':'Note'}).status_code==409

@pytest.mark.parametrize('extra',[{'citations':[]},{'source_ids':['x']},{'source_excerpt':'a'},{'term':'   '},{'definition':'\x00bad'}])
def test_input_rejections_are_atomic(client,extra):
 before=counts();r=client.post('/api/projects/one/glossary',json={'term':'x','definition':'y',**extra});assert r.status_code==422,r.text;assert counts()==before

def test_all_record_routes_project_scope(client):
 item=term(client);path='/api/projects/two/glossary/'+item['id']
 assert client.get('/api/projects/two/glossary').json()==[]
 for url in [path,path+'/versions']:assert client.get(url).status_code==404
 assert client.patch(path,json={'term':'x','definition':'y','expected_version':1}).status_code==404
 assert client.post(path+'/restore',json={'expected_version':1,'from_version':1}).status_code==404
 assert client.delete(path+'?expected_version=1').status_code==404

def test_glossary_history_stale_restore_delete(client):
 first=term(client,term_ar='ميزانية',source_message_id='m1');path='/api/projects/one/glossary/'+first['id']
 second=client.patch(path,json={'term':'budget','definition':'Revised','expected_version':1}).json();assert second['version']==2 and second['provenance']==first['provenance']
 assert client.patch(path,json={'term':'budget','definition':'Stale','expected_version':1}).status_code==409
 third=client.patch(path,json={'term':'budget','definition':'Detached','expected_version':2,'source_message_id':None}).json();assert third['provenance'] is None
 restored=client.post(path+'/restore',json={'expected_version':3,'from_version':1}).json();assert restored['version']==4 and restored['term_ar']=='ميزانية'
 assert [v['version'] for v in client.get(path+'/versions').json()]==[4,3,2,1]
 assert client.delete(path+'?expected_version=3').status_code==409
 assert client.delete(path+'?expected_version=4').status_code==200
 assert client.get(path+'/versions').status_code==404
 with db.get_db() as c:assert c.execute('SELECT COUNT(*) FROM studio_versions WHERE entry_id=?',(first['id'],)).fetchone()[0]==0

def test_duplicate_normalized_terms(client):
 term(client,term='  Budget ')
 assert client.post('/api/projects/one/glossary',json={'term':'ＢＵＤＧＥＴ','definition':'Other'}).status_code==409
 assert client.post('/api/projects/two/glossary',json={'term':'BUDGET','definition':'Own'}).status_code==201

def test_decision_confirmation_state_and_restore(client):
 item=decision(client);assert client.get('/api/projects/one/state').json()['decisions']==[]
 assert patch(client,item,status='adopted').status_code==422
 assert patch(client,item,status='adopted',confirm_adopted='true').status_code==422
 assert patch(client,item,status='implemented',confirm_implemented=True,implementation_evidence='Claim').status_code==409
 adopted=patch(client,item,status='adopted',confirm_adopted=True).json();assert len(client.get('/api/projects/one/state').json()['decisions'])==1
 assert patch(client,adopted,status='adopted',decision='Changed').status_code==422
 assert patch(client,adopted,status='implemented',confirm_implemented=True,implementation_evidence=' ').status_code==422
 assert patch(client,adopted,status='implemented',confirm_implemented=True,implementation_evidence='Test run',decision='Different choice').status_code==409
 implemented=patch(client,adopted,status='implemented',confirm_implemented=True,implementation_evidence='User checked local test log').json();assert implemented['implementation_attested_at']
 current=client.get('/api/projects/one/state').json();assert len(current['completed'])==1 and current['completed'][0]['verification']=='user-attestation'
 restored=client.post('/api/projects/one/decisions/'+item['id']+'/restore',json={'expected_version':3,'from_version':3}).json();assert restored['status']=='proposal' and restored['implementation_evidence']==''
 current=client.get('/api/projects/one/state').json();assert current['decisions']==[] and current['completed']==[]
 assert any(e['kind']=='studio_decision_implemented' for e in current['events'])
 assert client.delete('/api/projects/one/decisions/'+item['id']+'?expected_version=4').status_code==200
 assert client.get('/api/projects/one/state').json()['decisions']==[]

@pytest.mark.parametrize('template',['note','brief','checklist'])
def test_artifact_atomic_scoped_idempotent_versioned(client,template):
 path='/api/projects/one/answers/m1/artifact';body={'title':'Answer','template':template,'language':'en'}
 assert client.post('/api/projects/two/answers/m1/artifact',json=body).status_code==404
 assert client.post(path,json=body|{'selection':'Invented'}).status_code==422
 assert client.post(path,json=body|{'content':'forged'}).status_code==422
 first=client.post(path,json=body).json();a=first['artifact'];assert not first['reused']
 assert 'Budget is 731 [S1]. Foreign [unverified citation].' in a['content'] and '- [ ]' not in a['content']
 assert 'not verification of claims' in a['content']
 again=client.post(path,json=body).json();assert again['reused'] and again['artifact']['id']==a['id']
 url='/api/projects/one/artifacts/'+a['id']+'/provenance';origin=client.get(url).json();assert not origin['content_changed']
 assert client.get('/api/projects/two/artifacts/'+a['id']+'/provenance').status_code==404
 edit=client.patch('/api/artifacts/'+a['id'],json={'content':'User edit','expected_version':1}).json();assert edit['version']==2
 assert client.get(url).json()['content_changed'];assert client.get(url).json()['provenance']==origin['provenance']
 assert client.post(path,json=body).json()['artifact']['content']=='User edit'

@pytest.mark.parametrize('operation',['create','edit','convert'])
def test_transaction_failure_rolls_back(client,monkeypatch,operation):
 item=term(client);before=counts()
 def fail(*args):raise RuntimeError('event failed')
 monkeypatch.setattr(studio,'event',fail)
 with pytest.raises(RuntimeError,match='event failed'):
  if operation=='create':client.post('/api/projects/one/decisions',json={'title':'x','decision':'y'})
  elif operation=='edit':client.patch('/api/projects/one/glossary/'+item['id'],json={'term':'budget','definition':'Changed','expected_version':1})
  else:client.post('/api/projects/one/answers/m1/artifact',json={'title':'x'})
 assert counts()==before

def studio_snapshot(client):
 term(client,source_message_id='m1');d=decision(client,source_message_id='m1');d=patch(client,d,status='adopted',confirm_adopted=True).json();patch(client,d,status='implemented',confirm_implemented=True,implementation_evidence='User checked test')
 client.post('/api/projects/one/answers/m1/artifact',json={'title':'Saved answer'})
 r=client.get('/api/projects/one/snapshot');assert r.status_code==200,r.text;return r.json()
def origins(s):
 for entry in s['studio_entries']:
  yield entry['payload']['provenance']
  for v in entry['versions']:yield v['payload']['provenance']
 for a in s['artifacts']:yield a['studio_source']['provenance']

def test_studio_snapshot_roundtrip_all_history_no_authority(client):
 s=studio_snapshot(client);assert 'SECRET' not in json.dumps(s);before=copy.deepcopy(s)
 r=client.post('/api/projects/import',json={'snapshot':s,'confirm':True});assert r.status_code==201,r.text
 restored=client.get('/api/projects/'+r.json()['id']+'/snapshot').json()
 assert client.get('/api/projects/one/snapshot').json()==before
 old={e['kind']:e for e in s['studio_entries']};new={e['kind']:e for e in restored['studio_entries']}
 for kind,e in old.items():
  assert e['version']==new[kind]['version'] and len(e['versions'])==len(new[kind]['versions'])
  assert {k:v for k,v in e['payload'].items() if k!='provenance'}=={k:v for k,v in new[kind]['payload'].items() if k!='provenance'}
 for p in origins(restored):
  assert p['origin']=='imported_snapshot' and p['message_id'] is None and p['conversation_id'] is None
  assert p['citations'][0]['id']==restored['files'][0]['chunks'][0]['id']
  assert p['answer_sha256']==next(origins(s))['answer_sha256']
 assert restored['artifacts'][0]['content']==s['artifacts'][0]['content']
 assert restored['artifacts'][0]['versions']==s['artifacts'][0]['versions']
 with db.get_db() as c:assert c.execute('SELECT request_key FROM studio_artifact_sources WHERE project_id=?',(r.json()['id'],)).fetchone()[0].startswith('snapshot:')

@pytest.mark.parametrize('mutation',[
 lambda s:s['studio_entries'][0]['versions'].pop(),
 lambda s:s['studio_entries'][0]['versions'][0]['payload'].update(shell='run'),
 lambda s:s['studio_entries'][0]['versions'][0]['payload']['provenance'].update(answer_sha256='bad'),
 lambda s:s['studio_entries'][0]['versions'][0]['payload']['provenance'].update(captured_at='yesterday'),
 lambda s:s['artifacts'][0]['studio_source'].update(initial_content_sha256='0'*64),
 lambda s:s['artifacts'][0]['studio_source'].update(request_key='replay')])
def test_invalid_snapshot_is_atomic(client,mutation):
 s=studio_snapshot(client);mutation(s);before=counts();r=client.post('/api/projects/import',json={'snapshot':s,'confirm':True});assert r.status_code==422,r.text;assert counts()==before

def test_snapshot_dangling_historical_source_dropped(client):
 s=studio_snapshot(client);entry=next(e for e in s['studio_entries'] if e['kind']=='decision');entry['versions'][0]['payload']['provenance']['citations'][0]['file_id']='foreign'
 r=client.post('/api/projects/import',json={'snapshot':s,'confirm':True});assert r.status_code==201,r.text;assert r.json()['dropped_citations']==1
 restored=client.get('/api/projects/'+r.json()['id']+'/snapshot').json();entry=next(e for e in restored['studio_entries'] if e['kind']=='decision');assert entry['versions'][0]['payload']['provenance']['citations']==[]
 assert '[unverified citation]' in entry['versions'][0]['payload']['provenance']['excerpt']

def test_snapshot_rollback_preserves_all_tables(client,monkeypatch):
 s=studio_snapshot(client);before=counts()
 def fail(*args):raise RuntimeError('snapshot event failed')
 monkeypatch.setattr(snapshots,'event',fail)
 with pytest.raises(RuntimeError):client.post('/api/projects/import',json={'snapshot':s,'confirm':True})
 assert counts()==before

def test_old_snapshot_compatible(client):
 s=client.get('/api/projects/one/snapshot').json();s.pop('studio_entries');r=client.post('/api/projects/import',json={'snapshot':s,'confirm':True});assert r.status_code==201,r.text

def test_quota_checked_per_project(client,monkeypatch):
 monkeypatch.setattr(studio,'MAX_ENTRIES',1);term(client);assert client.post('/api/projects/one/decisions',json={'title':'x','decision':'y'}).status_code==413
 assert client.post('/api/projects/two/glossary',json={'term':'x','definition':'y'}).status_code==201

@pytest.mark.parametrize('broken',['null','{bad',json.dumps({'provenance':{'citations':None}})])
def test_corrupt_metadata_refuses_backup(client,broken):
 item=term(client)
 with db.get_db() as c:c.execute('UPDATE studio_entries SET payload=? WHERE id=?',(broken,item['id']))
 r=client.get('/api/projects/one/snapshot');assert r.status_code==409,r.text

def test_snapshot_envelope_does_not_consume_content_budget(client,monkeypatch):
 s=studio_snapshot(client);size=len(json.dumps(s,ensure_ascii=False,separators=(',',':')).encode())
 monkeypatch.setattr(snapshots,'MAX_SNAPSHOT_BYTES',size)
 assert client.get('/api/projects/one/snapshot').status_code==200
 r=client.post('/api/projects/import',content=json.dumps({'snapshot':s,'confirm':True},ensure_ascii=False,separators=(',',':')).encode(),headers={'Content-Type':'application/json'})
 assert r.status_code==201,r.text
