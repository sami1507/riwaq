"""Synthetic project-memory acceptance. Model race output is explicitly mocked."""
import threading

from fastapi.testclient import TestClient

from backend import app as service, context_lineage, db, local_auth, providers, voice
from test_project_memory_versions import client, project, create, correction


def test_memory_lineage_rejects_revoked_project_owner(client):
    pid=project(client);item=create(client,pid,'SYNTHETIC_REVOKED_MEMORY')
    with db.get_db() as conn:
        links=context_lineage.memory_links(conn,pid,[item])
        assert context_lineage.authorized(conn,pid,links)
        conn.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?',('synthetic_other_owner',pid))
        assert not context_lineage.authorized(conn,pid,links)
        assert context_lineage.memory_links(conn,pid,[item])==[context_lineage.BLOCKED]
    assert client.get('/api/memories?project_id='+pid).status_code==404


def test_missing_project_policy_fails_closed(client):
    pid=project(client);item=create(client,pid)
    with db.get_db() as conn:
        links=context_lineage.memory_links(conn,pid,[item])
        conn.execute('DELETE FROM guard_projects WHERE project_id=?',(pid,))
        assert not context_lineage.authorized(conn,pid,links)
        assert context_lineage.memory_links(conn,pid,[item])==[context_lineage.BLOCKED]


def test_active_voice_turn_stops_and_clears_after_memory_project_revocation(client,monkeypatch):
    pid=project(client);item=create(client,pid)
    with db.get_db() as conn:
        links=context_lineage.memory_links(conn,pid,[item])
        conn.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?',('synthetic_other_owner',pid))
    stop=threading.Event();sid='synthetic-voice-memory-revocation'
    turn={'_links':links,'epoch':4,'reply':'synthetic private text','spoken_text':'synthetic voice',
          'citations':[],'audio_ready':True,'_audio':b'synthetic not playable audio','state':'speaking'}
    # Remove the controlled in-memory turn before the real app lifespan shuts down.
    with monkeypatch.context() as state:
        state.setitem(voice.ACTIVE,sid,{'epoch':4,'stop':stop})
        assert voice.check_links({'id':sid,'project_id':pid},turn) is False
        assert stop.is_set() and turn['state']=='revoked'
        assert turn['reply']==turn['spoken_text']=='' and turn['_audio']==b'' and not turn['audio_ready']


def test_mock_late_generation_withdrawn_after_project_access_changes(client,monkeypatch):
    pid=project(client);create(client,pid,'SYNTHETIC_LATE_PRIVATE_MEMORY')
    cid=client.post('/api/conversations',json={'project_id':pid}).json()['id']
    async def validate(*args):pass
    async def stream(*args):
        yield {'content':'SYNTHETIC_DERIVED_PRIVATE_ANSWER'}
        yield {'finish_reason':'stop'}
        with db.get_db() as conn:
            conn.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?',('synthetic_other_owner',pid))
    monkeypatch.setattr(providers,'validate_provider',validate)
    monkeypatch.setattr(providers,'stream',stream)
    response=client.post('/api/conversations/'+cid+'/generate',json={'content':'What is the project fact?'})
    assert response.status_code==200
    with db.get_db() as conn:
        result=conn.execute("SELECT content,status FROM messages WHERE conversation_id=? AND role='assistant' ORDER BY rowid DESC",(cid,)).fetchone()
        assert result['status']=='error' and result['content']==''


def test_same_named_projects_and_corrected_then_deleted_lineage(client):
    first=project(client)
    other=client.post('/api/projects',json={'name':'Same synthetic name'}).json()['id']
    item=create(client,first,'النسخة 1 — version 1 — גרסה 1')
    with db.get_db() as conn:
        old=context_lineage.memory_links(conn,first,[item])
        assert context_lineage.memory_links(conn,other,[item])==[context_lineage.BLOCKED]
        assert not context_lineage.authorized(conn,other,old)
    revised=correction(client,item,'النسخة 2 — version 2 — גרסה 2')
    assert revised.status_code==200
    assert correction(client,item,'stale request').status_code==409
    with db.get_db() as conn:
        assert not context_lineage.authorized(conn,first,old)
        current=context_lineage.memory_links(conn,first,[revised.json()])
        assert context_lineage.authorized(conn,first,current)
    assert client.delete('/api/memories/'+item['id']+'?expected_version=2').status_code==200
    with db.get_db() as conn:
        assert not context_lineage.authorized(conn,first,current)
        assert conn.execute('SELECT count(*) FROM memory_revisions WHERE memory_id=?',(item['id'],)).fetchone()[0]==0
    assert correction(client,revised.json(),'late result cannot restore').status_code==404


def test_project_memory_survives_app_reopen_without_enabling_personal(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY',str(tmp_path/'no-registry.json'))
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as first:
        assert first.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
        pid=project(first);item=create(first,pid,'SYNTHETIC_REOPEN_PROJECT')
        assert first.post('/api/memories',json={'content':'personal preference'}).status_code==423
    # A new application lifespan and a fresh authenticated HTTP client.
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as reopened:
        assert reopened.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
        rows=reopened.get('/api/memories?project_id='+pid).json()
        assert len(rows)==1 and rows[0]['id']==item['id'] and rows[0]['version']==1
        assert correction(reopened,rows[0],'SYNTHETIC_REOPEN_CORRECTED').json()['version']==2
        assert reopened.post('/api/memories',json={'content':'still blocked'}).status_code==423
