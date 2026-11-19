# Bounded visual runtime setup; weights never download from the application.
import argparse,hashlib,importlib.util,json,subprocess,sys,venv
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'training'))
import runtime_setup as dl
RUNTIME=Path('D:/RiwaqRuntime/visual-v1')
PRIOR=3699001053
REPO='stable-diffusion-v1-5/stable-diffusion-v1-5'
REVISION='451f4fe16113bff5a5d2269ed5ad43b0592e9a14'
WEIGHTS={
'unet/diffusion_pytorch_model.fp16.safetensors':(1719125304,'c83908253f9a64d08c25fc90874c9c8aef9a329ce1ca5fb909d73b0c83d1ea21'),
'text_encoder/model.fp16.safetensors':(246144864,'77795e2023adcf39bc29a884661950380bd093cf0750a966d473d1718dc9ef4e'),
'vae/diffusion_pytorch_model.fp16.safetensors':(167335342,'4fbcf0ebe55a0984f5a5e00d8c4521d52359af7229bb4d81890039d2aa16dd7c'),
'safety_checker/model.fp16.safetensors':(608018440,'08902f19b1cfebd7c989f152fc0507bef6898c706a91d666509383122324b511')}
SMALL=['model_index.json','scheduler/scheduler_config.json','unet/config.json','text_encoder/config.json','vae/config.json','safety_checker/config.json','feature_extractor/preprocessor_config.json','tokenizer/merges.txt','tokenizer/vocab.json','tokenizer/tokenizer_config.json','tokenizer/special_tokens_map.json','README.md']
PINS={'diffusers':'0.35.2','pillow':'13.0.0','fonttools':'4.60.1','brotli':'1.2.1','importlib-metadata':'8.7.0','zipp':'3.23.0','httpx':'0.28.1','anyio':'4.11.0','httpcore':'1.0.9','h11':'0.16.0','sniffio':'1.3.1'}
def write(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8')
def configure(cap):
 RUNTIME.mkdir(parents=True,exist_ok=True);dl.RUNTIME=RUNTIME;dl.WHEELS=RUNTIME/'wheels';dl.WHEELS.mkdir(exist_ok=True);dl.LEDGER=ROOT/'reports/visual-downloads.json';dl.LIMIT=cap*1024**3;dl.PRIOR=PRIOR
 if not dl.LEDGER.exists():write(dl.LEDGER,{'version':'visual-downloads-v1','authorized_limit_bytes':dl.LIMIT,'previous_pilot_bytes':PRIOR,'received_bytes':0,'authorization':'Existing 4GiB aggregate; any increase requires explicit user approval','requests':[],'scope':'HTTP response bodies; no user assets uploaded'})
 book=dl.ledger()
 if cap==8 and book['authorized_limit_bytes']<dl.LIMIT:book.update(authorized_limit_bytes=dl.LIMIT,authorization='Explicit user approval of 8GiB aggregate for local image experiment');write(dl.LEDGER,book)
 if book['authorized_limit_bytes']!=dl.LIMIT:raise ValueError('Use the recorded authorized cap; never silently lower/reset ledger')
def fetch_small(url,path,max_bytes=5*1024**2):
 if path.exists():return path.read_bytes()
 data=dl.fetch(url,max_bytes=max_bytes);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return data
def tools_install():
 from packaging.tags import sys_tags
 from packaging.utils import parse_wheel_filename,canonicalize_name
 # Validate inherited pins before metadata lookup, download or installation.
 previous=dl.validate_plan(json.loads(Path('D:/RiwaqRuntime/training-v1/download-plan.json').read_text()))
 reused=[str(dl.resolve_wheel(item,cache_dir=Path('D:/RiwaqRuntime/training-v1/wheels'))) for item in previous['items']]
 supported=set(sys_tags());items=[]
 for name,version in PINS.items():
  meta=json.loads(fetch_small(f'https://pypi.org/pypi/{name}/{version}/json',RUNTIME/'metadata'/f'{name}-{version}.json'))
  if canonicalize_name(meta['info']['name'])!=canonicalize_name(name) or meta['info']['version']!=version:raise ValueError('Cached package metadata does not match the selected security pin')
  choices=[f for f in meta['urls'] if f['filename'].endswith('.whl') and canonicalize_name(parse_wheel_filename(f['filename'])[0])==canonicalize_name(name) and str(parse_wheel_filename(f['filename'])[1])==version and parse_wheel_filename(f['filename'])[3]&supported]
  if not choices:raise ValueError('No compatible wheel: '+name)
  f=min(choices,key=lambda x:x['size'])
  target=dl.resolve_wheel({'filename':f['filename'],'size':f['size'],'sha256':f['digests']['sha256'],'url':f['url']},cache_dir=dl.WHEELS,allow_download=True)
  items.append({'package':name,'version':version,'file':str(target),'sha256':f['digests']['sha256'],'bytes':f['size']})
 environment=RUNTIME/'venv'
 if not (environment/'Scripts/python.exe').exists():venv.create(environment,with_pip=True)
 python=str(environment/'Scripts/python.exe')
 subprocess.run([python,'-m','pip','install','--no-index','--no-deps',*reused,*[x['file'] for x in items]],check=True)
 subprocess.run([python,'-m','pip','check'],check=True)
 (ROOT/'training/requirements-visual.lock.txt').write_text(subprocess.check_output([python,'-m','pip','freeze'],text=True),encoding='utf-8')
 write(RUNTIME/'tools.json',{'reused_training_wheels':len(reused),'new_wheels':items})
 print(json.dumps({'tools_installed':True,'python':python}),flush=True)
def model():
 dest=RUNTIME/'sd15';dest.mkdir(exist_ok=True)
 remaining=dl.LIMIT-PRIOR-dl.ledger()['received_bytes']
 needed=sum(size for name,(size,sha) in WEIGHTS.items() if not (dest/name).exists())
 if needed+4*1024**2>remaining:raise ValueError('Model plan exceeds remaining authorized budget; no weight transfer started')
 records={}
 for name,(size,sha) in WEIGHTS.items():
  path=dest/name;path.parent.mkdir(parents=True,exist_ok=True)
  if path.exists():
   if path.stat().st_size!=size or dl.sha(path)!=sha:raise ValueError('Existing model component differs')
  else:dl.fetch(f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}',path,sha,size)
  records[name]={'bytes':size,'sha256':sha}
 for name in SMALL:
  data=fetch_small(f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}',dest/name)
  records[name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
 # Pin the original license repository before fetching the retained license.
 commitfile=RUNTIME/'license-commit.json'
 if not commitfile.exists():fetch_small('https://api.github.com/repos/CompVis/stable-diffusion/commits/main',commitfile)
 commit=json.loads(commitfile.read_text())['sha'];license_data=fetch_small(f'https://raw.githubusercontent.com/CompVis/stable-diffusion/{commit}/LICENSE',dest/'LICENSE')
 if b'CreativeML Open RAIL-M' not in license_data and b'CreativeML OpenRAIL' not in license_data:raise ValueError('Unexpected license')
 records['LICENSE']={'bytes':len(license_data),'sha256':hashlib.sha256(license_data).hexdigest()}
 report={'model':'Stable Diffusion v1.5','repository':REPO,'repository_role':'Unaffiliated mirror of deprecated Runway weights','revision':REVISION,'license':'CreativeML OpenRAIL-M','license_commit':commit,'files':records,'tested':False,'processing':'local','default_steps':24,'default_guidance':7.0}
 write(dest/'riwaq-model.json',report);write(ROOT/'reports/visual-model-manifest.json',report)
 print(json.dumps({'model_downloaded_verified':True,'bytes':sum(x['bytes'] for x in records.values()),'quality_tested':False}),flush=True)
def fonts():
 commit='2f9ba1b25957d958db71a849e85d72e3ecfb845a';base='https://raw.githubusercontent.com/IBM/plex/'+commit+'/packages/plex-sans-hebrew/'
 out=ROOT/'frontend/public/fonts';records=[]
 for weight,suffix in [(400,'Regular'),(600,'SemiBold')]:
  name='IBMPlexSansHebrew-'+suffix+'.woff2';data=fetch_small(base+'fonts/complete/woff2/'+name,out/name,2*1024**2)
  if not data.startswith(b'wOF2'):raise ValueError('Invalid font')
  records.append({'file':name,'weight':weight,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
 license_data=fetch_small(base+'LICENSE.txt',out/'plex-sans-hebrew-LICENSE.txt',100000)
 if b'SIL OPEN FONT LICENSE' not in license_data:raise ValueError('Missing font license')
 write(out/'visual-fonts.json',{'source':'https://github.com/IBM/plex','commit':commit,'license':'OFL-1.1','files':records,'license_sha256':hashlib.sha256(license_data).hexdigest()})
 print(json.dumps({'fonts_downloaded':len(records)}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['tools','model','fonts']);p.add_argument('--cap-gib',type=int,choices=[4,8],default=4);a=p.parse_args();configure(a.cap_gib)
 {'tools':tools_install,'model':model,'fonts':fonts}[a.action]()
