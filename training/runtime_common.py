"""Pinned local training runtime. Model loaders never fetch remote code or files."""
import hashlib,json,os,time
from pathlib import Path
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false')
import torch
from transformers import AutoModelForCausalLM,AutoTokenizer,BitsAndBytesConfig
SOURCE_SHA='85e4a5b7b8ef0e48af0e8658f5aaab9c2324c76c1641493f4d1e25fce54b18b9'
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(8*1024**2),b''):h.update(block)
 return h.hexdigest()
def write_json(path,value):
 path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');tmp.replace(path)
def verify_base(base):
 base=Path(base).resolve();report=json.loads((base/'reconstruction.json').read_text(encoding='utf-8'))
 if report.get('status')!='complete' or report.get('source_sha256')!=SOURCE_SHA:raise ValueError('Base provenance mismatch')
 for row in report['shards']:
  path=base/row['file']
  if path.parent!=base or path.stat().st_size!=row['bytes'] or sha(path)!=row['sha256']:raise ValueError('Base shard mismatch')
 if not {'config.json','model.safetensors.index.json','tokenizer_config.json','tokenizer.json'}<=set(report.get('runtime_files',{})):raise ValueError('Missing runtime metadata provenance')
 for name,record in report['runtime_files'].items():
  path=base/name
  if path.resolve().parent!=base or path.stat().st_size!=record['bytes'] or sha(path)!=record['sha256']:raise ValueError('Runtime metadata checksum mismatch')
 return sha(base/'reconstruction.json')
def load_runtime(base,device,adapter=None,training=False):
 base=Path(base).resolve();base_hash=verify_base(base);torch.set_num_threads(min(8,os.cpu_count() or 1))
 tokenizer=AutoTokenizer.from_pretrained(str(base),local_files_only=True,trust_remote_code=False);tokenizer.pad_token=tokenizer.eos_token
 kwargs=dict(local_files_only=True,trust_remote_code=False,use_safetensors=True,low_cpu_mem_usage=True,dtype=torch.bfloat16,attn_implementation='sdpa')
 if device=='cuda':
  if not torch.cuda.is_available() or tuple(map(int,torch.version.cuda.split('.')[:2]))<(12,8):raise RuntimeError('CUDA12.8+ required; no automatic fallback')
  kwargs.update(device_map={'':0},quantization_config=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=torch.bfloat16))
 elif device=='cpu':kwargs['device_map']={'':'cpu'}
 else:raise ValueError('Unknown device')
 model=AutoModelForCausalLM.from_pretrained(str(base),**kwargs)
 if model.config.head_dim!=128 or model.config.num_hidden_layers!=36:raise ValueError('Wrong instantiated architecture')
 if device=='cuda':
  from peft import prepare_model_for_kbit_training
  # Same nonquantized-module dtype in baseline, training and reloaded inference.
  model=prepare_model_for_kbit_training(model,use_gradient_checkpointing=training,gradient_checkpointing_kwargs={'use_reentrant':False})
 else:
  for parameter in model.parameters():parameter.requires_grad=False
 model.config.use_cache=not training
 if adapter is not None:
  from peft import PeftModel
  path=Path(adapter).resolve();evidence=json.loads((path/'runtime_manifest.json').read_text(encoding='utf-8'))
  if evidence['base_report_sha256']!=base_hash or evidence['device_recipe']!=device:raise ValueError('Adapter base/recipe mismatch')
  for filename,key in [('adapter_model.safetensors','adapter_sha256'),('adapter_config.json','adapter_config_sha256')]:
   if sha(path/filename)!=evidence[key]:raise ValueError('Adapter checksum mismatch')
  model=PeftModel.from_pretrained(model,str(path),is_trainable=training,local_files_only=True)
 return model,tokenizer,base_hash

def load_split(data_dir,manifest_sha,split):
 if split not in {'train','validation'}:raise ValueError('Training runtime never opens test data')
 data_dir=Path(data_dir).resolve();mp=data_dir/'manifest.json'
 if sha(mp)!=manifest_sha:raise ValueError('Dataset manifest changed')
 manifest=json.loads(mp.read_text(encoding='utf-8'))
 if manifest['version']!='sft-original-v3':raise ValueError('Use independently verified data-v3')
 path=data_dir/(split+'.jsonl')
 if sha(path)!=manifest['splits'][split]['sha256']:raise ValueError('Split checksum mismatch')
 rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
 if len(rows)!=manifest['splits'][split]['count']:raise ValueError('Split count mismatch')
 for row in rows:
  if row.get('split')!=split or row.get('training_permission') is not True or row.get('redaction_reviewed') is not True:raise ValueError('Dataset permission/split failed')
  if [m['role'] for m in row['messages']]!=['system','user','assistant']:raise ValueError('Unexpected message schema')
 return rows

def encode_rows(tokenizer,rows,max_length):
 encoded=[];oversized=[];lengths=[]
 for row in rows:
  ids=tokenizer.apply_chat_template(row['messages'],tokenize=True,add_generation_prompt=False)
  prefix=tokenizer.apply_chat_template(row['messages'][:-1],tokenize=True,add_generation_prompt=True)
  if ids[:len(prefix)]!=prefix or len(ids)<=len(prefix):raise ValueError('Assistant prefix mismatch: '+row['id'])
  lengths.append(len(ids))
  if len(ids)>max_length:oversized.append({'id':row['id'],'tokens':len(ids)})
  encoded.append({'id':row['id'],'input_ids':ids,'labels':[-100]*len(prefix)+ids[len(prefix):]})
 return encoded,{'count':len(rows),'min':min(lengths),'max':max(lengths),'median':sorted(lengths)[len(lengths)//2],'oversized':oversized,'truncation':False,'supervision':'assistant suffix including end-of-turn only'}
def tensor_batch(row,device):
 ids=torch.tensor([row['input_ids']],dtype=torch.long,device=device)
 return {'input_ids':ids,'attention_mask':torch.ones_like(ids),'labels':torch.tensor([row['labels']],dtype=torch.long,device=device)}
def adapter_parameter_hash(model):
 h=hashlib.sha256()
 for name,p in sorted(model.named_parameters()):
  if '.lora_' in name:h.update(name.encode());h.update(p.detach().cpu().contiguous().view(torch.uint8).numpy().tobytes())
 return h.hexdigest()
@torch.inference_mode()
def generate(model,tokenizer,messages,max_new_tokens=256):
 if not 1<=max_new_tokens<=512:raise ValueError('Output budget1..512')
 ids=tokenizer.apply_chat_template(messages,tokenize=True,add_generation_prompt=True,return_tensors='pt')
 if ids.shape[1]>1536:raise ValueError('Inference context exceeds pilot cap')
 ids=ids.to(model.get_input_embeddings().weight.device);start=time.monotonic()
 output=model.generate(input_ids=ids,attention_mask=torch.ones_like(ids),do_sample=False,num_beams=1,max_new_tokens=max_new_tokens,eos_token_id=tokenizer.eos_token_id,pad_token_id=tokenizer.pad_token_id,use_cache=True)
 generated=output[0,ids.shape[1]:].tolist()
 return {'content':tokenizer.decode(generated,skip_special_tokens=True),'input_tokens':ids.shape[1],'output_tokens':len(generated),'finished':bool(generated and generated[-1]==tokenizer.eos_token_id),'seconds':time.monotonic()-start}
