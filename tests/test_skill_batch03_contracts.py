"""Local contracts and real SQLite evidence scope; no semantic/model claims."""
import json
import pytest
from backend import skill_core
from test_app import client
from test_skills import pid,submit
from test_skill_batch02_api import upload


@pytest.mark.parametrize('prompt,slug',[
    ('أنشئ PDF','pdf-creation'),('Create PDF','pdf-creation'),('צור PDF','pdf-creation'),
    ('ادمج PDF','pdf-merge-split'),('احسب المتوسط','descriptive-statistics'),
    ('افحص جودة البيانات','data-profiling'),('اقرأ المصنف','spreadsheet-reading'),
    ('اصلح الصيغة','formula-debugging'),('اربط الجدولين','table-joins'),
    ('השווה מסמכים','document-comparison'),('Clean data','spreadsheet-cleaning')])
def test_specific_operation_routing(prompt,slug):
    assert skill_core.select_skills(skill_core.classify(prompt))==[slug]


@pytest.mark.parametrize('prompt',['اشرح إنشاء PDF','Explain "merge PDF"','Do not merge PDF','لا تدمج PDF','אל מזג PDF'])
def test_explanation_and_negation_do_not_mutate(prompt):
    assert not set(skill_core.select_skills(skill_core.classify(prompt)))&skill_core.WRITING_SKILLS


def test_75_registered_packages_and_progressive_budget():
    from backend.skill_loading import load_packages
    manifest=skill_core.catalog()
    assert [s['number'] for s in manifest['skills'][:75]]==list(range(1,76))
    for entry in manifest['skills'][50:75]:
        loaded=load_packages([entry['slug']],manifest,skill_core.ROOT)
        assert len(loaded['loaded'])==1 and not loaded['executed_scripts']
        assert loaded['characters']<3000
    assert skill_core.select_skills(skill_core.classify('Hello'))==['intent-routing']


def test_synthesis_keeps_sources_distinct_and_declares_semantic_limit(client):
    p=pid(client)
    files=[upload(client,p,'decision-a.txt','Project Alpha budget is 15 USD. Approval is not final.'),
           upload(client,p,'decision-b.txt','Project Alpha budget is 150 USD. Approval is final.'),
           upload(client,p,'other.txt','Organization Beta also calls its project Alpha. Budget is 42 EUR.')]
    run=submit(client,p,'cross-document-synthesis',{'documentIds':files},request='Combine document evidence',key='three-source-synthesis')
    assert run['status']=='partial',run
    result=run['result']['structured']['cross-document-synthesis']
    assert set(x['file_id'] for x in result['evidence_map'])==set(files)
    assert result['inferences']==[] and result['conflicts']=='not_semantically_adjudicated'
    assert all(x['kind']=='direct_source_text' for x in result['evidence_map'])
    for ref in run['result']['source_refs']:
        assert client.get(ref['url']).status_code==200
    # Revoked/deleted evidence cannot remain accessible via the prior answer.
    assert client.delete(f'/api/projects/{p}/files/{files[0]}').status_code in (200,204)
    assert client.get(f'/api/projects/{p}/skill-runs/{run["id"]}').status_code in (403,404,409)
