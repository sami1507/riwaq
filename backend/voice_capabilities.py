"""Offline language/capability discovery; no audio, model inference or provider calls.

SAPI voices must come from the fixed worker's successful runtime discovery.
Registry entries express preferences and tokenizer support, never dialect quality.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
from backend import media

ROOT=Path(__file__).resolve().parents[1]
REGISTRY=ROOT/'config'/'voice_languages.json'
MAX_TOKENIZER=4*1024**2


def bounded_json(path,maximum):
    with Path(path).open('rb') as stream:raw=stream.read(maximum+1)
    if len(raw)>maximum:raise ValueError('Local metadata exceeds its size limit')
    value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError('Local metadata must be an object')
    return value,hashlib.sha256(raw).hexdigest()


def registry():
    value,_=bounded_json(REGISTRY,128*1024)
    if value.get('version')!='voice-languages-v1':raise ValueError('Unsupported voice language registry')
    tags=[r['tag'].lower() for r in value['languages']]
    if len(tags)!=len(set(tags)):raise ValueError('Duplicate language tags')
    return value


def normalize_language(tag):
    if not isinstance(tag,str) or not 1<=len(tag)<=35:raise ValueError('Choose a listed language tag or auto')
    if tag.lower()=='auto':return 'auto'
    data=registry();key=tag.lower();key=data['aliases'].get(key,key)
    for row in data['languages']:
        if row['tag'].lower()==key:return row['tag']
    raise ValueError('Language preference is not in the declared BCP47 subset')


def model_languages(model_dir):
    folder=Path(model_dir)
    if not folder.is_absolute() or not folder.is_dir():raise ValueError('Speech model must be an existing absolute local directory')
    if not all((folder/name).is_file() for name in ('model.bin','config.json','tokenizer.json')):
        raise ValueError('Speech model files are incomplete; downloads are disabled')
    from faster_whisper.tokenizer import _LANGUAGE_CODES
    tokenizer,fingerprint=bounded_json(folder/'tokenizer.json',MAX_TOKENIZER)
    added=tokenizer.get('added_tokens',[])
    if not isinstance(added,list):raise ValueError('Tokenizer added_tokens is invalid')
    tokens={x.get('content') for x in added if isinstance(x,dict) and isinstance(x.get('content'),str)}
    model=tokenizer.get('model',{})
    if not isinstance(model,dict):raise ValueError('Tokenizer model metadata is invalid')
    vocab=model.get('vocab',{})
    if isinstance(vocab,dict):tokens.update(vocab)
    elif isinstance(vocab,list):tokens.update(x[0] for x in vocab if isinstance(x,list) and x and isinstance(x[0],str))
    else:raise ValueError('Tokenizer vocabulary is invalid')
    codes=sorted(code for code in _LANGUAGE_CODES if '<|'+code+'|>' in tokens)
    return {'codes':codes,'engine_declared_codes':sorted(_LANGUAGE_CODES),
            'tokenizer_sha256':fingerprint,'faster_whisper_version':importlib.metadata.version('faster-whisper'),
            'model_language_count':len(codes),'engine_language_count':len(_LANGUAGE_CODES)}


def resolve_language(tag,model_dir=None):
    normalized=normalize_language(tag)
    if normalized=='auto':return None
    folder=model_dir or os.environ.get('RIWAQ_WHISPER_MODEL')
    if not folder:raise ValueError('Local speech model is not configured')
    code=next(r['engine_code'] for r in registry()['languages'] if r['tag']==normalized)
    if code not in model_languages(folder)['codes']:raise ValueError('Language token is not present in the installed speech model')
    return code


def safe_voices(voices):
    """Copy only bounded public fields from a trusted successful discovery result."""
    clean=[];seen=set()
    if not isinstance(voices,list):return clean
    for item in voices[:64]:
        if not isinstance(item,dict) or item.get('enabled') is not True or item.get('local') is not True:continue
        identifier=item.get('id');name=item.get('name');language=item.get('language')
        if not isinstance(identifier,str) or not re.fullmatch(r'sapi-[a-f0-9]{32}',identifier):continue
        if not isinstance(name,str) or not 1<=len(name)<=200 or any(ord(c)<32 for c in name):continue
        if not isinstance(language,str) or not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,3}',language):continue
        if identifier in seen:continue
        seen.add(identifier)
        clean.append({'id':identifier,'name':name,'language':language,'enabled':True,'local':True,
                      'synthesis_tested':False,'quality_measured':False})
    return clean


def status(voices=None,tts_error=None):
    data=registry();cfg=media.whisper_config();details={'codes':[],'engine_declared_codes':[],
        'tokenizer_sha256':None,'model_language_count':0,'engine_language_count':0}
    reason=cfg.get('reason')
    if cfg.get('configured'):
        try:details=model_languages(cfg['model_path'])
        except (ImportError,OSError,ValueError,TypeError,KeyError):reason='Installed speech-model language metadata is unavailable or invalid'
    configured=bool(cfg.get('configured') and details['codes'])
    actual_voices=safe_voices(voices)
    rows=[]
    for original in data['languages']:
        row=deepcopy(original);present=row['engine_code'] in details['codes']
        matching=[v['id'] for v in actual_voices if v['language'].lower()==row['tag'].lower()
            or ('-' not in row['tag'] and v['language'].split('-')[0].lower()==row['tag'].lower())]
        row.update(engine_declared=row['engine_code'] in details['engine_declared_codes'],model_token_present=present,
                   stt_available=configured and present,measured=False,evaluation=None,dialect_verified=False,
                   tts_voice_ids=matching,tts_available=bool(matching))
        rows.append(row)
    arabic=[v for v in actual_voices if v['language'].split('-')[0].lower()=='ar']
    # A supplied empty list means the discovery ran. None means it was not performed.
    discovered=voices is not None and not tts_error
    return {'version':'voice-capabilities-v1','languages':rows,'language_notice':data['notice'],
      'stt':{'configured':configured,'available':configured,'tested':False,'quality_measured':False,
             'processing':'local','engine':'faster-whisper / CTranslate2','experimental':True,
             'model':cfg.get('model'),'fingerprint':cfg.get('fingerprint'),**details,
             'reason':reason if not configured else 'Installed tokenizer support only; no broad language or dialect accuracy validation',
             'auto_detection':{'available':configured,'dialect_detection':False,'quality_measured':False}},
      'tts':{'available':bool(actual_voices),'configured':bool(actual_voices),'voices':actual_voices,
             'discovery_performed':discovered,'engine':'Windows System.Speech','processing':'local',
             'synthesis_tested':False,'quality_measured':False,'arabic_available':bool(arabic),
             'reason':'Installed enabled voices discovered; synthesis quality is not certified' if actual_voices else
                      'Local voice discovery failed' if tts_error else 'No enabled local voices discovered' if discovered else 'Local voice discovery has not run',
             'error_code':tts_error if isinstance(tts_error,str) and re.fullmatch(r'[a-z0-9_]{1,64}',tts_error) else None},
      'modes':{'transcription':{'implemented_by':'voice pipeline','requires':['local_stt']},
               'conversation':{'implemented_by':'voice pipeline','requires':['local_stt','local_text_model'],'speech_output':'optional installed matching voice'},
               'translation':{'implemented_by':'voice pipeline','requires':['local_stt','local_text_model'],'whisper_translate_target':'en','arbitrary_target_is_text_model_translation':True}},
      'direct_audio':{'available':False,'reason':'Audio is transcribed before the text model; no audio-native model adapter'},
      'segmented_tts':{'implemented':True,'available':bool(actual_voices),'opt_in':True,
          'input':'completed_reply_text_only','max_parts':6,'max_text_characters':1800,
          'max_total_audio_seconds':180,'max_worker_seconds_per_part':60,
          'partial_audio_files_exposed':False,'automatic_playback':False,
          'quality_measured':False,'streaming_stt':False},
      'diarization':{'available':False,'reason':'Speaker separation and identity are not implemented'},
      'unsupported':data['unsupported'],'privacy':{'provider_requests_made':False,'automatic_downloads':False,
          'training_performed':False,'credential_values_read':False,'scope':'Single OS-user local service, not cloud rooms or multi-user voice authentication'}}
