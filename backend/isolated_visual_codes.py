"""Bounded stdlib contract for generated, decoded machine-readable symbols."""
import base64,hashlib,re
from backend.isolated_document_page import dump,loads,png_dimensions,ProtocolError
VERSION='isolated-visual-code-v1'
MAX_BYTES=512
MAX_PNG=2*1024**2
MAX_ENVELOPE=4*((MAX_PNG+2)//3)+65536


def metadata(value):
    if not isinstance(value,dict) or set(value)!={'operation','kind','scale'} or value['operation']!='code' or value['kind'] not in {'qr','code128'} or type(value['scale']) is not int or not 3<=value['scale']<=6:raise ProtocolError('Invalid code options')
    return value


def text_input(raw,options):
    metadata(options)
    if not isinstance(raw,bytes) or not 1<=len(raw)<=MAX_BYTES:raise ProtocolError('Code text must contain 1 to 512 UTF-8 bytes')
    text=raw.decode('utf-8')
    if not text.strip() or any(ord(c)<32 or 127<=ord(c)<160 for c in text):raise ProtocolError('Code text contains unsupported controls')
    if options['kind']=='code128' and (len(text)>80 or not re.fullmatch(r'[ -~]+',text)):raise ProtocolError('Code128 accepts 1 to 80 printable ASCII characters')
    return text


def response(raw,original,options):
    text_input(original,options)
    if not isinstance(raw,bytes) or len(raw)>MAX_ENVELOPE:raise ProtocolError('Code output limit')
    value=loads(raw);source=hashlib.sha256(original).hexdigest()
    if not isinstance(value,dict) or set(value)!={'version','input_sha256','success','proof','preview'} or value['version']!=VERSION or value['input_sha256']!=source or value['success'] is not True:raise ProtocolError('Code response identity')
    pic=value['preview']
    if not isinstance(pic,dict) or set(pic)!={'bytes','sha256','base64'} or type(pic['bytes']) is not int or not 1<=pic['bytes']<=MAX_PNG or not isinstance(pic['base64'],str) or len(pic['base64'])>4*((MAX_PNG+2)//3):raise ProtocolError('Code preview limit')
    data=base64.b64decode(pic['base64'],validate=True)
    if len(data)!=pic['bytes'] or hashlib.sha256(data).hexdigest()!=pic['sha256']:raise ProtocolError('Code preview hash')
    width,height=png_dimensions(data)
    proof=value['proof'];expected={'kind':options['kind'],'scale':options['scale'],'input_sha256':source,'decoded_sha256':source,'output_sha256':pic['sha256'],
      'width':width,'height':height,'quiet_zone_modules':4 if options['kind']=='qr' else 10,'decoder':'zxing-cpp','decoder_version':'3.1.1',
      'encoder':'segno' if options['kind']=='qr' else 'python-barcode','encoder_version':'1.6.6' if options['kind']=='qr' else '0.16.1','decode_matches':True}
    if not isinstance(proof,dict) or proof!=expected or any(type(proof.get(k)) is not int for k in ('width','height','scale','quiet_zone_modules')) or proof.get('decode_matches') is not True or width*height>4_000_000:raise ProtocolError('Code verification mismatch')
    return data,proof
