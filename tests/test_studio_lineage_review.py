"""Independent review probes: real pairing/API/storage, synthetic source receipts."""
import json
from pathlib import Path
import pytest
from backend import db,context_lineage,facts,studio,snapshots,guard,local_auth,personal_memory,learning
from test_learning_api import client
from test_facts import public_source

def project(client):
    pid=client.post('/api/projects',json={'name':'Synthetic source receipt'}).json()['id']
    cid=client.post('/api/conversations',json={'project_id':pid}).json()['id']
    return pid,cid

def capture(client,pid,cid,links):
    with db.get_db() as conn:
        mid=db.uid()
        conn.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',(mid,cid,'assistant','SYNTHETIC_BOUND_DERIVATIVE','complete','fixture',db.now(),'[]'))
        context_lineage.save(conn,mid,links)
    artifact=client.post(f'/api/projects/{pid}/answers/{mid}/artifact',json={'title':'Derivative'})
    entry=client.post(f'/api/projects/{pid}/glossary',json={'term':'derived','definition':'SYNTHETIC_BOUND_DERIVATIVE','source_message_id':mid})
    assert artifact.status_code==entry.status_code==201,(artifact.text,entry.text)
    return mid,artifact.json()['artifact']['id'],entry.json()['id']

def hidden(client,pid,artifact,entry):
    assert client.get(f'/api/artifacts/{artifact}').status_code==404
    assert client.get(f'/api/projects/{pid}/glossary/{entry}').status_code==404
    exported=client.get(f'/api/projects/{pid}/snapshot')
    assert exported.status_code==200,exported.text
    assert 'SYNTHETIC_BOUND_DERIVATIVE' not in exported.text


def test_deleted_parent_message_does_not_erase_derived_public_source_revocation(client):
    pid=client.post('/api/projects',json={'name':'Synthetic downstream-source review'}).json()['id']
    cid=client.post('/api/conversations',json={'project_id':pid}).json()['id']
    selector,sid,_=public_source('SYNTHETIC_WITHDRAWN_REFERENCE')
    with db.get_db() as conn:
        source,status=facts.source_raw(conn,pid,'public_chunk',selector['chunk_id'])
        assert status=='current'
        source['scope']='public'
        links=context_lineage.source_links(conn,pid,[source])
        assert links and links!=[context_lineage.BLOCKED]
        mid=db.uid()
        # Real server lineage storage; this is not a claim of live model quality.
        conn.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',
          (mid,cid,'assistant','SYNTHETIC_WITHDRAWN_DERIVATIVE','complete','synthetic',db.now(),'[]'))
        context_lineage.save(conn,mid,links)
    created=client.post(f'/api/projects/{pid}/answers/{mid}/artifact',json={'title':'Saved derivative'})
    assert created.status_code==201,created.text
    aid=created.json()['artifact']['id']
    assert client.delete(f'/api/conversations/{cid}').status_code==200
    with db.get_db() as conn:
        conn.execute("UPDATE public_sources SET status='revoked' WHERE id=?",(sid,))
        assert facts.source_raw(conn,pid,'public_chunk',selector['chunk_id'])==(None,'revoked')
    result=client.get(f'/api/artifacts/{aid}')
    snapshot=client.get(f'/api/projects/{pid}/snapshot')
    observed={'artifact_status':result.status_code,'artifact_leaked':'SYNTHETIC_WITHDRAWN_DERIVATIVE' in result.text,
      'snapshot_status':snapshot.status_code,'snapshot_leaked':'SYNTHETIC_WITHDRAWN_DERIVATIVE' in snapshot.text}
    target=Path('reports/closure-20261116/authority-review');target.mkdir(parents=True,exist_ok=True)
    (target/('deleted-parent-before.json' if any(observed[x] for x in ('artifact_leaked','snapshot_leaked')) else 'deleted-parent-after.json')).write_text(json.dumps(observed,indent=2))
    assert result.status_code==404 and not observed['artifact_leaked'],observed
    assert not observed['snapshot_leaked'],observed


def test_project_memory_receipt_survives_parent_deletion_and_snapshot_import_is_inert(client):
    pid,cid=project(client)
    memory=client.post('/api/memories',json={'scope':'project','project_id':pid,'content':'Project fact for this test'}).json()
    with db.get_db() as conn:links=context_lineage.memory_links(conn,pid,[memory])
    _,artifact,entry=capture(client,pid,cid,links)
    exported=client.get(f'/api/projects/{pid}/snapshot');assert exported.status_code==200,exported.text
    body=exported.json();assert body['conversations'][0]['messages'][0]['context_dependencies']==links
    restored=client.post('/api/projects/import',json={'snapshot':body,'confirm':True});assert restored.status_code==201,restored.text
    restored_pid=restored.json()['id']
    with db.get_db() as conn:
        mids=conn.execute('SELECT m.id FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.project_id=?',(restored_pid,)).fetchall()
        assert mids and all(context_lineage.read(conn,m['id'])==[context_lineage.BLOCKED] for m in mids)
        assert not conn.execute('SELECT 1 FROM studio_artifact_dependencies WHERE project_id=?',(restored_pid,)).fetchone()
        assert not conn.execute('SELECT 1 FROM studio_entry_dependencies WHERE project_id=?',(restored_pid,)).fetchone()
    assert client.delete(f'/api/conversations/{cid}').status_code==200
    assert client.delete(f"/api/memories/{memory['id']}?expected_version={memory['version']}").status_code==200
    hidden(client,pid,artifact,entry)
    # Deleting a withdrawn object by ID must remain possible without revealing it.
    assert client.delete(f'/api/projects/{pid}/glossary/{entry}?expected_version=1').status_code==200


def test_personal_consent_receipt_survives_parent_deletion_under_simulated_future_admission(client,monkeypatch):
    # Only admission is simulated. The production Stage8 gate is unchanged;
    # real consent/version/storage/authorization logic runs below.
    monkeypatch.setattr(personal_memory,'require_admission',lambda c,a:personal_memory._actor(c,a))
    policy=client.get('/api/personal-memory/settings').json()
    enabled=client.patch('/api/personal-memory/settings',json={'enabled':True,'expected_version':policy['version']}).json()
    made=client.post('/api/personal-memory',json={'rule':{'kind':'language','value':'en'},'idempotency_key':'lineage-review-preference','consent_version':enabled['version']})
    assert made.status_code==200,made.text
    pid,cid=project(client)
    with db.get_db() as conn:
        session=conn.execute('SELECT * FROM local_auth_sessions WHERE revoked_at IS NULL').fetchone()
        actor=guard.Actor(session['owner_id'],session['id'])
        links=personal_memory.active_context(conn,actor,pid,cid,'hello')['bindings']
    assert links and links[0]['kind']=='personal_memory'
    _,artifact,entry=capture(client,pid,cid,links)
    exported=client.get(f'/api/projects/{pid}/snapshot');assert exported.status_code==200,exported.text
    assert exported.json()['conversations'][0]['messages'][0]['context_dependencies']==links
    assert client.delete(f'/api/conversations/{cid}').status_code==200
    disabled=client.patch('/api/personal-memory/settings',json={'enabled':False,'expected_version':enabled['version']})
    assert disabled.status_code==200,disabled.text
    hidden(client,pid,artifact,entry)


def test_backfill_only_copies_matching_live_parent_receipts(client):
    pid,cid=project(client)
    memory=client.post('/api/memories',json={'scope':'project','project_id':pid,'content':'Migration fixture'}).json()
    with db.get_db() as conn:links=context_lineage.memory_links(conn,pid,[memory])
    _,artifact,entry=capture(client,pid,cid,links)
    with db.get_db() as conn:
        conn.execute('DELETE FROM studio_artifact_dependencies WHERE artifact_id=?',(artifact,))
        conn.execute('DELETE FROM studio_entry_dependencies WHERE entry_id=?',(entry,))
        studio.init_studio(conn)
        assert json.loads(conn.execute('SELECT links FROM studio_artifact_dependencies WHERE artifact_id=?',(artifact,)).fetchone()[0])==links
        assert json.loads(conn.execute('SELECT links FROM studio_entry_dependencies WHERE entry_id=?',(entry,)).fetchone()[0])==links
    assert client.delete(f'/api/conversations/{cid}').status_code==200
    with db.get_db() as conn:
        # Simulate an older record whose parent was already absent at upgrade.
        conn.execute('DELETE FROM studio_entry_dependencies WHERE entry_id=?',(entry,))
        studio.init_studio(conn)
        assert not conn.execute('SELECT 1 FROM studio_entry_dependencies WHERE entry_id=?',(entry,)).fetchone()
        assert conn.execute('SELECT 1 FROM studio_artifact_dependencies WHERE artifact_id=?',(artifact,)).fetchone()


def test_snapshot_rechecks_public_source_after_serialization(client,monkeypatch):
    pid,cid=project(client);selector,sid,_=public_source('SYNTHETIC_REFERENCE_AT_EXPORT')
    with db.get_db() as conn:
        source,state=facts.source_raw(conn,pid,'public_chunk',selector['chunk_id']);source['scope']='public'
        links=context_lineage.source_links(conn,pid,[source])
    capture(client,pid,cid,links)
    validate=snapshots.Snapshot.model_validate
    def revoke_after_validation(value,*args,**kwargs):
        result=validate(value,*args,**kwargs)
        with db.get_db() as conn:conn.execute("UPDATE public_sources SET status='revoked' WHERE id=?",(sid,))
        return result
    monkeypatch.setattr(snapshots.Snapshot,'model_validate',revoke_after_validation)
    response=client.get(f'/api/projects/{pid}/snapshot')
    assert response.status_code==409,response.text
    assert 'SYNTHETIC_BOUND_DERIVATIVE' not in response.text


def test_snapshot_rechecks_memory_receipt_after_parent_deleted_and_serialization(client,monkeypatch):
    pid,cid=project(client)
    memory=client.post('/api/memories',json={'scope':'project','project_id':pid,'content':'Export-only source fixture'}).json()
    with db.get_db() as conn:links=context_lineage.memory_links(conn,pid,[memory])
    capture(client,pid,cid,links)
    assert client.delete(f'/api/conversations/{cid}').status_code==200
    validate=snapshots.Snapshot.model_validate
    def revoke_after_validation(value,*args,**kwargs):
        result=validate(value,*args,**kwargs)
        with db.get_db() as conn:conn.execute('DELETE FROM memories WHERE id=?',(memory['id'],))
        return result
    monkeypatch.setattr(snapshots.Snapshot,'model_validate',revoke_after_validation)
    response=client.get(f'/api/projects/{pid}/snapshot')
    assert response.status_code==409,response.text
    assert 'SYNTHETIC_BOUND_DERIVATIVE' not in response.text


def test_learning_consent_binding_is_retained_after_derived_conversation_deleted(client):
    from test_learning_api import setup,feedback,checked,adopt
    pid,source_cid,base,ref=setup(client)
    row,_=feedback(client,base,source_cid,ref)
    verified=checked(client,base,row)
    adopt(client,base,source_cid,verified)
    cid=client.post('/api/conversations',json={'project_id':pid}).json()['id']
    with db.get_db() as conn:
        session=conn.execute('SELECT * FROM local_auth_sessions WHERE revoked_at IS NULL').fetchone()
        links=learning.active_rules(conn,guard.Actor(session['owner_id'],session['id']),pid,cid,'compare these products')['bindings']
    assert links and links[0]['kind']=='learning_release'
    _,artifact,entry=capture(client,pid,cid,links)
    exported=client.get(f'/api/projects/{pid}/snapshot');assert exported.status_code==200,exported.text
    assert any(m.get('context_dependencies')==links for c in exported.json()['conversations'] for m in c['messages'])
    assert client.delete(f'/api/conversations/{cid}').status_code==200
    assert client.get(f'/api/artifacts/{artifact}').status_code==200
    withdrawn=client.post(base+f"/feedback/{verified['id']}/withdraw-consent",json={'expected_version':verified['version'],'purposes':['project_memory']})
    assert withdrawn.status_code==200,withdrawn.text
    hidden(client,pid,artifact,entry)


def test_all_current_context_shapes_roundtrip_only_as_unbound_history(client):
    pid,cid=project(client)
    with db.get_db() as conn:
        conn.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',('shape-fixture',cid,'assistant','Portable historical text','complete','fixture',db.now(),'[]'))
        context_lineage.save(conn,'shape-fixture',[])
    snapshot=client.get(f'/api/projects/{pid}/snapshot').json()
    shapes=[{'kind':'project_memory','id':'old-memory','version':1,'content_sha256':'a'*64},
       {'kind':'personal_memory','id':'old-personal','version':1,'content_sha256':'a'*64,'consent_epoch':3},
       {'kind':'learning_release','id':'old-release','version':1,'candidate_version':1,'target_version':'b'*64,'rule_hash':'c'*64},
       {'kind':'learning_feedback','id':'old-feedback','version':1,'target_version':'b'*64,'conversation_id':'old-chat'}]
    snapshot['conversations'][0]['messages'][0]['context_dependencies']=shapes
    response=client.post('/api/projects/import',json={'snapshot':snapshot,'confirm':True});assert response.status_code==201,response.text
    restored=response.json()['id']
    with db.get_db() as conn:
        mid=conn.execute('SELECT m.id FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.project_id=?',(restored,)).fetchone()[0]
        assert context_lineage.read(conn,mid)==[context_lineage.BLOCKED]
        assert not context_lineage.authorized(conn,restored,context_lineage.read(conn,mid))
    snapshot['conversations'][0]['messages'][0]['context_dependencies'][0]['verified']=True
    assert client.post('/api/projects/import',json={'snapshot':snapshot,'confirm':True}).status_code==422


def test_corrupt_independent_receipt_fails_closed_and_cannot_be_exported(client):
    pid,cid=project(client)
    _,artifact,entry=capture(client,pid,cid,[])
    with db.get_db() as conn:
        conn.execute("UPDATE studio_artifact_dependencies SET links='null' WHERE artifact_id=?",(artifact,))
        conn.execute("UPDATE studio_entry_dependencies SET links='null' WHERE entry_id=?",(entry,))
    assert client.get(f'/api/artifacts/{artifact}').status_code==409
    assert client.get(f'/api/projects/{pid}/glossary/{entry}').status_code==409
    assert client.get(f'/api/projects/{pid}/snapshot').status_code==409


def test_legacy_deleted_parent_without_receipt_is_withheld_not_invented(client):
    pid,cid=project(client)
    _,artifact,entry=capture(client,pid,cid,[])
    # An older installation could lose the sole parent before this migration.
    with db.get_db() as conn:
        conn.execute('DELETE FROM studio_artifact_dependencies WHERE artifact_id=?',(artifact,))
        conn.execute('DELETE FROM studio_entry_dependencies WHERE entry_id=?',(entry,))
    assert client.delete(f'/api/conversations/{cid}').status_code==200
    with db.get_db() as conn:
        studio.init_studio(conn)
        assert not conn.execute('SELECT 1 FROM studio_artifact_dependencies WHERE artifact_id=?',(artifact,)).fetchone()
        assert not conn.execute('SELECT 1 FROM studio_entry_dependencies WHERE entry_id=?',(entry,)).fetchone()
    hidden(client,pid,artifact,entry)
    removed=client.delete(f'/api/projects/{pid}/glossary/{entry}?expected_version=1')
    assert removed.status_code==200 and 'SYNTHETIC_BOUND_DERIVATIVE' not in removed.text


def test_trusted_entry_receipt_survives_edit_detach_and_restore_after_parent_deletion(client):
    pid,cid=project(client)
    memory=client.post('/api/memories',json={'scope':'project','project_id':pid,'content':'Versioned source'}).json()
    with db.get_db() as conn:links=context_lineage.memory_links(conn,pid,[memory])
    _,artifact,entry=capture(client,pid,cid,links)
    assert client.delete(f'/api/conversations/{cid}').status_code==200
    path=f'/api/projects/{pid}/glossary/{entry}'
    changed=client.patch(path,json={'term':'derived','definition':'Edited authorized copy','expected_version':1})
    assert changed.status_code==200,changed.text
    detached=client.patch(path,json={'term':'derived','definition':'Detached authorized copy','expected_version':2,'source_message_id':None})
    assert detached.status_code==200,detached.text
    restored=client.post(path+'/restore',json={'expected_version':3,'from_version':1})
    assert restored.status_code==200,restored.text
    with db.get_db() as conn:
        receipts=conn.execute('SELECT version,links FROM studio_entry_dependencies WHERE entry_id=? ORDER BY version',(entry,)).fetchall()
        assert [r['version'] for r in receipts]==[1,2,4]
        assert all(json.loads(r['links'])==links for r in receipts)
    assert client.get(f'/api/projects/{pid}/snapshot').status_code==200
    assert client.delete(f"/api/memories/{memory['id']}?expected_version={memory['version']}").status_code==200
    hidden(client,pid,artifact,entry)
    assert client.get(path+'/versions').status_code==404
    assert client.post(path+'/restore',json={'expected_version':4,'from_version':1}).status_code==404
    deleted=client.delete(path+'?expected_version=4')
    assert deleted.status_code==200 and 'SYNTHETIC_BOUND_DERIVATIVE' not in deleted.text
