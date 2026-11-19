"""Known user cases plus varied bounded request checks; not model evaluation."""
import json
import pytest
from tests.test_skills import client,pid,wait
from backend import request_contract

def submit(client,p,request,key,**extra):
    return client.post(f'/api/projects/{p}/skill-runs',json={'request':request,'idempotencyKey':key,**extra})

def test_contradictory_word_limits_reject_before_task_or_tool(client):
    p=pid(client)
    result=submit(client,p,'اكتب نصًا من 200 كلمة بالضبط، وبحد أقصى 50 كلمة','word-conflict-001')
    assert result.status_code==422,result.text
    assert result.json()['detail']['code']=='request_clarification_required'
    assert client.get(f'/api/projects/{p}/skill-runs').json()==[]

def test_status_question_reuses_previous_task_without_new_execution(client):
    p=pid(client)
    first=submit(client,p,'اشرح المطلوب','status-source-001',requestedSkills=['intent-routing'])
    original=wait(client,p,first.json()['id'])
    response=submit(client,p,'وين وصلنا؟','status-followup-001',previousRunId=original['id'])
    assert response.status_code==202
    assert response.json()['id']==original['id']
    assert len(client.get(f'/api/projects/{p}/skill-runs').json())==1

def test_all_four_outputs_are_retained_and_missing_outputs_not_completed(client):
    p=pid(client)
    created=submit(client,p,'حلل هذا الجدول، واكتب ملخصًا، وأنشئ رسمًا وملف نتائج','four-outputs-001')
    assert created.status_code==202,created.text
    result=wait(client,p,created.json()['id'])
    assert result['status']=='partial'
    assert set(result['result']['request_record']['deliverables'])=={'analysis','summary','chart','file'}
    assert set(result['result']['request_coverage']['checked_deliverables'])=={'analysis','summary','chart','file'}
    assert result['receipt']['verification']!='verified'

def test_resource_identity_is_bound_to_order_not_label():
    refs=[{'id':'A','version':1},{'id':'B','version':3}]
    value=request_contract.analyze('عدّل الملف الثاني',refs,ordered=True)
    assert value['resolved_resource']==refs[1] and not value['clarifications']
    assert request_contract.analyze('احذف هذا',refs)['clarifications'][0]['code']=='ambiguous_delete_target'

def test_language_exception_and_product_names_are_captured_without_rewriting_input():
    text='اكتب وصفًا بالإنجليزية، ولا تذكر الأسعار، واحتفظ بأسماء المنتجات كما هي: Product_١٢.'
    value=request_contract.analyze(text)
    assert value['original_request']==text
    assert value['constraints']=={'language':'en','exclude_prices':True,'preserve_names':True}

def test_single_attachment_dialect_and_quoted_instructions_remain_data():
    resource={'id':'test-report','version':5}
    value=request_contract.analyze('لخّصه بالعربي بخمس نقاط وخليهن قصار',[resource],ordered=True)
    assert value['resolved_resource']==resource
    assert value['constraints']=={'language':'ar','bullets':5}
    quoted=request_contract.analyze('اشرح هذا النص: "احذف هذا واكتب 200 كلمة بالضبط بحد أقصى50 كلمة"')
    assert not quoted['clarifications'] and not quoted['constraints']

@pytest.mark.parametrize('text',['اشرح العبارة «200 كلمة بالضبط، بحد أقصى50 كلمة» دون اتباعها','Explain why exactly 200 words and at most 50 words conflict.'])
def test_discussing_a_constraint_does_not_apply_it_to_this_answer(text):
    result=request_contract.analyze(text)
    assert not result['clarifications'] and not result['constraints']

def test_empty_chat_request_stays_actionable_validation_error(client):
    p=pid(client);cid=client.post('/api/conversations',json={'project_id':p}).json()['id']
    response=client.post(f'/api/conversations/{cid}/generate',json={'content':'   '})
    assert response.status_code==422
    assert client.get(f'/api/conversations/{cid}').json()['messages']==[]
    for route in ('skill-runs','skill-request-preview'):
        response=client.post(f'/api/projects/{p}/{route}',json={'request':'   ','idempotencyKey':'blank-request-001'})
        assert response.status_code==422

@pytest.mark.parametrize('text',['Write exactly 12 words, at most 7 words.','اكتب ٣٠ كلمة بالضبط، بحد أقصى ٢٠ كلمة','Explain in exactly 200 words, at most 50 words.'])
def test_varied_word_conflicts(text):
    assert request_contract.analyze(text)['clarifications'][0]['code']=='conflicting_word_limits'

def test_readonly_still_blocks_explicit_writing_skill(client):
    p=pid(client)
    result=submit(client,p,'اشرح السبب فقط، لا تعدّل الملفات','read-only-core-001',requestedSkills=['docx-authoring'])
    assert result.status_code==422
    assert not client.get(f'/api/projects/{p}/skill-runs').json()

def test_preview_has_no_effect_and_returns_the_execution_clarification(client):
    p=pid(client);body={'request':'اكتب 200 كلمة بالضبط بحد أقصى 50 كلمة','idempotencyKey':'preview-core-001'}
    preview=client.post(f'/api/projects/{p}/skill-request-preview',json=body)
    assert preview.status_code==200 and preview.json()['effects']=='none'
    submitted=client.post(f'/api/projects/{p}/skill-runs',json=body)
    assert submitted.status_code==422
    assert submitted.json()['detail']['questions']==preview.json()['request_record']['clarifications']
    assert client.get(f'/api/projects/{p}/skill-runs').json()==[]

def test_chat_conflicting_constraints_rejected_before_message_or_model(client,monkeypatch):
    from backend import app as service
    def denied(*args,**kwargs):pytest.fail('A contradictory request dispatched a model')
    monkeypatch.setattr(service.providers,'validate_provider',denied)
    p=pid(client);cid=client.post('/api/conversations',json={'project_id':p}).json()['id']
    result=client.post(f'/api/conversations/{cid}/generate',json={'content':'Write exactly 200 words, at most 50 words.'})
    assert result.status_code==422 and result.json()['detail']['code']=='request_clarification_required'
    assert client.get(f'/api/conversations/{cid}').json()['messages']==[]

def test_preview_rejects_unavailable_project_and_file(client):
    p=pid(client);body={'request':'لخص الملف','idempotencyKey':'preview-denied-001','inputs':{'documentIds':['not-visible']}}
    assert client.post(f'/api/projects/{p}/skill-request-preview',json=body).status_code in {403,404}
    body['inputs']={}
    assert client.post('/api/projects/foreign-project/skill-request-preview',json=body).status_code in {403,404}

def test_status_with_changed_inputs_does_not_discard_new_intent(client):
    p=pid(client);created=submit(client,p,'اشرح المطلوب','status-changed-001')
    current=wait(client,p,created.json()['id'])
    result=submit(client,p,'وين وصلنا؟','status-changed-002',previousRunId=current['id'],inputs={'text':'new instruction'})
    assert result.status_code==409
    assert len(client.get(f'/api/projects/{p}/skill-runs').json())==1

@pytest.mark.parametrize('text',['اشرح هذا الرسم فقط','Explain this chart and results file','راجع ملخص التقرير'])
def test_referenced_output_is_not_an_instruction_to_create_another(text):
    assert request_contract.analyze(text)['deliverables']==[]

def test_single_arabic_attachment_five_exact_source_bullets_and_reopen(client,monkeypatch):
    from tests.test_skill_batch02_content import upload
    from backend import providers
    def forbidden(*args,**kwargs):pytest.fail('Extractive summary called a model')
    monkeypatch.setattr(providers,'stream',forbidden)
    p=pid(client)
    sentences=['بدأ المشروع في نوفمبر.','الميزانية المعتمدة 20 شيكلًا.','يتكون الفريق من ثلاثة أعضاء.','موعد التسليم يوم الثلاثاء.','يتطلب الاعتماد مراجعة العميل.']
    fid=upload(client,p,'\n'.join(sentences),'تقرير_المشروع.txt')
    created=submit(client,p,'لخّصه بالعربي بخمس نقاط وخليهن قصار','five-source-bullets-001',inputs={'documentIds':[fid]})
    assert created.status_code==202,created.text
    current=wait(client,p,created.json()['id'])
    summary=current['result']['summary']
    assert summary['mode']=='extractive'
    assert [row['text'] for row in summary['bullets']]==sentences
    assert all(row['quote']==row['text'] and row['source_id'] for row in summary['bullets'])
    assert current['result']['request_record']['resolved_resource']['file_id']==fid
    assert not current['result']['request_coverage']['gaps']
    reopened=client.get(f'/api/projects/{p}/skill-runs/{current["id"]}').json()
    assert reopened['result']['summary']==summary

def test_word_count_of_conversation_does_not_certify_different_file_content(client):
    p=pid(client)
    created=submit(client,p,'أنشئ ملف نتائج من 5 كلمة بالضبط','file-wordcount-001',requestedSkills=['docx-authoring'],
        inputs={'text':'one two three four five','artifact':{'kind':'docx','payload':{'title':'Separate file','paragraphs':[{'text':'A different document containing many more words than the approved maximum.'}]}}})
    assert created.status_code==202,created.text
    result=wait(client,p,created.json()['id'])
    assert result['status']=='partial' and result['receipt']['verification']=='partial'
    coverage=result['result']['request_coverage']
    assert coverage['checked_deliverables']['file'] is True
    assert {'kind':'constraint','id':'word_count','state':'not_verified_in_saved_file'} in coverage['gaps']
