"""Fixed supervised video probe/render worker. No model calls or downloads."""
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

def denied(*args,**kwargs):raise OSError('Network disabled in the fixed video worker')
# libavformat additionally uses custom file IO, a demuxer allowlist and io_open denial.
for name in ('socket','create_connection','create_server','getaddrinfo','gethostbyname','gethostbyname_ex','gethostbyaddr'):
    if hasattr(socket,name):setattr(socket,name,denied)
from backend.video_render import VideoError,local_asset,probe_local,render_local,packed


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['probe','render'],required=True)
    parser.add_argument('--input',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();success=False
    try:
        raw=Path(args.input).read_bytes()
        if len(raw)>512*1024:raise VideoError('Job manifest exceeds512KiB')
        payload=json.loads(raw)
        def progress(value):print(packed(value),flush=True)
        if args.mode=='probe':
            if not isinstance(payload,dict) or set(payload)!={'path'}:raise VideoError('Invalid probe request')
            result={'success':True,'metadata':probe_local(local_asset(payload['path'],output))}
        else:result=render_local(payload,output,progress)
        success=result['success']
    except Exception as exc:
        result={'success':False,'error':type(exc).__name__+': '+str(exc)[:2000]}
    result['seconds']=time.monotonic()-started
    path=output/('probe.json' if args.mode=='probe' else 'report.json')
    data=packed(result)
    if len(data.encode())>2*1024**2:result={'success':False,'error':'Report exceeds2MiB'};data=packed(result);success=False
    temporary=path.with_suffix('.json.tmp');temporary.write_text(data,encoding='utf-8');temporary.replace(path)
    print(packed({'stage':'complete','success':success}),flush=True)
    return 0 if success else 1


if __name__=='__main__':raise SystemExit(main())
