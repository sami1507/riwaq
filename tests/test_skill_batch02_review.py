"""Independent regressions, synthetic SQLite and controlled worker; no model."""
import asyncio
import json
import threading

import pytest

from backend import db, guard, skills, skill_operations, skill_lifecycle as life
from backend.skill_core import SkillRequest
from test_skill_batch02_lifecycle import environment, add_run, checkpoint, interrupted, setup_budget
from test_app import client
from test_skills import pid, wait


def add_payload_column():
    with db.get_db() as c:c.execute("ALTER TABLE skill_runs ADD COLUMN payload TEXT NOT NULL DEFAULT '{}'")


def test_second_cancel_waits_for_actual_worker_cleanup():
    started=threading.Event();release=threading.Event();finished=threading.Event()
    def worker(stop):
        started.set()
        # A bounded synthetic cleanup phase, representing a subprocess join.
        release.wait(2)
        finished.set()
        return 'synthetic'
    async def scenario():
        task=asyncio.create_task(skill_operations.stopped_thread(worker,lambda:None))
        try:
            for _ in range(100):
                if started.is_set():break
                await asyncio.sleep(.01)
            assert started.is_set()
            task.cancel();await asyncio.sleep(.02)
            assert not task.done()
            task.cancel();await asyncio.sleep(.02)
            assert not task.done(), 'Second cancellation acknowledged before the actual worker stopped'
        finally:
            release.set()
            await asyncio.gather(task,return_exceptions=True)
            for _ in range(100):
                if finished.is_set():break
                await asyncio.sleep(.01)
        assert finished.is_set()
    asyncio.run(scenario())


def test_new_resume_lease_fences_older_no_token_progress(environment):
    who=environment;add_run(who);add_payload_column();checkpoint(who);interrupted()
    with db.get_db() as c:
        life.acquire_resume(c,who,'run-a',1)
        doc=json.loads(c.execute('SELECT document FROM skill_runs').fetchone()[0])
    doc['result']['text']='synthetic stale worker output'
    with pytest.raises((life.LifecycleError,asyncio.CancelledError)):
        skills.progress('run-a',doc)
    with db.get_db() as c:
        row=c.execute('SELECT state,document FROM skill_runs').fetchone()
        assert row['state']=='queued'
        assert json.loads(row['document'])['result']['text']=='SYNTHETIC PRIVATE CONTENT'


def test_stale_worker_failure_cannot_erase_new_resume(environment):
    who=environment;add_run(who);add_payload_column();checkpoint(who);interrupted()
    with db.get_db() as c:life.acquire_resume(c,who,'run-a',1)
    skills.fail('run-a','failed','synthetic obsolete worker exception')
    with db.get_db() as c:
        row=c.execute('SELECT state,document FROM skill_runs').fetchone()
        assert row['state']=='queued', 'An older worker overwrote a newly leased task'
        assert json.loads(row['document'])['result']['text']=='SYNTHETIC PRIVATE CONTENT'


def test_resume_keeps_paragraph_to_source_mapping_for_editable_slides(environment,monkeypatch):
    who=environment
    steps=[{'id':'step-pdf-reading','skill':'pdf-reading','status':'completed','depends_on':[]},
        {'id':'step-translation','skill':'translation','status':'completed','depends_on':['step-pdf-reading']},
        {'id':'step-slide-authoring','skill':'slide-authoring','status':'pending','depends_on':['step-translation']}]
    add_run(who,steps=steps);add_payload_column()
    with db.get_db() as c:
        # This case executes real artifact persistence, unlike the minimal lifecycle
        # fixture. Use the production table contract and require actual completion.
        c.executescript('''DROP TABLE skill_artifacts;
          CREATE TABLE skill_artifacts(run_id TEXT,name TEXT,mime TEXT,sha256 TEXT,content BLOB,PRIMARY KEY(run_id,name));
          ALTER TABLE skill_runs ADD COLUMN created_at TEXT NOT NULL DEFAULT '2026-11-16';''')
        from backend.skill_files import init_files
        init_files(c)
        from backend import skill_execution
        skill_execution.init(c)
        c.execute('UPDATE skill_runs SET created_at=?',(db.now(),))
        c.executescript('''
        CREATE TABLE knowledge_files(id TEXT PRIMARY KEY,project_id TEXT,sha256 TEXT);
        CREATE TABLE knowledge_chunks(id TEXT,project_id TEXT,file_id TEXT,page INTEGER,line_start INTEGER,line_end INTEGER,text TEXT);
        CREATE TABLE guard_file_policies(file_id TEXT PRIMARY KEY,project_id TEXT,owner_id TEXT,sensitivity TEXT,
          trust_origin TEXT,destinations TEXT,retention TEXT,revision INTEGER,revoked INTEGER);
        INSERT INTO knowledge_files VALUES('file-a','project-a','synthetic-sha');
        INSERT INTO knowledge_chunks VALUES('chunk-a','project-a','file-a',1,1,2,'First paragraph\n\nSecond paragraph');
        INSERT INTO knowledge_chunks VALUES('chunk-b','project-a','file-a',2,3,3,'Third paragraph');
        INSERT INTO guard_file_policies VALUES('file-a','project-a','owner-a','private','uploaded','["local:ollama"]','until_deleted',1,0);
        ''')
        guard.init(c)
        snapshot,_,_=guard.resource(c,who,'project-a','file-a')
        refs=[{'id':'chunk-a','file_id':'file-a','page':1,'line_start':1,'line_end':2},
            {'id':'chunk-b','file_id':'file-a','page':2,'line_start':3,'line_end':3}]
        doc=json.loads(c.execute('SELECT document FROM skill_runs').fetchone()[0])
        doc['intent']={'intent':['translate','create']}
        doc['criteria']=[{'criterionId':'scope','status':'unverified'}]
        doc['result'].update(text='First paragraph\n\nSecond paragraph\n\nThird paragraph',
            source_refs=refs,paragraph_refs=[refs[0],refs[0],refs[1]],evidence=[])
        body=SkillRequest(request='Translate the PDF and create slides',requestedSkills=['pdf-reading','translation','slide-authoring'],
            inputs={'documentIds':['file-a'],'targetLanguage':'en'},idempotencyKey='synthetic-resume-map')
        c.execute('UPDATE skill_runs SET document=?,payload=?,resources=?',(json.dumps(doc),body.model_dump_json(),json.dumps([snapshot])))
        life.save_checkpoint(c,who,'run-a',0)
        c.execute("UPDATE skill_runs SET state='unknown'")
        lease=life.acquire_resume(c,who,'run-a',1)
        # The product resume endpoint initializes the same execution deadline
        # after accepting the checkpoint/lease; this fixture calls the helper.
        skill_execution.resume_deadline(c,c.execute('SELECT * FROM skill_runs').fetchone())
        life.ensure_budget(c,who,'project-a','local-steps','work_units',200)
        life.reserve_budget(c,who,'run-a','local-steps','resume-1',1)
    from backend import skill_artifacts
    original=skill_artifacts.build_artifact;captured=[]
    def capture(kind,payload):
        captured.append(payload)
        return original(kind,payload)
    monkeypatch.setattr(skill_artifacts,'build_artifact',capture)
    asyncio.run(skills.execute('run-a',lease['lease_token']))
    with db.get_db() as c:assert c.execute('SELECT state FROM skill_runs WHERE id=?',('run-a',)).fetchone()[0]=='completed'
    assert captured, 'The resumed operation failed before it could verify the slide data flow'
    labels=[slide.get('source_labels',[]) for slide in captured[0]['slides']]
    assert 'evidence chunk-a' in labels[1][0], 'Second paragraph was attributed to the next page after resume'
    assert 'evidence chunk-b' in labels[2][0]


def test_real_api_resume_preserves_completed_artifact_without_rewriting(client):
    """Simulated persisted interruption, real HTTP/SQLite/writer; not a crash."""
    project=pid(client)
    response=client.post(f'/api/projects/{project}/skill-runs',json={
        'request':'Create a Word file from this explicit text.',
        'requestedSkills':['intent-routing','docx-authoring'],
        'inputs':{'text':'Synthetic retained artifact. العربية English עברית.'},
        'idempotencyKey':'batch02-real-resume-artifact'})
    assert response.status_code==202,response.text
    run_id=response.json()['id'];initial=wait(client,project,run_id)
    assert initial['status']=='completed'
    artifact=initial['result']['artifacts'][0]
    download=f'/api/projects/{project}/skill-runs/{run_id}/artifacts/{artifact["name"]}'
    original=client.get(download).content
    with db.get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM skill_runs WHERE id=?',(run_id,)).fetchone()
        who=guard.Actor(row['owner_id'],row['session_id'])
        doc=json.loads(row['document'])
        completed=next(s.copy() for s in doc['steps'] if s['skill']=='docx-authoring')
        # The controlled checkpoint needs only a safe deterministic read rerun.
        next(s for s in doc['steps'] if s['skill']=='intent-routing')['status']='running'
        doc['status']='running';doc['receipt']['status']='running'
        c.execute("UPDATE skill_runs SET document=?,state='unknown' WHERE id=?",(json.dumps(doc),run_id))
        revision=c.execute('SELECT revision FROM skill_checkpoints WHERE run_id=?',(run_id,)).fetchone()[0]
        cp=life.save_checkpoint(c,who,run_id,revision)
    current=client.get(f'/api/projects/{project}/skill-runs/{run_id}').json()
    assert current['result']['checkpoint']['resumable']
    resumed=client.post(f'/api/projects/{project}/skill-runs/{run_id}/resume',json={'expected_revision':cp['revision']})
    assert resumed.status_code==202,resumed.text
    final=wait(client,project,run_id)
    assert final['status']=='completed',final.get('error')
    assert next(s for s in final['steps'] if s['skill']=='docx-authoring')==completed
    assert final['result']['artifacts']==[artifact]
    assert client.get(download).content==original
    assert client.post(f'/api/projects/{project}/skill-runs/{run_id}/resume',json={'expected_revision':cp['revision']}).status_code==409
    with db.get_db() as c:
        assert c.execute('SELECT count(*) FROM skill_artifacts WHERE run_id=?',(run_id,)).fetchone()[0]==1
