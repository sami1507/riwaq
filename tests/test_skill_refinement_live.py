"""Opt-in official-source reading through Riwaq, not a mocked web connector."""
import json
import os
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
import pytest
from backend.db import get_db
from test_documents import client,project
from test_skills_live import task

pytestmark=pytest.mark.skipif(os.environ.get('RIWAQ_TEST_LIVE_REFINEMENT')!='1',reason='Explicit approved public-source network acceptance required')
ROOT=Path(__file__).resolve().parents[1]


def test_live_official_documentation_to_version_bound_word(client,monkeypatch):
    # Temporary synthetic DB only. Query is local; only registered official URLs leave it.
    monkeypatch.delenv('RIWAQ_EMBED_MODEL',raising=False)
    with get_db() as c:c.execute("INSERT OR REPLACE INTO settings VALUES('mode','\"hybrid\"')")
    p=project(client)
    result,seconds=task(client,p,{
        'request':'اقرأ مصدر Python الرسمي وأنشئ مستند Word بالمقاطع والمراجع',
        'requestedSkills':['web-research','citation-grounding','docx-authoring'],
        'inputs':{'researchQuestion':'Python 3.12 control flow range()',
                  'researchOptions':{'scopes':['web'],'source_id':'python312','version':'3.12','allow_network':True}},
        'idempotencyKey':'refine-live-python-word',
    })
    report=ROOT/'reports/skills-refinement-20261116/live-research.json'
    report.write_text(json.dumps({'date':'2026-11-16','mocked':False,'scope':'Official Python3.12 source excerpts, not general search or semantic verdict',
        'seconds':seconds,'status':result['status'],'steps':result['steps'],'receipt':result['receipt'],
        'research':result['result'].get('research_receipt'),'source_refs':result['result'].get('source_refs',[]),'error':result.get('error')},ensure_ascii=False,indent=2),'utf-8')
    assert result['status']=='completed',result
    receipt=result['result']['research_receipt']
    assert receipt['documents_read']>=1 and 0<receipt['network_requests']<=12
    assert receipt['external_query_sent'] is False
    refs=result['result']['source_refs'];assert refs
    for ref in refs:
        response=client.get(ref['url']);assert response.status_code==200
        assert response.json()['text'] and ref['version_id'] and ref['checked_at']
        assert ref['source_url'].startswith('https://docs.python.org/3.12/')
    item=result['result']['artifacts'][0]
    raw=client.get(f'/api/projects/{p}/skill-runs/{result["id"]}/artifacts/{item["name"]}').content
    with ZipFile(BytesIO(raw)) as z:
        xml=z.read('word/document.xml').decode()
        assert 'range' in xml and 'https://docs.python.org/3.12/' in xml
    (report.parent/'live-official-source.docx').write_bytes(raw)


def test_live_private_hybrid_api_preserves_scope_and_revocation(client,monkeypatch):
    monkeypatch.setenv('RIWAQ_EMBED_MODEL','embeddinggemma-2:270m-q8_0-text')
    p=project(client)
    private=client.post(f'/api/projects/{p}/files',files={'file':('selected.txt','The exact synthetic code ZX731 identifies 42 units.','text/plain')}).json()
    other=client.post(f'/api/projects/{p}/files',files={'file':('unselected.txt','UNSELECTED_SENTINEL ZX731 means 900 units.','text/plain')}).json()
    result,seconds=task(client,p,{'request':'ابحث في الملف عن ZX731','inputs':{'documentIds':[private['id']],'retrievalMode':'hybrid'},'idempotencyKey':'refine-live-private-search'})
    report=ROOT/'reports/skills-refinement-20261116/live-private-api.json'
    report.write_text(json.dumps({'mocked':False,'seconds':seconds,'status':result['status'],'selected':result['selected_skills'],
        'retrieval':result['result'].get('retrieval_receipt'),'references':result['result'].get('source_refs',[]),'error':result.get('error')},ensure_ascii=False,indent=2),'utf-8')
    assert result['status']=='completed',result
    assert result['selected_skills']==['hybrid-retrieval']
    receipt=result['result']['retrieval_receipt']
    assert receipt['method']=='private-hybrid-rrf' and receipt['semantic_status']=='completed'
    assert receipt['semantic_support']=='not_certified'
    assert '42 units' in result['result']['text'] and 'UNSELECTED_SENTINEL' not in result['result']['text']
    ref=result['result']['source_refs'][0]
    assert ref['file_id']==private['id'] and ref['file_id']!=other['id']
    assert client.get(ref['url']).status_code==200
    assert client.delete(f'/api/projects/{p}/files/{private["id"]}').status_code==200
    assert client.get(ref['url']).status_code in {403,404,409}
