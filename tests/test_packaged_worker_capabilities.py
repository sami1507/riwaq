"""Synthetic frozen-runtime regressions; no packaged/hardware execution claim."""
import asyncio
import io
import json
import sys
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

from backend import document_isolation, documents, local_auth, media, video_render, visual_jobs


def frozen(module, monkeypatch):
    monkeypatch.setattr(module, 'sys', SimpleNamespace(frozen=True, executable='riwaq-service.exe'))


def no_call(*args, **kwargs):
    pytest.fail('Unavailable packaged worker must not spawn or access the database')


@pytest.fixture
def paired_principal():
    # Synthetic trusted middleware context only; no DB session validation is
    # reached because the unavailable capability must reject before dispatch.
    token=local_auth._principal.set({'owner_id':'synthetic-owner','session_id':'synthetic-session'})
    try:yield
    finally:local_auth._principal.reset(token)


def test_document_capabilities_do_not_reuse_source_tested_flags(tmp_path, monkeypatch):
    frozen(document_isolation, monkeypatch)
    monkeypatch.setattr(document_isolation, 'load_config', no_call)
    runtime=tmp_path/'runtime'; (runtime/'tessdata').mkdir(parents=True)
    for language in ('ara', 'eng', 'heb'):
        (runtime/'tessdata'/f'{language}.traineddata').write_bytes(b'synthetic-presence-only')
    monkeypatch.setenv('RIWAQ_DOCUMENT_RUNTIME', str(runtime))
    async def vision(): return {'model':'synthetic-test-no-network'}
    monkeypatch.setattr(media, 'inspect_vision', vision)
    caps=asyncio.run(documents.capabilities())
    assert caps['available'] is False
    assert caps['reason_code']=='packaged_worker_unavailable'
    assert caps['ocr']['languages']==[]  # Host language files cannot enable the frozen worker.
    assert caps['ocr']['available'] is False
    assert all(not row['available'] and not row['tested'] and row['status']=='unavailable' for row in caps['formats'])


def test_document_parse_frozen_never_creates_workdir_or_process(monkeypatch):
    frozen(document_isolation, monkeypatch)
    monkeypatch.setattr(document_isolation, 'data_dir', no_call)
    monkeypatch.setattr(document_isolation, 'execute', no_call)
    with pytest.raises(ValueError, match='packaged_worker_unavailable'):
        documents.parse(b'local text', 'source.txt', 0, 'eng', threading.Event())


def test_document_upload_closes_input_without_saving_or_enqueuing(monkeypatch, paired_principal):
    frozen(document_isolation, monkeypatch)
    monkeypatch.setattr(documents, 'get_db', no_call)
    monkeypatch.setattr(documents, 'enqueue', no_call)
    file=UploadFile(filename='source.txt', file=io.BytesIO(b'local text'))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(documents.upload('synthetic-project', file, 'eng'))
    assert caught.value.status_code==503
    assert 'packaged_worker_unavailable' in caught.value.detail
    assert file.file.closed


def test_document_resume_frozen_never_queues(monkeypatch, paired_principal):
    frozen(document_isolation, monkeypatch)
    monkeypatch.setattr(documents, 'get_db', no_call)
    monkeypatch.setattr(documents, 'enqueue', no_call)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(documents.resume('synthetic-project', 'synthetic-file'))
    assert caught.value.status_code==503


def test_source_document_worker_requires_pinned_configuration_not_host_script(tmp_path, monkeypatch):
    monkeypatch.setattr(document_isolation, 'data_dir', lambda:tmp_path)
    monkeypatch.setattr(document_isolation, 'sys', SimpleNamespace(frozen=False, executable=sys.executable))
    monkeypatch.setattr(document_isolation, 'execute', no_call)
    assert not documents.worker_status()['available']
    (tmp_path/'scripts').mkdir(); (tmp_path/'scripts/document_worker.py').write_text('# inert host fixture')
    assert not documents.worker_status()['available']
    config={'docker_executable':str(tmp_path/'docker.exe'),'docker_sha256':'a'*64,
            'endpoint':'npipe:////./pipe/dockerDesktopLinuxEngine','daemon_id':'synthetic-no-probe',
            'images':{'document_extract':'sha256:'+'b'*64}}
    path=tmp_path/'isolated-workers.json';path.write_text(json.dumps(config))
    assert documents.worker_status()['reason_code']=='document_image_unconfigured'
    config['images']['document_page']='sha256:'+'c'*64
    path.write_text(json.dumps(config))
    caps=documents.worker_status()
    assert caps['available'] and caps['configured'] and not caps['tested']
    assert caps['image_id']==config['images']['document_page'] and not caps['host_fallback']


def visual_assets(tmp_path, monkeypatch):
    python=tmp_path/'python.exe'; python.write_bytes(b'presence-only')
    model=tmp_path/'model'; model.mkdir(); (model/'riwaq-model.json').write_text('{}')
    monkeypatch.setattr(visual_jobs, 'ROOT', tmp_path)
    monkeypatch.setattr(visual_jobs, 'paths', lambda:(python,model))


def test_visual_frozen_does_not_claim_external_model_is_usable(tmp_path, monkeypatch):
    visual_assets(tmp_path, monkeypatch)
    frozen(visual_jobs, monkeypatch)
    caps=visual_jobs.capabilities()
    for name in ('generation','render','editing'):
        assert caps[name]['available'] is False
        assert caps[name]['reason_code']=='packaged_worker_unavailable'


def test_visual_source_requires_fixed_worker_file(tmp_path, monkeypatch):
    visual_assets(tmp_path, monkeypatch)
    monkeypatch.setattr(visual_jobs, 'sys', SimpleNamespace(frozen=False))
    assert visual_jobs.capabilities()['generation']['available'] is False
    (tmp_path/'scripts').mkdir(); (tmp_path/'scripts/visual_generate.py').write_text('# fixed fixture')
    assert visual_jobs.capabilities()['generation']['available'] is True


def test_visual_create_frozen_never_creates_job(monkeypatch):
    from backend import app, coding, voice
    for module,name in ((app,'ACTIVE'),(coding,'ACTIVE'),(voice,'ACTIVE')):
        monkeypatch.setattr(module,name,{})
    monkeypatch.setattr(media,'ACTIVE_MEDIA',None)
    frozen(visual_jobs, monkeypatch)
    monkeypatch.setattr(visual_jobs,'get_db',no_call)
    body=visual_jobs.JobInput(kind='generate',design_id='synthetic',version=1,prompt='A red circle')
    with pytest.raises(HTTPException) as caught:
        asyncio.run(visual_jobs.create('synthetic-project',body))
    assert caught.value.status_code==503 and 'packaged_worker_unavailable' in caught.value.detail


def test_visual_work_frozen_never_reads_job_or_spawns(monkeypatch):
    frozen(visual_jobs,monkeypatch)
    monkeypatch.setattr(visual_jobs,'get_db',no_call)
    with pytest.raises(ValueError,match='packaged_worker_unavailable'):
        visual_jobs.work('synthetic-job','synthetic-project',{'stop':threading.Event()})


@pytest.mark.parametrize('packaged',[True,False])
def test_video_missing_worker_never_writes_input_or_spawns(tmp_path,monkeypatch,packaged):
    monkeypatch.setattr(video_render,'sys',SimpleNamespace(frozen=packaged))
    monkeypatch.setattr(video_render,'ROOT',tmp_path/'missing-root')
    output=tmp_path/'not-created'
    with pytest.raises(video_render.VideoError,match='packaged_worker_unavailable' if packaged else 'video_worker_unavailable'):
        video_render._invoke('probe',{},output,threading.Event())
    assert not output.exists()
