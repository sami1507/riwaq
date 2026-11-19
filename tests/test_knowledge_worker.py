"""Regression for the legacy upload's fixed worker; synthetic inputs only."""
import io
import json
import threading
import zipfile

import pytest
from fastapi import HTTPException

from backend import knowledge_worker, visual_runner


def docx():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>اختبار اصطناعي — 731</w:t></w:r></w:p></w:body></w:document>')
    return output.getvalue()


@pytest.fixture
def isolated_protocol(tmp_path, monkeypatch):
    from backend import isolated_extract
    from backend.isolated_parsers import VERSION
    import hashlib
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(isolated_extract,'load_config',lambda path:type('Config',(),{'images':{'document_extract':'sha256:'+'a'*64}})())
    control={'status':'completed','sha':None,'calls':0}
    def execute(files,runner,targets,**kwargs):
        control['calls']+=1
        result={'version':VERSION,'success':True,'input_sha256':control['sha'] or hashlib.sha256(files['input.bin']).hexdigest(),'pages':[[None,'source text 731']]}
        return {'status':control['status'],'cleanup_verified':True,'policy_inspected':True,'stdout':json.dumps(result),'stderr':''}
    monkeypatch.setattr(isolated_extract,'execute',execute)
    def forbidden(*a,**k):raise AssertionError('No host parser worker may start')
    monkeypatch.setattr(visual_runner,'execute',forbidden)
    return control


def test_docx_fixed_container_protocol_returns_bound_text_without_transients(tmp_path,isolated_protocol):
    assert knowledge_worker.extract_bounded('sample.docx',docx())==[(None,'source text 731')]
    assert isolated_protocol['calls']==1 and not (tmp_path/'private-extract-workers').exists()


@pytest.mark.parametrize('reason,status',[('timeout',504),('resource_limit',422),('cancelled',499),('failed',422),('blocked',503),('cleanup_failed',503)])
def test_failed_worker_has_no_in_process_fallback(monkeypatch,isolated_protocol,reason,status):
    from backend import knowledge
    def forbidden(*a,**k):raise AssertionError('No parent parser fallback')
    monkeypatch.setattr(knowledge,'extract_document',forbidden)
    isolated_protocol['status']=reason
    with pytest.raises(HTTPException) as error:knowledge_worker.extract_bounded('sample.docx',docx())
    assert error.value.status_code==status


def test_worker_result_bound_to_input_hash_and_slot_released(isolated_protocol):
    isolated_protocol['sha']='0'*64
    for _ in range(2):
        with pytest.raises(HTTPException) as error:knowledge_worker.extract_bounded('sample.docx',docx())
        assert error.value.status_code==422


def test_cancelled_input_never_starts_worker(isolated_protocol):
    stop=threading.Event();stop.set()
    with pytest.raises(HTTPException) as error:knowledge_worker.extract_bounded('sample.docx',docx(),stop)
    assert error.value.status_code==499 and isolated_protocol['calls']==0


def test_frozen_worker_unavailable_fails_closed(isolated_protocol,monkeypatch):
    monkeypatch.setattr(knowledge_worker.sys,'frozen',True,raising=False)
    with pytest.raises(HTTPException) as error:knowledge_worker.extract_bounded('sample.docx',docx())
    assert error.value.status_code==503 and isolated_protocol['calls']==0


def test_missing_isolated_configuration_never_runs_host(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    def forbidden(*a,**k):raise AssertionError('Unavailable container must not fall back')
    monkeypatch.setattr(visual_runner,'execute',forbidden)
    with pytest.raises(HTTPException) as error:knowledge_worker.extract_bounded('sample.docx',docx())
    assert error.value.status_code==503 and 'fallback' in error.value.detail
