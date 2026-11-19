"""After-fix retrieval and deletion proof using only the audit's own fixtures."""
import json,time
from pathlib import Path
import httpx
root=Path(__file__).resolve().parents[1];before=json.loads((root/'reports/audit-live.json').read_text(encoding='utf-8'));pid=before['project_id']
c=httpx.Client(base_url='http://127.0.0.1:8765/api',headers={'X-Riwaq-Client':'local'},timeout=180,trust_env=False)
def call(method,path,**kw):
 r=c.request(method,path,**kw);r.raise_for_status();return r.json()
lookup=call('GET',f'/projects/{pid}/retrieve',params={'q':'أقصد2024، وليس2026.'});assert lookup['chunks']
report={'project_id':pid,'retrieval_after_fix':lookup,'cases':[]}
def chat(text,cid=None):
 if cid is None:cid=call('POST','/conversations',json={'project_id':pid,'title':'After audit fix'})['id']
 start=time.monotonic();r=c.post(f'/conversations/{cid}/generate',json={'content':text,'use_files':True});r.raise_for_status()
 events=[json.loads(x) for x in r.text.splitlines() if x];done=next(x for x in reversed(events) if x['type']=='done')
 actual=next(x['conversation_id'] for x in events if x['type']=='start');saved=call('GET','/conversations/'+actual)['messages'][-1];assert saved['content']==done['message']['content']
 report['cases'].append({'prompt':text,'message':saved,'seconds':time.monotonic()-start,'saved_equal':True});return saved
source_case=next(x for x in before['cases'] if x['label']=='dated_sources')
after=chat('أصحّح السنة المطلوبة: أقصد2024، وليس2026. أجب من المرجع المناسب.',source_case['conversation_id'])
assert after['citations'],'Numeric correction should now retrieve evidence; model correctness reviewed separately'
files=call('GET',f'/projects/{pid}/files')
for item in files:
 assert item['filename'] in ('chamber-2024.txt','chamber-2026.txt')
 call('DELETE',f"/projects/{pid}/files/{item['id']}")
empty=call('GET',f'/projects/{pid}/retrieve',params={'q':'pressure2026 2026 chamber'});assert not empty['chunks']
deleted=chat('من ملفات المشروع فقط: ما ضغط الحجرة في سنة2026؟');assert not deleted['citations']
report.update(fixture_files_deleted=True,deleted_not_retrieved=True,script_error_before='Wrong unscoped delete URL in baseline harness, corrected; not an application failure')
(root/'reports/audit-followup.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'retrieved_after_fix':len(after['citations']),'deleted_not_retrieved':True,'answer':after['content']},ensure_ascii=False))
