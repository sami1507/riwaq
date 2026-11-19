"""Synthetic contract and cleanup checks, not a live Docker/model certificate."""
import asyncio
import base64
import hashlib
import io
import threading
import time
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from backend import media,document_isolation as adapter,isolated_document_page as p
from test_document_isolation import png


RAW=png(12,10)
def envelope():
    return {'version':p.NORMALIZE_VERSION,'success':True,'input_sha256':hashlib.sha256(RAW).hexdigest(),
      'dimensions':{'width':12,'height':10},'preview':{'bytes':len(RAW),'sha256':hashlib.sha256(RAW).hexdigest(),'base64':base64.b64encode(RAW).decode()}}


def fixture_adapter(tmp_path,monkeypatch,status='completed'):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(adapter,'load_config',lambda _:SimpleNamespace(images={'document_page':'sha256:'+'c'*64}))
    calls=[]
    def execute(files,runner,targets,**kwargs):
        calls.append((files,runner,targets,kwargs))
        return {'status':status,'stdout':p.dump(envelope()).decode(),'cleanup_verified':True,'policy_inspected':True}
    monkeypatch.setattr(adapter,'execute',execute)
    return calls


def test_host_no_decoder_and_fixed_contract(tmp_path,monkeypatch):
    calls=fixture_adapter(tmp_path,monkeypatch)
    import PIL.Image
    monkeypatch.setattr(PIL.Image,'open',lambda *a,**k:pytest.fail('Native host decoder reached'))
    picture,dimensions,receipt=media.validate_image(RAW,authorize=lambda:True,with_receipt=True)
    assert picture==RAW and dimensions=={'width':12,'height':10}
    assert receipt['host_fallback'] is False and receipt['cleanup_verified']
    files,runner,targets,kwargs=calls[0]
    assert runner=='document_page' and targets==['input.bin']
    assert p.loads(files['metadata.json'])=={'operation':'normalize'}
    assert kwargs['memory_mb']==512 and 1<=kwargs['timeout_seconds']<=30


@pytest.mark.parametrize('change',[
 lambda e:e.update(input_sha256='f'*64),lambda e:e.update(version=p.VERSION),
 lambda e:e['preview'].update(bytes=True),lambda e:e['preview'].update(sha256='f'*64),
 lambda e:e['dimensions'].update(width=True),lambda e:e['dimensions'].update(height=11),
 lambda e:e.update(command='exec'),lambda e:e.update(success=1)])
def test_forged_normalize_output_rejected(change):
    item=envelope();change(item)
    with pytest.raises(ValueError):p.normalize_response(p.dump(item),RAW)


@pytest.mark.parametrize('metadata',[{'operation':'normalize','path':'secret'},{'operation':'normalize','limit':10000000}])
def test_input_cannot_select_paths_or_limits(metadata):
    with pytest.raises(ValueError):p.validate_metadata(metadata)


@pytest.mark.parametrize('status,code',[('cancelled',499),('revoked',403),('timeout',504),('failed',503)])
def test_failure_does_not_publish_picture(tmp_path,monkeypatch,status,code):
    fixture_adapter(tmp_path,monkeypatch,status)
    with pytest.raises(HTTPException) as err:media.validate_image(RAW,authorize=lambda:True)
    assert err.value.status_code==code


def test_missing_config_or_authority_no_host_fallback(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(adapter,'execute',lambda *a,**k:pytest.fail('Unconfigured worker ran'))
    with pytest.raises(HTTPException) as err:media.validate_image(RAW)
    assert err.value.status_code==401
    with pytest.raises(HTTPException) as err:media.validate_image(RAW,authorize=lambda:True)
    assert err.value.status_code==503


def test_native_exif_orientation_and_metadata_preserved_then_stripped():
    from PIL import Image
    from backend.image_normalize import normalize
    source=Image.new('RGB',(20,10),'red');exif=Image.Exif();exif[274]=6;exif[270]='synthetic private metadata'
    buf=io.BytesIO();source.save(buf,format='JPEG',exif=exif)
    raw,size=normalize(buf.getvalue())
    assert size=={'width':10,'height':20}
    assert p.png_dimensions(raw)==(10,20)
    with Image.open(io.BytesIO(raw)) as image:assert not image.getexif() and not image.info


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['stop','disconnect'])
async def test_cancellation_waits_for_actual_thread_cleanup(monkeypatch,mode):
    entered=threading.Event();cleaned=threading.Event()
    def work(*args,**kw):
        entered.set();assert kw['stop'].wait(2);time.sleep(.03);cleaned.set();raise HTTPException(499,'cancelled')
    monkeypatch.setattr(media,'validate_image',work)
    class Request:
        async def is_disconnected(self):return mode=='disconnect' and entered.is_set()
    job=media.begin_media('vision')
    async def signal():
        while not entered.is_set():await asyncio.sleep(.005)
        if mode=='stop':job['stop'].set()
    task=asyncio.create_task(signal())
    try:
        with pytest.raises(HTTPException) as err:await media.isolated_image(RAW,job,Request(),lambda:True)
        assert err.value.status_code==499 and cleaned.is_set()
    finally:await task;media.finish_media(job)
