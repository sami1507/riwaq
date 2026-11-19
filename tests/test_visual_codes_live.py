"""Opt-in actual authenticated API -> pinned code worker -> stored PNG download."""
import hashlib,json,os,shutil
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import app,local_auth,visual_codes
from backend.db import get_db
ROOT=Path(__file__).resolve().parents[1]
pytestmark=pytest.mark.skipif(os.environ.get('RIWAQ_TEST_VISUAL_CODES_LIVE')!='1',reason='Opt-in actual Docker code worker')

def test_real_code_api_exact_payload_replay_acl_and_failed_decode(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY',str(tmp_path/'absent'))
    shutil.copyfile(Path(os.environ.get('RIWAQ_TEST_DOCUMENT_CONFIG',str(ROOT/'reports/closure-20261116/visual-normalize/isolated-workers.json'))),tmp_path/'isolated-workers.json')
    out=Path(os.environ.get('RIWAQ_CODE_REPORT',str(ROOT/'reports/closure-20261116/visual-codes/api-live')));out.mkdir(parents=True,exist_ok=True)
    report={'worker_mocked':False,'model_called':False,'synthetic_data':True,'cases':[]}
    def save():(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with TestClient(app.app,headers={'X-Riwaq-Client':'local'}) as client:
        assert client.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
        pid=client.post('/api/projects',json={'name':'Synthetic machine code test'}).json()['id'];base=f'/api/projects/{pid}/visual'
        assert client.get(base+'/codes/status').json()['available']
        assets=[]
        for i,(kind,text) in enumerate([('qr','Riwaq 001'),('qr','مرحبا 001'),('qr','001'),('code128','001')]):
            body={'kind':kind,'text':text,'request_key':'code-test-'+str(i)}
            response=client.post(base+'/codes',json=body);assert response.status_code==201,response.text
            item=response.json();proof=item['origin']['proof'];assets.append(item)
            assert proof['input_sha256']==proof['decoded_sha256']==hashlib.sha256(text.encode()).hexdigest() and proof['decode_matches']
            raw=client.get(base+'/assets/'+item['id']+'/content');assert raw.status_code==200 and hashlib.sha256(raw.content).hexdigest()==item['sha256']
            (out/f'{i}-{kind}.png').write_bytes(raw.content)
            replay=client.post(base+'/codes',json=body);assert replay.status_code==201 and replay.json()['id']==item['id']
            altered=client.post(base+'/codes',json={**body,'text':'different'});assert altered.status_code==409
            report['cases'].append({'kind':kind,'text':text,'asset':item,'replay_same_id':True,'conflict_status':409});save()
        before=len(client.get(base+'/assets').json()['items'])
        invalid=client.post(base+'/codes',json={'kind':'code128','text':'عربي','request_key':'invalid-code'});assert invalid.status_code==422
        # Explicit injected decoder-verdict failure after real worker execution.
        original=visual_codes.protocol.response
        def reject(*a,**k):original(*a,**k);raise ValueError('Synthetic mismatch')
        monkeypatch.setattr(visual_codes.protocol,'response',reject)
        failed=client.post(base+'/codes',json={'kind':'qr','text':'Unpublished fixture','request_key':'decode-failure'})
        assert failed.status_code==503 and len(client.get(base+'/assets').json()['items'])==before
        monkeypatch.setattr(visual_codes.protocol,'response',original)
        with get_db() as c:c.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?',('other',pid))
        for path in ('/assets','/assets/'+assets[0]['id']+'/content','/codes/status'):
            assert client.get(base+path).status_code==404
        report.update(passed=True,invalid_input_status=422,failed_decode_no_asset=True,failed_decode_kind='controlled injected rejection after actual worker verification',revocation_hides_assets=True);save()
