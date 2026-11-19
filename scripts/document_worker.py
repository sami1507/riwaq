"""Fixed local document worker. Run under the parent's process-tree supervisor."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
RUNTIME=Path(os.environ.get('RIWAQ_DOCUMENT_RUNTIME','D:/RiwaqRuntime/documents-v1')).resolve()
os.environ['RIWAQ_DOCUMENT_RUNTIME']=str(RUNTIME)
PACKAGES=RUNTIME/'python-packages'
if PACKAGES.is_dir():sys.path.insert(0,str(PACKAGES))
# No package resolution or installation is attempted here. The administrator owns
# the explicit runtime directory; document contents cannot select code or engines.
def deny_python_network():
    # Defense in depth for Python libraries; the fixed Node OCR wrapper has its
    # own main/worker-thread guard. This is not an OS network sandbox.
    import socket
    def denied(*args, **kwargs):
        raise OSError('Network access is disabled in the local document worker')
    for name in ('socket','create_connection','create_server','getaddrinfo','gethostbyname','gethostbyname_ex','gethostbyaddr'):
        if hasattr(socket,name):setattr(socket,name,denied)

deny_python_network()
from backend.document_extract import DocumentError, extract_document, load_limits, languages


def main(argv=None):
    parser=argparse.ArgumentParser(description='Local single-page document extraction')
    parser.add_argument('--input',required=True)
    parser.add_argument('--name',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--page',required=True,type=int)
    parser.add_argument('--languages',default='ara+eng+heb')
    args=parser.parse_args(argv)
    output=Path(args.output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    success=False
    try:
        language=languages(args.languages)
        print(json.dumps({'stage':'inspect' if args.page==0 else 'parse','step':args.page}),flush=True)
        result=extract_document(args.input,args.name,output,args.page,language,load_limits())
        preview=output/'page.png'
        if preview.exists() and preview.stat().st_size>8*1024*1024:
            raise DocumentError('Preview exceeds 8 MiB')
        success=result['success']
    except (DocumentError,ValueError,OSError) as exc:
        result={'success':False,'page_count':0,'error':str(exc)[:500],'error_code':'document_rejected'}
    except Exception:
        result={'success':False,'page_count':0,'error':'Local document parser failed; no result was invented.','error_code':'parser_failed'}
    raw=json.dumps(result,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    if len(raw)>32*1024*1024:
        result={'success':False,'page_count':0,'error':'Worker result exceeds 32 MiB','error_code':'result_limit'}
        raw=json.dumps(result).encode('utf-8');success=False
    temporary=output/'result.json.tmp'
    temporary.write_bytes(raw)
    temporary.replace(output/'result.json')
    print(json.dumps({'stage':'complete','success':success,'step':args.page}),flush=True)
    return 0 if success else 1


if __name__=='__main__':
    raise SystemExit(main())
