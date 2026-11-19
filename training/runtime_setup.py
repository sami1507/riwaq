"""Bounded, recorded official wheel downloads; installs only explicitly selected local wheels."""
import argparse,hashlib,json,os,re,subprocess,sys,time,urllib.request,venv
from pathlib import Path
from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename, canonicalize_name
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=Path('D:/RiwaqRuntime/training-v1');WHEELS=RUNTIME/'wheels';LEDGER=ROOT/'reports/training-downloads.json'
RUNTIME_ROOT=Path('D:/RiwaqRuntime')
TORCH_VERSION='2.10.0+cu128'
TORCH_FILENAME='torch-2.10.0+cu128-cp312-cp312-win_amd64.whl'
TORCH_SIZE=2867409626
TORCH_SHA256='fbde8f6a9ec8c76979a0d14df21c10b9e5cab6f0d106a73ca73e2179bc597cae'
LIMIT=4*1024**3;PRIOR=305449457
PINS={'transformers':'4.57.1','peft':'0.17.1','accelerate':'1.11.0','bitsandbytes':'0.48.1','gguf':'0.17.1','safetensors':'0.6.2','tokenizers':'0.22.1','huggingface-hub':'0.35.3','numpy':'2.3.4','packaging':'25.0','pyyaml':'6.0.3','regex':'2025.9.18','requests':'2.32.5','tqdm':'4.67.1','psutil':'7.1.2','filelock':'3.20.0','typing-extensions':'4.15.0','sympy':'1.13.3','networkx':'3.5','jinja2':'3.1.6','fsspec':'2025.9.0','setuptools':'80.9.0','colorama':'0.4.6','charset-normalizer':'3.4.4','idna':'3.11','urllib3':'2.8.0','certifi':'2025.10.5','markupsafe':'3.0.3','mpmath':'1.3.0','hf-xet':'1.2.0'}

def validate_plan(data):
 expected={canonicalize_name(k):v for k,v in {'torch':TORCH_VERSION,**PINS}.items()}
 if not isinstance(data,dict) or not isinstance(data.get('items'),list):raise ValueError('Invalid wheel plan')
 seen=set();total=0
 for item in data['items']:
  if not isinstance(item,dict) or not isinstance(item.get('package'),str):raise ValueError('Invalid wheel item')
  name=canonicalize_name(item['package'])
  if name in seen or name not in expected or item.get('version')!=expected[name]:raise ValueError('Stale or duplicate wheel plan; adopt the verified security-update artifacts first')
  seen.add(name);filename=item.get('filename')
  if not isinstance(filename,str) or Path(filename).name!=filename or any(c in filename for c in ('/','\\',':','\x00')):raise ValueError('Unsafe wheel filename')
  wheel_name,version,_,tags=parse_wheel_filename(filename)
  if canonicalize_name(wheel_name)!=name or str(version)!=expected[name] or not tags.intersection(sys_tags()):raise ValueError('Wheel identity or platform does not match plan')
  if type(item.get('size')) is not int or not 0<item['size']<=4*1024**3 or not isinstance(item.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',item['sha256']):raise ValueError('Invalid wheel size/hash')
  if 'local_file' in item and (not isinstance(item['local_file'],str) or not item['local_file']):raise ValueError('Invalid local wheel reference')
  if name=='torch' and (filename!=TORCH_FILENAME or item['size']!=TORCH_SIZE or item['sha256']!=TORCH_SHA256):raise ValueError('Torch artifact does not match the approved security update')
  total+=item['size']
 if seen!=set(expected) or type(data.get('wheel_bytes')) is not int or data['wheel_bytes']!=total:raise ValueError('Incomplete wheel plan or incorrect total')
 return data

def resolve_wheel(item,cache_dir=None,allow_download=False):
 """Resolve only a verified local artifact; explicit local_file never falls back."""
 explicit='local_file' in item
 value=item.get('local_file') if explicit else Path(cache_dir or WHEELS)/item['filename']
 if explicit and (not isinstance(value,str) or not value or not Path(value).is_absolute()):raise ValueError('local_file must be an absolute runtime path')
 target=Path(value).resolve();runtime=RUNTIME_ROOT.resolve()
 if not target.is_relative_to(runtime) or target.name!=item['filename']:raise ValueError('Wheel path must stay within the configured runtime root')
 if not target.is_file():
  if explicit or not allow_download:raise ValueError('Verified local wheel is missing; no network fallback is allowed')
  fetch(item['url'],target,item['sha256'],item['size'])
 if target.stat().st_size!=item['size'] or sha(target)!=item['sha256']:raise ValueError('Local wheel size or checksum mismatch')
 return target
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(8*1024**2),b''):h.update(block)
 return h.hexdigest()
def save(x):
 p=LEDGER.with_suffix('.tmp');p.write_text(json.dumps(x,indent=2),encoding='utf-8');os.replace(p,LEDGER)
def ledger():
 return json.loads(LEDGER.read_text()) if LEDGER.exists() else {'version':'training-downloads-v1','authorized_limit_bytes':LIMIT,'authorization':'User explicitly approved GPU tools and aggregate4GiB on2026-11-14','previous_pilot_bytes':PRIOR,'received_bytes':0,'requests':[],'scope':'HTTP response bodies; protocol headers not counted; no user files transmitted'}
def fetch(url,path=None,expected=None,max_bytes=None):
 if (ROOT/'reports/security-update-downloads.json').exists():raise ValueError('Legacy downloads are retired after the security update; use the current cumulative 10 GiB download ledger. Existing verified local wheels remain usable.')
 book=ledger();entry={'url':url,'bytes':0,'status':'running','time':time.time()}
 tmp=path.with_suffix(path.suffix+'.partial') if path else None
 offset=tmp.stat().st_size if tmp and tmp.exists() else 0
 if offset and (not expected or max_bytes is None or offset>=max_bytes):raise ValueError('Unexpected partial file; inspect before reuse')
 entry['resume_offset']=offset;book['requests'].append(entry);save(book)
 target=None;data=bytearray();h=hashlib.sha256();last=0
 if offset:
  with tmp.open('rb') as previous:
   for block in iter(lambda:previous.read(8*1024**2),b''):h.update(block)
 try:
  headers={'User-Agent':'Riwaq-local-training-setup/1.0'}
  if offset:headers['Range']=f'bytes={offset}-'
  with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=60) as response:
   length=int(response.headers.get('Content-Length','0'));entry['http_status']=response.status
   if offset:
    expected_range=f'bytes {offset}-'
    content_range=response.headers.get('Content-Range','')
    if response.status!=206 or not content_range.startswith(expected_range) or not content_range.endswith('/'+str(max_bytes)):raise ValueError('Server did not honor exact resume range; no body downloaded')
   remaining=LIMIT-PRIOR-book['received_bytes']
   if length>remaining or max_bytes is not None and offset+length>max_bytes:raise ValueError('Download exceeds authorized remaining budget')
   if tmp:target=tmp.open('ab' if offset else 'wb')
   while True:
    remaining=LIMIT-PRIOR-book['received_bytes']
    if max_bytes is not None:remaining=min(remaining,max_bytes-offset-entry['bytes'])
    if remaining<=0:break
    block=response.read(min(1024**2,remaining))
    if not block:break
    book['received_bytes']+=len(block);entry['bytes']+=len(block);h.update(block)
    if target:target.write(block)
    else:data.extend(block)
    if entry['bytes']-last>=16*1024**2:
     save(book);last=entry['bytes']
     if path and entry['bytes']%(256*1024**2)<1024**2:print(json.dumps({'file':path.name,'downloaded_this_request':entry['bytes'],'file_bytes':offset+entry['bytes'],'aggregate':PRIOR+book['received_bytes']}),flush=True)
  if target:target.close();target=None
  if length and entry['bytes']!=length:raise ValueError('Transfer ended early; partial file retained for exact Range resume')
  if path and max_bytes is not None and offset+entry['bytes']!=max_bytes:raise ValueError('Incomplete file retained; never install incomplete data')
  digest=h.hexdigest();entry['sha256']=digest
  if expected and digest!=expected:raise ValueError('Official SHA256 mismatch')
  if path:os.replace(tmp,path)
  entry['status']='complete';save(book);return bytes(data)
 except BaseException as error:
  entry['status']='failed';entry['error']=str(error)[:300];save(book);raise
 finally:
  if target:target.close()

def plan():
 WHEELS.mkdir(parents=True,exist_ok=True);path=RUNTIME/'download-plan.json'
 if path.exists():return validate_plan(json.loads(path.read_text()))
 index=fetch('https://download.pytorch.org/whl/cu128/torch/',max_bytes=5*1024**2).decode()
 links=re.findall(r'href="([^"]+)"',index);match=next(x for x in links if 'torch-2.10.0%2Bcu128-cp312-cp312-win_amd64.whl#sha256=' in x)
 url,digest=match.split('#sha256=');items=[{'package':'torch','version':TORCH_VERSION,'url':url,'filename':TORCH_FILENAME,'sha256':digest,'size':TORCH_SIZE}]
 supported=set(sys_tags())
 for name,version in PINS.items():
  metadata=json.loads(fetch(f'https://pypi.org/pypi/{name}/{version}/json',max_bytes=5*1024**2));choices=[]
  for file in metadata['urls']:
   if not file['filename'].endswith('.whl'):continue
   _,_,_,tags=parse_wheel_filename(file['filename'])
   if tags&supported:choices.append(file)
  if not choices:raise ValueError('No compatible wheel: '+name+' '+version)
  file=min(choices,key=lambda x:x['size']);items.append({'package':name,'version':version,'url':file['url'],'filename':file['filename'],'sha256':file['digests']['sha256'],'size':file['size'],'requires_dist':metadata['info']['requires_dist']})
 if sum(i['size'] for i in items)>LIMIT-PRIOR-ledger()['received_bytes']:raise ValueError('Resolved wheel plan exceeds authorized budget')
 data=validate_plan({'items':items,'wheel_bytes':sum(i['size'] for i in items)})
 path.write_text(json.dumps(data,indent=2),encoding='utf-8');return data
def main():
 args=argparse.ArgumentParser();args.add_argument('action',choices=['plan','install']);a=args.parse_args()
 data=plan();print(json.dumps({'wheel_bytes':data['wheel_bytes'],'remaining':LIMIT-PRIOR-ledger()['received_bytes'],'packages':len(data['items'])}),flush=True)
 if a.action=='plan':return
 resolved=[resolve_wheel(item,allow_download=True) for item in data['items']]
 environment=RUNTIME/'venv'
 if not (environment/'Scripts/python.exe').exists():venv.create(environment,with_pip=True)
 python=str(environment/'Scripts/python.exe')
 subprocess.run([python,'-m','pip','install','--no-index','--no-deps',*[str(path) for path in resolved]],check=True)
 subprocess.run([python,'-m','pip','check'],check=True)
 result=subprocess.check_output([python,'-m','pip','freeze'],text=True);(ROOT/'training/requirements-runtime.lock.txt').write_text(result,encoding='utf-8')
 print(json.dumps({'installed':True,'aggregate_download_bytes':PRIOR+ledger()['received_bytes']}),flush=True)
if __name__=='__main__':main()
