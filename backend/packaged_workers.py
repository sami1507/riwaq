"""Capabilities shipped by our build, never enabled by a request or environment flag.

The manifest is a build declaration, not evidence that an external Docker image
is installed, nor a security certificate. Each real call still checks its worker.
"""
import json
import os
from pathlib import Path
import sys

VERSION='riwaq-packaged-workers-v1'
KINDS={'checker','ast','document_page','document_extract','skill_transform','media_decode','voice_tts'}

def bundled(kind,runtime=None):
    runtime=runtime or sys
    if kind not in KINDS or not getattr(runtime,'frozen',False) or not getattr(runtime,'_MEIPASS',None):return False
    root=Path(runtime._MEIPASS)
    try:
        raw=(root/'packaged-workers.json').read_bytes()
        if len(raw)>16384:return False
        value=json.loads(raw)
        return isinstance(value,dict) and value.get('version')==VERSION and value.get('dispatch')=='fixed-v1' and value.get('workers')==sorted(KINDS)
    except (OSError,ValueError,TypeError):return False


def fixed_command(kind,runtime=None):
    runtime=runtime or sys
    if kind not in {'checker','ast','voice_tts'} or not bundled(kind,runtime):raise ValueError('packaged_worker_unavailable')
    return [runtime.executable,'--fixed-worker',kind]


def worker_environment(environment):
    # Preserve the bootloader's own inherited state only for our same-executable
    # worker. This lets it reuse unpacked resources while its parent stays alive.
    # Never accept these values from a request or invent private bootloader state.
    return environment|{k:v for k,v in os.environ.items() if k.startswith('_PYI_')}


def source_fingerprint(paths,runtime=None):
    import hashlib,re
    runtime=runtime or sys
    if not bundled('voice_tts',runtime):raise ValueError('packaged_worker_unavailable')
    value=json.loads((Path(runtime._MEIPASS)/'packaged-workers.json').read_bytes())
    hashes=value.get('source_sha256',{})
    if any(not isinstance(hashes.get(p),str) or not re.fullmatch('[0-9a-f]{64}',hashes[p]) for p in paths):
        raise ValueError('packaged_source_identity_missing')
    return hashlib.sha256(b'\n'.join(p.encode()+b':'+hashes[p].encode() for p in paths)).hexdigest()


def dispatch(kind,arguments=None):
    if not bundled(kind) or kind not in {'checker','ast','voice_tts'}:raise SystemExit('Unsupported fixed packaged worker')
    if arguments and kind!='voice_tts':raise SystemExit('Unexpected fixed worker arguments')
    try:
        if kind=='checker':
            from backend.check_worker import main
            job_handle=main()
        elif kind=='ast':
            from backend.coding_context import _worker_main
            _worker_main()
        else:
            from backend.voice_worker import main
            return main(arguments or [],tts_only=True)
    except (Exception,MemoryError) as exc:
        print(json.dumps({'status':'error','error':str(exc)[:400]},ensure_ascii=False));raise SystemExit(1)
