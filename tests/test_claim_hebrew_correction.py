"""Known failed language path plus nearby independently asserted quantities."""
import json
import pytest
from backend import claim_checks,research_answer
from test_research_chat import client

QUESTION='בטיוטה כתוב שמשך הבדיקה הוא 80 דקות. תקן את משך הבדיקה לפי הקובץ וענה בעברית.'
SOURCE='משך הבדיקה הוא 8 דקות. במלאי יש 80 פריטים.'


@pytest.mark.parametrize('label,actual,draft,unit,canonical',[
    ('משך הבדיקה','8','80','דקות','minute'),
    ('אורך המסלול','17.5','175','מטרים','m'),
])
def test_named_hebrew_field_exact_quantity(label,actual,draft,unit,canonical):
    question=f'בטיוטה כתוב ש{label} הוא {draft} {unit}. תקן את {label} לפי הקובץ וענה בעברית.'
    source=f'{label} הוא {actual} {unit}. במלאי יש 175 פריטים.'
    result=claim_checks.literal_quantity_correction(question,[{'text':source}],research_answer._UNIT_ALIASES)
    assert result is not None
    from fractions import Fraction
    assert result['value']==str(Fraction(actual)) and result['unit']==canonical
    assert result['quote'] in source and result['language']=='he'


@pytest.mark.parametrize('source',[
    'משך הבדיקה הוא לא 8 דקות.',
    'אולי משך הבדיקה הוא 8 דקות.',
    'אם יש חשמל, משך הבדיקה הוא 8 דקות.',
    'משך הבדיקה הוא 8 דקות או 9 דקות.',
    'משך הבדיקה הוא 8 דקות. משך הבדיקה הוא 9 דקות.',
    'משך הבדיקה הוא 8 שעות.',
    'משך הניסוי הוא 8 דקות.',
])
def test_hebrew_correction_does_not_invent_positive_or_matching_evidence(source):
    assert claim_checks.literal_quantity_correction(QUESTION,[{'text':source}],research_answer._UNIT_ALIASES) is None


def test_hebrew_target_mismatch_is_not_a_requested_correction():
    assert claim_checks.literal_quantity_correction(QUESTION.replace('תקן את משך הבדיקה','תקן את משך הניסוי'),[{'text':SOURCE}],research_answer._UNIT_ALIASES) is None


def test_authenticated_hebrew_correction_is_saved_with_literal_source(client,monkeypatch):
    pid=client.get('/api/projects').json()[0]['id'];cid=client.post('/api/conversations',json={'project_id':pid}).json()['id']
    assert client.post(f'/api/projects/{pid}/files',files={'file':('duration.txt',SOURCE.encode(),'text/plain')}).status_code==201
    async def unexpected(*args,**kwargs):
        raise AssertionError('A supported literal quantity uses verified source data')
        yield {}
    monkeypatch.setattr(research_answer.providers,'stream',unexpected)
    response=client.post(f'/api/conversations/{cid}/generate',json={'content':QUESTION,'use_files':True})
    assert response.status_code==200,response.text
    message=json.loads(response.text.splitlines()[-1])['message']
    assert message['research']['model_calls']==0
    assert '8 דקות' in message['content'] and '80 דקות' not in message['content']
    assert message['research']['deterministic_correction']['value']=='8'
    assert message['research']['validation']['semantic_support']=='not_certified'
    assert client.get(f'/api/conversations/{cid}').json()['messages'][-1]['content']==message['content']
