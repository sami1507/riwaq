"""Frozen synthetic workflow evaluation; not language-model or live integration proof."""
import json
from pathlib import Path

import pytest

from backend import skill_core

FIXTURES = Path(__file__).parent/'fixtures/skill_routing/cases-batch03.json'
CASES = json.loads(FIXTURES.read_text('utf-8'))


def evaluate_case(case):
    previous = skill_core.classify(case['previous']).model_dump() if case.get('previous') else None
    intent = skill_core.classify(case['text'], previous)
    skills = skill_core.select_skills(intent, document_ids=case.get('document_ids', []))
    errors = []
    if skills != case['skills']: errors.append({'field':'skills','expected':case['skills'],'actual':skills})
    for field in ['outputs','references']:
        actual = getattr(intent, {'outputs':'requestedOutputs','references':'references'}[field])
        if field in case and actual != case[field]: errors.append({'field':field,'expected':case[field],'actual':actual})
    for field, actual in [('intents',intent.intent), ('constraints',intent.constraints)]:
        if not set(case.get(field,[])) <= set(actual): errors.append({'field':field,'expected':case[field],'actual':actual})
    if set(case.get('forbidden_intents',[])) & set(intent.intent): errors.append({'field':'forbidden_intents','actual':intent.intent})
    if case.get('same_goal') and intent.goal != previous['goal']: errors.append({'field':'goal','actual':intent.goal})
    return errors


@pytest.mark.parametrize('case',CASES['development'],ids=lambda x:x['id'])
def test_development_routing_contract(case):
    assert not (errors:=evaluate_case(case)), errors


@pytest.mark.parametrize('case',CASES['held_out'],ids=lambda x:x['id'])
def test_held_out_routing_contract(case):
    assert not (errors:=evaluate_case(case)), errors


def test_read_only_cannot_override_with_explicit_writer():
    intent = skill_core.classify('Review only, do not modify any files')
    for slug in ['code-generation','docx-authoring','spreadsheet-creation','slide-authoring','memory-correction']:
        with pytest.raises(ValueError): skill_core.select_skills(intent,[slug])


def test_parallel_exports_do_not_depend_on_each_other_and_ids_survive_insertions():
    intent = skill_core.classify('Translate PDF and create Word and slides')
    selected = skill_core.select_skills(intent)
    steps = {s['skill']:s for s in skill_core.plan(intent,selected)}
    assert steps['translation']['depends_on']==[steps['pdf-reading']['id']]
    assert steps['docx-authoring']['depends_on']==[steps['translation']['id']]
    assert steps['slide-authoring']['depends_on']==[steps['translation']['id']]
    expanded = {s['skill']:s for s in skill_core.plan(intent,['intent-routing',*selected])}
    assert all(expanded[slug]['id']==step['id'] for slug,step in steps.items())


def test_unrelated_review_and_translation_are_not_serial_dependencies():
    intent=skill_core.classify('Review code and translate the paragraph')
    steps={s['skill']:s for s in skill_core.plan(intent,['code-review','translation'])}
    assert steps['code-review']['depends_on']==[]
    assert steps['translation']['depends_on']==[]


def test_native_formula_criterion_only_when_requested():
    a=skill_core.criteria(skill_core.classify('Create an Excel workbook with formulas'),['spreadsheet-creation'])
    b=skill_core.criteria(skill_core.classify('Create an Excel workbook with static values'),['spreadsheet-creation'])
    assert 'native formulas required' in a[1]['requirement']
    assert 'native formulas required' not in b[1]['requirement']


def test_classifier_bounds_and_does_not_mutate_previous():
    previous=skill_core.classify('Create a Word document').model_dump()
    snapshot=json.dumps(previous,sort_keys=True)
    skill_core.classify('continue',previous)
    assert json.dumps(previous,sort_keys=True)==snapshot
    with pytest.raises(ValueError):skill_core.classify('x'*8001)
    with pytest.raises(ValueError):skill_core.classify('   ')
    assert skill_core.chat_instructions('x'*8001)==''


def test_typed_artifact_requests_and_research_defaults_are_bounded():
    assert skill_core.SkillInputs().artifacts==[]
    assert skill_core.SkillInputs().researchOptions is None
    assert skill_core.SkillInputs().retrievalMode=='lexical'
    assert skill_core.SkillInputs(retrievalMode='hybrid').retrievalMode=='hybrid'
    with pytest.raises(ValueError):skill_core.SkillInputs(retrievalMode='remote')
    assert skill_core.SkillInputs(artifacts=[{'kind':'docx','payload':{}}]).artifacts[0].kind=='docx'
    with pytest.raises(ValueError):skill_core.SkillInputs(artifacts=[{'kind':'docx','payload':{}},{'kind':'docx','payload':{}}])
    with pytest.raises(ValueError):skill_core.SkillInputs(artifact={},artifacts=[{'kind':'docx','payload':{}}])
    with pytest.raises(ValueError):skill_core.SkillInputs(artifacts=[{'kind':'exe','payload':{}}])
    with pytest.raises(ValueError):skill_core.SkillInputs(researchQuestion='x'*2001)
    options=skill_core.SkillInputs(researchOptions={'scopes':['web']}).researchOptions
    assert options.allow_network is False
    with pytest.raises(ValueError):skill_core.SkillInputs(researchOptions={'scopes':['web'],'allow_network':'true'})


def test_unrelated_spreadsheet_does_not_depend_on_research():
    intent=skill_core.classify('Research and create Word and Excel')
    steps={s['skill']:s for s in skill_core.plan(intent,skill_core.select_skills(intent))}
    assert steps['spreadsheet-creation']['depends_on']==[]
    assert steps['docx-authoring']['depends_on']==[steps['web-research']['id']]


def test_research_text_must_exist_before_translation():
    intent=skill_core.classify('Research and translate the result, create Word')
    steps={s['skill']:s for s in skill_core.plan(intent,skill_core.select_skills(intent))}
    assert steps['translation']['depends_on']==[steps['web-research']['id']]
    assert steps['docx-authoring']['depends_on']==[steps['translation']['id'],steps['web-research']['id']]


def test_typed_xlsx_uses_its_own_data_but_typed_docx_cannot_override_translation():
    intent=skill_core.classify('Read PDF and translate, then create Excel and Word')
    steps={s['skill']:s for s in skill_core.plan(intent,skill_core.select_skills(intent),typed_outputs=['xlsx','docx'])}
    assert steps['spreadsheet-creation']['depends_on']==[]
    assert steps['docx-authoring']['depends_on']==[steps['translation']['id']]


@pytest.mark.parametrize('prompt',['وين وصلنا؟','continue'])
def test_status_or_unbound_continue_does_not_process_selected_attachments(prompt):
    intent=skill_core.classify(prompt)
    assert skill_core.select_skills(intent,document_ids=['synthetic-selected-file'])==['intent-routing']


@pytest.mark.parametrize('prompt',['ابحث عن القيمة في الملف','Search the selected document','חפש בקובץ','What is the amount?'])
def test_selected_file_lookup_uses_private_retrieval_not_web(prompt):
    assert skill_core.select_skills(skill_core.classify(prompt),document_ids=['synthetic-file'])==['hybrid-retrieval']


def test_explicit_web_can_be_added_but_plain_pdf_read_remains_full_read():
    assert skill_core.select_skills(skill_core.classify('Search the web and my file'),document_ids=['synthetic-file'])==['hybrid-retrieval','web-research']
    assert skill_core.select_skills(skill_core.classify('Read PDF'),document_ids=['synthetic-file'])==['pdf-reading']
    assert skill_core.select_skills(skill_core.classify('Summarize the selected file'),document_ids=['synthetic-file'])==['pdf-reading','text-summarization']


def test_negated_operation_never_falls_back_to_create():
    intent=skill_core.classify('Do not modify')
    assert 'create' not in intent.intent
    assert skill_core.select_skills(intent)==['intent-routing']


@pytest.mark.parametrize('prompt',['لخص الفقرة ولا تعمل عرضا','Summarize the paragraph and do not create slides'])
def test_coordinated_negation_does_not_create_an_artifact(prompt):
    intent=skill_core.classify(prompt)
    assert intent.requestedOutputs==[]
    assert skill_core.select_skills(intent)==['text-summarization']


@pytest.mark.parametrize('prompt',['Create Excel without formulas','أنشئ مصنف Excel بدون صيغ','צור גיליון בלי נוסחאות'])
def test_negated_formula_requirement_is_not_added(prompt):
    rule=skill_core.criteria(skill_core.classify(prompt),['spreadsheet-creation'])[1]
    assert 'native formulas required' not in rule['requirement']
