"""Bounded stdlib-only document-page protocol shared by host and container.

No image decoder runs here. PNG checks are structural, not an image-safety or
OCR-quality certificate. The native parser/renderer remains inside the worker.
"""
from __future__ import annotations
import base64
import hashlib
import json
import math
import re
import struct
import zlib

VERSION='isolated-document-page-v1'
REGION_VERSION='isolated-document-region-v1'
NORMALIZE_VERSION='isolated-image-normalize-v1'
VISUAL_NORMALIZE_VERSION='isolated-visual-normalize-v1'
MAX_IMAGE_INPUT=3*1024**2
MAX_VISUAL_INPUT=6*1024**2
MAX_IMAGE_PIXELS=4_000_000
# Four million RGB pixels plus PNG scanline/compression/chunk overhead; no resizing.
MAX_NORMALIZED_BYTES=12_500_000
MAX_VISUAL_NORMALIZED_BYTES=16_500_000
MAX_REGION_BYTES=3*1024**2
MAX_REGION_PIXELS=4_000_000
MAX_INPUT=8*1024**2
MAX_RESULT=2*1024**2
MAX_PREVIEW=8*1024**2
MAX_ENVELOPE=MAX_RESULT+4*((MAX_PREVIEW+2)//3)+65536
MAX_NORMALIZE_ENVELOPE=4*((MAX_NORMALIZED_BYTES+2)//3)+65536
MAX_VISUAL_NORMALIZE_ENVELOPE=4*((MAX_VISUAL_NORMALIZED_BYTES+2)//3)+65536
MAX_PAYLOAD=4*((MAX_INPUT+2)//3)+65536
MAX_PIXELS=8000000
HEX=re.compile(r'^[0-9a-f]{64}$')


class ProtocolError(ValueError):pass


def dump(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')


def loads(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ProtocolError('Duplicate JSON field')
            result[key]=value
        return result
    def invalid(value):raise ProtocolError('Nonfinite JSON number')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)


def validate_metadata(value):
    if isinstance(value,dict) and value.get('operation')=='code':
        from backend.isolated_visual_codes import metadata
        return metadata(value)
    if isinstance(value,dict) and value.get('operation')=='normalize':
        if set(value)!={'operation'} and not (set(value)=={'operation','variant'} and value['variant']=='visual'):raise ProtocolError('Invalid normalize metadata')
        return value
    if isinstance(value,dict) and value.get('operation')=='region':return validate_region_metadata(value)
    from backend.document_extract import Limits,languages
    from dataclasses import asdict
    if not isinstance(value,dict) or set(value)!={'name','page','languages','limits'}:raise ProtocolError('Invalid document metadata')
    name=value['name']
    if not isinstance(name,str) or not 1<=len(name)<=255 or re.search(r'[\\/\x00-\x1f]',name):raise ProtocolError('Invalid original basename')
    if type(value['page']) is not int or not 0<=value['page']<=200:raise ProtocolError('Invalid page')
    if languages(value['languages'])!=value['languages']:raise ProtocolError('Noncanonical OCR languages')
    limits=value['limits'];defaults=asdict(Limits())
    if not isinstance(limits,dict) or set(limits)!=set(defaults):raise ProtocolError('Invalid fixed limits')
    for key,number in limits.items():
        if type(number) is not int or not 1<=number<=defaults[key]:raise ProtocolError('Limit exceeds reviewed maximum')
    if len(dump(value))>8192:raise ProtocolError('Metadata limit')
    return value


def validate_region_metadata(value):
    if not isinstance(value,dict) or set(value)!={'operation','bbox'} or value['operation']!='region':raise ProtocolError('Invalid region metadata')
    box=value['bbox']
    if box is not None and (not isinstance(box,list) or len(box)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in box) or not (0<=box[0]<box[2]<=1 and 0<=box[1]<box[3]<=1)):
        raise ProtocolError('Invalid region rectangle')
    return value


def region_response(raw,metadata,original):
    """Validate identity, byte bounds and geometry without decoding pixels."""
    validate_region_metadata(metadata)
    if not isinstance(raw,bytes) or len(raw)>MAX_ENVELOPE:raise ProtocolError('Region response limit')
    value=loads(raw)
    if not isinstance(value,dict) or set(value)!={'version','input_sha256','success','region','preview'} or value['version']!=REGION_VERSION or value['input_sha256']!=hashlib.sha256(original).hexdigest() or value['success'] is not True:
        raise ProtocolError('Region response identity')
    pic=value['preview']
    if not isinstance(pic,dict) or set(pic)!={'bytes','sha256','base64'} or type(pic['bytes']) is not int or not 1<=pic['bytes']<=MAX_REGION_BYTES or not isinstance(pic['base64'],str) or len(pic['base64'])>4*((MAX_REGION_BYTES+2)//3):raise ProtocolError('Region preview envelope')
    data=base64.b64decode(pic['base64'],validate=True)
    if len(data)!=pic['bytes'] or hashlib.sha256(data).hexdigest()!=pic['sha256']:raise ProtocolError('Region preview hash')
    width,height=png_dimensions(original);cw,ch=png_dimensions(data);box=metadata['bbox'] or [0.,0.,1.,1.]
    pixels=[math.floor(box[0]*width),math.floor(box[1]*height),math.ceil(box[2]*width),math.ceil(box[3]*height)]
    if cw*ch>MAX_REGION_PIXELS or cw>pixels[2]-pixels[0] or ch>pixels[3]-pixels[1]:raise ProtocolError('Region geometry limit')
    expected={'bbox':[pixels[0]/width,pixels[1]/height,pixels[2]/width,pixels[3]/height],
        'pixel_bbox':pixels,'preview_width':width,'preview_height':height,'model_image_width':cw,'model_image_height':ch,
        'region_sha256':pic['sha256'],'resized':(cw,ch)!=(pixels[2]-pixels[0],pixels[3]-pixels[1])}
    region=value['region']
    if not isinstance(region,dict) or set(region)!=set(expected) or any(type(region[k]) is not int for k in ('preview_width','preview_height','model_image_width','model_image_height')) or type(region['resized']) is not bool:
        raise ProtocolError('Region geometry types')
    if not isinstance(region['pixel_bbox'],list) or any(type(v) is not int for v in region['pixel_bbox']) or not isinstance(region['bbox'],list) or any(type(v) not in (int,float) or not math.isfinite(v) for v in region['bbox']):raise ProtocolError('Region coordinate types')
    if region!=expected:raise ProtocolError('Region geometry mismatch')
    return data,expected


def png_dimensions(raw,maximum_bytes=MAX_PREVIEW,*,allow_alpha=False):
    if not isinstance(raw,bytes) or not 1<=len(raw)<=maximum_bytes or raw[:8]!=b'\x89PNG\r\n\x1a\n':raise ProtocolError('Invalid PNG envelope')
    pos=8;count=0;dimensions=None;data_seen=False;ended=False
    while pos<len(raw):
        if pos+12>len(raw) or count>=4096:raise ProtocolError('PNG chunk limit')
        size=struct.unpack('>I',raw[pos:pos+4])[0];kind=raw[pos+4:pos+8]
        if size>maximum_bytes or pos+12+size>len(raw):raise ProtocolError('Invalid PNG chunk size')
        content=raw[pos+8:pos+8+size];crc=struct.unpack('>I',raw[pos+8+size:pos+12+size])[0]
        if zlib.crc32(kind+content)&0xffffffff!=crc:raise ProtocolError('PNG CRC mismatch')
        if count==0:
            if kind!=b'IHDR' or size!=13:raise ProtocolError('PNG header missing')
            width,height,depth,color,compression,filter_,interlace=struct.unpack('>IIBBBBB',content)
            if not width or not height or width*height>MAX_PIXELS:raise ProtocolError('PNG pixel limit')
            if depth!=8 or color not in ({2,6} if allow_alpha else {2}) or (compression,filter_,interlace)!=(0,0,0):raise ProtocolError('Unexpected normalized PNG format')
            dimensions=(width,height)
        elif kind==b'IDAT':
            if ended:raise ProtocolError('Trailing PNG data')
            data_seen=True
        elif kind==b'IEND':
            if size or not data_seen:raise ProtocolError('Invalid PNG end')
            ended=True
        else:raise ProtocolError('Unexpected PNG chunk')
        pos+=size+12;count+=1
        if ended and pos!=len(raw):raise ProtocolError('Trailing PNG bytes')
    if not ended:raise ProtocolError('PNG end missing')
    return dimensions


def normalize_response(raw,original,*,variant=None):
    if variant not in (None,'visual'):raise ProtocolError('Invalid normalize variant')
    maximum=MAX_VISUAL_NORMALIZED_BYTES if variant=='visual' else MAX_NORMALIZED_BYTES
    envelope=MAX_VISUAL_NORMALIZE_ENVELOPE if variant=='visual' else MAX_NORMALIZE_ENVELOPE
    version=VISUAL_NORMALIZE_VERSION if variant=='visual' else NORMALIZE_VERSION
    if not isinstance(raw,bytes) or len(raw)>envelope:raise ProtocolError('Normalize response limit')
    value=loads(raw)
    if not isinstance(value,dict) or value.get('version')!=version or value.get('input_sha256')!=hashlib.sha256(original).hexdigest():raise ProtocolError('Normalize response identity')
    if value.get('success') is False:
        if set(value)!={'version','input_sha256','success','error_code'} or value['error_code'] not in {'image_input_limit','image_pixel_limit','image_format','image_invalid','image_output_limit'}:raise ProtocolError('Invalid image error')
        return None,{'error_code':value['error_code']}
    if value.get('success') is not True or set(value)!={'version','input_sha256','success','dimensions','preview'}:raise ProtocolError('Normalize response fields')
    pic=value['preview']
    if not isinstance(pic,dict) or set(pic)!={'bytes','sha256','base64'} or type(pic['bytes']) is not int or not 1<=pic['bytes']<=maximum or not isinstance(pic['base64'],str) or len(pic['base64'])>4*((maximum+2)//3):raise ProtocolError('Normalize preview limit')
    data=base64.b64decode(pic['base64'],validate=True)
    if len(data)!=pic['bytes'] or hashlib.sha256(data).hexdigest()!=pic['sha256']:raise ProtocolError('Normalize preview hash')
    width,height=png_dimensions(data,maximum,allow_alpha=variant=='visual')
    dimensions=value['dimensions']
    if width*height>MAX_IMAGE_PIXELS or not isinstance(dimensions,dict) or set(dimensions)!={'width','height'} or any(type(v) is not int for v in dimensions.values()) or dimensions!={'width':width,'height':height}:raise ProtocolError('Normalize dimensions')
    return data,dimensions


def validate_result(result,metadata,source_hash,source_size,preview):
    from backend.document_extract import MIMES
    if not isinstance(result,dict) or result.get('success') is not True or len(dump(result))>metadata['limits']['max_extracted_bytes']:raise ProtocolError('Invalid document result')
    count=result.get('page_count')
    if type(count) is not int or not 1<=count<=metadata['limits']['max_pages']:raise ProtocolError('Invalid page count')
    if result.get('mime') not in MIMES.values():raise ProtocolError('Unapproved result MIME')
    info=result.get('metadata')
    if not isinstance(info,dict) or info.get('sha256')!=source_hash or type(info.get('bytes')) is not int or info['bytes']!=source_size or info.get('filename')!=metadata['name'] or info.get('source_network') is not False:raise ProtocolError('Source identity mismatch')
    requested=metadata['page']
    if not requested:
        if 'page' in result or preview is not None:raise ProtocolError('Inspection returned a page')
        return result
    page=result.get('page')
    if requested>count or not isinstance(page,dict) or type(page.get('number')) is not int or page['number']!=requested:raise ProtocolError('Page identity mismatch')
    if page.get('status') not in {'complete','partial','failed'} or page.get('kind') not in {'native','scan','mixed','text','sheet','slide'}:raise ProtocolError('Invalid page state')
    if preview is None:
        if page.get('preview_file') is not None:raise ProtocolError('Promised preview missing')
    else:
        width,height=png_dimensions(preview)
        if page.get('preview_file')!='page.png' or type(page.get('width')) is not int or type(page.get('height')) is not int or page['width']!=width or page['height']!=height:raise ProtocolError('Preview dimensions differ')
        if width*height>metadata['limits']['max_pixels']:raise ProtocolError('Configured pixel limit')
    elements=page.get('elements')
    if not isinstance(elements,list) or len(elements)>metadata['limits']['max_elements']:raise ProtocolError('Element limit')
    seen=set();cells=0
    for element in elements:
        if not isinstance(element,dict) or not isinstance(element.get('id'),str) or not re.fullmatch(r'p'+str(requested)+r'-e[0-9]{5}',element['id']) or element['id'] in seen:raise ProtocolError('Invalid element identity')
        seen.add(element['id'])
        if element.get('type') not in {'text','table','image','chart'} or not all(isinstance(element.get(k),str) for k in ['text','text_raw']):raise ProtocolError('Invalid element text')
        locator=element.get('locator')
        if not isinstance(locator,dict) or type(locator.get('page')) is not int or locator['page']!=requested:raise ProtocolError('Invalid source locator')
        box=element.get('bbox')
        if box is not None and (not isinstance(box,list) or len(box)!=4 or any(type(n) not in (int,float) or not math.isfinite(n) or not 0<=n<=1 for n in box) or box[2]<box[0] or box[3]<box[1]):raise ProtocolError('Invalid normalized box')
        if 'cells' in element:
            if not isinstance(element['cells'],list):raise ProtocolError('Invalid cells')
            cells+=len(element['cells'])
            if cells>metadata['limits']['max_cells']:raise ProtocolError('Cell limit')
    return result


def decode_response(raw,metadata,source_hash,source_size):
    if not isinstance(raw,bytes) or len(raw)>MAX_ENVELOPE:raise ProtocolError('Response byte limit')
    value=loads(raw)
    if not isinstance(value,dict) or value.get('version')!=VERSION or value.get('input_sha256')!=source_hash:raise ProtocolError('Response identity mismatch')
    if value.get('success') is not True:
        raise ProtocolError('Document worker rejected input: '+str(value.get('error_code','parser_failed'))[:80])
    if set(value)!={'version','input_sha256','success','result','preview'}:raise ProtocolError('Unknown response fields')
    preview=value['preview']
    if preview is not None:
        if not isinstance(preview,dict) or set(preview)!={'bytes','sha256','base64'} or type(preview['bytes']) is not int or not 1<=preview['bytes']<=MAX_PREVIEW or not isinstance(preview['base64'],str) or len(preview['base64'])>4*((MAX_PREVIEW+2)//3):raise ProtocolError('Preview envelope limit')
        data=base64.b64decode(preview['base64'],validate=True)
        if len(data)!=preview['bytes'] or hashlib.sha256(data).hexdigest()!=preview['sha256']:raise ProtocolError('Preview identity mismatch')
        preview=data
    validate_result(value['result'],metadata,source_hash,source_size,preview)
    return value['result'],preview
