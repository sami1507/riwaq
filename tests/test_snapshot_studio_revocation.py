"""Actual authenticated delivery boundaries for cached synthetic source text."""
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import app, local_auth
from backend.db import get_db, now, uid


@pytest.fixture
def source_scope(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY', str(tmp_path/'absent'))
    with TestClient(app.app, headers={'X-Riwaq-Client':'local'}) as client:
        assert client.post('/api/auth/login', json={'pairing_code':local_auth.issue_pairing_code()}).status_code == 200
        pid=client.post('/api/projects',json={'name':'Synthetic source boundary'}).json()['id']
        base=f'/api/projects/{pid}'
        uploaded=client.post(base+'/files',files={'file':('PRIVATE_FILE_NAME.txt',b'SYNTHETIC_WITHDRAWN_CONTENT 731')})
        assert uploaded.status_code==201
        fid=uploaded.json()['id']
        cid=client.post('/api/conversations',json={'project_id':pid,'title':'Source conversation'}).json()['id']
        with get_db() as conn:
            chunk=dict(conn.execute('SELECT * FROM knowledge_chunks WHERE file_id=?',(fid,)).fetchone())
            chunk['filename']='PRIVATE_FILE_NAME.txt'
            mid=uid();conn.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',
                (mid,cid,'assistant','SYNTHETIC_WITHDRAWN_CONTENT 731 [S1]','complete','fixture',now(),json.dumps([chunk])))
        glossary=client.post(base+'/glossary',json={'term':'SYNTHETIC_WITHDRAWN_TERM','definition':'SYNTHETIC_WITHDRAWN_CONTENT', 'source_message_id':mid})
        assert glossary.status_code==201,glossary.text
        artifact=client.post(base+f'/answers/{mid}/artifact',json={'title':'SYNTHETIC_WITHDRAWN_ARTIFACT'})
        assert artifact.status_code==201
        independent=client.post(base+'/artifacts',json={'title':'Independent note','content':'KEEP_INDEPENDENT_USER_TEXT'})
        assert independent.status_code==201
        yield client,pid,base,fid,mid,glossary.json()['id'],artifact.json()['artifact']['id'],independent.json()['id']


def revoke(fid):
    with get_db() as conn:
        conn.execute('UPDATE guard_file_policies SET revoked=1,revision=revision+1 WHERE file_id=?',(fid,))


def test_snapshot_excludes_withdrawn_source_and_derived_content(source_scope):
    client,pid,base,fid,mid,gid,aid,independent=source_scope
    revoke(fid)
    result=client.get(base+'/snapshot')
    target=Path('reports/closure-20261116/project-authority');target.mkdir(parents=True,exist_ok=True)
    leaked='SYNTHETIC_WITHDRAWN' in result.text or 'PRIVATE_FILE_NAME' in result.text
    (target/('snapshot-before.json' if leaked else 'snapshot-after.json')).write_text(json.dumps({'status':result.status_code,'private_fixture_leaked':leaked,'independent_present':'KEEP_INDEPENDENT_USER_TEXT' in result.text}))
    assert result.status_code==200,result.text
    assert not leaked
    assert 'KEEP_INDEPENDENT_USER_TEXT' in result.text


def test_studio_denies_recapture_history_and_cached_artifact(source_scope):
    client,pid,base,fid,mid,gid,aid,independent=source_scope
    revoke(fid)
    results={
        'recapture':client.post(base+f'/answers/{mid}/artifact',json={'title':'Different output'}),
        'glossary':client.get(base+f'/glossary/{gid}'),
        'history':client.get(base+f'/glossary/{gid}/versions'),
        'provenance':client.get(base+f'/artifacts/{aid}/provenance'),
        'artifact':client.get(f'/api/artifacts/{aid}'),
        'artifact_history':client.get(f'/api/artifacts/{aid}/versions')}
    assert all(r.status_code in (403,404) for r in results.values()),{k:r.status_code for k,r in results.items()}
    for suffix in ('/glossary','/artifacts'):
        response=client.get(base+suffix)
        assert response.status_code==200 and 'SYNTHETIC_WITHDRAWN' not in response.text
    assert client.get(f'/api/artifacts/{independent}').status_code==200
