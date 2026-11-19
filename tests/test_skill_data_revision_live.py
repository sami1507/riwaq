"""Real authenticated API → pinned Docker → SQLite → downloaded output proof."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import time
from xml.etree import ElementTree as ET
from zipfile import ZipFile
import pytest
from fastapi.testclient import TestClient
from backend import app as service, local_auth
from backend.db import get_db

ROOT=Path(__file__).resolve().parents[1]
pytestmark=pytest.mark.skipif(os.environ.get('RIWAQ_TEST_DATA_REVISION_LIVE')!='1',reason='Opt-in pinned existing Docker worker and isolated test database')


def test_real_versioned_correction_updates_every_output_and_preserves_acl(tmp_path,monkeypatch):
    config=Path(os.environ.get('RIWAQ_TEST_ISOLATED_CONFIG',str(ROOT/'data/isolated-workers.json')))
    output=Path(os.environ.get('RIWAQ_DATA_REVISION_REPORT',str(ROOT/'reports/closure-20261116/data-revision/live')))
    output.mkdir(parents=True,exist_ok=True)
    data=tmp_path/'data';data.mkdir();shutil.copyfile(config,data/'isolated-workers.json')
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(data))
    monkeypatch.setenv('RIWAQ_KNOWLEDGE_REGISTRY',str(tmp_path/'absent-registry.json'))
    report={'synthetic_data':True,'api_mocked':False,'worker_mocked':False,'production_config_changed':False,
            'model_called':False,'native_office_rendered':False,'cases':[],
            'config_sha256':hashlib.sha256(config.read_bytes()).hexdigest()}
    from backend import skill_data_revision
    actual_run=skill_data_revision.run
    async def observed_run(*args,**kwargs):
        try:return await actual_run(*args,**kwargs)
        except Exception as exc:
            # Diagnostic wrapper preserves the actual implementation and effects.
            # Only synthetic fixture input is used in this isolated database.
            report.setdefault('diagnostic_errors',[]).append({'type':type(exc).__name__,'detail':str(exc)[:300]})
            raise
    monkeypatch.setattr(skill_data_revision,'run',observed_run)
    started=time.monotonic()
    try:
      with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as client:
        assert client.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
        pid=client.post('/api/projects',json={'name':'Synthetic dependent-output correction'}).json()['id']
        base=f'/api/projects/{pid}'
        def poll(rid,expected='completed'):
            deadline=time.monotonic()+110
            while time.monotonic()<deadline:
                response=client.get(f'{base}/skill-runs/{rid}')
                assert response.status_code==200,response.text
                result=response.json()
                if result['status'] not in {'queued','running','cancelling'}:break
                time.sleep(.04)
            assert result['status']==expected,(result['status'],result.get('error'),result.get('result',{}).get('limitations'))
            return result
        def submit(payload,expected='completed'):
            response=client.post(f'{base}/skill-runs',json=payload)
            assert response.status_code==202,response.text
            return poll(response.json()['id'],expected)
        def files(run,expected_sum,expected_mean,version):
            record=run['result']['structured']['exploratory-analysis']
            stats=record['output']['summary']['statistics']['amount']
            assert (stats['sum'],stats['mean'],stats['numeric_count'],stats['missing'])==(expected_sum,expected_mean,3,1)
            assert len(run['result']['artifacts'])==3
            received={}
            for item in run['result']['artifacts']:
                assert item['version']==version
                response=client.get(item['url']);assert response.status_code==200,response.text
                assert hashlib.sha256(response.content).hexdigest()==item['sha256']
                received[item['name']]=response.content
                (output/f'v{version}-{item["name"]}').write_bytes(response.content)
            table=json.loads(received['data-analysis.json'])['table']
            assert table['rows'][0][1]==0 and table['rows'][3][1] is None
            assert json.loads(received['data-analysis.json'])['summary']['statistics']['amount']==stats
            with ZipFile(BytesIO(received['data-chart.xlsx'])) as z:
                chart=ET.fromstring(z.read('xl/charts/chart1.xml'))
                ns={'c':'http://schemas.openxmlformats.org/drawingml/2006/chart'}
                values=[x.text for x in chart.findall('.//c:val/c:numRef/c:numCache/c:pt/c:v',ns)]
                assert values==['0','10',str(table['rows'][2][1])]
                assert chart.find('.//c:dispBlanksAs',ns).get('val')=='gap'
            with ZipFile(BytesIO(received['data-report.docx'])) as z:
                doc=z.read('word/document.xml').decode()
                assert f'المجموع: {expected_sum}' in doc and f'المتوسط: {expected_mean}' in doc
                assert '<w:tbl>' in doc
            for step in record['data_report']['execution_receipts']:
                receipt=step['receipt']
                assert receipt['status']=='completed' and receipt['exit_code']==0
                assert receipt['policy_inspected'] and receipt['cleanup_verified']
                assert receipt['network_policy']=='none' and receipt['host_fallback'] is False
            report['cases'].append({'id':run['id'],'version':version,'sum':expected_sum,'mean':expected_mean,
                                    'artifacts':run['result']['artifacts'],'receipts':record['data_report']['execution_receipts']})
            return received
        table={'columns':['order','amount'],'rows':[['A',0],['B',10],['C',20],['D',None]]}
        # Actual wrong field rejected before effect, followed by the supported
        # public contract adapter's singular value → worker values mapping.
        wrong={'request':'أنشئ الرسم المحدد','requestedSkills':['spreadsheet-charts'],'idempotencyKey':'wrong-field-contract',
               'inputs':{'dataOperation':{'operation':'chart','table':table,'chart':{'kind':'bar','category':'order','value':'amount'}}}}
        response=client.post(f'{base}/skill-runs',json=wrong)
        assert response.status_code==422
        assert client.get(f'{base}/skill-runs').json()==[]
        report['wrong_field']={'status':422,'no_job_created':True,'field':'chart.value','worker_field':'chart.values',
                               'scope':'New report adapter contract repair witness, not an invented historical connector defect'}
        source=submit({'request':'أنشئ المصنف من البيانات المحددة','requestedSkills':['spreadsheet-reading'],
            'idempotencyKey':'source-workbook-data','inputs':{'dataOperation':{'operation':'read','table':table,'format':'xlsx'}}})
        ref=source['result']['artifacts'][0]
        descriptors=client.get(f'{base}/skill-files').json()
        descriptor=next(x for x in descriptors if x['id']==ref['artifact_id'])
        source_ref={k:descriptor[k] for k in ('kind','id','version','sha256')}
        payload={'request':'Create analysis, summary, table, chart and report from the selected source',
            'requestedSkills':['exploratory-analysis'],'idempotencyKey':'linked-initial-report',
            'inputs':{'sourceRefs':[source_ref],'dataOperation':{'operation':'explore','sourceId':source_ref['id'],'columns':['amount']},
                      'dataReport':{'title':'القيم المصححة','language':'ar','chart':{'category':'order','value':'amount','unit':'وحدة'}}}}
        initial=submit(payload)
        original=files(initial,'30','10',1)
        target=client.get(f'{base}/learning/targets/tool/{initial["id"]}').json()
        feedback_response=client.post(f'{base}/learning/feedback',json={
            'idempotency_key':'linked-data-feedback','target':{k:target[k] for k in ('kind','id','version')},
            'signal':'correction','dimensions':['input','correctness'],'reason':'Correct declared amount and regenerate dependent outputs',
            'scope':'project','purposes':['current_task']})
        assert feedback_response.status_code==200,feedback_response.text
        feedback=feedback_response.json()
        revision={'request':'صحح الخلية وأعد حساب جميع المخرجات التابعة','previousRunId':initial['id'],
                  'idempotencyKey':'linked-cell-correction','inputs':{'dataRevision':{
                      'expectedResultHash':initial['result']['data_revision_available']['result_hash'],
                      'feedbackId':feedback['id'],'expectedFeedbackVersion':feedback['version'],
                      'edits':[{'cell':'B4','expected':20,'value':30}]}}}
        corrected=submit(revision)
        files(corrected,'40','13.33333333333333333333333333333333',2)
        verified=client.get(f'{base}/learning/feedback/{feedback["id"]}').json()
        assert verified['status']=='verified' and verified['verification']['corrected_output']['run_id']==corrected['id']
        assert verified['verification']['objective_error_proven'] is False
        assert verified['verification']['target']==feedback['target']
        report['feedback']={'id':verified['id'],'version':verified['version'],'status':verified['status'],
                            'scope':verified['verification']['verification_scope'],'run_id':corrected['id']}
        assert {x['artifact_id'] for x in initial['result']['artifacts']}=={x['artifact_id'] for x in corrected['result']['artifacts']}
        # Exact replay returns the same operation and does not add any versions.
        replay=client.post(f'{base}/skill-runs',json=revision)
        assert replay.status_code==202 and replay.json()['id']==corrected['id']
        other=deepcopy(revision);other['idempotencyKey']='stale-parent-new-key'
        assert client.post(f'{base}/skill-runs',json=other).status_code==409
        other=deepcopy(revision);other['inputs']['dataRevision']['edits'][0]['value']=40
        assert client.post(f'{base}/skill-runs',json=other).status_code==409
        for item in initial['result']['artifacts']:
            old=client.get(item['url']);assert old.status_code==200 and old.content==original[item['name']]
        bad={'request':'صحح الخلية وأعد الحساب','previousRunId':corrected['id'],'idempotencyKey':'stale-cell-value',
             'inputs':{'dataRevision':{'expectedResultHash':corrected['result']['data_revision_available']['result_hash'],
                                      'edits':[{'cell':'B4','expected':20,'value':50}]}}}
        failed=submit(bad,'failed');assert not failed['result']['artifacts']
        assert failed.get('error')!='Permission or source revision changed; output withheld'
        # Recompute-only is a genuine operation and updates every output version,
        # while retaining zero and the explicit missing-value policy.
        recompute={'request':'أعد حساب التحليل والملفات التابعة من الجدول المحفوظ','previousRunId':corrected['id'],
                   'idempotencyKey':'linked-recompute-policy','inputs':{'dataRevision':{
                       'expectedResultHash':corrected['result']['data_revision_available']['result_hash'],'edits':[]}}}
        recomputed=submit(recompute);files(recomputed,'40','13.33333333333333333333333333333333',3)
        # Real SQLite failure in the middle of multi-head commit: all heads roll
        # back, no failed output downloads or success are published.
        with get_db() as c:
            c.executescript("CREATE TRIGGER fail_revision_commit BEFORE UPDATE OF latest_version ON skill_file_heads WHEN NEW.latest_version=4 AND NEW.name='data-report.docx' BEGIN SELECT RAISE(ABORT,'synthetic_storage_failure'); END;")
        fault={'request':'أعد بناء جميع الملفات التابعة','previousRunId':recomputed['id'],'idempotencyKey':'atomic-publication-failure',
               'inputs':{'dataRevision':{'expectedResultHash':recomputed['result']['data_revision_available']['result_hash'],
                                        'edits':[{'cell':'B4','expected':30,'value':40}]}}}
        storage_failed=submit(fault,'failed');assert not storage_failed['result']['artifacts']
        with get_db() as c:
            c.execute('DROP TRIGGER fail_revision_commit')
            versions=c.execute('SELECT latest_version FROM skill_file_heads WHERE id IN (?,?,?)',
                               [x['artifact_id'] for x in recomputed['result']['artifacts']]).fetchall()
            assert [r['latest_version'] for r in versions]==[3,3,3]
        foreign=client.post('/api/projects',json={'name':'Other synthetic project'}).json()['id']
        assert client.post(f'/api/projects/{foreign}/skill-runs',json=fault).status_code==404
        # Withdrawal of the upstream source also withholds current derived files.
        with get_db() as c:c.execute('UPDATE skill_file_heads SET deleted=1 WHERE id=?',(source_ref['id'],))
        for item in recomputed['result']['artifacts']:
            assert client.get(item['url']).status_code in {403,404,409}
        withdrawn=client.get(f'{base}/learning/feedback/{feedback["id"]}').json()
        assert withdrawn['status']=='stale' and withdrawn['verification']=={}
        report.update(passed=True,all_heads_atomic=True,originals_retained=True,replay_reused_run=True,
                      revoked_source_withheld=True,zero_preserved=True,missing_preserved=True,
                      fixture_fault='Explicit SQLite trigger abort, not a real storage outage')
    except BaseException as exc:
        report.update(passed=False,error_type=type(exc).__name__)
        raise
    finally:
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
