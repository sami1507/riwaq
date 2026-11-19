"""Fixed local diffusion worker. Structured JSON input, no user code/tools/network."""
import os
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false')
import sys,json,time,hashlib,socket
from pathlib import Path
def blocked(*args,**kwargs):raise RuntimeError('Network disabled in local image worker')
socket.create_connection=blocked
socket.socket.connect=blocked
socket.socket.connect_ex=blocked
socket.getaddrinfo=blocked
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(4*1024**2),b''):h.update(b)
 return h.hexdigest()
def emit(**v):print(json.dumps(v),flush=True)
def main():
 import torch
 from diffusers import StableDiffusionPipeline,StableDiffusionImg2ImgPipeline
 from PIL import Image
 started=time.monotonic();source=Path(sys.argv[1]);out=Path(sys.argv[2]);model=Path(sys.argv[3]).resolve()
 raw=source.read_bytes()
 if len(raw)>10000000:raise ValueError('Input limit')
 spec=json.loads(raw);manifest=model/'riwaq-model.json';info=json.loads(manifest.read_text(encoding='utf-8'))
 if info['revision']!='451f4fe16113bff5a5d2269ed5ad43b0592e9a14':raise ValueError('Unapproved base revision')
 emit(stage='verifying_model')
 for name,item in info['files'].items():
  p=(model/name).resolve()
  if not p.is_relative_to(model) or p.stat().st_size!=item['bytes'] or sha(p)!=item['sha256']:raise ValueError('Model integrity failed: '+name)
 if not torch.cuda.is_available():raise RuntimeError('CUDA unavailable; no silent CPU/cloud fallback')
 torch.cuda.set_per_process_memory_fraction(.85,0);torch.cuda.reset_peak_memory_stats()
 emit(stage='loading_model')
 pipe=StableDiffusionPipeline.from_pretrained(str(model),variant='fp16',torch_dtype=torch.float16,use_safetensors=True,local_files_only=True).to('cuda')
 if pipe.safety_checker is None:raise RuntimeError('Safety checker required')
 pipe.enable_attention_slicing();pipe.enable_vae_slicing();pipe.set_progress_bar_config(disable=True)
 prompt=spec['prompt'];negative=spec.get('negative_prompt','text, letters, watermark, signature')
 if not isinstance(prompt,str) or not 1<=len(prompt)<=2500:raise ValueError('Prompt limit')
 if len(pipe.tokenizer(prompt,truncation=False)['input_ids'])>77:raise ValueError('Visual prompt exceeds 77 CLIP tokens; shorten it explicitly')
 if len(pipe.tokenizer(negative,truncation=False)['input_ids'])>77:raise ValueError('Negative prompt exceeds 77 CLIP tokens')
 w,h=spec.get('width',512),spec.get('height',512);steps=spec.get('steps',24);seed=spec.get('seed',37)
 if any(type(v)!=int for v in (w,h,steps,seed)) or w%64 or h%64 or min(w,h)<256 or max(w,h)>768 or w*h>512*768 or not 4<=steps<=40 or not 0<=seed<2**31:raise ValueError('Resource bounds')
 def callback(p,i,t,kw):emit(stage='diffusion',step=i+1,total=len(p.scheduler.timesteps));return kw
 opts=dict(prompt=prompt,negative_prompt=negative,num_inference_steps=steps,guidance_scale=7.0,generator=torch.Generator('cuda').manual_seed(seed),callback_on_step_end=callback)
 mask=None;original=None
 if spec.get('kind')=='edit':
  import base64,io
  original=Image.open(io.BytesIO(base64.b64decode(spec['image_base64'],validate=True))).convert('RGB')
  if original.size!=(w,h):raise ValueError('Edit requires matching source dimensions; no implicit resize/crop')
  box=spec['mask_rect']
  if len(box)!=4 or any(type(x)!=int for x in box) or not(0<=box[0]<box[2]<=w and 0<=box[1]<box[3]<=h):raise ValueError('Invalid edit rectangle')
  from PIL import ImageDraw
  mask=Image.new('L',(w,h));ImageDraw.Draw(mask).rectangle((box[0],box[1],box[2]-1,box[3]-1),fill=255)
  pipe=StableDiffusionImg2ImgPipeline(**pipe.components);pipe.set_progress_bar_config(disable=True)
  opts.update(image=original,strength=.65)
 else:opts.update(width=w,height=h)
 with torch.inference_mode():result=pipe(**opts)
 torch.cuda.synchronize()
 flagged=not result.nsfw_content_detected or bool(result.nsfw_content_detected[0])
 report={'model':info['model'],'revision':info['revision'],'license':info['license'],'model_manifest_sha256':sha(manifest),'input_sha256':hashlib.sha256(raw).hexdigest(),'seed':seed,'steps':steps,'guidance_scale':7,'width':w,'height':h,'elapsed_seconds':time.monotonic()-started,'cuda_peak_allocated':torch.cuda.max_memory_allocated(),'cuda_peak_reserved':torch.cuda.max_memory_reserved(),'torch':torch.__version__,'safety_flagged':flagged,'network':'Python socket calls denied; all model loads local_files_only','success':not flagged,'quality_verified':False,'text_generation_tested':False,'attempt':1,'edit_mode':'img2img then exact outside-mask pixel composite' if mask else None}
 out.mkdir(parents=True,exist_ok=True)
 if not flagged:
  im=result.images[0].convert('RGB')
  if mask is not None:
   im=Image.composite(im,original,mask);report['protected_pixels_exact']=True
  im.save(out/'image.png')
  report['image_sha256']=sha(out/'image.png')
 (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 emit(stage='complete',success=not flagged)
 if flagged:raise RuntimeError('Output blocked by safety checker; not a successful generation')
if __name__=='__main__':main()
