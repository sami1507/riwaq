"""Explicit operator-authorized first/batch import using the running local API.

Switches only the network mode for a bounded job, then restores the prior mode.
No cloud model is selected and no private project document is sent anywhere.
"""
import argparse
import json
from pathlib import Path
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('phase',choices=['first','batch','general','repeat'])
args=parser.parse_args()
client=httpx.Client(base_url='http://127.0.0.1:8765/api',headers={'X-Riwaq-Client':'local'},trust_env=False,timeout=120)
def request(method,path,body=None):
    r=client.request(method,path,json=body);r.raise_for_status();return r.json()
before=request('GET','/settings')
sources=(['fastapi0162'] if args.phase=='first' else ['usgs-water'] if args.phase=='general' else
         ['python312','react19','typescript59','fastapi0162'] + (['usgs-water'] if any(s['id']=='usgs-water' for s in request('GET','/knowledge/sources')) else ['wikidata-general']))
plan={'source_ids':sources,'max_documents':1 if args.phase=='first' else 100}
preview=request('POST','/knowledge/preview',plan)
record={'phase':args.phase,'preview':preview,'mode_before':before['mode'],'started_at':time.strftime('%Y-%m-%dT%H:%M:%S'),'job':None}
job=None
try:
    request('PATCH','/settings',{'mode':'hybrid'})
    job=request('POST','/knowledge/jobs',plan|{'allow_network':True})
    started=time.monotonic();last=None
    while True:
        detail=request('GET','/knowledge/jobs/'+job['id'])
        record['job']=detail
        status=(detail['status'],detail.get('completed_documents'),detail.get('downloaded_bytes'))
        if status!=last:
            print(json.dumps({'job':job['id'],'status':status},ensure_ascii=True),flush=True);last=status
        if detail['status'] not in ('queued','running'):break
        if time.monotonic()-started>1850:
            request('POST','/knowledge/jobs/'+job['id']+'/cancel',{})
            raise TimeoutError('Operator test deadline; job cancellation requested')
        time.sleep(1)
    record['status']=request('GET','/knowledge/status')
    record['documents']=request('GET','/knowledge/documents')
finally:
    # Settings reject a switch while jobs still run; preserve that protection.
    try:record['settings_restored']=request('PATCH','/settings',{'mode':before['mode']})
    except Exception as exc:record['settings_restore_error']=str(exc)
    (ROOT/'reports'/f'knowledge-import-{args.phase}.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'phase':args.phase,'status':record.get('status'),'job_status':record['job']['status'] if record['job'] else None},ensure_ascii=True),flush=True)
if record['job'] is None or record['job']['status']!='completed':raise SystemExit(1)
