"""Bounded stdlib audio wire contract. No compressed-media parser on the host."""
from __future__ import annotations
import base64,hashlib,json,math,struct

VERSION='isolated-audio-decode-v1'
RATE=16000
MAX_SECONDS=60
MAX_INPUT=8*1024**2
MAX_SAMPLES=RATE*MAX_SECONDS
MAX_PCM=4*MAX_SAMPLES
MAX_PAYLOAD=4*((MAX_INPUT+2)//3)+4096
MAX_ENVELOPE=4*((MAX_PCM+2)//3)+8192
ERRORS={'audio_input_limit','audio_format','audio_streams','audio_rate','audio_duration','audio_invalid','audio_samples','audio_too_short'}

class AudioProtocolError(ValueError):pass

def dump(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
def loads(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise AudioProtocolError('audio_duplicate_field')
            result[key]=value
        return result
    def invalid(_):raise AudioProtocolError('audio_nonfinite_json')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)

def kind(raw):
    if not isinstance(raw,bytes) or not 1<=len(raw)<=MAX_INPUT:raise AudioProtocolError('audio_input_limit')
    if raw[:4]==b'RIFF' and raw[8:12]==b'WAVE':return 'wav'
    if raw[:4]==b'\x1aE\xdf\xa3':return 'webm'
    raise AudioProtocolError('audio_format')

def validate_pcm(raw):
    if not isinstance(raw,bytes) or not RATE//10*4<=len(raw)<=MAX_PCM or len(raw)%4:raise AudioProtocolError('audio_pcm_length')
    if any(not math.isfinite(value) for (value,) in struct.iter_unpack('<f',raw)):raise AudioProtocolError('audio_pcm_nonfinite')
    return len(raw)//4

def validate_source(source):
    if not isinstance(source,dict) or set(source)!={'kind','sample_rate','channels'} or source['kind'] not in {'wav','webm'}:
        raise AudioProtocolError('audio_source_fields')
    if type(source['sample_rate']) is not int or not 8000<=source['sample_rate']<=192000 or type(source['channels']) is not int or not 1<=source['channels']<=2:
        raise AudioProtocolError('audio_source_limits')
    return source

def decode_response(raw,original):
    if not isinstance(raw,bytes) or len(raw)>MAX_ENVELOPE:raise AudioProtocolError('audio_response_limit')
    value=loads(raw)
    if not isinstance(value,dict) or value.get('version')!=VERSION or value.get('input_sha256')!=hashlib.sha256(original).hexdigest():raise AudioProtocolError('audio_response_identity')
    if value.get('success') is False:
        if set(value)!={'version','input_sha256','success','error_code'} or value['error_code'] not in ERRORS:raise AudioProtocolError('audio_error_fields')
        return None,{'error_code':value['error_code']}
    if value.get('success') is not True or set(value)!={'version','input_sha256','success','pcm','source','engine'}:raise AudioProtocolError('audio_response_fields')
    pcm=value['pcm']
    if not isinstance(pcm,dict) or set(pcm)!={'format','sample_rate','channels','samples','bytes','sha256','base64'}:raise AudioProtocolError('audio_pcm_fields')
    if pcm['format']!='f32le' or type(pcm['sample_rate']) is not int or pcm['sample_rate']!=RATE or type(pcm['channels']) is not int or pcm['channels']!=1:raise AudioProtocolError('audio_pcm_format')
    if type(pcm['samples']) is not int or not RATE//10<=pcm['samples']<=MAX_SAMPLES or type(pcm['bytes']) is not int or pcm['bytes']!=pcm['samples']*4:
        raise AudioProtocolError('audio_pcm_shape')
    if not isinstance(pcm['base64'],str) or len(pcm['base64'])>4*((MAX_PCM+2)//3):raise AudioProtocolError('audio_pcm_encoding')
    data=base64.b64decode(pcm['base64'],validate=True)
    if len(data)!=pcm['bytes'] or hashlib.sha256(data).hexdigest()!=pcm['sha256'] or validate_pcm(data)!=pcm['samples']:raise AudioProtocolError('audio_pcm_integrity')
    source=validate_source(value['source'])
    if source['kind']!=kind(original):raise AudioProtocolError('audio_source_kind')
    engine=value['engine']
    if not isinstance(engine,dict) or set(engine)!={'av','numpy'} or any(not isinstance(v,str) or not 1<=len(v)<=30 for v in engine.values()):raise AudioProtocolError('audio_engine_fields')
    return data,{'source':source,'engine':engine}

def diagnostics(raw,source):
    """Independent numeric inspection of bounded flat PCM, never codec parsing."""
    validate_source(source);count=validate_pcm(raw)
    import numpy as np
    values=np.frombuffer(raw,dtype='<f4')
    peak=float(np.max(np.abs(values)));rms=float(np.sqrt(np.mean(values.astype(np.float64)**2)))
    return values,{'duration':count/RATE,'source_sample_rate':source['sample_rate'],'source_channels':source['channels'],
        'sample_rate':RATE,'channels':1,'decoded_samples':count,'peak':peak,'rms':rms,
        'rms_dbfs':20*math.log10(rms) if rms else None,'clipped_fraction':float(np.count_nonzero(np.abs(values)>=.999))/count,
        'zero_crossing_rate':float(np.count_nonzero(np.diff(np.signbit(values))))/max(1,count-1),
        'resampled':source['sample_rate']!=RATE or source['channels']!=1,'source_kind':source['kind'],'decode_complete':True}
