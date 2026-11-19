"""Visual variant protocol checks and fixed worker behavior on synthetic pixels."""
import asyncio,base64,hashlib,io,threading,time
from types import SimpleNamespace
import pytest
from PIL import Image
from backend import image_normalize,isolated_document_page as p,visuals,document_isolation


def pixels(format='PNG'):
    out=io.BytesIO();Image.new('RGBA',(11,9),(20,30,40,96)).save(out,format=format);return out.getvalue()


def envelope(original,output):
    return p.dump({'version':p.VISUAL_NORMALIZE_VERSION,'input_sha256':hashlib.sha256(original).hexdigest(),'success':True,
      'dimensions':{'width':11,'height':9},'preview':{'bytes':len(output),'sha256':hashlib.sha256(output).hexdigest(),'base64':base64.b64encode(output).decode()}})


@pytest.mark.parametrize('format',['PNG','WEBP'])
def test_worker_preserves_alpha_and_strips_metadata(format):
    raw=pixels(format);output,dimensions=image_normalize.normalize(raw,variant='visual')
    with Image.open(io.BytesIO(output)) as pic:
        assert pic.mode=='RGBA' and pic.getpixel((0,0))[3]==96 and not pic.info
    assert dimensions=={'width':11,'height':9}
    assert p.normalize_response(envelope(raw,output),raw,variant='visual')[0]==output
    with pytest.raises(ValueError):p.normalize_response(envelope(raw,output),raw)


def test_variant_is_not_client_selectable_arbitrary_policy():
    assert p.validate_metadata({'operation':'normalize','variant':'visual'})['variant']=='visual'
    for value in ({'operation':'normalize','variant':'other'},{'operation':'normalize','variant':'visual','path':'secret'}):
        with pytest.raises(ValueError):p.validate_metadata(value)


def test_visual_adapter_has_no_host_image_decoder(monkeypatch):
    raw=pixels();output,_=image_normalize.normalize(raw,variant='visual');calls=[]
    def isolated(data,**kwargs):
        calls.append(kwargs);assert kwargs['authorize']()
        return output,{'width':11,'height':9},{'protocol':p.VISUAL_NORMALIZE_VERSION,'host_fallback':False}
    monkeypatch.setattr(document_isolation,'normalize_image',isolated)
    monkeypatch.setattr(Image,'open',lambda *a,**k:pytest.fail('Host native decoder reached'))
    data,meta=visuals.normalize_image(raw,authorize=lambda:True)
    assert data==output and meta['mime']=='image/png' and meta['isolation']['host_fallback'] is False
    assert calls[0]['variant']=='visual' and calls[0]['deadline']>0


def test_missing_visual_decoder_is_explicit_no_fallback(monkeypatch,tmp_path):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));raw=pixels()
    monkeypatch.setattr(Image,'open',lambda *a,**k:pytest.fail('Host fallback reached'))
    with pytest.raises(visuals.HTTPException) as error:visuals.normalize_image(raw,authorize=lambda:True)
    assert error.value.status_code==503


def test_visual_input_stays_six_mib_while_rgba_output_has_own_bound():
    assert p.MAX_VISUAL_INPUT==6*1024**2 and p.MAX_VISUAL_NORMALIZED_BYTES>4_000_000*4
    assert p.MAX_IMAGE_INPUT==3*1024**2 and p.MAX_NORMALIZED_BYTES< p.MAX_VISUAL_NORMALIZED_BYTES
    with pytest.raises(visuals.HTTPException) as error:visuals.normalize_image(b'x'*(p.MAX_VISUAL_INPUT+1),authorize=lambda:True)
    assert error.value.status_code==413


@pytest.mark.parametrize('operation',['media','visual','region','extract'])
def test_unbundled_frozen_execution_wrappers_never_dispatch(operation,monkeypatch):
    from test_document_isolation import png
    monkeypatch.setattr(document_isolation,'sys',SimpleNamespace(frozen=True))
    monkeypatch.setattr(document_isolation,'load_config',lambda *a,**k:pytest.fail('Unbundled worker loaded configuration'))
    monkeypatch.setattr(document_isolation,'execute',lambda *a,**k:pytest.fail('Unbundled worker dispatched'))
    calls={'media':lambda:document_isolation.normalize_image(b'fixture',authorize=lambda:True),
      'visual':lambda:document_isolation.normalize_image(b'fixture',authorize=lambda:True,variant='visual'),
      'region':lambda:document_isolation.prepare_region(png(2,2),None,authorize=lambda:True),
      'extract':lambda:document_isolation.extract(b'plain fixture','source.txt',0,'eng',authorize=lambda:True)}
    with pytest.raises(document_isolation.IsolationError) as error:calls[operation]()
    assert error.value.code=='packaged_worker_unavailable' and error.value.status_code==503


@pytest.mark.asyncio
async def test_route_cancellation_waits_for_worker_cleanup(tmp_path,monkeypatch):
    """Controlled worker delay checks await semantics; actual cleanup is covered live."""
    from backend.db import init_db,get_db,now
    from visual_auth_fixture import trusted_visual_session
    from starlette.datastructures import UploadFile
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));init_db()
    with get_db() as c:c.execute('INSERT INTO projects VALUES(?,?,?,?)',('cancel-test','Synthetic','',''+now()))
    entered=threading.Event();cleaned=threading.Event()
    def delayed(raw,*,stop,authorize):
        assert authorize();entered.set()
        while not stop.wait(.01):pass
        time.sleep(.05);cleaned.set()
        raise visuals.HTTPException(499,'cancelled')
    class Request:
        async def is_disconnected(self):return False
    monkeypatch.setattr(visuals,'normalize_image',delayed)
    with trusted_visual_session():
        task=asyncio.create_task(visuals.upload_asset('cancel-test',Request(),UploadFile(io.BytesIO(b'fixture'),filename='test.png')))
        assert await asyncio.to_thread(entered.wait,2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert cleaned.is_set()
    with get_db() as c:assert c.execute('SELECT COUNT(*) FROM visual_assets').fetchone()[0]==0
