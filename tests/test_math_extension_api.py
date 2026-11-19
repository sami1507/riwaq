"""New operations through authenticated HTTP, real worker, and stored receipts."""
import json
import pytest
from test_math_api import client,chat,calculate,saved


@pytest.mark.parametrize('text',[
    'حل 2x+3=11',
    'ارسم 1/x واشرح سلوكها قرب الصفر',
    'مثال افتراضي لنفس السكان والسنة: انخفض الخطر من 2 في 1000 إلى 1 في 1000',
    'حوّل 500 mcg إلى mg في تمرين وحدات',
])
def test_extension_is_saved_and_read_from_persistent_receipt(client,text):
    cid=chat(client);message,record=saved(calculate(client,cid,text))
    assert record['original_text']==text
    restored=client.get(f'/api/conversations/{cid}').json()['messages'][-1]
    assert restored['id']==message['id']
    check=next(c for c in restored['run']['checks'] if c['kind']=='solve_math')
    assert json.loads(check['details'])['result']==record['result']
    if record['request']['operation']=='plot':
        assert len(record['result']['series'])==2
    if record['request']['operation']=='solve':
        assert record['result']['teaching_steps'] and record['result']['substitution']['verified']


def test_mean_correction_recomputes_in_existing_edit_flow(client):
    cid=chat(client)
    original='احسب المتوسط للقيم 0 و10 و25 وnull وفق قاعدة استبعاد المفقود فقط'
    first,before=saved(calculate(client,cid,original))
    original_id=client.get(f'/api/conversations/{cid}').json()['messages'][0]['id']
    after_text=original.replace('25','20')
    second,after=saved(calculate(client,cid,after_text,edit_message_id=original_id))
    assert first['id']!=second['id'] and before['result']['sum']=='35'
    assert after['result']['sum']=='30' and after['result']['mean']=='10' and after['result']['known']==3
    assert after['original_text']==after_text
