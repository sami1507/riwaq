"""Real local parser integration; provider and fault simulations are labelled separately."""
import json
import time
import threading
import os
from pathlib import Path
from fractions import Fraction
from authority_fixture import fixture_owner
import pytest
from fastapi.testclient import TestClient
from backend import app as service, documents, knowledge
from backend.db import get_db


@pytest.fixture
def client(tmp_path,monkeypatch):
    configured=os.environ.get('RIWAQ_TEST_ISOLATED_CONFIG')
    if not configured:pytest.skip('Real document integration requires explicit local Docker config via RIWAQ_TEST_ISOLATED_CONFIG; no host fallback')
    from backend.isolated_worker import load_config
    runtime=load_config(configured)
    if 'document_page' not in runtime.images:pytest.skip('Explicit test config has no reviewed document_page image')
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    (tmp_path/'isolated-workers.json').write_text(runtime.model_dump_json(),encoding='utf-8')
    service.ACTIVE.clear()
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as c:
        from backend.local_auth import issue_pairing_code
        assert c.post('/api/auth/login', json={'pairing_code': issue_pairing_code()}).status_code == 200
        yield c
    assert not documents.JOBS


def project(c):return c.get('/api/projects').json()[0]['id']
def base(pid,fid=''):return f'/api/projects/{pid}/documents'+('/'+fid if fid else '')
def upload(c,pid,name,data):
    r=c.post(base(pid),files={'file':(name,data)},data={'languages':'ara+eng+heb'})
    assert r.status_code==202,r.text
    return r.json()
def wait(c,pid,fid):
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        row=c.get(base(pid,fid)).json()
        if row['status'] not in {'queued','running','cancelling'}:return row
        time.sleep(.05)
    raise AssertionError('Job did not settle')


def test_real_text_original_correction_scope_and_delete(client):
    p=project(client);raw='رواق يحفظ الأصل.\nالقيمة الموثقة 27 مترًا.\n'.encode()
    f=upload(client,p,'evidence.txt',raw)['id'];row=wait(client,p,f)
    assert row['status']=='complete',row
    assert row['pages_done']==row['page_count']==1
    assert client.get(base(p,f)+'/original').content==raw
    page=client.get(base(p,f)+'/pages/1').json();e=page['elements'][0]
    with fixture_owner(), get_db() as c:
        hit=knowledge.retrieve(c,p,'الموثقة',file_scope=[{'file_id':f,'pages':[1]}])[0]
        assert hit['document']['file_id']==f and hit['document']['page']==1
        assert hit['page'] is None  # TXT does not have printed PDF pages.
    corrected=client.patch(base(p,f)+'/elements/'+e['id'],json={'text':'القيمة المصححة 28 مترًا.','expected_revision':1})
    assert corrected.status_code==200,corrected.text
    assert corrected.json()['text_raw']==e['text_raw']
    assert corrected.json()['revision']==2
    assert client.patch(base(p,f)+'/elements/'+e['id'],json={'text':'stale','expected_revision':1}).status_code==409
    versions=client.get(base(p,f)+'/elements/'+e['id']+'/versions').json()
    assert len(versions)==2 and versions[0]['text']==e['text']
    with get_db() as c:assert not c.execute('SELECT 1 FROM knowledge_chunks WHERE id=?',(hit['id'],)).fetchone()
    other=client.post('/api/projects',json={'name':'Other'}).json()['id']
    for suffix in ('','/original','/pages/1','/pages/1/preview','/elements/'+e['id']+'/versions'):
        assert client.get(base(other,f)+suffix).status_code==404
    assert client.patch(base(other,f)+'/elements/'+e['id'],json={'text':'leak','expected_revision':2}).status_code==404
    assert client.delete(f'/api/projects/{other}/files/{f}').status_code==404
    assert client.delete(f'/api/projects/{p}/files/{f}').status_code==200
    assert client.get(base(p,f)+'/original').status_code==404
    with get_db() as c:
        for table in ('knowledge_chunks','document_originals','document_pages','document_elements','document_element_versions','document_chunk_refs','document_jobs'):
            assert c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0


def test_real_csv_exact_calculation_dedup_and_whole_range(client):
    p=project(client);raw=b'id,amount,currency\n001,0.1,ILS\n002,0.2,ILS\n003,,ILS\n004,8,USD\n'
    f=upload(client,p,'table.csv',raw)['id'];assert wait(client,p,f)['status']=='complete'
    assert upload(client,p,'table.csv',raw)['id']==f
    e=client.get(base(p,f)+'/pages/1').json()['elements'][0]
    cells={x['address']:x for x in e['cells']}
    assert cells['A2']['value']=='001' and cells['B4']['value']==''
    def calc(**opts):return client.post(base(p,f)+'/table-calculate',json={'element_id':e['id'],'operation':'sum','column':'B','row_start':2,'row_end':3,'unit':'ILS',**opts})
    r=calc();assert r.status_code==200,r.text
    assert r.json()['result']['exact']=='3/10' and r.json()['result']['decimal']=='0.3'
    assert [x['address'] for x in r.json()['cellrefs']]==['B2','B3']
    assert r.json()['scope']['all_requested_cells']
    assert calc(row_end=4).status_code==422
    assert calc(column='A').status_code==422  # Leading-zero IDs are not amounts.
    assert calc(row_start=5,row_end=5).status_code==422  # conflicting requested unit
    assert calc(row_end=7).status_code==422  # missing requested rows
    assert calc(operation='count',row_end=4,unit='').json()['result']['exact']=='2'
    assert client.patch(base(p,f)+'/elements/'+e['id'],json={'text':'fake grid','expected_revision':1}).status_code==422


def test_actual_invalid_input_never_becomes_document(client):
    p=project(client)
    for name,data in [('broken.pdf',b'%PDF-1.7 broken'),('image.png',b'plain text'),('old.xls',b'old')]:
        r=client.post(base(p),files={'file':(name,data)})
        assert r.status_code==422,r.text
    assert client.get(base(p)).json()==[]


def test_simulated_late_worker_cannot_republish_after_delete(client,monkeypatch):
    p=project(client);started=threading.Event();release=threading.Event()
    real=documents.parse
    def delayed(data,name,page,lang,stop,authorize=None):
        if page:
            started.set();release.wait(5)
            # Deliberately ignore cancellation to model a stale external response.
            result={'page':{'number':1,'status':'complete','kind':'text','elements':[{'id':'x','type':'text','text':'private','text_raw':'private','locator':{},'bbox':None}]}}
            return result,None,{'seconds':1}
        return real(data,name,page,lang,stop,authorize=authorize)
    monkeypatch.setattr(documents,'parse',delayed)
    f=upload(client,p,'late.txt',b'private')['id'];assert started.wait(5)
    assert client.delete(f'/api/projects/{p}/files/{f}').status_code==200
    release.set()
    for _ in range(100):
        if f not in documents.JOBS:break
        time.sleep(.02)
    with get_db() as c:
        assert c.execute('SELECT COUNT(*) FROM document_pages').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM knowledge_chunks').fetchone()[0]==0


def test_simulated_failed_page_resume_reuses_good_page(client,monkeypatch):
    p=project(client);calls=[];fail=[True]
    def fake(data,name,page,lang,stop,authorize=None):
        if not page:return {'page_count':2,'mime':'application/pdf','metadata':{}},None,{}
        calls.append(page)
        if page==2 and fail[0]:raise ValueError('Controlled page failure')
        return {'page':{'number':page,'status':'complete','kind':'native','elements':[{'id':f'p{page}-e1','type':'text','text':'evidence','text_raw':'evidence','locator':{'page':page},'bbox':None}]}},None,{'seconds':0}
    monkeypatch.setattr(documents,'parse',fake)
    f=upload(client,p,'controlled.pdf',b'%PDF-fixture')['id'];assert wait(client,p,f)['status']=='partial'
    fail[0]=False
    assert client.post(base(p,f)+'/resume').status_code==200
    assert wait(client,p,f)['status']=='complete'
    assert calls==[1,2,2]


def test_scope_blocks_wrong_project_before_provider_and_restores_retry(client,monkeypatch):
    p=project(client);f=upload(client,p,'trusted.txt','قيمة المشروع الموثقة 27 مترًا'.encode())['id'];wait(client,p,f)
    other=client.post('/api/projects',json={'name':'Other'}).json()['id']
    cid=client.post('/api/conversations',json={'project_id':other}).json()['id']
    async def forbidden(*a,**k):pytest.fail('Unauthorized scope reached provider')
    monkeypatch.setattr(service.providers,'validate_provider',forbidden)
    r=client.post(f'/api/conversations/{cid}/generate',json={'content':'قيمة','use_files':True,'file_scope':[{'file_id':f}]})
    assert r.status_code==404


def test_formula_and_units_rejected_without_guessing():
    b=documents.TableCalculation(element_id='e',operation='sum',column='B',row_start=2,row_end=2)
    with pytest.raises(Exception):documents.calculate_table([{'address':'B2','row':2,'column':'B','value':'42','formula':'=SUM(B3:B9)'}],b)
    with pytest.raises(Exception):documents.calculate_table([{'address':'B2','row':2,'column':'B','value':'42','formula':'','kind':'formula'}],b)
    b=documents.TableCalculation(element_id='e',operation='sum',column='B',row_start=2,row_end=3)
    with pytest.raises(Exception):documents.calculate_table([{'address':'B2','row':2,'column':'B','value':'2','unit':'m'},{'address':'B3','row':3,'column':'B','value':'3','unit':'s'}],b)


@pytest.mark.parametrize('kind,fmt,code,value',[('boolean',0,'General','1'),('number',164,'yyyy-mm-dd','46340'),('number',9,'0%','0.07'),('number',None,None,'42')])
def test_styled_or_non_numeric_cells_are_not_silently_amounts(kind,fmt,code,value):
    body=documents.TableCalculation(element_id='e',operation='sum',column='B',row_start=2,row_end=2)
    cell={'address':'B2','row':2,'column':2,'value':value,'kind':kind,'formula':None,'style_index':'0','number_format_id':fmt,'number_format':code}
    with pytest.raises(Exception):documents.calculate_table([cell],body)
    counted=documents.calculate_table([cell],body.model_copy(update={'operation':'count'}))[0]
    assert counted['exact']=='1'


def test_office_table_native_coordinate_is_preserved():
    body=documents.TableCalculation(element_id='e',operation='sum',column='B',row_start=2,row_end=2)
    cell={'address':'R2C2','row':2,'column':2,'value':'73','kind':'text'}
    result,refs,_=documents.calculate_table([cell],body)
    assert result['exact']=='73' and refs[0]['address']=='R2C2'


def test_real_comparison_uses_full_cells_and_rejects_other_project(client):
    p=project(client)
    a=upload(client,p,'v1.csv',b'id,amount,date\n001,19,2025-01-01\n002,,2025-01-01\n')['id']
    b=upload(client,p,'v2.csv',b'id,amount,date\n001,21,2026-01-01\n002,,2026-01-01\n')['id']
    wait(client,p,a);wait(client,p,b)
    body={'left':{'file_id':a},'right':{'file_id':b}}
    r=client.post(base(p)+'/compare',json=body);assert r.status_code==200,r.text
    result=r.json();assert result['kind']=='structural_difference' and result['total_differences']==3
    assert result['coverage']['left']=={'selected':1,'processed':1,'failed':0,'partial':0}
    amount=next(d for d in result['differences'] if d['left']['locator']['cells']==['B2'])
    assert json.loads(amount['before'])['value']=='19' and json.loads(amount['after'])['value']=='21'
    other=client.post('/api/projects',json={'name':'Other'}).json()['id']
    assert client.post(base(other)+'/compare',json=body).status_code==404


def test_real_original_versions_remain_immutable(client):
    p=project(client)
    a=upload(client,p,'versions.txt',b'evidence value 11')['id'];wait(client,p,a)
    b=upload(client,p,'versions.txt',b'evidence value 22')['id'];wait(client,p,b)
    assert a!=b
    assert client.get(base(p,a)).json()['version']==1 and client.get(base(p,b)).json()['version']==2
    assert client.get(base(p,a)+'/original').content==b'evidence value 11'
    with fixture_owner(), get_db() as c:
        result=knowledge.retrieve(c,p,'evidence',file_scope=[{'file_id':b}])
        assert result and all(x['document']['version']==2 for x in result)


def test_resume_refuses_mixed_extractor_recipes(client,monkeypatch):
    p=project(client);f=upload(client,p,'recipe.txt',b'original')['id'];wait(client,p,f)
    with get_db() as c:documents.set_status(c,f,'interrupted')
    monkeypatch.setattr(documents,'VERSION','future-extractor')
    r=client.post(base(p,f)+'/resume')
    assert r.status_code==409 and 'recipe changed' in r.text
    assert client.get(base(p,f)+'/original').content==b'original'


def test_comparison_rechecks_deletion_after_read(client,monkeypatch):
    p=project(client);a=upload(client,p,'left.csv',b'v\n12\n')['id'];b=upload(client,p,'right.csv',b'v\n13\n')['id'];wait(client,p,a);wait(client,p,b)
    real=documents.pack;deleted=[]
    def revoke(value):
        if isinstance(value,dict) and 'cached_value' in value and 'value' in value and not deleted:
            with get_db() as c:c.execute('DELETE FROM knowledge_files WHERE id=?',(a,))
            deleted.append(a)
        return real(value)
    monkeypatch.setattr(documents,'pack',revoke)
    r=client.post(base(p)+'/compare',json={'left':{'file_id':a},'right':{'file_id':b}})
    assert deleted and r.status_code==409,r.text


def test_correction_quota_and_concurrent_revision_are_atomic(client,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    p=project(client);f=upload(client,p,'editable.txt',b'original')['id'];wait(client,p,f)
    e=client.get(base(p,f)+'/pages/1').json()['elements'][0]
    path=base(p,f)+'/elements/'+e['id']
    def correction(value):return client.patch(path,json={'text':value,'expected_revision':1}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(correction,['first','second']))
    assert sorted(codes)==[200,409]
    monkeypatch.setattr(knowledge,'MAX_PROJECT_EXTRACTED',2)
    r=client.patch(path,json={'text':'too long','expected_revision':2})
    assert r.status_code==413
    history=client.get(path+'/versions').json();assert len(history)==2
    assert client.get(base(p,f)+'/pages/1').json()['elements'][0]['text'] in {'first','second'}


@pytest.mark.parametrize('pages',[[True],['1'],[1.0],[0],[201]])
def test_scope_page_types_are_strict(client,pages):
    p=project(client);cid=client.post('/api/conversations',json={'project_id':p}).json()['id']
    assert client.post(f'/api/conversations/{cid}/generate',json={'content':'x','use_files':True,'file_scope':[{'file_id':'x','pages':pages}]}).status_code==422


def test_simulated_deletion_during_model_stream_blocks_late_output(client,monkeypatch):
    p=project(client);f=upload(client,p,'secret.txt',b'evidence unique marker')['id'];wait(client,p,f)
    cid=client.post('/api/conversations',json={'project_id':p}).json()['id']
    async def validate(*a,**k):return None
    async def stream(*a,**k):
        yield {'content':'Before deletion. '}
        with get_db() as c:c.execute('DELETE FROM knowledge_files WHERE id=?',(f,))
        yield {'content':'LATE_PRIVATE_RESULT_MUST_NOT_APPEAR'}
    monkeypatch.setattr(service.providers,'validate_provider',validate);monkeypatch.setattr(service.providers,'stream',stream)
    r=client.post(f'/api/conversations/{cid}/generate',json={'content':'evidence unique','use_files':True,'file_scope':[{'file_id':f}]})
    assert r.status_code==200,r.text
    assert 'LATE_PRIVATE_RESULT' not in r.text
    events=[json.loads(x) for x in r.text.splitlines() if x]
    assert events[-1]['message']['status']=='error'
