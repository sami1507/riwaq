"""Actual PNG metadata/pixels and strict saved design/job contracts; no print proof."""
import hashlib
import io
import random
import pytest
from PIL import Image
from pydantic import ValidationError
from backend.visuals import DesignSpec,MAX_STORED_ASSET
from backend.visual_jobs import JobInput
from backend.visual_export import sanitize_worker_export
from test_visuals import spec,client,database,worker_simulation


def test_plane_survives_actual_save_read_and_version_restore(client):
    original=spec('رواق 42');quad=[[30,100],[470,40],[470,230],[30,200]]
    original['layers'][0]['perspective']=quad
    created=client.post('/api/projects/p1/visual/designs',json={'title':'Plane','spec':original})
    assert created.status_code==201,created.text
    record=created.json();path='/api/projects/p1/visual/designs/'+record['id']
    assert client.get(path).json()['spec']['layers'][0]['perspective']==quad
    original['layers'][0]['perspective']=None
    assert client.patch(path,json={'title':'Plane','spec':original,'expected_version':1}).status_code==200
    restored=client.post(path+'/restore',json={'expected_version':2,'from_version':1})
    assert restored.status_code==200 and restored.json()['spec']['layers'][0]['perspective']==quad
    assert restored.json()['spec']['layers'][0]['text']=='رواق 42'


@pytest.mark.parametrize('quad',[
    [[1,1],[30,30],[1,30],[30,1]], # crossed
    [[1,1],[1,1],[1,1],[1,1]], # collapsed
    [[0,0],[600,0],[600,100],[0,100]], # out of canvas
    [[0,0],[100,0],[100,100]],
    [[0,0],[100,0],[100,100],[float('nan'),100]],
    [[True,0],[100,0],[100,100],[0,100]],
])
def test_invalid_plane_rejected_before_storage(quad):
    value=spec('literal');value['layers'][0]['perspective']=quad
    with pytest.raises(ValidationError):DesignSpec.model_validate(value)


@pytest.mark.parametrize('dpi',[72,150,300,600])
def test_png_print_density_is_read_back_and_pixels_identical_without_resampling(dpi,monkeypatch):
    image=Image.new('RGBA',(512,256),(31,92,75,128));stream=io.BytesIO();image.save(stream,'PNG',dpi=(96,96))
    raw=stream.getvalue();before=hashlib.sha256(image.tobytes()).hexdigest()
    monkeypatch.setattr(Image.Image,'save',lambda *a,**k:pytest.fail('Print metadata must not re-encode pixels'))
    data,meta,receipt=sanitize_worker_export(raw,'image/png',(512,256),print_dpi=dpi)
    with Image.open(io.BytesIO(data)) as actual:
        actual.load();assert actual.size==(512,256)
        assert hashlib.sha256(actual.convert('RGBA').tobytes()).hexdigest()==before
        assert actual.info['srgb']==0 and actual.info['dpi']==pytest.approx((dpi,dpi),abs=.02)
        measured_dpi=actual.info['dpi'][0]
    # PNG pHYs uses integer pixels/metre; use the decoded density, not an
    # impossible exact requested DPI. At 72 DPI, 512 px differs by ~0.023 mm.
    proof=receipt['print'];assert proof['width_mm']==pytest.approx(512/measured_dpi*25.4,abs=1e-8)
    assert proof['metadata_verified'] and not proof['physical_print_verified'] and not proof['resampled']
    assert receipt['decoded_rgba_identical'] and not receipt['reencoded']


@pytest.mark.parametrize('patch',[{'print_dpi':71},{'print_dpi':601},{'print_dpi':True},{'print_dpi':300,'format':'jpeg','matte':'#ffffff'},{'print_dpi':300,'kind':'generate','prompt':'cup'}])
def test_print_contract_never_implies_other_format_or_generation(patch):
    with pytest.raises(ValidationError):JobInput.model_validate({'kind':'render','design_id':'saved','version':1,**patch})


def test_normalized_rgba_output_larger_than_old_transport_limit_remains_usable():
    image=Image.frombytes('RGBA',(2000,2000),random.Random(12).randbytes(16_000_000))
    stream=io.BytesIO();image.save(stream,'PNG',compress_level=0);raw=stream.getvalue()
    assert 10_000_000<len(raw)<MAX_STORED_ASSET
    data,meta,receipt=sanitize_worker_export(raw,'image/png',(2000,2000),print_dpi=300)
    assert len(data)<MAX_STORED_ASSET and receipt['decoded_rgba_identical']
    assert meta['width']==meta['height']==2000


def test_output_still_has_a_bounded_byte_limit():
    with pytest.raises(ValueError,match='byte limit'):
        sanitize_worker_export(b'x'*(MAX_STORED_ASSET+1),'image/png',(2000,2000))
