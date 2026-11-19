"""Initial real-model baseline against untouched identity-v1 implementation."""
import asyncio,json,os,sys,sqlite3,tempfile,time,hashlib
from pathlib import Path
from contextlib import closing
import httpx
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from evaluate_knowledge import deny_external_connections
DEST=ROOT/'reports/reasoning-initial-baseline.json'
if DEST.exists():raise SystemExit('Baseline already exists; refusing overwrite')
CASES=[{'id':'dev-decimal-total','category':'math','question':'احسب القيمة الدقيقة للتعبير (19.95 * 3) * 1.17 - 10، ثم قرّب إلى منزلتين عشريتين.','reference':'60.0245; rounded 60.02'}, {'id':'dev-python-return-sort','category':'code','question':'لماذا تعيد الدالة None؟ اقترح تصحيحًا يحافظ على القائمة الأصلية، ولا تدّع تشغيل الشيفرة.\n```python\ndef ordered(values: list[int]):\n    return values.sort()\n```','reference':'list.sort mutates in-place and returns None; use return sorted(values).'}, {'id':'dev-fastapi-model','category':'retrieval','question':'في FastAPI ما نوع النموذج الذي يعرّف حقول جسم الطلب؟ أجب بجملة عربية واذكر Pydantic مع مرجع.','reference':'Pydantic BaseModel; cite supplied FastAPI source.'}]
async def main():
 with tempfile.TemporaryDirectory(prefix='riwaq-reasoning-baseline-',dir='D:/tmp') as directory:
  with closing(sqlite3.connect((ROOT/'data/riwaq.sqlite3').as_uri()+'?mode=ro',uri=True)) as src:
   with closing(sqlite3.connect(Path(directory)/'riwaq.sqlite3')) as dst:src.backup(dst)
  os.environ['RIWAQ_DATA_DIR']=directory;os.environ['RIWAQ_EMBED_MODEL']='embeddinggemma-2:270m-q8_0-text'
  from backend import app as service,providers
  report={'date':'2026-11-13','baseline':'identity-v1','cases':[],'production_data_modified':False,'transport':'Real ASGI application and local Ollama; buffered NDJSON, not first-token timing','source_hashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['backend/app.py','backend/providers.py','backend/grounding.py']}}
  with deny_external_connections() as proof:
   async with service.lifespan(service.app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),base_url='http://127.0.0.1:8765',headers={'X-Riwaq-Client':'local'},timeout=240) as client:
     config=(await client.get('/api/settings')).json();assert config['mode']=='local' and config['provider']=='ollama';report['settings']=config;report['models']=await providers.ollama_status()
     project=(await client.post('/api/projects',json={'name':'Reasoning baseline temporary'})).json()
     for case in CASES:
      chat=(await client.post('/api/conversations',json={'project_id':project['id'],'title':case['id']})).json();start=time.perf_counter();response=await client.post('/api/conversations/'+chat['id']+'/generate',json={'content':case['question'],'use_knowledge':case['category']=='retrieval'});response.raise_for_status();events=[json.loads(line) for line in response.text.splitlines()];assert events[-1]['type']=='done';answer=events[-1]['message'];restored=(await client.get('/api/conversations/'+chat['id'])).json()['messages'][-1];assert answer==restored
      output={**case,'answer':answer,'metrics':events[-1].get('metrics',{}),'seconds':time.perf_counter()-start,'restored_equal':True};report['cases'].append(output);DEST.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'id':case['id'],'answer':answer['content'],'status':answer['status'],'seconds':output['seconds']},ensure_ascii=True),flush=True)
   report['network_guard']=proof
  DEST.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
asyncio.run(main())
