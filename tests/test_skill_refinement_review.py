"""Independent regression review; synthetic source lineage and a mocked translator."""
import json

import pytest
from fastapi import HTTPException

from backend import skills
from test_skill_runtime_security import sandbox, request, finish
from test_app import client
from test_skill_refinement_workflows import run
from test_skills import wait
from backend.db import get_db


@pytest.mark.asyncio
async def test_deleting_original_task_withholds_transitive_derivative(sandbox):
    original=await finish(request())
    verifier=await finish(request('review-parent-001',request='تحقق من النتيجة',requestedSkills=['tool-result-verification'],inputs={},previousRunId=original))
    derived=await finish(request('review-child-001',previousRunId=verifier))
    assert skills.get_run('a',derived)['result']['artifacts']
    await skills.delete_run('a',original)
    with pytest.raises(HTTPException) as rejected:skills.get_run('a',derived)
    assert rejected.value.status_code in {404,409}


@pytest.mark.asyncio
async def test_independent_typed_spreadsheet_survives_failed_translation(sandbox,monkeypatch):
    async def rejected(*args,**kwargs):raise ValueError('Synthetic translation unavailable')
    monkeypatch.setattr(skills,'translate',rejected)
    rid=await finish(request('review-sibling-001',requestedSkills=['translation','spreadsheet-creation'],
       inputs={'text':'Translate this paragraph','targetLanguage':'ar','artifacts':[{'kind':'xlsx','payload':{
          'title':'Independent typed data','columns':[{'name':'Amount','type':'number'}],'rows':[[0],[12.5]],'totals':[0]}}]}))
    result=skills.get_run('a',rid)
    assert result['status']=='partial'
    assert len(result['result']['artifacts'])==1
    assert result['result']['artifacts'][0]['name'].endswith('.xlsx')


@pytest.mark.asyncio
async def test_independent_typed_spreadsheet_is_not_replaced_by_successful_translation(sandbox,monkeypatch):
    async def translated(*args,**kwargs):return 'תרגום לדוגמה'
    monkeypatch.setattr(skills,'translate',translated)
    rid=await finish(request('review-sibling-002',requestedSkills=['translation','spreadsheet-creation'],
       inputs={'text':'Synthetic independent paragraph','targetLanguage':'he','artifacts':[{'kind':'xlsx','payload':{
          'title':'Independent typed data','columns':[{'name':'Amount','type':'number'}],'rows':[[42]],'totals':[0]}}]}))
    result=skills.get_run('a',rid)
    assert result['status']=='completed'
    assert result['result']['text']=='תרגום לדוגמה'
    item=result['result']['artifacts'][0]
    from io import BytesIO
    from zipfile import ZipFile
    with ZipFile(BytesIO(skills.download('a',rid,item['name']).body)) as package:
        assert '<v>42</v>' in package.read('xl/worksheets/sheet1.xml').decode()


def test_status_query_reads_actual_previous_steps_without_replaying_output(client):
    p,first=run(client,['docx-authoring'],{'text':'Synthetic completed task'},key='review-status-original')
    response=client.post(f'/api/projects/{p}/skill-runs',json={'request':'وين وصلنا؟','previousRunId':first['id'],'idempotencyKey':'review-status-query'})
    assert response.status_code==202
    result=wait(client,p,response.json()['id'])
    # Status now returns the existing operation instead of creating an
    # inspection run. The original outputs remain readable, never re-created.
    assert result['id']==first['id'] and result['status']=='completed' and result['steps']==first['steps']
    assert result['result']['artifacts']==first['result']['artifacts']
    with get_db() as conn:
        assert conn.execute('SELECT count(*) FROM skill_artifacts').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM skill_runs').fetchone()[0]==1


def test_tool_routing_describes_public_egress_without_executing_it(client,monkeypatch):
    from backend import research
    async def forbidden(*args):raise AssertionError('Routing is not execution')
    monkeypatch.setattr(research,'prepare',forbidden)
    _,result=run(client,['tool-routing'],{},request='ابحث في الويب وأنشئ Word',key='review-tool-metadata')
    output=result['result']['structured']['tool-routing']
    assert output['executed'] is False
    assert 'destination' not in output  # No misleading global local-only claim.
    tool=next(x for x in output['tools'] if x['skill']=='web-research')
    assert tool['destination']=='explicit-approved-public-reads'
    assert tool['inputs_validated'] is False


def test_local_only_request_overrides_stale_network_checkbox(client,monkeypatch):
    from backend import research
    assert client.patch('/api/settings',json={'mode':'hybrid'}).status_code==200
    calls=[]
    async def observed(*args):calls.append(True);return []
    monkeypatch.setattr(research,'prepare',observed)
    _,result=run(client,['web-research'],{'researchQuestion':'synthetic public query',
         'researchOptions':{'scopes':['web'],'allow_network':True}},request='Research locally only',key='review-local-policy')
    assert calls==[]
    assert result['status'] in {'failed','partial'} and result['result']['artifacts']==[]
