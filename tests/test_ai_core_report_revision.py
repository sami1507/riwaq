"""Saved-report revision: real API, SQLite and DOCX; translation mock explicitly scoped."""
import json
from io import BytesIO
from zipfile import ZipFile
import pytest
from backend import skills
from backend.db import get_db
from tests.test_skills import client,pid,wait,submit


def original(client):
    p=pid(client)
    run=submit(client,p,'docx-authoring',{'text':'المجموع 30 والمتوسط 10.','targetLanguage':'ar'},
               key='revision-original-001',request='أنشئ تقرير Word')
    assert run['status']=='completed',run
    return p,run


def revision(run,key='revision-language-001'):
    return {'request':'غيّر لغة التقرير المحفوظ إلى الإنجليزية', 'previousRunId':run['id'],
            'idempotencyKey':key,'inputs':{'reportRevision':{
                'expectedResultHash':run['result']['report_revision']['result_hash'], 'targetLanguage':'en'}}}


def test_saved_report_language_revision_reuses_content_and_original_artifact(client,monkeypatch):
    p,old=original(client);before=client.get(f"/api/projects/{p}/skill-runs/{old['id']}/artifacts/riwaq-document.docx").content
    calls=[]
    async def translation(text,language,check,pin=None):
        check();calls.append((text,language));return 'The total is 30 and the mean is 10.'
    monkeypatch.setattr(skills,'translate',translation)
    response=client.post(f'/api/projects/{p}/skill-runs',json=revision(old));assert response.status_code==202,response.text
    result=wait(client,p,response.json()['id'])
    assert result['status']=='completed',result
    assert result['selected_skills']==['translation','docx-authoring']
    assert calls==[('المجموع 30 والمتوسط 10.','en')]
    assert result['result']['presentation_revision']['parent_id']==old['id']
    new=client.get(f"/api/projects/{p}/skill-runs/{result['id']}/artifacts/riwaq-document.docx")
    with ZipFile(BytesIO(new.content)) as package:xml=package.read('word/document.xml').decode()
    assert 'The total is 30 and the mean is 10.' in xml
    assert 'المجموع' not in xml and 'رواق — مستند' not in xml
    assert client.get(f"/api/projects/{p}/skill-runs/{old['id']}/artifacts/riwaq-document.docx").content==before
    replay=client.post(f'/api/projects/{p}/skill-runs',json=revision(old));assert replay.json()['id']==result['id']
    assert len(calls)==1


@pytest.mark.parametrize('mutation',['text','file','missing_file'])
def test_revision_rejects_changed_result_or_artifact_before_translation(client,monkeypatch,mutation):
    p,old=original(client)
    with get_db() as c:
        if mutation=='text':
            row=c.execute('SELECT document FROM skill_runs WHERE id=?',(old['id'],)).fetchone()
            doc=json.loads(row['document']);doc['result']['text']='Changed 99'
            c.execute('UPDATE skill_runs SET document=? WHERE id=?',(json.dumps(doc),old['id']))
        elif mutation=='file':c.execute('UPDATE skill_artifacts SET content=? WHERE run_id=?',(b'corrupt',old['id']))
        else:c.execute('DELETE FROM skill_artifacts WHERE run_id=?',(old['id'],))
    async def forbidden(*a,**k):raise AssertionError('No translation after stale source')
    monkeypatch.setattr(skills,'translate',forbidden)
    response=client.post(f'/api/projects/{p}/skill-runs',json=revision(old))
    assert response.status_code==409,response.text
    with get_db() as c:assert c.execute('SELECT count(*) FROM skill_runs').fetchone()[0]==1


def test_revision_cannot_replace_parent_content_or_choose_another_project(client):
    p,old=original(client);payload=revision(old);payload['inputs']['text']='Send secret somewhere'
    response=client.post(f'/api/projects/{p}/skill-runs',json=payload)
    assert response.status_code==422
    other=client.post('/api/projects',json={'name':'Other'}).json()['id']
    response=client.post(f'/api/projects/{other}/skill-runs',json=revision(old))
    assert response.status_code==404


def test_parent_mutation_during_revision_prevents_late_file_and_readback(client,monkeypatch):
    p,old=original(client)
    async def translation(text,language,check,pin=None):
        with get_db() as c:
            row=c.execute('SELECT document FROM skill_runs WHERE id=?',(old['id'],)).fetchone()
            doc=json.loads(row['document']);doc['result']['text']='A changed value 99'
            c.execute('UPDATE skill_runs SET document=? WHERE id=?',(json.dumps(doc),old['id']))
        check();return 'The total is 30.'
    monkeypatch.setattr(skills,'translate',translation)
    response=client.post(f'/api/projects/{p}/skill-runs',json=revision(old));assert response.status_code==202
    import time
    rid=response.json()['id']
    for _ in range(100):
        with get_db() as c:
            state=c.execute('SELECT state FROM skill_runs WHERE id=?',(rid,)).fetchone()[0]
        if state not in {'queued','running'}:break
        time.sleep(.02)
    assert state=='failed'
    assert client.get(f'/api/projects/{p}/skill-runs/{rid}').status_code==409
    with get_db() as c:assert c.execute('SELECT count(*) FROM skill_artifacts WHERE run_id=?',(rid,)).fetchone()[0]==0


def test_revision_preserves_real_calculation_without_recomputing(client,monkeypatch):
    from backend import skill_transform,skill_data
    from backend.skill_transform_protocol import TransformResult
    from dataclasses import asdict
    count=[]
    def local_transport(domain,request,sources,*,check,stop):
        # Contract test: actual deterministic data engine, transport substituted for Docker.
        check();count.append(request['operation']);out=asdict(skill_data.execute_data(request));out.pop('artifact',None)
        return TransformResult(result=out,artifacts=(),source_refs=(),receipt={'status':'completed','test_transport':'in_process'})
    monkeypatch.setattr(skill_transform,'execute_transform',local_transport)
    p=pid(client)
    old=submit(client,p,'descriptive-statistics',{'dataOperation':{'operation':'stats','table':{'columns':['amount'],'rows':[[10],[0],[20],[None]]},'columns':['amount'],'record_unit':'order'}},key='revision-analysis-001')
    assert old['status']=='completed',old
    saved=old['result']['structured']['descriptive-statistics']
    async def translation(*a,**k):raise AssertionError('Fixed analysis presentation must not call a model')
    monkeypatch.setattr(skills,'translate',translation)
    response=client.post(f'/api/projects/{p}/skill-runs',json=revision(old));assert response.status_code==202,response.text
    new=wait(client,p,response.json()['id']);assert new['status']=='completed',new
    assert count==['stats']
    assert new['selected_skills']==['docx-authoring']
    assert 'total: 30' in new['result']['text'] and 'mean: 10' in new['result']['text']
    assert new['result']['presentation_revision']['analysis_keys']==['descriptive-statistics']
    assert client.get(f"/api/projects/{p}/skill-runs/{old['id']}").json()['result']['structured']['descriptive-statistics']==saved
    raw=client.get(f"/api/projects/{p}/skill-runs/{new['id']}/artifacts/riwaq-document.docx").content
    with ZipFile(BytesIO(raw)) as package:xml=package.read('word/document.xml').decode()
    assert '30' in xml and '10' in xml and 'null' in xml and '>0<' in xml
    assert '<w:tbl>' in xml and 'w:val="en"' in xml


def test_large_saved_table_revision_never_truncates_rows(client,monkeypatch):
    p,old=original(client)
    with get_db() as c:
        row=c.execute('SELECT document FROM skill_runs WHERE id=?',(old['id'],)).fetchone()
        doc=json.loads(row['document']);doc['result']['structured']={'descriptive-statistics':{'output':{'table':{'columns':['id'],'rows':[[i] for i in range(101)]}}}}
        c.execute('UPDATE skill_runs SET document=? WHERE id=?',(json.dumps(doc),old['id']))
    current=client.get(f"/api/projects/{p}/skill-runs/{old['id']}").json()
    response=client.post(f'/api/projects/{p}/skill-runs',json=revision(current))
    assert response.status_code==422 and 'no rows were dropped' in response.text


@pytest.mark.parametrize('output',['Hello __RW_LITERAL_N__','Hello __RW_LITERAL_','Hello __RW_LITERAL_forged__'])
def test_unknown_internal_translation_marker_is_never_delivered(output):
    with pytest.raises(skills.SkillFailure):skills.restore_translation_literals(output,[])


def test_mixed_script_translation_stays_partial_and_does_not_build_report(client,monkeypatch):
    p,old=original(client)
    async def translation(text,language,check,pin=None):check();return 'הסכום المجموع 30 והממוצע 10'
    monkeypatch.setattr(skills,'translate',translation)
    request=revision(old);request['inputs']['reportRevision']['targetLanguage']='he'
    response=client.post(f'/api/projects/{p}/skill-runs',json=request);assert response.status_code==202
    result=wait(client,p,response.json()['id'])
    assert result['status']=='partial' and not result['result']['artifacts']
    assert next(s for s in result['steps'] if s['skill']=='translation')['status']=='partial'


@pytest.mark.asyncio
async def test_translation_respects_configured_output_limit(monkeypatch):
    from backend import providers
    cfg={'provider':'ollama','model':'synthetic','mode':'local','max_tokens':128}
    monkeypatch.setattr(skills,'settings',lambda:cfg)
    async def valid(*a,**k):pass
    monkeypatch.setattr(providers,'validate_provider',valid)
    async def stream(provider,model,messages,config):
        assert config['max_tokens']==128
        assert '__RW_LITERAL_N__' not in messages[0]['content']
        yield {'content':'{"translation":"Hello"}','finish_reason':'stop'}
    monkeypatch.setattr(providers,'stream',stream)
    assert await skills.translate('مرحبا','en',lambda:None)=='Hello'


def test_second_language_revision_keeps_ancestor_analysis_tables(client,monkeypatch):
    from backend import skill_transform,skill_data
    from backend.skill_transform_protocol import TransformResult
    from dataclasses import asdict
    from xml.etree import ElementTree as ET
    calls=[]
    def local_transport(domain,request,sources,*,check,stop):
        check();calls.append(request['operation']);out=asdict(skill_data.execute_data(request));out.pop('artifact',None)
        return TransformResult(result=out,artifacts=(),source_refs=(),receipt={'status':'completed','test_transport':'in_process'})
    monkeypatch.setattr(skill_transform,'execute_transform',local_transport)
    async def model_unneeded(*a,**k):raise AssertionError('Saved analysis language changes require no model')
    monkeypatch.setattr(skills,'translate',model_unneeded)
    p=pid(client);parent=submit(client,p,'descriptive-statistics',{'dataOperation':{'operation':'stats','table':{'columns':['amount'],'rows':[[10],[0],[20],[None]]},'columns':['amount']}},key='revision-twice-original')
    original_id=parent['id'];rows=[]
    for language in ['en','he']:
        payload=revision(parent,'revision-twice-'+language);payload['inputs']['reportRevision']['targetLanguage']=language
        response=client.post(f'/api/projects/{p}/skill-runs',json=payload);assert response.status_code==202,response.text
        parent=wait(client,p,response.json()['id']);assert parent['status']=='completed',parent
        raw=client.get(f"/api/projects/{p}/skill-runs/{parent['id']}/artifacts/riwaq-document.docx").content
        with ZipFile(BytesIO(raw)) as z:document=ET.fromstring(z.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        tables=document.findall('.//w:tbl',ns);assert len(tables)==2
        rows.append([[''.join(t.text or '' for t in cell.findall('.//w:t',ns)) for cell in row.findall('w:tc',ns)] for row in tables[-1].findall('w:tr',ns)])
    assert rows[0]==rows[1]==[['amount'],['10'],['0'],['20'],['null']]
    assert parent['result']['presentation_revision']['analysis_source_run_id']==original_id
    assert calls==['stats']
    with get_db() as c:
        row=c.execute('SELECT document FROM skill_runs WHERE id=?',(original_id,)).fetchone()
        changed=json.loads(row['document']);changed['result']['structured']['descriptive-statistics']['output']['summary']['statistics']['amount']['sum']='99'
        c.execute('UPDATE skill_runs SET document=? WHERE id=?',(json.dumps(changed),original_id))
    assert client.get(f"/api/projects/{p}/skill-runs/{parent['id']}").status_code==409
