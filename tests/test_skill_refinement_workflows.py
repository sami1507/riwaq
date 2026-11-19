"""Synthetic executor integration. Model/network doubles are explicitly marked."""
import json
from io import BytesIO
from zipfile import ZipFile

from backend import skills
from test_app import client
from test_skills import pid, wait, mock_translation
from backend.db import get_db
import pytest


def run(client, selected, inputs, key='refinement-workflow', request='أنشئ المخرجات المحددة'):
    p = pid(client)
    response = client.post(f'/api/projects/{p}/skill-runs', json={
        'request': request, 'requestedSkills': selected, 'inputs': inputs, 'idempotencyKey': key,
    })
    assert response.status_code == 202, response.text
    return p, wait(client, p, response.json()['id'])


def test_independent_output_survives_blocked_specialist(client):
    p, result = run(client, ['code-review', 'docx-authoring'], {'text': 'Synthetic review brief. No execution was requested.'})
    assert result['status'] == 'partial'
    assert len(result['result']['artifacts']) == 1, result
    assert client.get(f'/api/projects/{p}/skill-runs/{result["id"]}/artifacts/{result["result"]["artifacts"][0]["name"]}').status_code == 200
    assert next(s for s in result['steps'] if s['skill'] == 'code-review')['status'] == 'blocked'


def test_invalid_spreadsheet_does_not_erase_valid_document(client):
    _, result = run(client, ['docx-authoring', 'spreadsheet-creation'], {'text': 'A complete synthetic document survives a sibling failure.'})
    assert result['status'] == 'partial', result
    assert len(result['result']['artifacts']) == 1
    assert result['result']['artifacts'][0]['name'].endswith('.docx')
    assert result['receipt']['verification'] == 'partial'


def test_mock_translation_feeds_two_editable_outputs(client, monkeypatch):
    mock_translation(monkeypatch, json.dumps({'translation': 'הסכום אינו __RW_LITERAL_0__ __RW_LITERAL_1__'}))
    p, result = run(client, ['translation', 'docx-authoring', 'slide-authoring'],
                    {'text': 'The amount is not 42 ₪', 'targetLanguage': 'he'})
    assert result['status'] == 'completed', result
    assert result['result']['original'] == 'The amount is not 42 ₪'
    assert len(result['result']['artifacts']) == 2
    for item in result['result']['artifacts']:
        raw = client.get(f'/api/projects/{p}/skill-runs/{result["id"]}/artifacts/{item["name"]}').content
        with ZipFile(BytesIO(raw)) as z:
            content = z.read('word/document.xml' if item['name'].endswith('.docx') else 'ppt/slides/slide1.xml').decode()
            assert 'הסכום אינו 42 ₪' in content
    assert result['receipt']['verification'] == 'partial'  # Language/layout remain separate reviews.


def test_failed_translation_does_not_export_untranslated_source(client, monkeypatch):
    mock_translation(monkeypatch, 'invalid JSON')
    _, result = run(client, ['translation', 'docx-authoring'], {'text': 'Do not lose the negation.', 'targetLanguage': 'ar'})
    assert result['status'] == 'failed', result
    assert result['result']['artifacts'] == []
    assert next(s for s in result['steps'] if s['skill'] == 'docx-authoring')['status'] == 'blocked'


def test_distinct_typed_outputs_and_legacy_retry_upgrade(client):
    inputs={'artifacts':[
        {'kind':'docx','payload':{'title':'تقرير','paragraphs':[{'text':'مستند مستقل'}]}},
        {'kind':'xlsx','payload':{'title':'بيانات','columns':[{'name':'قيمة','type':'number'}],'rows':[[0],[None],[12.5]],'totals':[0]}}
    ]}
    p,result=run(client,['docx-authoring','spreadsheet-creation'],inputs)
    assert result['status']=='completed',result
    assert len(result['result']['artifacts'])==2
    # A stored pre-upgrade request omitted fields which now have safe empty defaults.
    with get_db() as c:
        row=c.execute('SELECT payload FROM skill_runs WHERE id=?',(result['id'],)).fetchone()
        old=json.loads(row[0]);old['inputs'].pop('researchQuestion');old['inputs'].pop('researchOptions')
        c.execute('UPDATE skill_runs SET payload=?,request_hash=? WHERE id=?',(skills.pack(old),'pre-upgrade-hash',result['id']))
    _,again=run(client,['docx-authoring','spreadsheet-creation'],inputs)
    assert again['id']==result['id']


def test_saved_research_to_cited_docx_real_storage_no_network(client,monkeypatch):
    from test_research_chat import setup,TEXT
    from backend import public_knowledge as public
    monkeypatch.delenv('RIWAQ_EMBED_MODEL',raising=False)
    p,_=setup(client)
    async def forbidden(*a,**k):raise AssertionError('No external fetch for saved-index scope')
    monkeypatch.setattr(public,'start_selected_job',forbidden)
    _,result=run(client,['web-research','citation-grounding','docx-authoring'],
        {'researchQuestion':'range stop value','researchOptions':{'scopes':['knowledge'],'source_id':'research-fixture'}})
    assert result['status']=='completed',result
    assert result['result']['research_kind']=='source-excerpts'
    assert result['result']['research_receipt']['network_requests']==0
    ref=result['result']['source_refs'][0]
    evidence=client.get(ref['url']);assert evidence.status_code==200
    assert evidence.json()['text']==TEXT and evidence.json()['current'] is True
    item=result['result']['artifacts'][0]
    link=f'/api/projects/{p}/skill-runs/{result["id"]}/artifacts/{item["name"]}'
    with ZipFile(BytesIO(client.get(link).content)) as z:
        xml=z.read('word/document.xml').decode()
        assert TEXT in xml and 'https://example.org/docs/range.txt' in xml
        assert ref['version_id'] in xml
    with get_db() as c:c.execute("UPDATE public_sources SET status='suspended' WHERE id='research-fixture'")
    assert client.get(ref['url']).status_code==404
    assert client.get(link).status_code==404
    assert client.get(f'/api/projects/{p}/skill-runs/{result["id"]}').status_code==404


@pytest.mark.parametrize('inputs',[
    {},
    {'researchQuestion':'range','researchOptions':{'scopes':['web'],'allow_network':True}},
    {'researchQuestion':'range','researchOptions':{'scopes':['web'],'allow_network':False}},
])
def test_research_missing_query_or_network_permission_never_falls_back(client,monkeypatch,inputs):
    from backend import research
    async def forbidden(*a,**k):raise AssertionError('Missing explicit scope must not reach research')
    monkeypatch.setattr(research,'prepare',forbidden)
    _,result=run(client,['web-research','docx-authoring'],inputs)
    assert result['status']=='failed',result
    assert result['result']['artifacts']==[]
    assert result['steps'][-1]['status']=='blocked'


def test_research_no_evidence_blocks_document_instead_of_inventing_content(client,monkeypatch):
    from backend import research
    async def empty(*a,**k):return []  # Controlled network double, not a live source.
    monkeypatch.setattr(research,'prepare',empty)
    _,result=run(client,['web-research','docx-authoring'],{'researchQuestion':'unknown synthetic entity'})
    assert result['status']=='partial'
    assert result['result']['artifacts']==[]
    assert all(s['status']=='blocked' for s in result['steps'])


def test_semantic_document_edit_cannot_succeed_by_copying_input(client):
    _,result=run(client,['docx-authoring'],{'text':'Old title must not be reported as edited.'},request='عدّل مستند Word وغير عنوانه')
    assert result['status']=='partial' and result['steps'][0]['status']=='blocked'
    assert result['result']['artifacts']==[]


def test_deleting_ancestor_withholds_transitive_derivative(client):
    p,first=run(client,['docx-authoring'],{'text':'Original synthetic output.'})
    previous=first['id']
    for index in range(2):
        response=client.post(f'/api/projects/{p}/skill-runs',json={
            'request':'أنشئ مستندًا جديدًا','requestedSkills':['docx-authoring'],
            'inputs':{'text':'Derived synthetic output.'},'previousRunId':previous,'idempotencyKey':f'ancestor-child-{index}'})
        assert response.status_code==202
        value=wait(client,p,response.json()['id']);assert value['status']=='completed'
        previous=value['id']
    assert client.delete(f'/api/projects/{p}/skill-runs/{first["id"]}').status_code==200
    assert client.get(f'/api/projects/{p}/skill-runs/{previous}').status_code==409
    assert client.get(f'/api/projects/{p}/skill-runs/{previous}/artifacts/{value["result"]["artifacts"][0]["name"]}').status_code==409
