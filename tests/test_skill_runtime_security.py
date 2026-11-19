"""Independent executor security checks with a real temporary SQLite store.

Trusted principals are supplied at the handler boundary. These do not test HTTP
pairing/cookies, production data, a second hosted account, or model quality.
"""
import asyncio
import time

from fastapi import HTTPException
import pytest

from backend import guard, local_auth, skills
from backend.db import get_db
from backend.skill_core import SkillRequest


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    with get_db() as conn:
        conn.executescript('''
        CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,instructions TEXT,created_at TEXT);
        CREATE TABLE local_auth_owner(slot INTEGER PRIMARY KEY,owner_id TEXT,created_at REAL,paired_at REAL);
        CREATE TABLE local_auth_sessions(id TEXT PRIMARY KEY,token_hash TEXT,owner_id TEXT,created_at REAL,expires_at REAL,revoked_at REAL,last_seen REAL);
        CREATE TABLE knowledge_files(id TEXT PRIMARY KEY,project_id TEXT,filename TEXT,sha256 TEXT);
        CREATE TABLE knowledge_chunks(id TEXT PRIMARY KEY,file_id TEXT,project_id TEXT,page INTEGER,line_start INTEGER,line_end INTEGER,text TEXT);
        CREATE TABLE document_originals(file_id TEXT PRIMARY KEY,status TEXT);
        CREATE TABLE document_chunk_refs(chunk_id TEXT PRIMARY KEY,element_id TEXT,payload TEXT);
        INSERT INTO projects VALUES('a','same project label','','now');
        INSERT INTO projects VALUES('b','same project label','','now');
        INSERT INTO projects VALUES('foreign','same project label','','now');
        INSERT INTO local_auth_owner VALUES(1,'owner-a',0,0);
        ''')
        stamp=time.time()
        conn.execute('INSERT INTO local_auth_sessions VALUES(?,?,?,?,?,NULL,?)',('session-a','synthetic','owner-a',stamp,stamp+600,stamp))
        guard.init(conn)
        conn.execute("UPDATE guard_projects SET owner_id='owner-b' WHERE project_id='foreign'")
        conn.execute("INSERT INTO knowledge_files VALUES('source-a','a','synthetic.txt','synthetic-sha')")
        conn.execute("INSERT INTO knowledge_chunks VALUES('chunk-a','source-a','a',2,1,1,'SYNTHETIC_PRIVATE_MARKER 42')")
        skills.init_skills(conn)
    token=local_auth._principal.set({'owner_id':'owner-a','session_id':'session-a'})
    try:
        yield
    finally:
        local_auth._principal.reset(token)


def request(key='test-key-001',**overrides):
    return SkillRequest.model_validate({
        'request':'أنشئ مستندًا من البيانات المختارة', 'idempotencyKey':key,
        'requestedSkills':['docx-authoring'],
        'inputs':{'artifact':{'kind':'docx','payload':{'title':'Synthetic','paragraphs':[{'text':'SYNTHETIC_PRIVATE_MARKER 42'}]}}},
    }|overrides)


async def finish(body):
    created=await skills.create_run('a',body)
    task=skills.ACTIVE.get(created['id'])
    if task:
        await task
    return created['id']


@pytest.mark.asyncio
async def test_other_project_or_owner_cannot_read_execute_or_download(sandbox):
    rid=await finish(request())
    assert skills.get_run('a',rid)['status']=='completed'
    for call in [lambda:skills.get_run('b',rid),lambda:skills.download('b',rid,'riwaq-document.docx')]:
        with pytest.raises(HTTPException) as error: call()
        assert error.value.status_code==404
    for call in [lambda:skills.get_run('foreign',rid),lambda:skills.list_runs('foreign'),lambda:skills.download('foreign',rid,'riwaq-document.docx')]:
        with pytest.raises(HTTPException) as error: call()
        assert error.value.status_code==403
    with pytest.raises(HTTPException) as error:
        await skills.create_run('foreign',request('test-key-foreign'))
    assert error.value.status_code==403


@pytest.mark.asyncio
async def test_idempotency_reuses_persisted_result_and_rejects_changed_parameters(sandbox):
    body=request()
    rid=await finish(body)
    first=skills.download('a',rid,'riwaq-document.docx').body
    repeated=await skills.create_run('a',body)
    assert repeated['id']==rid
    assert skills.download('a',rid,'riwaq-document.docx').body==first
    with pytest.raises(HTTPException) as error:
        await skills.create_run('a',request(request='Different operation'))
    assert error.value.status_code==409
    with get_db() as conn:
        assert conn.execute('SELECT count(*) FROM skill_runs').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM skill_artifacts').fetchone()[0]==1


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation,expected',[
    ("UPDATE guard_file_policies SET revoked=1 WHERE file_id='source-a'",403),
    ("UPDATE guard_file_policies SET revision=revision+1 WHERE file_id='source-a'",409),
    ("UPDATE knowledge_chunks SET text='changed' WHERE id='chunk-a'",409),
    ("DELETE FROM knowledge_files WHERE id='source-a'",404),
])
async def test_revoked_or_changed_source_blocks_saved_output_and_listing(sandbox,mutation,expected):
    base=request().model_dump()
    base['inputs']['documentIds']=['source-a']
    rid=await finish(SkillRequest.model_validate(base))
    assert skills.download('a',rid,'riwaq-document.docx').status_code==200
    with get_db() as conn: conn.execute(mutation)
    for call in [lambda:skills.get_run('a',rid),lambda:skills.download('a',rid,'riwaq-document.docx')]:
        with pytest.raises(HTTPException) as error: call()
        assert error.value.status_code==expected
    assert skills.list_runs('a')==[]
    if expected==404:
        # Source deletion now physically removes the run and its stored outputs,
        # a stronger contract than the previous logical revocation-only denial.
        with get_db() as conn:
            assert conn.execute('SELECT count(*) FROM skill_runs').fetchone()[0]==0
            assert conn.execute('SELECT count(*) FROM skill_artifacts').fetchone()[0]==0


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation',[
    "UPDATE guard_file_policies SET revoked=1 WHERE file_id='source-a'",
    "UPDATE local_auth_sessions SET revoked_at=1 WHERE id='session-a'",
    "UPDATE guard_projects SET owner_id='owner-b' WHERE project_id='a'",
])
async def test_revocation_before_worker_claim_cannot_publish_late_output(sandbox,mutation):
    base=request().model_dump();base['inputs']['documentIds']=['source-a']
    created=await skills.create_run('a',SkillRequest.model_validate(base))
    with get_db() as conn: conn.execute(mutation)
    await skills.ACTIVE[created['id']]
    with get_db() as conn:
        assert conn.execute('SELECT state FROM skill_runs WHERE id=?',(created['id'],)).fetchone()[0]=='failed'
        assert conn.execute('SELECT count(*) FROM skill_artifacts').fetchone()[0]==0


@pytest.mark.asyncio
async def test_corrupt_persisted_artifact_does_not_download_as_success(sandbox):
    rid=await finish(request())
    with get_db() as conn:
        conn.execute('UPDATE skill_artifacts SET content=? WHERE run_id=?',(b'broken package',rid))
    with pytest.raises(HTTPException) as error:
        skills.download('a',rid,'riwaq-document.docx')
    assert error.value.status_code==409


@pytest.mark.asyncio
async def test_cancel_before_claim_does_not_leave_or_publish_artifacts(sandbox):
    created=await skills.create_run('a',request())
    task=skills.ACTIVE[created['id']]
    await skills.cancel('a',created['id'])
    await asyncio.gather(task,return_exceptions=True)
    assert skills.get_run('a',created['id'])['status']=='cancelled'
    with get_db() as conn:
        assert conn.execute('SELECT count(*) FROM skill_artifacts').fetchone()[0]==0


@pytest.mark.asyncio
async def test_untrusted_request_cannot_supply_actor_or_destination(sandbox):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        request(owner_id='owner-b')
    with pytest.raises(ValidationError):
        request(inputs={'destination':'https://attacker.invalid','text':'leak'})


@pytest.mark.asyncio
async def test_duplicate_worker_claim_publishes_only_one_artifact(sandbox):
    created=await skills.create_run('a',request())
    worker=skills.ACTIVE[created['id']]
    await asyncio.gather(worker,skills.execute(created['id']),skills.execute(created['id']))
    assert skills.get_run('a',created['id'])['status']=='completed'
    with get_db() as conn:
        assert conn.execute('SELECT count(*) FROM skill_artifacts').fetchone()[0]==1


@pytest.mark.asyncio
async def test_revocation_during_actual_artifact_build_blocks_result(sandbox,monkeypatch):
    # A timing barrier injects revocation while the real writer is in flight;
    # the generated file and permission checks themselves are not mocked.
    import threading
    from backend import skill_artifacts
    original=skill_artifacts.build_artifact
    entered=threading.Event();release=threading.Event()
    def delayed(kind,payload):
        entered.set()
        if not release.wait(5):
            raise RuntimeError('Test barrier timed out')
        return original(kind,payload)
    monkeypatch.setattr(skill_artifacts,'build_artifact',delayed)
    base=request().model_dump();base['inputs']['documentIds']=['source-a']
    created=await skills.create_run('a',SkillRequest.model_validate(base))
    worker=skills.ACTIVE[created['id']]
    try:
        assert await asyncio.to_thread(entered.wait,5)
        with get_db() as conn:
            conn.execute("UPDATE guard_file_policies SET revoked=1 WHERE file_id='source-a'")
    finally:
        release.set()
    await worker
    with get_db() as conn:
        assert conn.execute('SELECT state FROM skill_runs WHERE id=?',(created['id'],)).fetchone()[0]=='failed'
        assert conn.execute('SELECT count(*) FROM skill_artifacts').fetchone()[0]==0


def test_explicit_export_translation_read_order_is_normalized():
    from backend.skill_core import classify, select_skills, plan
    intent=classify('ترجم هذا PDF واعمله عرضًا')
    chosen=select_skills(intent,['slide-authoring','translation','pdf-reading'])
    assert chosen==['pdf-reading','translation','slide-authoring']
    steps=plan(intent,chosen)
    assert steps[0]['depends_on']==[]
    assert steps[1]['depends_on']==[steps[0]['id']]
    assert steps[2]['depends_on']==[steps[1]['id']]


@pytest.mark.asyncio
async def test_verification_without_server_run_is_blocked_not_verified(sandbox):
    rid=await finish(request(request='تحقق من النتيجة',requestedSkills=['tool-result-verification'],inputs={}))
    result=skills.get_run('a',rid)
    assert result['status']=='partial'
    assert result['steps'][0]['status']=='blocked'
    assert result['receipt']['verification']!='verified'


@pytest.mark.asyncio
async def test_plan_criteria_and_tool_selection_have_structured_outputs(sandbox):
    rid=await finish(request(request='ترجم النص وأنشئ مستند Word',requestedSkills=['task-decomposition','acceptance-criteria','tool-routing'],inputs={}))
    result=skills.get_run('a',rid)
    structured=result['result']['structured']
    assert result['status']=='completed'
    assert structured['task-decomposition']['executed'] is False
    assert [s['skill'] for s in structured['task-decomposition']['steps']]==['translation','docx-authoring']
    assert structured['acceptance-criteria']['evaluated'] is False
    assert all(c['requirement'] and c['verificationMethod'] for c in structured['acceptance-criteria']['criteria'])
    assert {t['tool_id'] for t in structured['tool-routing']['tools']}=={'local-translation','local-office-writer'}
    assert structured['tool-routing']['executed'] is False


@pytest.mark.asyncio
async def test_verification_receipt_inherits_source_revocation(sandbox):
    source_run=await finish(request(
        requestedSkills=['spreadsheet-creation'],
        inputs={'documentIds':['source-a'],'artifact':{'kind':'xlsx','payload':{
            'title':'Synthetic private totals','columns':[{'name':'Amount','type':'number'}],
            'rows':[[4187]],'totals':[0],
        }}},
    ))
    verifier=await finish(request('verify-key-001',request='تحقق من الإيصال السابق',requestedSkills=['tool-result-verification'],inputs={},previousRunId=source_run))
    receipt=skills.get_run('a',verifier)['result']['structured']['tool-result-verification']
    assert receipt['operation_id']==source_run
    assert any('4187' in evidence for evidence in receipt['evidence'])
    with get_db() as conn:
        conn.execute("UPDATE guard_file_policies SET revoked=1 WHERE file_id='source-a'")
    with pytest.raises(HTTPException) as error:
        skills.get_run('a',verifier)
    assert error.value.status_code in {403,409}


@pytest.mark.asyncio
async def test_deleting_verified_run_withholds_dependent_receipt(sandbox):
    source_run=await finish(request())
    verifier=await finish(request('verify-key-001',request='تحقق من الإيصال السابق',requestedSkills=['tool-result-verification'],inputs={},previousRunId=source_run))
    assert skills.get_run('a',verifier)['result']['structured']['tool-result-verification']['operation_id']==source_run
    await skills.delete_run('a',source_run)
    with pytest.raises(HTTPException) as error:
        skills.get_run('a',verifier)
    assert error.value.status_code==409
    assert skills.list_runs('a')==[]


@pytest.mark.asyncio
async def test_source_deletion_purges_transitively_derived_runs(sandbox):
    base=request().model_dump();base['inputs']['documentIds']=['source-a']
    source_run=await finish(SkillRequest.model_validate(base))
    verifier=await finish(request('verify-key-001',request='تحقق من الإيصال السابق',requestedSkills=['tool-result-verification'],inputs={},previousRunId=source_run))
    await finish(request('verify-key-002',request='تحقق من الإيصال السابق',requestedSkills=['tool-result-verification'],inputs={},previousRunId=verifier))
    with get_db() as conn:
        assert conn.execute('SELECT count(*) FROM skill_runs').fetchone()[0]==3
        conn.execute("DELETE FROM knowledge_files WHERE id='source-a'")
    with get_db() as conn:
        assert conn.execute('SELECT count(*) FROM skill_runs').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM skill_artifacts').fetchone()[0]==0

