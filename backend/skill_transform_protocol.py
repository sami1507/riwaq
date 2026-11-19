"""Bounded data-only wire contract; no parser, subprocess or database imports."""
from __future__ import annotations
import base64
from dataclasses import dataclass
import hashlib
import json
import re

VERSION = 'skill-transform-v1'
MAX_INPUT = MAX_OUTPUT = 8 * 1024 ** 2
MAX_JSON = 1024 ** 2
MAX_PAYLOAD = MAX_ENVELOPE = 13 * 1024 ** 2
MIMES = {'pdf':'application/pdf','docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
         'xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','csv':'text/csv','txt':'text/plain','png':'image/png','json':'application/json'}

class TransformError(ValueError):
    def __init__(self,code): self.code=code; super().__init__(code)

def dump(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
def digest(value): return hashlib.sha256(dump(value)).hexdigest()
def loads(raw):
    def pairs(items):
        out={}
        for k,v in items:
            if k in out: raise TransformError('duplicate_json_key')
            out[k]=v
        return out
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(TransformError('nonfinite_json')))

def name(value):
    if not isinstance(value,str) or not 1<=len(value)<=200 or value in {'.','..'} or re.search(r'[/\\:\x00-\x1f\x7f]',value):
        raise TransformError('invalid_basename')
    return value

def identity(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',value): raise TransformError('invalid_source_id')
    return value

def validate_payload(value):
    if not isinstance(value,dict) or set(value)!={'version','domain','request','sources'} or value['version']!=VERSION or value['domain'] not in {'documents','data'}:
        raise TransformError('invalid_transform_envelope')
    if not isinstance(value['request'],dict) or len(dump(value['request']))>MAX_JSON: raise TransformError('request_limit')
    sources=value['sources']
    if not isinstance(sources,list) or len(sources)>8: raise TransformError('source_limit')
    seen=set(); total=0
    for source in sources:
        if not isinstance(source,dict) or set(source)!={'id','name','version','sha256','role','base64'}: raise TransformError('source_envelope')
        key=identity(source['id']); name(source['name'])
        if key in seen: raise TransformError('duplicate_source')
        seen.add(key)
        if type(source['version']) is not int or source['version']<1 or source['role'] not in {'source','template'}: raise TransformError('source_version_or_role')
        if not isinstance(source['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',source['sha256']): raise TransformError('source_hash')
        if not isinstance(source['base64'],str) or len(source['base64'])>4*((MAX_INPUT+2)//3): raise TransformError('source_size')
        raw=base64.b64decode(source['base64'],validate=True);total+=len(raw)
        if not raw or total>MAX_INPUT or hashlib.sha256(raw).hexdigest()!=source['sha256']: raise TransformError('source_size_or_hash')
    return value

def encode_payload(domain,request,sources):
    packed=[]
    for source in sources:
        raw=source['content']
        if not isinstance(raw,bytes): raise TransformError('source_bytes_required')
        packed.append({'id':source.get('id',source.get('file_id')),'name':source['name'],'version':source['version'],
                       'sha256':source['sha256'],'role':source.get('role','source'),'base64':base64.b64encode(raw).decode('ascii')})
    raw=dump(validate_payload({'version':VERSION,'domain':domain,'request':request,'sources':packed}))
    if len(raw)>MAX_PAYLOAD: raise TransformError('payload_limit')
    return raw

@dataclass(frozen=True)
class Artifact:
    content: bytes
    filename: str
    media_type: str
    sha256: str
    verification: dict
    limitations: tuple[str,...]

@dataclass(frozen=True)
class TransformResult:
    result: dict
    artifacts: tuple[Artifact,...]
    source_refs: tuple[dict,...]
    receipt: dict

def output_artifact(raw,filename,verification=None,limitations=()):
    if not isinstance(raw,bytes) or not 1<=len(raw)<=MAX_OUTPUT: raise TransformError('artifact_size_limit')
    name(filename); extension=filename.rsplit('.',1)[-1]
    if extension not in MIMES: raise TransformError('artifact_format')
    return Artifact(raw,filename,MIMES[extension],hashlib.sha256(raw).hexdigest(),verification or {},tuple(limitations))

def encode_result(payload,result,artifacts,source_refs=()):
    rows=[];total=0
    for artifact in artifacts:
        total+=len(artifact.content)
        if total>MAX_OUTPUT: raise TransformError('output_limit')
        rows.append({'filename':artifact.filename,'media_type':artifact.media_type,'bytes':len(artifact.content),'sha256':artifact.sha256,
                     'base64':base64.b64encode(artifact.content).decode('ascii'),'verification':artifact.verification,'limitations':list(artifact.limitations)})
    envelope={'version':VERSION,'request_sha256':hashlib.sha256(payload).hexdigest(),'success':True,'result':result,'artifacts':rows,'source_refs':list(source_refs)}
    raw=dump(envelope)
    if len(raw)>MAX_ENVELOPE: raise TransformError('output_limit')
    return raw

def decode_result(raw,payload,receipt):
    if len(raw)>MAX_ENVELOPE: raise TransformError('output_limit')
    envelope=loads(raw)
    if not isinstance(envelope,dict) or envelope.get('version')!=VERSION or envelope.get('request_sha256')!=hashlib.sha256(payload).hexdigest(): raise TransformError('result_identity_mismatch')
    if envelope.get('success') is not True:
        code=envelope.get('error_code')
        raise TransformError(code if isinstance(code,str) and re.fullmatch('[a-z0-9_]{1,100}',code) else 'worker_rejected')
    if set(envelope)!={'version','request_sha256','success','result','artifacts','source_refs'}: raise TransformError('result_envelope')
    if not isinstance(envelope['result'],dict) or len(dump(envelope['result']))>MAX_JSON: raise TransformError('result_limit')
    refs=envelope['source_refs'];sources={s['id']:s for s in loads(payload)['sources']}
    if not isinstance(refs,list) or len(refs)>2000 or len(dump(refs))>MAX_JSON: raise TransformError('source_reference_limit')
    for ref in refs:
        if not isinstance(ref,dict) or ref.get('source_id') not in sources: raise TransformError('source_reference_identity')
        source=sources[ref['source_id']]
        if ref.get('sha256')!=source['sha256'] or ref.get('version')!=source['version']: raise TransformError('source_reference_version')
    rows=envelope['artifacts'];artifacts=[];total=0;names=set()
    if not isinstance(rows,list) or len(rows)>8: raise TransformError('artifact_count')
    for item in rows:
        if not isinstance(item,dict) or set(item)!={'filename','media_type','bytes','sha256','base64','verification','limitations'}: raise TransformError('artifact_envelope')
        if type(item['bytes']) is not int or not 1<=item['bytes']<=MAX_OUTPUT or not isinstance(item['base64'],str) or len(item['base64'])>4*((MAX_OUTPUT+2)//3): raise TransformError('artifact_size')
        raw=base64.b64decode(item['base64'],validate=True);total+=len(raw)
        output=output_artifact(raw,item['filename'],item['verification'],item['limitations'])
        if output.filename in names or len(raw)!=item['bytes'] or output.sha256!=item['sha256'] or output.media_type!=item['media_type'] or total>MAX_OUTPUT: raise TransformError('artifact_integrity')
        if not isinstance(output.verification,dict) or not isinstance(item['limitations'],list) or any(not isinstance(s,str) for s in item['limitations']): raise TransformError('artifact_verification')
        names.add(output.filename);artifacts.append(output)
    return TransformResult(envelope['result'],tuple(artifacts),tuple(refs),receipt)
