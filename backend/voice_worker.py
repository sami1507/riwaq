"""Fixed voice IPC worker. No arbitrary code, cloud or automatic model download."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_DATASETS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',OMP_NUM_THREADS='4',TOKENIZERS_PARALLELISM='false')
def denied(*args,**kwargs):raise OSError('Network disabled in the fixed voice worker')
# Retain the class for imports that subclass socket.socket; deny outgoing calls.
socket.socket.connect=denied
socket.socket.connect_ex=denied
for name in ('create_connection','create_server','getaddrinfo','gethostbyname','gethostbyname_ex','gethostbyaddr'):
    if hasattr(socket,name):setattr(socket,name,denied)
from backend.voice_audio import VoiceAudioError,packed,sapi_local,transcribe_local


def main(arguments=None, *, tts_only=False):
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['voices','tts'] if tts_only else ['voices','stt','tts'],required=True)
    parser.add_argument('--input',required=True);parser.add_argument('--output',required=True);args=parser.parse_args(arguments)
    folder=Path(args.output).resolve();started=time.monotonic()
    try:
        source=Path(args.input).resolve()
        if not source.is_relative_to(folder) or not source.is_file() or source.stat().st_size>64*1024:
            raise VoiceAudioError('Invalid bounded voice manifest','invalid_input')
        payload=json.loads(source.read_text(encoding='utf-8'))
        if not isinstance(payload,dict):raise VoiceAudioError('Voice manifest must be an object','invalid_input')
        def progress(value):print(packed(value),flush=True)
        result=transcribe_local(payload,folder,progress) if args.mode=='stt' else sapi_local(args.mode,payload,folder,progress)
    except VoiceAudioError as exc:
        result={'success':False,'code':exc.code,'error':str(exc)}
    except Exception:
        result={'success':False,'code':'engine_failed','error':'Local voice engine failed; no network fallback was attempted.'}
    result['seconds']=time.monotonic()-started
    data=packed(result)
    if len(data.encode())>512*1024:
        result={'success':False,'code':'output_limit','error':'Voice result exceeds512KiB'};data=packed(result)
    destination=folder/'voice-result.json';temporary=folder/'voice-result.json.tmp'
    temporary.write_text(data,encoding='utf-8');temporary.replace(destination)
    print(packed({'stage':'complete','success':result['success']}),flush=True)
    # A bounded, explicit rejection is a successfully delivered IPC result.
    return 0

if __name__=='__main__':raise SystemExit(main())
