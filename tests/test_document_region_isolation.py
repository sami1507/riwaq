"""Region boundary: contract/fault tests, separate from opt-in Docker proof."""
import asyncio
import base64
import copy
import hashlib
import threading
import time
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from backend import document_isolation as adapter, isolated_document_page as p, document_vision as dv, media
from test_document_isolation import png

RAW=png(100,80)
META={'operation':'region','bbox':[.101,.12,.505,.75]}

def envelope():
    data=png(41,51);sha=hashlib.sha256(data).hexdigest()
    return {'version':p.REGION_VERSION,'success':True,'input_sha256':hashlib.sha256(RAW).hexdigest(),
        'preview':{'bytes':len(data),'sha256':sha,'base64':base64.b64encode(data).decode()},
        'region':{'bbox':[.1,9/80,.51,.75],'pixel_bbox':[10,9,51,60],'preview_width':100,'preview_height':80,
                  'model_image_width':41,'model_image_height':51,'region_sha256':sha,'resized':False}}


def test_region_protocol_and_parent_never_decode_on_host(tmp_path,monkeypatch):
    import PIL.Image
    monkeypatch.setattr(PIL.Image,'open',lambda *a,**k:pytest.fail('Host image decoder reached'))
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(adapter,'load_config',lambda _:SimpleNamespace(images={'document_page':'sha256:'+'c'*64}))
    calls=[]
    def execute(files,runner,targets,**kw):
        calls.append((files,runner,targets,kw))
        return {'status':'completed','stdout':p.dump(envelope()).decode(),'cleanup_verified':True,'policy_inspected':True}
    monkeypatch.setattr(adapter,'execute',execute)
    data,region=dv.prepare_region(RAW,META['bbox'],authorize=lambda:True)
    assert p.png_dimensions(data)==(41,51) and region['isolation']['host_fallback'] is False
    files,runner,targets,kw=calls[0]
    assert runner=='document_page' and set(files)=={'input.bin','metadata.json'} and targets==['input.bin']
    assert p.loads(files['metadata.json'])==META and 1<=kw['timeout_seconds']<=30 and kw['memory_mb']==512


@pytest.mark.parametrize('change',[
    lambda e:e.update(input_sha256='a'*64),lambda e:e.update(version=p.VERSION),
    lambda e:e['preview'].update(sha256='b'*64),lambda e:e['preview'].update(bytes=True),
    lambda e:e['region'].update(pixel_bbox=[0,0,41,51]),lambda e:e['region'].update(model_image_width=True),
    lambda e:e['region'].update(resized=0),lambda e:e['region'].update(bbox=[False,9/80,.51,.75]),
    lambda e:e.update(success=False),lambda e:e.update(command='read secrets'),
])
def test_forged_crop_rejected(change):
    value=envelope();change(value)
    with pytest.raises(ValueError):p.region_response(p.dump(value),META,RAW)


@pytest.mark.parametrize('meta',[{'operation':'region','bbox':[0,0,1,1],'path':'secret'},
    {'operation':'region','bbox':[True,0,1,1]},{'operation':'region','bbox':[0,0,2,1]},
    {'operation':'region','bbox':[0,0,float('nan'),1]}])
def test_region_input_cannot_choose_operation_paths_or_limits(meta):
    with pytest.raises(ValueError):p.validate_metadata(meta)


@pytest.mark.parametrize('status,code',[('timeout',504),('cancelled',499),('revoked',403),('failed',503)])
def test_region_failures_are_not_valid_images(tmp_path,monkeypatch,status,code):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(adapter,'load_config',lambda _:SimpleNamespace(images={'document_page':'sha256:'+'c'*64}))
    monkeypatch.setattr(adapter,'execute',lambda *a,**k:{'status':status,'stdout':p.dump(envelope()).decode(),'cleanup_verified':True,'policy_inspected':True})
    with pytest.raises(adapter.IsolationError) as exc:adapter.prepare_region(RAW,META['bbox'],authorize=lambda:True)
    assert exc.value.status_code==code


def test_region_missing_authority_configuration_and_cancel_fail_closed(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(adapter,'execute',lambda *a,**k:pytest.fail('No execution authorized'))
    with pytest.raises(adapter.IsolationError,match='authority_required'):adapter.prepare_region(RAW,None)
    stop=threading.Event();stop.set()
    with pytest.raises(adapter.IsolationError,match='cancelled'):adapter.prepare_region(RAW,None,stop=stop,authorize=lambda:True)
    with pytest.raises(adapter.IsolationError):adapter.prepare_region(RAW,None,authorize=lambda:True)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['stop','disconnect'])
async def test_region_cancellation_waits_for_worker_cleanup(monkeypatch,mode):
    entered=threading.Event();cleaned=threading.Event()
    def work(preview,bbox,**kwargs):
        entered.set()
        assert kwargs['stop'].wait(3)
        time.sleep(.03);cleaned.set()
        raise HTTPException(499,'cancelled')
    class Request:
        async def is_disconnected(self):return mode=='disconnect' and entered.is_set()
    monkeypatch.setattr(dv,'prepare_region',work)
    job=media.begin_media('document-vision')
    async def stop():
        while not entered.is_set():await asyncio.sleep(.005)
        if mode=='stop':job['stop'].set()
    signal=asyncio.create_task(stop())
    try:
        with pytest.raises(HTTPException) as exc:await dv.isolated_region(RAW,None,{},job,Request(),time.monotonic()+3)
        assert exc.value.status_code==499 and cleaned.is_set()
    finally:
        await signal;media.finish_media(job)
