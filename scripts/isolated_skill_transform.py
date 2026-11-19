"""Fixed container entry point; no filenames, paths or commands from the caller."""
from __future__ import annotations
import base64
from dataclasses import asdict
import hashlib
import json
import os
import sys
sys.path[:0]=['/opt/riwaq','/opt/document-runtime/python-packages','/opt/transform-packages']

def main():
    from backend import skill_transform_protocol as protocol
    raw=sys.stdin.buffer.read(protocol.MAX_PAYLOAD+1)
    request_hash=hashlib.sha256(raw).hexdigest()
    try:
        if len(raw)>protocol.MAX_PAYLOAD:raise protocol.TransformError('payload_limit')
        payload=protocol.validate_payload(protocol.loads(raw));sources=[]
        for item in payload['sources']:
            source={k:v for k,v in item.items() if k!='base64'};source['content']=base64.b64decode(item['base64'],validate=True);sources.append(source)
        os.environ.update(HOME='/tmp',OMP_NUM_THREADS='1',OMP_THREAD_LIMIT='1')
        if payload['domain']=='documents':
            from backend.isolated_skill_documents import execute_documents
            result,artifacts,refs=execute_documents(payload['request'],sources)
        else:
            from backend.skill_data import execute_data
            source=[s for s in sources if s['role']=='source'];template=[s for s in sources if s['role']=='template']
            if len(source)>1 or len(template)>1:raise protocol.TransformError('data_source_roles')
            value=execute_data(payload['request'],source_bytes=source[0]['content'] if source else None,template_bytes=template[0]['content'] if template else None)
            result=asdict(value);artifact=result.pop('artifact',None);artifacts=[]
            if artifact:artifacts=[protocol.output_artifact(artifact['content'],artifact['filename'],artifact['verification'],artifact['limitations'])]
            refs=[{'source_id':s['id'],'sha256':s['sha256'],'version':s['version'],'role':s['role']} for s in sources]
        output=protocol.encode_result(raw,result,artifacts,refs)
    except Exception as exc:
        code=getattr(exc,'code','transform_rejected')
        if not isinstance(code,str) or not __import__('re').fullmatch('[a-z0-9_]{1,100}',code):code='transform_rejected'
        output=protocol.dump({'version':protocol.VERSION,'request_sha256':request_hash,'success':False,'error_code':code})
    sys.stdout.buffer.write(output);sys.stdout.buffer.flush();return 0

if __name__=='__main__':raise SystemExit(main())
