"""Offline capability discovery. Presence is not generation or quality validation.

This module does not read credential values, query keyring, contact providers,
install dependencies, download models, or execute FFmpeg/voice binaries.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil

from backend import video_schema

ROOT=Path(__file__).resolve().parent.parent
REGISTRY=ROOT/'config'/'video_capabilities.json'
REPORT=ROOT/'reports'/'video-render-smoke.json'
IMPLEMENTATION_FILES=('backend/video_render.py','backend/video_schema.py','scripts/video_render.py','scripts/video_render_text.mjs',
                      'frontend/src/designEngine.mjs','frontend/public/fonts/visual-coverage.json')


def implementation_fingerprint():
    digest=hashlib.sha256()
    for name in IMPLEMENTATION_FILES:
        path=ROOT/name
        if not path.is_file():return None
        digest.update(name.encode('utf-8')+b'\0'+hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def registry():
    raw=REGISTRY.read_bytes()
    if len(raw)>64*1024:raise ValueError('Video capability registry is too large')
    value=json.loads(raw)
    if value.get('version')!='video-capabilities-v1':raise ValueError('Unsupported video capability registry')
    return value


def _pyav():
    try:
        av=importlib.import_module('av')
        encoders={}
        for name in ('libx264','aac'):
            try:
                codec=av.codec.Codec(name,'w');encoders[name]=bool(codec.is_encoder)
            except (ValueError,RuntimeError):encoders[name]=False
        return {'installed':True,'version':str(av.__version__),
                'library_versions':{key:list(value) for key,value in av.library_versions.items()},'encoders':encoders}
    except (ImportError,OSError):
        return {'installed':False,'version':None,'library_versions':{},'encoders':{'libx264':False,'aac':False}}


def _text_runtime():
    chrome=any((Path(os.environ.get(name,''))/'Google/Chrome/Application/chrome.exe').is_file()
               for name in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA')) if os.name=='nt' else bool(shutil.which('google-chrome'))
    return {'node':bool(shutil.which('node')),'chrome':chrome,
            'playwright':(ROOT/'tests/browser/node_modules/@playwright/test/index.mjs').is_file(),
            'font_manifest':(ROOT/'frontend/public/fonts/visual-coverage.json').is_file()}


def _validation(runtime,fingerprint, *, audio=False):
    if not REPORT.is_file() or REPORT.stat().st_size>1024*1024:return False
    try:
        value=json.loads(REPORT.read_text(encoding='utf-8'))
        return (value.get('success') is True and value.get('pyav_version')==runtime['version']
                and fingerprint is not None and value.get('implementation_sha256')==fingerprint
                and value.get('qc',{}).get('full_decode') is True
                and (not audio or value.get('qc',{}).get('audio_mix') is True))
    except (OSError,ValueError,AttributeError,TypeError):return False


def status():
    entries=registry();runtime=_pyav();fingerprint=implementation_fingerprint();text_runtime=_text_runtime()
    available=runtime['installed'] and all(runtime['encoders'].values()) and fingerprint is not None and all(text_runtime.values())
    tested=available and _validation(runtime,fingerprint)
    limits={'upload_bytes':video_schema.MAX_ASSET,'seconds':video_schema.MAX_SECONDS,
            'clips':12,'texts':24,'audio_tracks':4,'output_bytes':video_schema.MAX_OUTPUT,
            'project_bytes':video_schema.PROJECT_BYTES,'global_bytes':video_schema.GLOBAL_BYTES,
            'fps':[24,30],'sizes':[list(size) for size in sorted(video_schema.SIZES)]}
    generation=deepcopy(entries['generation']);generation.update(available=False,tested=False,status='needs_configuration')
    generation['credential_name_present']='RUNWAYML_API_SECRET' in os.environ
    generation['credential_value_read']=False;generation['keyring_inspected']=False
    tts=deepcopy(entries['arabic_tts']);tts.update(available=False,tested=False,status='needs_configuration')
    audio_tested=bool(tested and _validation(runtime,fingerprint,audio=True))
    capabilities=deepcopy(entries['capabilities'])
    for capability in capabilities:
        if capability['id'] in ('local_timeline','local_pan_zoom','exact_text','mp4_export','subtitle_sidecars'):
            capability.update(available=available,tested=bool(tested),status='validated_experimental' if tested else 'available_unvalidated' if available else 'needs_configuration')
        elif capability['id']=='provided_audio':
            capability.update(available=available,tested=audio_tested,status='validated_experimental' if audio_tested else 'available_unvalidated' if available else 'needs_configuration')
    return {'version':'video-capabilities-v1',
      'render':{'available':available,'status':'validated_experimental' if tested else 'available_unvalidated' if available else 'needs_configuration',
                'engine':'PyAV / FFmpeg libraries','tested':tested,'experimental':True,
                'reason':'Matching local encode/decode smoke receipt exists; quality limits still apply.' if tested else
                         'Local codecs and worker files are present; no matching successful encode/decode validation receipt.' if available else
                         'PyAV, required encoders, fixed workers, or local text-rendering dependencies are unavailable.',
                'runtime':runtime,'text_runtime':text_runtime,'implementation_sha256':fingerprint},
      'generation':generation,'arabic_tts':tts,'limits':limits,
      'ffmpeg_cli':{'installed':shutil.which('ffmpeg') is not None,'tested':False,'required_for_pyav':False},
      'ffprobe_cli':{'installed':shutil.which('ffprobe') is not None,'tested':False,'required_for_pyav':False},
      'audio_import':{'supported':True,'available':available,'generated_speech':False,
                      'tested':audio_tested,
                      'reason':'User-supplied audio can be assembled by the local renderer; this is not text-to-speech.'},
      'capabilities':capabilities,
      'privacy':{'provider_requests_made':False,'credentials_read':False,'automatic_downloads':False,
                 'cloud_requires':'A future enabled adapter, hybrid mode, explicit per-job upload/cost approval, and configured credentials.'}}
