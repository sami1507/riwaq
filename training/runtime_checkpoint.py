"""Atomic local LoRA + AdamW/RNG checkpoint; no pickle and no executable state."""
import json,random,uuid
from pathlib import Path
from safetensors.torch import save_file,load_file
from runtime_common import torch,sha,write_json,adapter_parameter_hash,SOURCE_SHA

def save_checkpoint(path,model,optimizer,trainable,state,recipe,base_hash,data_hash,device):
 path=Path(path)
 if path.exists():raise FileExistsError('Checkpoint exists')
 stage=path.with_name(path.name+'.incomplete-'+uuid.uuid4().hex[:8]);stage.mkdir()
 model.save_pretrained(stage,safe_serialization=True)
 tensors={};index=[]
 for name,p in trainable:
  values=optimizer.state.get(p,{})
  if set(values)-{'step','exp_avg','exp_avg_sq'}:raise ValueError('Unexpected AdamW state')
  for field,value in values.items():
   if not torch.is_tensor(value):raise ValueError('Non-tensor optimizer state')
   key=f'o{len(index):05d}';tensors[key]=value.detach().cpu().contiguous();index.append({'key':key,'parameter':name,'field':field})
 tensors['rng_cpu']=torch.get_rng_state()
 for i,value in enumerate(torch.cuda.get_rng_state_all() if device=='cuda' else []):tensors[f'rng_cuda_{i}']=value.cpu()
 save_file(tensors,str(stage/'optimizer_rng.safetensors'))
 state={**state,'python_rng':random.getstate(),'optimizer_index':index,'recipe':recipe,'accumulation_pending':0}
 write_json(stage/'training_state.json',state)
 metadata={'version':'adapter-runtime-v1','base_report_sha256':base_hash,'device_recipe':device,'manifest_sha256':data_hash,'adapter_sha256':sha(stage/'adapter_model.safetensors'),'adapter_config_sha256':sha(stage/'adapter_config.json'),'source_gguf_sha256':SOURCE_SHA,'optimizer_steps':state['completed_step'],'trainable_parameter_sha256':adapter_parameter_hash(model),'base_preserved':True,'test_data_opened':False,'state_sha256':sha(stage/'training_state.json'),'optimizer_sha256':sha(stage/'optimizer_rng.safetensors')}
 write_json(stage/'runtime_manifest.json',metadata);stage.rename(path);return metadata

def read_checkpoint(path,recipe,base_hash,data_hash,count):
 path=Path(path);manifest=json.loads((path/'runtime_manifest.json').read_text(encoding='utf-8'))
 if manifest['base_report_sha256']!=base_hash or manifest['manifest_sha256']!=data_hash:raise ValueError('Resume base/data mismatch')
 for file,key in [('training_state.json','state_sha256'),('optimizer_rng.safetensors','optimizer_sha256')]:
  if sha(path/file)!=manifest[key]:raise ValueError('Resume checksum mismatch')
 state=json.loads((path/'training_state.json').read_text(encoding='utf-8'))
 if state['recipe']!=recipe or state['accumulation_pending']!=0:raise ValueError('Resume recipe or optimizer boundary mismatch')
 order=state['order'];cursor=state['cursor'];step=state['completed_step']
 if any(type(x) is not int for x in order) or sorted(order)!=list(range(count)) or type(cursor) is not int or not 0<=cursor<=count or type(step) is not int or not 0<=step<=recipe['steps']:raise ValueError('Invalid resume data order/cursor/step')
 if not 0<=state['elapsed_active_seconds']<recipe['max_seconds']:raise ValueError('Resume time budget exhausted')
 return state,load_file(str(path/'optimizer_rng.safetensors'))

def restore_optimizer_rng(optimizer,trainable,state,tensors,device):
 params=dict(trainable);seen=set();step=state['completed_step'];expected_keys={'rng_cpu'}
 expected_keys|={f'rng_cuda_{i}' for i in range(torch.cuda.device_count())} if device=='cuda' else set()
 for entry in state['optimizer_index']:
  name,field,key=entry['parameter'],entry['field'],entry['key']
  if name not in params or field not in {'step','exp_avg','exp_avg_sq'} or (name,field) in seen:raise ValueError('Invalid optimizer index')
  seen.add((name,field));expected_keys.add(key);value=tensors[key];parameter=params[name]
  if not torch.isfinite(value).all():raise ValueError('Nonfinite optimizer tensor')
  if field=='step':
   if value.numel()!=1 or value.item()!=step:raise ValueError('Optimizer step differs from completed state')
   value=value.cpu()
  else:
   if value.shape!=parameter.shape:raise ValueError('Optimizer moment shape mismatch')
   value=value.to(device=parameter.device,dtype=parameter.dtype)
  optimizer.state[parameter][field]=value
 if seen!={(name,field) for name in params for field in ('step','exp_avg','exp_avg_sq')} or set(tensors)!=expected_keys:raise ValueError('Incomplete or extra optimizer/RNG state')
 def tuples(x):return tuple(tuples(v) for v in x) if isinstance(x,list) else x
 random.setstate(tuples(state['python_rng']));torch.set_rng_state(tensors['rng_cpu'])
 if device=='cuda':torch.cuda.set_rng_state_all([tensors[f'rng_cuda_{i}'] for i in range(torch.cuda.device_count())])
