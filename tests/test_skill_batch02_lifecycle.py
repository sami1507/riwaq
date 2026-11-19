"""Synthetic lifecycle tests: real SQLite/Guard, no provider/spending/content."""
import json
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend import db, guard, skill_lifecycle as life


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    with db.get_db() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE projects(id TEXT PRIMARY KEY);
        CREATE TABLE guard_projects(project_id TEXT PRIMARY KEY,owner_id TEXT);
        CREATE TABLE local_auth_owner(slot INTEGER PRIMARY KEY,owner_id TEXT);
        CREATE TABLE local_auth_sessions(id TEXT PRIMARY KEY,owner_id TEXT,expires_at REAL,revoked_at REAL,last_seen REAL);
        CREATE TABLE skill_runs(id TEXT PRIMARY KEY,project_id TEXT REFERENCES projects(id),owner_id TEXT,
          session_id TEXT,request_hash TEXT,resources TEXT,state TEXT,document TEXT,updated_at TEXT);
        CREATE TABLE skill_artifacts(run_id TEXT,name TEXT,content BLOB);
        INSERT INTO projects VALUES('project-a');
        INSERT INTO projects VALUES('project-b');
        INSERT INTO guard_projects VALUES('project-a','owner-a');
        INSERT INTO guard_projects VALUES('project-b','owner-b');
        INSERT INTO local_auth_owner VALUES(1,'owner-a');
        ''')
        c.execute('INSERT INTO local_auth_sessions VALUES(?,?,?,?,?)', ('session-a','owner-a',time.time()+3600,None,time.time()))
        c.execute('INSERT INTO local_auth_sessions VALUES(?,?,?,?,?)', ('session-new','owner-a',time.time()+3600,None,time.time()))
        life.init_lifecycle(c)
    return guard.Actor('owner-a','session-a')


def add_run(who, run_id='run-a', *, state='queued', steps=None, text='SYNTHETIC PRIVATE CONTENT'):
    steps = steps if steps is not None else [{'id':'step-intent-routing','skill':'intent-routing','status':'pending','output_refs':[]}]
    doc = {'id':run_id,'status':state,'steps':steps,'result':{'text':text,'artifacts':[]},
        'policy_version':'riwaq-skills-1','criteria':[{'id':'c1','status':'unverified'}]}
    with db.get_db() as c:
        c.execute('INSERT INTO skill_runs VALUES(?,?,?,?,?,?,?,?,?)',
            (run_id,'project-a',who.owner_id,who.session_id,'a'*64,'[]',state,json.dumps(doc),db.now()))
    return run_id


def setup_budget(who, limit=10, key='local-steps', unit='work_units', currency=None):
    with db.get_db() as c:
        life.ensure_budget(c,who,'project-a',key,unit,limit,currency)


def reserve(who, run_id='run-a', amount=5, key='attempt-1'):
    with db.get_db() as c:
        return life.reserve_budget(c,who,run_id,'local-steps',key,amount)


def checkpoint(who, run_id='run-a'):
    with db.get_db() as c:
        return life.save_checkpoint(c,who,run_id,expected_revision=0)


def interrupted(run_id='run-a'):
    with db.get_db() as c:
        c.execute("UPDATE skill_runs SET state='unknown' WHERE id=?", (run_id,))


def test_shared_budget_cannot_overspend_concurrently(environment):
    who=environment;setup_budget(who)
    for n in range(8):add_run(who,f'run-{n}')
    def attempt(n):
        try:return reserve(who,f'run-{n}',6)['id']
        except life.LifecycleError as exc:return str(exc)
    with ThreadPoolExecutor(max_workers=8) as workers:
        results=list(workers.map(attempt,range(8)))
    assert results.count('budget_exhausted')==7
    with db.get_db() as c:
        status=life.budget_status(c,who,'project-a','local-steps')
        assert status['held']==6 and status['available']==4 and not status['actual_known']


def test_duplicate_reservation_and_claim_are_atomic(environment):
    who=environment;add_run(who);setup_budget(who)
    with ThreadPoolExecutor(max_workers=6) as workers:
        reservations=list(workers.map(lambda _:reserve(who),range(6)))
    assert len({r['id'] for r in reservations})==1
    def claim(_):
        with db.get_db() as c:return life.claim_reservation(c,who,reservations[0]['id'])
    with ThreadPoolExecutor(max_workers=6) as workers:
        assert list(workers.map(claim,range(6))).count(True)==1
    with pytest.raises(life.LifecycleError,match='reservation_conflict'):reserve(who,amount=4)


def test_unknown_usage_survives_restart_and_run_deletion(environment):
    who=environment;add_run(who);setup_budget(who);r=reserve(who,amount=8)
    with db.get_db() as c:
        assert life.claim_reservation(c,who,r['id'])
        result=life.settle_budget(c,who,r['id'],None)
        assert result['actual'] is None and result['state']=='unknown'
    # Reopen a fresh connection and schema initialization, as after restart.
    with db.get_db() as c:
        life.init_lifecycle(c)
        status=life.budget_status(c,who,'project-a','local-steps')
        assert status['held']==8 and status['available']==2 and not status['actual_known']
        with pytest.raises(life.LifecycleError,match='dispatched_usage'):life.release_reservation(c,who,r['id'])
        c.execute('DELETE FROM skill_runs WHERE id=?',('run-a',))
    with db.get_db() as c:
        assert life.budget_status(c,who,'project-a','local-steps')['held']==8
        assert life.settle_budget(c,who,r['id'],6)['actual']==6
        assert life.budget_status(c,who,'project-a','local-steps')['available']==4


def test_settlement_records_real_overrun_and_prevents_more_work(environment):
    who=environment;add_run(who);add_run(who,'run-b');setup_budget(who);r=reserve(who,amount=5)
    with db.get_db() as c:
        life.claim_reservation(c,who,r['id'])
        life.settle_budget(c,who,r['id'],12)
        s=life.budget_status(c,who,'project-a','local-steps')
        assert s['spent']==12 and s['available']==0 and s['over_budget']
        assert life.settle_budget(c,who,r['id'],12)['state']=='settled'
        with pytest.raises(life.LifecycleError,match='settlement_conflict'):life.settle_budget(c,who,r['id'],0)
    with pytest.raises(life.LifecycleError,match='budget_exhausted'):reserve(who,'run-b',1)


@pytest.mark.parametrize('amount',[-1,True,1.5,float('nan'),float('inf'),'5',10**12+1])
def test_invalid_budget_amounts_are_rejected(environment,amount):
    who=environment;add_run(who);setup_budget(who)
    with pytest.raises(life.LifecycleError,match='invalid_budget_amount'):reserve(who,amount=amount)


def test_units_currency_and_caps_cannot_be_redefined(environment):
    who=environment;setup_budget(who)
    for unit,limit,currency in [('tokens',10,None),('work_units',11,None),('money_microunits',10,'USD')]:
        with pytest.raises(life.LifecycleError,match='budget_definition_conflict'):
            setup_budget(who,limit,unit=unit,currency=currency)
    with pytest.raises(life.LifecycleError,match='currency_required'):setup_budget(who,key='money',unit='money_microunits')
    with pytest.raises(life.LifecycleError,match='currency_requires'):setup_budget(who,key='money',unit='work_units',currency='USD')
    setup_budget(who,key='money',unit='money_microunits',currency='USD')
    with pytest.raises(life.LifecycleError,match='budget_definition_conflict'):
        setup_budget(who,key='money',unit='money_microunits',currency='EUR')


def test_caller_transaction_rollback_removes_run_and_reservation(environment):
    who=environment;setup_budget(who)
    with pytest.raises(RuntimeError):
        with db.get_db() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('INSERT INTO skill_runs VALUES(?,?,?,?,?,?,?,?,?)',('run-a','project-a',who.owner_id,who.session_id,'a'*64,'[]','queued','{}',db.now()))
            life.reserve_budget(c,who,'run-a','local-steps','first',3)
            raise RuntimeError('synthetic rollback')
    with db.get_db() as c:
        assert c.execute('SELECT count(*) FROM skill_runs').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM skill_budget_reservations').fetchone()[0]==0


def test_guard_revocation_blocks_reserve_and_settlement(environment):
    who=environment;add_run(who);setup_budget(who);r=reserve(who)
    with db.get_db() as c:c.execute("UPDATE guard_projects SET owner_id='owner-b' WHERE project_id='project-a'")
    with db.get_db() as c:
        with pytest.raises(guard.Denied):life.settle_budget(c,who,r['id'],0)
        with pytest.raises(guard.Denied):life.budget_status(c,who,'project-a','local-steps')


def test_zero_budget_and_undispatched_release(environment):
    who=environment;add_run(who);setup_budget(who,limit=0)
    assert reserve(who,amount=0)['reserved']==0
    with pytest.raises(life.LifecycleError,match='budget_exhausted'):reserve(who,amount=1,key='second')
    r=reserve(who,amount=0)
    with db.get_db() as c:
        assert life.release_reservation(c,who,r['id'])['actual']==0
        assert not life.claim_reservation(c,who,r['id'])


def test_checkpoint_is_reference_only_versioned_and_resumes_once(environment):
    who=environment
    add_run(who,state='running',steps=[
        {'id':'step-docx-authoring','skill':'docx-authoring','status':'completed','output_refs':['private-report.docx']},
        {'id':'step-citation-grounding','skill':'citation-grounding','status':'running','output_refs':[]}])
    cp=checkpoint(who);interrupted()
    with db.get_db() as c:
        stored=c.execute('SELECT body FROM skill_checkpoints').fetchone()[0]
        assert 'SYNTHETIC PRIVATE CONTENT' not in stored and 'private-report.docx' not in stored
        assert json.loads(stored)['document_ref']=='skill_runs:run-a'
    def acquire(_):
        try:
            with db.get_db() as c:return life.acquire_resume(c,guard.Actor(who.owner_id,'session-new'),'run-a',cp['revision'])
        except life.LifecycleError as exc:return str(exc)
    with ThreadPoolExecutor(max_workers=4) as workers:results=list(workers.map(acquire,range(4)))
    successes=[r for r in results if isinstance(r,dict)]
    assert len(successes)==1 and successes[0]['completed_step_ids']==['step-docx-authoring']
    lease=successes[0]
    with db.get_db() as c:
        assert life.check_lease(c,guard.Actor(who.owner_id,'session-new'),'run-a',lease['lease_token'])
        row=c.execute('SELECT * FROM skill_runs').fetchone()
        assert row['state']=='queued' and row['session_id']=='session-new'
        assert json.loads(row['document'])['result']['text']=='SYNTHETIC PRIVATE CONTENT'


@pytest.mark.parametrize('skill',['translation','web-research','docx-authoring','hybrid-retrieval','speech-transcription'])
def test_unknown_model_or_effect_is_not_replayed(environment,skill):
    who=environment;add_run(who,state='running',steps=[{'id':'step-'+skill,'skill':skill,'status':'running'}])
    checkpoint(who);interrupted()
    with db.get_db() as c:
        with pytest.raises(life.LifecycleError,match='unknown_effect'):life.acquire_resume(c,who,'run-a',1)
        assert c.execute('SELECT state FROM skill_runs').fetchone()[0]=='unknown'


@pytest.mark.parametrize('column,value',[
    ('body','{"steps":[]}'),('body','not-json'),('schema_version','riwaq-skill-checkpoint-99'),
    ('binding_hash','0'*64),('document_hash','0'*64)])
def test_corrupt_or_incompatible_checkpoint_fails_closed(environment,column,value):
    who=environment;add_run(who);checkpoint(who);interrupted()
    with db.get_db() as c:c.execute(f'UPDATE skill_checkpoints SET {column}=?',(value,))
    with db.get_db() as c:
        with pytest.raises(life.LifecycleError):life.acquire_resume(c,who,'run-a',1)
        assert c.execute('SELECT state FROM skill_runs').fetchone()[0]=='unknown'


def test_checkpoint_cas_binding_and_session_revocation(environment):
    who=environment;add_run(who);checkpoint(who)
    with db.get_db() as c:
        with pytest.raises(life.LifecycleError,match='revision_conflict'):life.save_checkpoint(c,who,'run-a',0)
        assert life.save_checkpoint(c,who,'run-a',1)['revision']==2
        c.execute("UPDATE skill_runs SET request_hash=?,state='unknown'",('b'*64,))
    with db.get_db() as c:
        with pytest.raises(life.LifecycleError):life.acquire_resume(c,who,'run-a',2)
        c.execute("UPDATE local_auth_sessions SET revoked_at=? WHERE id=?",(time.time(),who.session_id))
    with db.get_db() as c:
        with pytest.raises(guard.Denied):life.save_checkpoint(c,who,'run-a',2)


def test_expired_lease_cannot_write_or_renew_and_cancel_invalidates(environment,monkeypatch):
    who=environment;add_run(who);checkpoint(who);interrupted()
    with db.get_db() as c:lease=life.acquire_resume(c,who,'run-a',1,ttl_seconds=1)
    with db.get_db() as c:
        c.execute('UPDATE skill_checkpoints SET lease_expires=?',(time.time()-1,))
        with pytest.raises(life.LifecycleError,match='lease_invalid'):life.renew_lease(c,who,'run-a',lease['lease_token'])
        with pytest.raises(life.LifecycleError,match='lease_invalid'):life.check_lease(c,who,'run-a','wrong-token')
        assert life.cancel_state(c,who,'run-a')['changed']
        with pytest.raises(life.LifecycleError):life.check_lease(c,who,'run-a',lease['lease_token'])


def test_cancel_preserves_completed_output_and_unknown_usage(environment):
    who=environment;add_run(who,state='running',steps=[
        {'id':'step-docx-authoring','skill':'docx-authoring','status':'completed','output_refs':['result.docx']},
        {'id':'step-translation','skill':'translation','status':'running'}])
    setup_budget(who);r=reserve(who,amount=6);unstarted=reserve(who,amount=2,key='not-started')
    with db.get_db() as c:
        c.execute('INSERT INTO skill_artifacts VALUES(?,?,?)',('run-a','result.docx',b'synthetic'))
        assert life.claim_reservation(c,who,r['id'])
        result=life.cancel_state(c,who,'run-a')
        assert result['status']=='cancelling' and result['changed']
        assert not life.cancel_state(c,who,'run-a')['changed']
        doc=json.loads(c.execute('SELECT document FROM skill_runs').fetchone()[0])
        assert [s['status'] for s in doc['steps']]==['completed','cancelling']
        assert not doc['receipt']['worker_stop_confirmed']
        assert c.execute('SELECT count(*) FROM skill_artifacts').fetchone()[0]==1
        status=life.budget_status(c,who,'project-a','local-steps')
        assert status['held']==6 and status['available']==4 and status['unknown_count']==1
        assert doc['receipt']['monetary_cost'] is None
        with pytest.raises(life.LifecycleError,match='worker_stop_not_confirmed'):
            life.acknowledge_cancellation(c,who,'run-a',worker_stopped=False)
        assert life.acknowledge_cancellation(c,who,'run-a',worker_stopped=True)['status']=='cancelled'
        doc=json.loads(c.execute('SELECT document FROM skill_runs').fetchone()[0])
        assert doc['receipt']['worker_stop_confirmed'] and doc['receipt']['usage_unresolved']
        assert [s['status'] for s in doc['steps']]==['completed','cancelled']


def test_closed_store_never_allows_or_fabricates_success(environment):
    who=environment;add_run(who);setup_budget(who)
    with db.get_db() as c:pass
    with pytest.raises(sqlite3.ProgrammingError):life.reserve_budget(c,who,'run-a','local-steps','first',1)


def test_completion_review_needs_real_criteria_and_does_not_grade_semantics():
    assert life.completion_review({'status':'completed','steps':[],'criteria':[]})['status']=='needs_review'
    doc={'status':'completed','steps':[{'id':'s1','status':'completed'}],
        'criteria':[{'id':'c1','status':'unverified'}],'receipt':{'verification':'verified'}}
    result=life.completion_review(doc)
    assert result['status']=='needs_review' and result['gaps'][0]['id']=='c1'
    doc['criteria'][0]['status']='passed'
    result=life.completion_review(doc)
    assert result['status']=='verified_contract_only' and result['semantic_quality']=='not_assessed'
    assert result['monetary_cost'] is None


def test_completion_review_rejects_missing_corrupt_or_unverified_artifact():
    doc={'status':'completed','steps':[{'id':'s1','status':'completed','output_refs':['answer.docx']}],
        'criteria':[{'id':'c1','status':'passed'}],'receipt':{'verification':'verified'},'result':{'artifacts':[]}}
    assert life.completion_review(doc)['gaps'][0]['state']=='missing_from_result'
    doc['result']['artifacts']=[{'name':'answer.docx'}]
    assert life.completion_review(doc)['status']=='needs_review'
    assert life.completion_review(doc,{'answer.docx':False})['status']=='needs_review'
    assert life.completion_review(doc,{'answer.docx':True})['status']=='verified_contract_only'


def test_release_lease_after_terminal_write_does_not_clear_another_token(environment):
    who=environment;add_run(who);checkpoint(who);interrupted()
    with db.get_db() as c:
        lease=life.acquire_resume(c,who,'run-a',1)
        c.execute("UPDATE skill_runs SET state='completed' WHERE id='run-a'")
        with pytest.raises(life.LifecycleError):life.release_lease(c,who,'run-a','not-the-token')
        life.release_lease(c,who,'run-a',lease['lease_token'])
        assert c.execute('SELECT lease_hash FROM skill_checkpoints').fetchone()[0] is None


@pytest.mark.parametrize('where',['document','receipt','step'])
def test_restart_never_turns_interrupted_cancellation_into_resume(environment,where):
    who=environment;add_run(who)
    with db.get_db() as c:
        doc=json.loads(c.execute('SELECT document FROM skill_runs').fetchone()[0])
        if where=='document':doc['status']='cancelling'
        elif where=='receipt':doc['receipt']={'status':'cancelling'}
        else:doc['steps'][0]['status']='cancelling'
        c.execute('UPDATE skill_runs SET document=?',(json.dumps(doc),))
        life.save_checkpoint(c,who,'run-a',0)
        c.execute("UPDATE skill_runs SET state='unknown'")
    with db.get_db() as c:
        with pytest.raises(life.LifecycleError,match='cancellation_cannot_resume'):life.acquire_resume(c,who,'run-a',1)


def test_completion_review_checks_requested_outputs_and_current_sources():
    doc={'status':'completed','intent':{'requestedOutputs':['docx','wav']},
        'steps':[{'id':'s1','status':'completed'}],'criteria':[{'id':'c1','status':'passed'}],
        'receipt':{'verification':'verified'},'result':{'artifacts':[{'name':'report.docx'}],
            'source_refs':[{'id':'synthetic-source'}]}}
    result=life.completion_review(doc,{'report.docx':True})
    assert {'kind':'requested_output','id':'wav','state':'missing'} in result['gaps']
    assert {'kind':'sources','id':None,'state':'not_rechecked'} in result['gaps']
    doc['result']['artifacts'].append({'name':'voice.wav'})
    assert life.completion_review(doc,{'report.docx':True,'voice.wav':True},source_checks=True)['status']=='verified_contract_only'


@pytest.mark.parametrize('change',['text','policy','deleted'])
def test_changed_private_source_blocks_checkpoint_resume(environment,change):
    who=environment;add_run(who)
    with db.get_db() as c:
        c.executescript('''
        CREATE TABLE knowledge_files(id TEXT PRIMARY KEY,project_id TEXT,sha256 TEXT);
        CREATE TABLE knowledge_chunks(id TEXT,project_id TEXT,file_id TEXT,page INTEGER,line_start INTEGER,line_end INTEGER,text TEXT);
        CREATE TABLE guard_file_policies(file_id TEXT PRIMARY KEY,project_id TEXT,owner_id TEXT,sensitivity TEXT,
          trust_origin TEXT,destinations TEXT,retention TEXT,revision INTEGER,revoked INTEGER);
        INSERT INTO knowledge_files VALUES('file-a','project-a','synthetic-sha');
        INSERT INTO knowledge_chunks VALUES('chunk-a','project-a','file-a',1,1,1,'synthetic source');
        INSERT INTO guard_file_policies VALUES('file-a','project-a','owner-a','private','uploaded','["local:ollama"]','until_deleted',1,0);
        ''')
        snapshot,_,_=guard.resource(c,who,'project-a','file-a')
        c.execute('UPDATE skill_runs SET resources=?',(json.dumps([snapshot]),))
        life.save_checkpoint(c,who,'run-a',0)
        c.execute("UPDATE skill_runs SET state='unknown'")
        if change=='text':c.execute("UPDATE knowledge_chunks SET text='changed source'")
        elif change=='policy':c.execute('UPDATE guard_file_policies SET revoked=1')
        else:c.execute('DELETE FROM knowledge_files')
    with db.get_db() as c:
        with pytest.raises((life.LifecycleError,guard.Denied)):
            life.acquire_resume(c,who,'run-a',1)
        assert c.execute('SELECT state FROM skill_runs').fetchone()[0]=='unknown'


def test_budget_and_checkpoint_survive_real_process_restart(environment):
    who=environment;add_run(who);setup_budget(who);r=reserve(who,amount=7)
    with db.get_db() as c:
        life.claim_reservation(c,who,r['id'])
        life.settle_budget(c,who,r['id'],None)
        life.save_checkpoint(c,who,'run-a',0)
        c.execute("UPDATE skill_runs SET state='unknown'")
    # A separate interpreter reads the same temporary SQLite file. It prints
    # only synthetic counters/status, never document content or lease tokens.
    source="""import json
from backend import db,guard,skill_lifecycle as life
with db.get_db() as c:
    who=guard.Actor('owner-a','session-new')
    status=life.budget_status(c,who,'project-a','local-steps')
    claim=life.acquire_resume(c,who,'run-a',1)
    print(json.dumps({'held':status['held'],'actual_known':status['actual_known'],'revision':claim['revision']}))
"""
    child=subprocess.run([sys.executable,'-X','utf8','-B','-c',source],capture_output=True,text=True,timeout=15,check=True)
    assert json.loads(child.stdout)=={'held':7,'actual_known':False,'revision':1}
    with db.get_db() as c:
        assert c.execute('SELECT state FROM skill_runs').fetchone()[0]=='queued'
