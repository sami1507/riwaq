"""Fixed Docker-only document page worker: bounded stdin, no host paths."""
from __future__ import annotations
import base64
import hashlib
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,'/opt/document-runtime/python-packages')


def main():
    from backend.isolated_document_page import VERSION,REGION_VERSION,MAX_INPUT,MAX_PAYLOAD,MAX_ENVELOPE,MAX_PREVIEW,loads,dump,validate_metadata,validate_result
    raw=sys.stdin.buffer.read(MAX_PAYLOAD+1)
    source_hash=None
    try:
        if len(raw)>MAX_PAYLOAD:raise ValueError('payload_limit')
        value=loads(raw)
        if not isinstance(value,dict) or set(value)!={'body','metadata'}:raise ValueError('payload_fields')
        metadata=validate_metadata(value['metadata']);body=base64.b64decode(value['body'],validate=True)
        if not 1<=len(body)<=min(MAX_INPUT,metadata.get('limits',{}).get('max_input_bytes',MAX_INPUT)):raise ValueError('input_limit')
        source_hash=hashlib.sha256(body).hexdigest()
        if metadata.get('operation')=='code':
            from backend.visual_codes_worker import generate
            from backend import isolated_visual_codes as code_protocol
            picture,proof=generate(body,metadata)
            output=dump({'version':code_protocol.VERSION,'input_sha256':source_hash,'success':True,'proof':proof,
                'preview':{'bytes':len(picture),'sha256':hashlib.sha256(picture).hexdigest(),'base64':base64.b64encode(picture).decode('ascii')}})
            if len(output)>code_protocol.MAX_ENVELOPE:raise ValueError('output_limit')
            sys.stdout.buffer.write(output);sys.stdout.buffer.flush();return 0
        if metadata.get('operation')=='normalize':
            from backend.image_normalize import normalize,ImageRejected
            from backend.isolated_document_page import NORMALIZE_VERSION,MAX_NORMALIZE_ENVELOPE,VISUAL_NORMALIZE_VERSION,MAX_VISUAL_NORMALIZE_ENVELOPE
            variant=metadata.get('variant')
            version=VISUAL_NORMALIZE_VERSION if variant=='visual' else NORMALIZE_VERSION
            maximum=MAX_VISUAL_NORMALIZE_ENVELOPE if variant=='visual' else MAX_NORMALIZE_ENVELOPE
            try:
                preview,dimensions=normalize(body,variant=variant)
                envelope={'version':version,'input_sha256':source_hash,'success':True,'dimensions':dimensions,
                    'preview':{'bytes':len(preview),'sha256':hashlib.sha256(preview).hexdigest(),'base64':base64.b64encode(preview).decode('ascii')}}
            except ImageRejected as exc:
                envelope={'version':version,'input_sha256':source_hash,'success':False,'error_code':exc.code}
            output=dump(envelope)
            if len(output)>maximum:raise ValueError('output_limit')
            sys.stdout.buffer.write(output);sys.stdout.buffer.flush();return 0
        if metadata.get('operation')=='region':
            from backend.document_region import prepare
            preview,region=prepare(body,metadata['bbox'])
            output=dump({'version':REGION_VERSION,'input_sha256':source_hash,'success':True,'region':region,
                'preview':{'bytes':len(preview),'sha256':hashlib.sha256(preview).hexdigest(),'base64':base64.b64encode(preview).decode('ascii')}})
            if len(output)>MAX_ENVELOPE:raise ValueError('output_limit')
            sys.stdout.buffer.write(output);sys.stdout.buffer.flush();return 0
        folder=Path('/tmp/riwaq-document');folder.mkdir(mode=0o700)
        source=folder/'input.bin';source.write_bytes(body)
        os.environ.update(RIWAQ_DOCUMENT_RUNTIME='/opt/document-runtime',RIWAQ_DOCUMENT_NODE='/usr/local/bin/node',HOME='/tmp',OMP_NUM_THREADS='1',OMP_THREAD_LIMIT='1')
        from backend.document_extract import extract_document,Limits
        result=extract_document(source,metadata['name'],folder,metadata['page'],metadata['languages'],Limits(**metadata['limits']))
        path=folder/'page.png';preview=None
        if path.exists():
            if path.is_symlink() or path.stat().st_size>MAX_PREVIEW:raise ValueError('preview_limit')
            preview=path.read_bytes()
        validate_result(result,metadata,source_hash,len(body),preview)
        envelope={'version':VERSION,'input_sha256':source_hash,'success':True,'result':result,'preview':None if preview is None else {'bytes':len(preview),'sha256':hashlib.sha256(preview).hexdigest(),'base64':base64.b64encode(preview).decode('ascii')}}
        output=dump(envelope)
        if len(output)>MAX_ENVELOPE:raise ValueError('output_limit')
    except Exception as exc:
        # No filesystem paths, document text or process exception are returned.
        output=dump({'version':VERSION,'input_sha256':source_hash,'success':False,'error_code':'document_rejected','error_type':type(exc).__name__})
    sys.stdout.buffer.write(output);sys.stdout.buffer.flush()
    return 0


if __name__=='__main__':raise SystemExit(main())
