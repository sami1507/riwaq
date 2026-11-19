"""Opt-in real local model/document integration. Never contacts a cloud provider."""
import json
import os
from pathlib import Path
import time
from io import BytesIO
from zipfile import ZipFile
import pytest
from backend.db import get_db
from test_documents import client, project, upload, wait as wait_document

pytestmark=pytest.mark.skipif(os.environ.get('RIWAQ_TEST_LIVE_SKILLS')!='1',reason='Explicit local model and reviewed Docker acceptance opt-in required')
ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'reports/skills-batch-01/live-local.json'

def record(value):
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    data=json.loads(REPORT.read_text('utf-8')) if REPORT.exists() else {'mocked':False,'cloud_calls':0,'fixture_rights':'Original synthetic development fixtures; not held-out language evaluation','cases':[]}
    data['cases'].append(value)
    REPORT.write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf-8')

def task(c,p,payload):
    start=time.monotonic()
    response=c.post(f'/api/projects/{p}/skill-runs',json=payload)
    assert response.status_code==202,response.text
    rid=response.json()['id'];deadline=start+190
    while time.monotonic()<deadline:
        result=c.get(f'/api/projects/{p}/skill-runs/{rid}').json()
        if result['status'] not in {'queued','running'}:return result,round(time.monotonic()-start,3)
        time.sleep(.1)
    raise AssertionError('Live local skill deadline')

@pytest.mark.parametrize('source,target,text',[
    ('ar','en','السعر ليس 42 ₪. لا تغيّر الموعد 2026-11-15.'),
    ('en','he','The price is not 42 ₪. Keep the date 2026-11-15.'),
    ('he','ar','המחיר אינו 42 ₪. התאריך הוא 2026-11-15.'),
])
def test_live_translation(client,source,target,text):
    p=project(client)
    with get_db() as c:
        c.execute("INSERT OR REPLACE INTO settings VALUES('provider','\"ollama\"')")
        c.execute("INSERT OR REPLACE INTO settings VALUES('model','\"qwen3:4b-instruct\"')")
    result,seconds=task(client,p,{'request':'ترجم النص كاملًا','requestedSkills':['translation'],'inputs':{'text':text,'targetLanguage':target},'idempotencyKey':f'live-{source}-{target}'})
    record({'case':'translation','source_language':source,'target_language':target,'input':text,'output':result['result'].get('text'),'status':result['status'],'seconds':seconds,'receipt':result['receipt'],'human_review':False})
    assert result['status']=='completed',result
    assert result['result']['quality_status']=='needs_review'

def test_live_pdf_to_editable_referenced_document(client):
    p=project(client)
    path=ROOT/'reports/completion-documents-ui/2026-11-15T19-20-40.684Z/native-evidence.pdf'
    assert path.is_file(),'Previously authored synthetic PDF fixture is required'
    f=upload(client,p,'skills-native-fixture.pdf',path.read_bytes())['id']
    extracted=wait_document(client,p,f);assert extracted['status']=='complete',extracted
    result,seconds=task(client,p,{'request':'اقرأ PDF وأنشئ Word','requestedSkills':['docx-authoring','pdf-reading'],'inputs':{'documentIds':[f]},'idempotencyKey':'live-pdf-docx'})
    record({'case':'pdf-to-docx','status':result['status'],'seconds':seconds,'steps':result['steps'],'references':len(result['result'].get('source_refs',[])),'human_review':False})
    assert result['status']=='completed',result
    assert result['selected_skills']==['pdf-reading','docx-authoring']
    artifact=result['result']['artifacts'][0]
    raw=client.get(f'/api/projects/{p}/skill-runs/{result["id"]}/artifacts/{artifact["name"]}').content
    with ZipFile(BytesIO(raw)) as z:
        xml=z.read('word/document.xml').decode()
        assert '42 kPa' in xml and '99 kPa' in xml and 'evidence ' in xml
    for ref in result['result']['source_refs']:
        evidence=client.get(ref['url']);assert evidence.status_code==200
        assert evidence.json()['page'] in {1,2}
    (ROOT/'reports/skills-batch-01/live-pdf-output.docx').write_bytes(raw)
