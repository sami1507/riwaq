"""Cached learning results must use the same live boundary as tool downloads."""
import hashlib
import json
import time
import pytest
from backend import db,guard,learning,local_auth,skill_files


@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));db.init_db()
    with db.get_db() as c:
        owner=local_auth.owner_id(c);stamp=time.time()
        c.execute('INSERT INTO local_auth_sessions VALUES(?,?,?,?,?,NULL,?)',('tool-review-session','tool-review-hash',owner,stamp,stamp+3600,stamp))
        pid=c.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
    return guard.Actor(owner,'tool-review-session'),pid


def saved_tool(c,who,pid,rid,*,source=None,parent=None,artifact=False):
    payload={'request':'Synthetic source-bound tool result','inputs':{'sourceRefs':[source] if source else []},'previousRunId':parent}
    document={'selected_skills':[],'result':{'text':'SYNTHETIC PRIVATE TOOL VALUE','artifacts':[]},'steps':[]}
    c.execute('INSERT INTO skill_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(rid,pid,who.owner_id,who.session_id,rid,guard.digest(payload),json.dumps(payload),'[]','completed',json.dumps(document),db.now(),db.now()))
    if artifact:
        content=b'SYNTHETIC PRIVATE TOOL VALUE';digest=hashlib.sha256(content).hexdigest()
        item={'name':'source.json','mime':'application/json','sha256':digest,'content':content}
        c.execute('INSERT INTO skill_artifacts VALUES(?,?,?,?,?)',(rid,item['name'],item['mime'],digest,content))
        identity=skill_files.register(c,who,pid,rid,item)
        document['result']['artifacts']=[{**identity,'name':item['name'],'mime':item['mime'],'sha256':digest,'size':len(content)}]
        c.execute('UPDATE skill_runs SET document=? WHERE id=?',(json.dumps(document),rid))
        return {'kind':'artifact','id':identity['artifact_id'],'version':1,'sha256':digest}


def feedback(c,who,pid,rid):
    target=learning.target_snapshot(c,who,pid,'tool',rid)
    target={k:target[k] for k in ('kind','id','version')}
    row=learning.add_feedback(c,who,pid,{'target':target,'signal':'correction','scope':'project','purposes':['current_task','project_memory'],'reason':'SYNTHETIC PRIVATE TOOL VALUE'},'feedback-'+rid)
    proof={'status':'verified','target':target,'corrected_output':{'text':'SYNTHETIC PRIVATE TOOL VALUE'}}
    row=learning.record_verification(c,who,pid,row['id'],row['version'],proof)
    candidate=learning.propose_candidate(c,who,pid,row['id'],row['version'],{'kind':'terminology','source':'Private term','target':'SYNTHETIC PRIVATE TOOL VALUE'},{'task':'any'},scope='project')
    return row,candidate


def withheld(c,who,pid,row,candidate):
    value=learning.get_feedback(c,who,pid,row['id'])
    assert value['status']=='stale' and value['verification']=={} and value['reason']==''
    assert 'SYNTHETIC PRIVATE TOOL VALUE' not in json.dumps(learning.list_feedback(c,who,pid))
    assert learning.get_candidate(c,who,pid,candidate['id'])['rule']=={}
    assert 'SYNTHETIC PRIVATE TOOL VALUE' not in json.dumps(learning.list_candidates(c,who,pid))
    with pytest.raises(learning.LearningError) as exc:
        learning.target_snapshot(c,who,pid,'tool',row['target']['id'])
    assert exc.value.code=='learning_resource_unavailable'
    with pytest.raises(learning.LearningError):
        learning.update_candidate(c,who,pid,candidate['id'],candidate['version'],{'kind':'tone','value':'formal'},{'task':'any'})


@pytest.mark.parametrize('path',['source_ref','ancestor','own_artifact'])
def test_revoked_artifact_withholds_cached_get_list_candidates_and_target(store,path):
    who,pid=store
    with db.get_db() as c:
        source=saved_tool(c,who,pid,'source',artifact=True)
        if path=='source_ref':saved_tool(c,who,pid,'target',source=source)
        elif path=='ancestor':
            saved_tool(c,who,pid,'parent',source=source);saved_tool(c,who,pid,'target',parent='parent')
        else:source=saved_tool(c,who,pid,'target',artifact=True)
        row,candidate=feedback(c,who,pid,'target')
        c.execute('UPDATE skill_file_heads SET deleted=1 WHERE id=?',(source['id'],))
        withheld(c,who,pid,row,candidate)


def test_tool_lineage_cycle_is_bounded_and_sanitized(store):
    who,pid=store
    with db.get_db() as c:
        saved_tool(c,who,pid,'target');row,candidate=feedback(c,who,pid,'target')
        payload={'request':'Synthetic','inputs':{},'previousRunId':'target'}
        c.execute('UPDATE skill_runs SET payload=? WHERE id=?',(json.dumps(payload),'target'))
        withheld(c,who,pid,row,candidate)


def test_missing_or_corrupt_actual_tool_output_cannot_keep_cached_receipt(store):
    who,pid=store
    with db.get_db() as c:
        saved_tool(c,who,pid,'target',artifact=True);row,candidate=feedback(c,who,pid,'target')
        c.execute("UPDATE skill_artifacts SET content=? WHERE run_id='target'",(b'CORRUPT',))
        withheld(c,who,pid,row,candidate)
