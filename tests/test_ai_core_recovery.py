"""Real SQLite/artifact generation, injected deadline only; no external effect."""
import json
import pytest
from backend import skills,skill_artifacts
from backend.db import get_db
from backend.skill_core import SkillRequest
from tests.test_skill_runtime_security import sandbox
from tests.test_skill_batch05_execution import expire

@pytest.mark.asyncio
async def test_deadline_keeps_committed_output_and_resume_does_not_rebuild_it(sandbox,monkeypatch):
    original=skill_artifacts.build_artifact;calls=[]
    def deadline_on_second(kind,payload):
        calls.append(kind)
        result=original(kind,payload)
        if len(calls)==2:
            with get_db() as c:rid=c.execute('SELECT id FROM skill_runs').fetchone()[0]
            expire(rid)
        return result
    monkeypatch.setattr(skill_artifacts,'build_artifact',deadline_on_second)
    body=SkillRequest(request='أنشئ مستندًا وعرضًا',requestedSkills=['docx-authoring','slide-authoring'],
        inputs={'artifacts':[{'kind':'docx','payload':{'title':'Test','paragraphs':[{'text':'First result 20'}]}},
                             {'kind':'pptx','payload':{'title':'Test','slides':[{'title':'Result','body':['Second result 20']} ]}}]},
        idempotencyKey='core-preserve-001')
    created=await skills.create_run('a',body);rid=created['id'];await skills.ACTIVE[rid]
    current=skills.get_run('a',rid)
    assert current['status']=='unknown' and current['result']['checkpoint']['resumable']
    assert [a['name'] for a in current['result']['artifacts']]==['riwaq-document.docx']
    with get_db() as c:
        saved=bytes(c.execute('SELECT content FROM skill_artifacts WHERE run_id=?',(rid,)).fetchone()[0])
        assert c.execute('SELECT count(*) FROM skill_artifacts WHERE run_id=?',(rid,)).fetchone()[0]==1
    assert skills.download('a',rid,'riwaq-document.docx').body==saved
    await skills.resume_run('a',rid,skills.ResumeRequest(expected_revision=current['result']['checkpoint']['revision']))
    await skills.ACTIVE[rid]
    result=skills.get_run('a',rid)
    assert result['status']=='completed',result
    assert calls==['docx','pptx','pptx']
    assert skills.download('a',rid,'riwaq-document.docx').body==saved
    assert len(result['result']['artifacts'])==2

@pytest.mark.asyncio
async def test_retention_never_overrides_revoked_access(sandbox,monkeypatch):
    from tests.test_skill_runtime_security import request,finish
    rid=await finish(request('core-revocation-001'))
    with get_db() as c:
        doc=json.loads(c.execute('SELECT document FROM skill_runs WHERE id=?',(rid,)).fetchone()[0])
        doc['status']='running'
        c.execute("UPDATE skill_runs SET state='running',document=? WHERE id=?",(json.dumps(doc),rid))
        c.execute('UPDATE local_auth_sessions SET revoked_at=1')
    skills.fail(rid,'failed','Task deadline reached',preserve_checkpoint=True)
    with get_db() as c:
        assert c.execute('SELECT count(*) FROM skill_artifacts WHERE run_id=?',(rid,)).fetchone()[0]==0
        assert c.execute('SELECT state FROM skill_runs WHERE id=?',(rid,)).fetchone()[0]=='failed'
