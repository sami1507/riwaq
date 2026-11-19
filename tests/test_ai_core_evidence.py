"""Deterministic source/quote/coverage assertions, not a semantic model score."""
import asyncio
import copy
import importlib.util
import json
import os
from pathlib import Path

import pytest

from backend import grounding, research_answer as answer
from backend.research_schema import ResearchOptions

if os.environ.get('RIWAQ_AI_EVIDENCE_BEFORE') == '1':
    root=Path(__file__).resolve().parents[1]/'reports/ai-core-20261116/baseline/backend'
    def baseline(name):
        spec=importlib.util.spec_from_file_location('baseline_'+name,root/(name+'.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    answer=baseline('research_answer');grounding=baseline('grounding')

def claim(text,quote,source=1):
    return {'text':text,'kind':'source_statement','references':[{'source':source,'quote':quote}]}

@pytest.mark.parametrize('source,version',[
    ('The measured amount is 20 units. The unrelated shelf contains 200 items.',None),
    ('The measured amount is 20 units.','200'),
    ('القيمة المقاسة ٢٠ وحدة. يحتوي المخزن الآخر على ٢٠٠ قطعة.',None),
])
def test_cited_twenty_cannot_support_two_hundred_elsewhere(source,version):
    arabic=source.startswith('القيمة')
    proposed=claim('القيمة المقاسة ٢٠٠ وحدة.' if arabic else 'The measured amount is 200 units.',source.split('.')[0]+'.')
    result,errors=answer.validate(json.dumps({'claims':[proposed],'gaps':[]},ensure_ascii=False),[{'text':source,'version':version}])
    assert result is None
    assert 'number_without_evidence_provenance' in errors

def test_unrelated_quote_with_same_number_is_rejected():
    proposed=claim('There are 42 planets.','The measured result is 42 units.')
    result,errors=answer.validate(json.dumps({'claims':[proposed],'gaps':[]}),[{'text':'The measured result is 42 units.'}])
    assert result is None and 'citation_topic_mismatch' in errors

def test_duplicate_json_claim_fields_are_not_silently_overwritten():
    result,errors=answer.validate('{"claims":[],"claims":[],"gaps":[]}',[])
    assert result is None and 'invalid_answer_schema' in errors

@pytest.mark.parametrize('question',[
    'Calculate the total for the entire order table.',
    'ما إجمالي الجدول الكامل؟',
    'מה סכום כל השורות בטבלה?',
])
def test_full_table_aggregate_rejects_partial_excerpts_before_model(question):
    cfg={'context_size':8192,'max_tokens':1024}
    chunks=[{'id':'row-1','file_id':'orders','filename':'orders.csv','line_start':1,'line_end':2,'text':'order,amount\nO1,10'}]
    with pytest.raises(ValueError,match='complete.*table|full.*scope'):
        grounding.compose({'instructions':''},[],[],question,chunks,cfg,True)

def test_explicit_full_aggregate_research_has_zero_model_calls(monkeypatch):
    calls=[]
    async def forbidden(*args,**kwargs):
        calls.append(True)
        yield {'content':'invented total'}
    monkeypatch.setattr(answer.providers,'stream',forbidden)
    async def allowed():pass
    async def collect():
        receipt={}
        packets=[p async for p in answer.stream('synthetic','Compute the total of all rows in the table.',[{'id':'r1','text':'O1: 10 units.','title':'Orders'}],ResearchOptions(scopes=['knowledge'],response_language='en'),{'context_size':8192,'max_tokens':1024},receipt,allowed)]
        return receipt,packets
    receipt,packets=asyncio.run(collect())
    assert calls==[] and receipt['status']=='insufficient_evidence' and receipt['model_calls']==0
    assert any('complete' in p.get('content','') for p in packets)

def test_budget_coverage_preserves_each_candidate_document_before_second_excerpt():
    def chunk(id,file,text):return {'id':id,'file_id':file,'filename':file,'line_start':1,'line_end':2,'text':text}
    chunks=[chunk('a1','A','A'*600),chunk('a2','A','B'*600),chunk('b1','B','C'*600)]
    cfg={'context_size':4096,'max_tokens':1024}
    messages,selected=grounding.compose({'instructions':''},[],[],'Compare the constraints from A and B.',chunks,cfg,True)
    assert {c['file_id'] for c in selected}=={'A','B'}
    assert {c['id'] for c in selected}=={'a1','b1'}
    coverage=cfg['_context_budget']['evidence_coverage']
    assert coverage['candidate_resource_count']==coverage['selected_resource_count']==2
    assert coverage['complete_source_read'] is False
    assert 'retrieved excerpts' in messages[-1]['content'].lower()

def test_missing_document_in_budget_is_explicit_not_full_coverage():
    chunks=[{'id':str(i),'file_id':str(i),'filename':str(i),'line_start':1,'line_end':1,'text':'X'*1300} for i in range(2)]
    cfg={'context_size':4096,'max_tokens':1024}
    _,selected=grounding.compose({'instructions':''},[],[],'Compare both files.',chunks,cfg,True)
    assert len(selected)==1
    coverage=cfg['_context_budget']['evidence_coverage']
    assert coverage['omitted_resource_count']==1 and coverage['complete_source_read'] is False

def test_direct_fact_can_preserve_twenty_and_unit_without_aggregate_guard():
    raw=json.dumps({'claims':[claim('The measured amount is 20 units.','The measured amount is 20 units.')],'gaps':[]})
    result,errors=answer.validate(raw,[{'text':'The measured amount is 20 units.'}])
    assert not errors and '20 units' in answer.render(result,'en')

def test_cross_language_quote_is_not_claimed_as_semantic_certification():
    raw=json.dumps({'claims':[claim('القيمة المقاسة ٢٠ وحدة.','The measured amount is 20 units.')],'gaps':[]},ensure_ascii=False)
    result,errors=answer.validate(raw,[{'text':'The measured amount is 20 units.'}])
    assert result is not None and not errors

def test_research_revocation_after_model_blocks_answer_publication(monkeypatch):
    raw=json.dumps({'claims':[claim('The measured amount is 20 units.','The measured amount is 20 units.')],'gaps':[]})
    async def model(*args,**kwargs):
        yield {'content':raw}
        yield {'finish_reason':'stop'}
    monkeypatch.setattr(answer.providers,'stream',model)
    async def scenario():
        checks=0;emitted=[]
        async def check():
            nonlocal checks
            checks+=1
            if checks==2:raise PermissionError('Synthetic access revoked')
        with pytest.raises(PermissionError):
            async for packet in answer.stream('synthetic','What is the amount?',[{'text':'The measured amount is 20 units.'}],ResearchOptions(scopes=['knowledge']),{'context_size':8192,'max_tokens':1024},{},check):emitted.append(packet)
        assert all('content' not in p for p in emitted)
    asyncio.run(scenario())

def test_research_output_schema_is_accepted_by_actual_provider_contract(monkeypatch):
    from backend.output_contracts import validate_schema
    observed=[]
    async def model(*args,**kwargs):
        validate_schema(args[3]['output_schema'])
        observed.append(True)
        yield {'content':json.dumps({'claims':[claim('The amount is 20 units.','The amount is 20 units.')],'gaps':[]})}
        yield {'finish_reason':'stop'}
    monkeypatch.setattr(answer.providers,'stream',model)
    async def allowed():pass
    async def scenario():
        return [p async for p in answer.stream('synthetic','What is the amount?',[{'text':'The amount is 20 units.'}],ResearchOptions(scopes=['knowledge']),{'context_size':8192,'max_tokens':1024},{},allowed)]
    result=asyncio.run(scenario())
    assert observed==[True] and '20' in result[-1]['content']

def test_runtime_schema_still_rejects_logical_depth_six():
    from backend.output_contracts import validate_schema
    schema={'type':'string'}
    for _ in range(6):schema={'type':'object','properties':{'nested':schema},'required':['nested'],'additionalProperties':False}
    with pytest.raises(ValueError):validate_schema(schema)

def test_stated_total_question_is_not_confused_with_computing_full_scope():
    assert not grounding.full_scope_aggregate('What total does the quoted paragraph state?')
    assert not grounding.full_scope_aggregate('ما قيمة الإجمالي المذكورة في هذه الفقرة؟')

def test_same_validator_preserves_authorized_conversation_constraints(monkeypatch):
    prior=[{'role':'system','content':'Trusted project instruction.'},{'role':'user','content':'Answer in English and retain SKU-Z9.'},{'role':'assistant','content':'Acknowledged.'},{'role':'user','content':'What is the measured amount? [S1] The amount is 20 units.'}]
    sent=[]
    async def model(provider,model,messages,cfg):
        sent.extend(copy.deepcopy(messages))
        yield {'content':json.dumps({'claims':[claim('The amount is 20 units.','The amount is 20 units.')],'gaps':[]})}
        yield {'finish_reason':'stop'}
    monkeypatch.setattr(answer.providers,'stream',model)
    async def allowed():pass
    async def scenario():
        receipt={}
        packets=[p async for p in answer.stream('synthetic','What is the amount?',[{'text':'The amount is 20 units.'}],ResearchOptions(scopes=['files'],response_language='en'),{'context_size':8192,'max_tokens':1024},receipt,allowed,context_messages=prior)]
        return receipt,packets
    receipt,packets=asyncio.run(scenario())
    assert sent[1:]==prior[1:] and sent[0]['content'].startswith(prior[0]['content'])
    assert prior[0]['content']=='Trusted project instruction.'
    assert receipt['context_mode']=='authorized_conversation_preserved' and '20 units' in packets[-1]['content']

def test_internal_json_serialization_resolves_later_citation_policy():
    messages,_=grounding.compose({'instructions':'Keep the user language.'},[],[],'What is measured?',[],{'context_size':8192,'max_tokens':1024},True,system_extra='Internal JSON: references only, no markers in claim fields.',structured_evidence=True)
    system=messages[0]['content']
    assert 'Cite excerpts exactly' not in system
    assert 'Internal JSON:' in system
    assert 'Keep the user language.' in system


def test_structured_context_preserves_exact_sources_without_rendered_markers():
    chunk={'id':'c1','file_id':'f1','filename':'same.txt','text':'The amount is 20 units.','line_start':3,'line_end':3,'version':'v7'}
    messages,selected=grounding.compose({'instructions':'Keep the user language.'},[],[],'What is measured?',[chunk],{'context_size':8192,'max_tokens':1024},True,system_extra=answer.context_instructions('en'),structured_evidence=True)
    content=messages[-1]['content'];raw=content.split('<untrusted_source_excerpts>\n',1)[1].split('\n</untrusted_source_excerpts>',1)[0]
    evidence=json.loads(raw)
    assert evidence[0]['source']==1 and evidence[0]['text']==chunk['text'] and evidence[0]['version']=='v7'
    assert selected==[chunk] and '[S1]' not in raw


@pytest.mark.parametrize('text,quote',[
    ('The statement about 200 units refers to another shelf.','Another shelf contains 200 items.'),
    ('The mass is 17.5 kilograms.','The distance is 17.5 meters.'),
    ('القيمة ٢٠٠ وحدة.','المصدر يذكر ٢٠٠ قطعة.'),
    ('הערך הוא 200 יחידות.','המקור מציין 200 פריטים.'),
])
def test_known_unit_cannot_be_swapped_even_in_inference(text,quote):
    raw=json.dumps({'claims':[dict(claim(text,quote),kind='inference')],'gaps':[]},ensure_ascii=False)
    checked,errors=answer.validate(raw,[{'text':quote}])
    assert checked is None and 'number_unit_without_evidence_provenance' in errors

@pytest.mark.parametrize('text,quote',[
    ('The maximum load is 17.5 kg.','The maximum load is 17.5 kilograms.'),
    ('The crate contains 200 items.','The crate contains 200 items.'),
    ('الكتلة ٥ كيلوجرامات.','الكتلة ٥ كيلوجرامات.'),
    ('The machine uses 20 units.','The machine uses 20 units.'),
])
def test_known_unit_reference_accepts_genuine_measurements(text,quote):
    raw=json.dumps({'claims':[claim(text,quote)],'gaps':[]},ensure_ascii=False)
    checked,errors=answer.validate(raw,[{'text':quote}])
    assert checked is not None and errors==[]


def test_known_unit_cannot_be_borrowed_from_another_number_or_uncited_sentence():
    quote='The capacity is 20 units and the crate contains 200 items.'
    raw=json.dumps({'claims':[dict(claim('The capacity is 200 units.',quote),kind='inference')],'gaps':[]})
    checked,errors=answer.validate(raw,[{'text':quote+' Elsewhere a machine uses 200 units.'}])
    assert checked is None and 'number_unit_without_evidence_provenance' in errors
