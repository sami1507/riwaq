"""Small real-provider audit, separate from historical benchmarks and user projects."""
import json,time,hashlib,platform,sys
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];dest=ROOT/'reports/audit-live.json'
c=httpx.Client(base_url='http://127.0.0.1:8765/api',headers={'X-Riwaq-Client':'local'},timeout=200,trust_env=False)
def call(method,path,**kwargs):
 r=c.request(method,path,**kwargs);r.raise_for_status();return r.json()
settings=call('GET','/settings')
assert settings['provider']=='ollama' and settings['mode']=='local'
project=call('POST','/projects',json={'name':'تدقيق رواق — 14 نوفمبر','instructions':'بيانات اختبار اصطناعية. أجب بالعربية بإيجاز. ميّز نقص البيانات عن النتيجة.'})
pid=project['id'];report={'kind':'actual Ollama/API; self-authored fixtures; no cloud','model':settings['model'],'settings':{k:settings[k] for k in ['provider','mode','context_size','max_tokens']},'project_id':pid,'cases':[],'started_at':time.time()}
def save():dest.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
def chat(label,prompt,cid=None,cancel=False,**extra):
 if not cid:cid=call('POST','/conversations',json={'project_id':pid,'title':label})['id']
 events=[];start=time.monotonic();first=None;cancel_sent=False
 with c.stream('POST',f'/conversations/{cid}/generate',json={'content':prompt,**extra}) as response:
  response.raise_for_status()
  for line in response.iter_lines():
   if not line:continue
   item=json.loads(line);events.append(item)
   if item['type']=='delta':
    first=first or time.monotonic()
    if cancel and not cancel_sent:call('POST',f'/conversations/{cid}/stop',json={});cancel_sent=True
 done=next((x for x in reversed(events) if x['type']=='done'),None)
 assert done,events
 actual_cid=next((x['conversation_id'] for x in events if x['type']=='start'),cid)
 stored=call('GET','/conversations/'+actual_cid)['messages'][-1]
 assert stored['content']==done['message']['content'] and stored['status']==done['message']['status']
 row={'label':label,'conversation_id':actual_cid,'message_id':stored['id'],'status':stored['status'],'response':stored['content'],'citations':stored.get('citations',[]),'run':stored.get('run'),'restored_exact':True,'delta_count':sum(x['type']=='delta' for x in events),'seconds':time.monotonic()-start,'first_delta_seconds':first-start if first else None,'errors':[x for x in events if x['type']=='error']}
 report['cases'].append(row);save();print(json.dumps({'label':label,'status':row['status'],'seconds':round(row['seconds'],2)},ensure_ascii=False),flush=True);return row
chat('missing_information','ما سرعة المركبة؟ لم أحدد المسافة أو الزمن. لا تفترض أرقامًا.')
chat('false_premise','هل كل عدد أولي فردي؟ صحّح العبارة بدليل قصير.')
chat('exact_calculation','احسب التعبير المرفق وأعطني القيمة الدقيقة دون تقريب.',depth='analytical',calculation={'expression':'(19.95 * 3) * 1.17 - 10'})
files=[]
for year,value in [(2024,17),(2026,29)]:
 text=f'Audit chamber fictional reference.\nValid year: {year}.\nMeasured pressure for {year}: {value} kPa.\nهذه بيانات اصطناعية للاختبار فقط وليست قياسًا علميًا.\n'
 r=c.post(f'/projects/{pid}/files',files={'file':(f'chamber-{year}.txt',text.encode(),'text/plain')});r.raise_for_status();files.append(r.json())
a=chat('dated_sources','وفق الملفات فقط: ما ضغط الحجرة الخيالية في2026؟ اذكر السنة والوحدة ومرجعًا.',use_files=True)
chat('user_correction','أصحّح السنة المطلوبة: أقصد2024، وليس2026. أجب من المرجع المناسب.',cid=a['conversation_id'],use_files=True)
chat('retry_preserves_sources','',cid=a['conversation_id'],retry_message_id=a['message_id'],use_files=True)
b=chat('actual_cancel','اكتب مئة فقرة مطولة عن توثيق البرمجيات، ولا تختصر.',cancel=True);assert b['status']=='cancelled'
for f in files:call('DELETE',f"/projects/{pid}/files/{f['id']}")
after=chat('deleted_evidence','من الملفات فقط: ما ضغط الحجرة الخيالية في2026؟ اذكر المرجع.',use_files=True);assert not after['citations']
report['fixture_files_deleted']=True;report['finished_at']=time.time();save()
