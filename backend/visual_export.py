"""Lossless metadata removal for fixed local worker exports, not image uploads.

The canvas renderer explicitly uses sRGB. This removes descriptive/profile
containers without encoding pixels again, and verifies decoded RGBA identity.
It is not a color-management guarantee for arbitrary externally supplied ICCs.
"""
import hashlib
import io
import warnings
import struct
import zlib

from fastapi import HTTPException
from PIL import Image
from backend import visuals as v

VERSION='worker-export-metadata-v1'


def _jpeg(raw):
    if not raw.startswith(b'\xff\xd8'):raise ValueError('Invalid JPEG start')
    out=bytearray(raw[:2]);i=2;removed=[];scans=0
    while i<len(raw):
        start=i
        if raw[i]!=255:raise ValueError('Invalid JPEG marker boundary')
        while i<len(raw) and raw[i]==255:i+=1
        if i>=len(raw):raise ValueError('Truncated JPEG marker')
        marker=raw[i];i+=1
        if marker==0xd9:
            if i!=len(raw) or not scans:raise ValueError('JPEG trailing data or missing scan')
            out.extend(raw[start:i]);return bytes(out),removed
        if marker in {0,1,0xd8} or 0xd0<=marker<=0xd7:raise ValueError('Unexpected JPEG standalone marker')
        if i+2>len(raw):raise ValueError('Truncated JPEG length')
        length=int.from_bytes(raw[i:i+2],'big');end=i+length
        if length<2 or end>len(raw):raise ValueError('Invalid JPEG segment length')
        payload=raw[i+2:end]
        if marker in {0xe1,0xe2,0xfe}:
            removed.append({'container':f'JPEG:{marker:02X}','bytes':end-start})
        else:
            if 0xe0<=marker<=0xef:
                # JFIF's fixed density fields and Adobe color transform are
                # structural. Reject other APP containers, including thumbnails.
                if marker==0xe0:
                    if len(payload)!=14 or not payload.startswith(b'JFIF\x00') or payload[-2:]!=b'\x00\x00':
                        raise ValueError('Unexpected JPEG APP0 metadata')
                elif marker==0xee:
                    if len(payload)!=12 or not payload.startswith(b'Adobe'):
                        raise ValueError('Unexpected JPEG APP14 metadata')
                else:raise ValueError('Unsupported JPEG application metadata')
            out.extend(raw[start:end])
        i=end
        if marker==0xda:
            scans+=1;entropy=i
            while True:
                marker_start=raw.find(b'\xff',i)
                if marker_start<0:raise ValueError('JPEG scan has no end marker')
                after=marker_start+1
                while after<len(raw) and raw[after]==255:after+=1
                if after>=len(raw):raise ValueError('Truncated JPEG scan')
                code=raw[after]
                if code==0 or 0xd0<=code<=0xd7:i=after+1;continue
                out.extend(raw[entropy:marker_start]);i=marker_start;break
    raise ValueError('JPEG has no end marker')


def _webp(raw):
    if len(raw)<12 or raw[:4]!=b'RIFF' or raw[8:12]!=b'WEBP' or int.from_bytes(raw[4:8],'little')!=len(raw)-8:
        raise ValueError('Invalid WebP RIFF boundary')
    out=bytearray(b'WEBP');i=12;removed=[];pixels=0;extended=False
    while i<len(raw):
        if i+8>len(raw):raise ValueError('Truncated WebP chunk header')
        name=raw[i:i+4];length=int.from_bytes(raw[i+4:i+8],'little');end=i+8+length;padded=end+(length&1)
        if padded>len(raw) or (length&1 and raw[end]!=0):raise ValueError('Invalid WebP chunk length/padding')
        payload=raw[i+8:end]
        if name in {b'ICCP',b'EXIF',b'XMP '}:
            removed.append({'container':'WebP:'+name.decode('ascii').strip(),'bytes':padded-i})
        elif name==b'VP8X':
            if extended or i!=12 or length!=10 or payload[0]&0xc3 or payload[1:4]!=b'\x00\x00\x00':
                raise ValueError('Unsupported WebP extended/animated header')
            extended=True
            # Clear only ICC, EXIF and XMP presence flags; preserve alpha/size.
            cleaned=bytes([payload[0]&~0x2c])+payload[1:]
            out.extend(name+length.to_bytes(4,'little')+cleaned)
        elif name in {b'VP8 ',b'VP8L',b'ALPH'}:
            if name!=b'ALPH':pixels+=1
            out.extend(raw[i:padded])
        else:raise ValueError('Unsupported WebP chunk')
        i=padded
    if pixels!=1:raise ValueError('Expected one WebP image payload')
    return b'RIFF'+len(out).to_bytes(4,'little')+bytes(out),removed


def _inspect(raw):
    with Image.open(io.BytesIO(raw)) as image:
        kind=image.format
        if kind not in {'JPEG','PNG','WEBP'} or getattr(image,'n_frames',1)!=1:
            raise ValueError('Unsupported worker image format/frames')
        if image.width*image.height>v.MAX_PIXELS:raise ValueError('Worker image pixel limit')
        if kind=='JPEG' and image.mode not in {'RGB','L'}:raise ValueError('Worker JPEG must be RGB or grayscale')
        size=image.size;image.verify()
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        if image.getexif().get(274,1)!=1:raise ValueError('Worker image orientation must be encoded into pixels')
        info=dict(image.info);pixel_hash=hashlib.sha256(image.convert('RGBA').tobytes()).hexdigest()
    return kind,size,pixel_hash,info


def _png_print_density(raw,dpi):
    """Set PNG pHYs and known canvas sRGB without re-encoding IDAT pixels."""
    ppm=round(dpi/0.0254)
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    out=bytearray(raw[:8]);pos=8;inserted=False
    while pos<len(raw):
        length=int.from_bytes(raw[pos:pos+4],'big');end=pos+12+length;kind=raw[pos+4:pos+8]
        if end>len(raw):raise ValueError('PNG chunk boundary')
        if kind in {b'PLTE',b'IDAT'} and not inserted:
            out.extend(chunk(b'sRGB',b'\x00'))
            out.extend(chunk(b'pHYs',struct.pack('>IIB',ppm,ppm,1)));inserted=True
        if kind not in {b'pHYs',b'sRGB',b'gAMA',b'cHRM'}:out.extend(raw[pos:end])
        pos=end
    if not inserted:raise ValueError('PNG pixels missing')
    return bytes(out),ppm


def sanitize_worker_export(raw, expected_mime, expected_size, *, print_dpi=None):
    """Return exact-pixel bytes, asset metadata and an inspectable receipt."""
    if not raw or len(raw)>v.MAX_STORED_ASSET:raise ValueError('Worker image byte limit')
    if print_dpi is not None and (type(print_dpi) is not int or not 72<=print_dpi<=600 or expected_mime!='image/png'):
        raise ValueError('Print density requires PNG and 72 to 600 DPI')
    if not v.DECODER_SLOT.acquire(blocking=False):raise HTTPException(409,'Another image is being decoded; retry after it finishes')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            kind,size,pixels,info=_inspect(raw)
            mime={'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}[kind]
            if mime!=expected_mime or size!=tuple(expected_size):raise ValueError('Worker output format/dimensions mismatch')
            if kind=='JPEG':cleaned,removed=_jpeg(raw)
            elif kind=='WEBP':cleaned,removed=_webp(raw)
            else:
                if info.get('exif') or info.get('icc_profile'):raise ValueError('Unexpected PNG export metadata')
                cleaned,removed=raw,[]
            ppm=None
            if print_dpi is not None:cleaned,ppm=_png_print_density(cleaned,print_dpi)
            if len(cleaned)>v.MAX_STORED_ASSET:raise ValueError('Worker image byte limit')
            after_kind,after_size,after_pixels,after_info=_inspect(cleaned)
            if (after_kind,after_size,after_pixels)!=(kind,size,pixels):raise ValueError('Metadata removal changed image pixels or geometry')
            if any(after_info.get(name) for name in ('exif','icc_profile','xmp','comment')):
                raise ValueError('Descriptive worker metadata remains')
            if ppm is not None and (after_info.get('srgb')!=0 or any(abs(n-ppm*.0254)>.001 for n in after_info.get('dpi',(0,0)))):
                raise ValueError('PNG density verification failed')
        original=v.digest(raw);final=v.digest(cleaned)
        metadata={'mime':mime,'width':size[0],'height':size[1],'normalizer':VERSION,'original_sha256':original}
        receipt={'version':VERSION,'reencoded':False,'decoded_rgba_identical':True,'decoded_rgba_sha256':pixels,
                 'original_sha256':original,'stored_sha256':final,'removed':removed,
                 'color_scope':'Fixed canvas sRGB export; embedded profile removed. No arbitrary ICC color-conversion claim.'}
        if ppm is not None:
            receipt['print']={'requested_dpi':print_dpi,'pixels_per_metre':ppm,'actual_dpi':ppm*.0254,
                'width_mm':size[0]/ppm*1000,'height_mm':size[1]/ppm*1000,'color_space':'sRGB',
                'metadata_verified':True,'physical_print_verified':False,'cmyk':False,'resampled':False,
                'scope':'PNG density and sRGB metadata only; no printer profile, bleed or physical proof.'}
        return cleaned,metadata,receipt
    finally:v.DECODER_SLOT.release()
