"""Actual Chromium fixture regressions plus synthetic malformed containers."""
import hashlib
import io
from pathlib import Path

from PIL import Image
import pytest

from backend.visual_export import sanitize_worker_export

FIXTURES=Path(__file__).parent/'fixtures/visual-export'


def image_bytes(fmt='PNG',**opts):
    out=io.BytesIO();image=Image.new('RGBA',(256,256),(31,92,75,128))
    if fmt=='JPEG':image=image.convert('RGB')
    image.save(out,format=fmt,**opts)
    return out.getvalue()


def pixels(raw):
    with Image.open(io.BytesIO(raw)) as image:
        image.load();return image.size,image.convert('RGBA').tobytes(),dict(image.info)


@pytest.mark.parametrize('fmt,sha',[('jpeg','b85e352c0c282f521049beef48d728146fbb2e532581672db354ac5f2d284bc2'),('webp','082ca46ff71c6aaa34f0088604dd8ec1478fd24e5acd1140b406ffcc919e46d9')])
def test_actual_canvas_icc_previously_rejected_now_preserves_pixels(fmt,sha,monkeypatch):
    raw=(FIXTURES/('canvas-srgb.'+fmt)).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==sha
    before=pixels(raw)
    assert before[2].get('icc_profile')  # The actual old guard rejected this.
    monkeypatch.setattr(Image.Image,'save',lambda *a,**k:pytest.fail('No re-encoding permitted'))
    data,meta,proof=sanitize_worker_export(raw,'image/'+fmt,(256,256))
    after=pixels(data)
    assert before[:2]==after[:2]
    assert not any(after[2].get(k) for k in ('exif','icc_profile','xmp','comment'))
    assert meta['mime']=='image/'+fmt and proof['reencoded'] is False
    assert proof['decoded_rgba_identical'] and proof['removed']
    assert proof['stored_sha256']==hashlib.sha256(data).hexdigest()


def test_jpeg_comments_exif_and_progressive_scans_are_losslessly_removed():
    exif=Image.Exif();exif[274]=1;exif[270]='Original synthetic metadata to remove'
    raw=image_bytes('JPEG',quality=91,progressive=True,exif=exif)
    comment=b'original synthetic comment';segment=b'\xff\xfe'+(len(comment)+2).to_bytes(2,'big')+comment
    raw=raw[:2]+segment+raw[2:]
    data,_,proof=sanitize_worker_export(raw,'image/jpeg',(256,256))
    assert pixels(raw)[:2]==pixels(data)[:2]
    assert {item['container'] for item in proof['removed']}=={'JPEG:E1','JPEG:FE'}
    assert comment not in data and b'Original synthetic metadata' not in data


def test_webp_alpha_metadata_and_image_payload_preserved():
    with Image.open(FIXTURES/'canvas-srgb.webp') as image:icc=image.info['icc_profile']
    exif=Image.Exif();exif[274]=1;exif[270]='Synthetic description'
    raw=image_bytes('WEBP',lossless=True,icc_profile=icc,exif=exif,xmp=b'<xmp>synthetic</xmp>')
    data,_,proof=sanitize_worker_export(raw,'image/webp',(256,256))
    assert pixels(raw)[:2]==pixels(data)[:2]
    assert {item['container'] for item in proof['removed']}=={'WebP:ICCP','WebP:EXIF','WebP:XMP'}
    def payload(value):
        pos=12
        while pos<len(value):
            name=value[pos:pos+4];size=int.from_bytes(value[pos+4:pos+8],'little')
            if name==b'VP8L':return value[pos+8:pos+8+size]
            pos+=8+size+(size&1)
        pytest.fail('Expected lossless image payload')
    assert payload(raw)==payload(data)


def test_png_without_metadata_stays_byte_identical():
    raw=image_bytes()
    data,_,proof=sanitize_worker_export(raw,'image/png',(256,256))
    assert data==raw and proof['removed']==[]


@pytest.mark.parametrize('fmt',['JPEG','WEBP'])
def test_rotated_exif_is_rejected_instead_of_changing_display_orientation(fmt):
    exif=Image.Exif();exif[274]=6
    raw=image_bytes(fmt,exif=exif)
    with pytest.raises(ValueError,match='orientation'):
        sanitize_worker_export(raw,'image/'+fmt.lower(),(256,256))


def test_jpeg_unknown_metadata_and_trailing_payload_fail_closed():
    raw=(FIXTURES/'canvas-srgb.jpeg').read_bytes()
    for candidate in [raw+b'not-image',raw[:2]+b'\xff\xed\x00\x06test'+raw[2:]]:
        with pytest.raises(ValueError):sanitize_worker_export(candidate,'image/jpeg',(256,256))


def test_webp_trailing_bytes_and_false_riff_size_rejected():
    raw=(FIXTURES/'canvas-srgb.webp').read_bytes()
    for candidate in [raw+b'extra',raw[:4]+(4).to_bytes(4,'little')+raw[8:]]:
        with pytest.raises((ValueError,OSError)):
            sanitize_worker_export(candidate,'image/webp',(256,256))


def test_mismatch_rejects_and_decoder_slot_is_released():
    raw=(FIXTURES/'canvas-srgb.jpeg').read_bytes()
    for mime,size in [('image/png',(256,256)),('image/jpeg',(512,256))]:
        with pytest.raises(ValueError,match='dimensions mismatch'):
            sanitize_worker_export(raw,mime,size)
    assert sanitize_worker_export(raw,'image/jpeg',(256,256))[2]['decoded_rgba_identical']
