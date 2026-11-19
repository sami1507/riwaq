"""Actual API render/generation checks; no UI claim and no training."""
import hashlib,json,time
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/audit-visual';OUT.mkdir(parents=True,exist_ok=True)
c=httpx.Client(base_url='http://127.0.0.1:8765/api',headers={'X-Riwaq-Client':'local'},timeout=15,trust_env=False)
def call(method,path,body=None):
 r=c.request(method,path,json=body);r.raise_for_status();return r.json()
p=call('POST','/projects',{'name':'تدقيق الصور المحلي — API فقط','instructions':''});path='/projects/'+p['id']+'/visual'
spec=json.loads((ROOT/'reports/visual-first-arabic-input.json').read_text(encoding='utf-8'))['spec']
d=call('POST',path+'/designs',{'title':'نص عربي حرفي — تدقيق','spec':spec})
report={'project_id':p['id'],'design_id':d['id'],'scope':'Actual API and local workers; no design UI or image training','jobs':[]}
def save():(OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
def wait(j):
 started=time.monotonic()
 while time.monotonic()-started<330:
  r=call('GET',path+'/jobs/'+j['id'])
  if r['status'] not in ('queued','running','cancelling'):return r
  time.sleep(.25)
 raise TimeoutError('Visual job exceeded supervisor window')
for kind,extra in [('render',{'format':'png'}),('generate',{'prompt':'A complete red ceramic mug centered on a wooden table, handle visible on the right, entire object and base visible, soft daylight, cream background, no writing','seed':53,'steps':24,'width':512,'height':512})]:
 j=wait(call('POST',path+'/jobs',{'kind':kind,'design_id':d['id'],'version':1,**extra}));report['jobs'].append(j);save()
 print(json.dumps({'kind':kind,'status':j['status'],'error':j['error']},ensure_ascii=False),flush=True)
 assert j['status']=='completed',j
 result=c.get(path+'/assets/'+j['asset_id']+'/content');result.raise_for_status();meta=call('GET',path+'/assets/'+j['asset_id'])
 assert hashlib.sha256(result.content).hexdigest()==meta['sha256']
 (OUT/(kind+'.png')).write_bytes(result.content)
reload=call('GET',path+'/designs/'+d['id']);assert reload['spec']==d['spec'];report['restored_exact']=True
j=call('POST',path+'/jobs',{'kind':'generate','design_id':d['id'],'version':1,'prompt':'A woodland landscape','steps':40})
for i in range(80):
 current=call('GET',path+'/jobs/'+j['id'])
 if current['progress'].get('stage'):break
 time.sleep(.1)
call('POST',path+'/jobs/'+j['id']+'/cancel');stopped=wait(j);report['cancellation']=stopped;save()
assert stopped['status']=='cancelled' and stopped['asset_id'] is None
report['passed']=True;save();print('ACTUAL VISUAL API CHECKS PASSED',flush=True)
