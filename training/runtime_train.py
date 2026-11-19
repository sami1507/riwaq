"""Bounded real assistant-only SFT with validation, atomic checkpoints and resume."""
import argparse,importlib.metadata,json,os,random,threading,time
from pathlib import Path
from runtime_common import sha,write_json,load_runtime,load_split,encode_rows,tensor_batch,adapter_parameter_hash,torch
from runtime_checkpoint import save_checkpoint,read_checkpoint,restore_optimizer_rng
from transformers import AutoTokenizer
from peft import LoraConfig,get_peft_model

def main():
 p=argparse.ArgumentParser();p.add_argument('--base',required=True);p.add_argument('--data',required=True);p.add_argument('--manifest-sha',required=True);p.add_argument('--output',required=True);p.add_argument('--device',choices=['cpu','cuda'],required=True)
 p.add_argument('--profile-only',action='store_true');p.add_argument('--max-length',type=int,default=512);p.add_argument('--rank',type=int,default=4);p.add_argument('--last-layers',type=int,default=4);p.add_argument('--steps',type=int,default=4);p.add_argument('--accumulation',type=int,default=8);p.add_argument('--learning-rate',type=float,default=.0001);p.add_argument('--seed',type=int,default=37);p.add_argument('--max-seconds',type=int,default=1800);p.add_argument('--max-rss-gib',type=float,default=12)
 p.add_argument('--validate-every',type=int,default=8);p.add_argument('--patience',type=int,default=2);p.add_argument('--min-delta',type=float,default=.0001);p.add_argument('--resume');p.add_argument('--pause-after',type=int);a=p.parse_args()
 if not 64<=a.max_length<=512 or a.rank not in (2,4,8) or not 1<=a.last_layers<=36:raise ValueError('Sequence/rank/layer limits exceeded')
 if not 1<=a.steps<=256 or not 1<=a.accumulation<=16 or not 0<a.learning_rate<=.001:raise ValueError('Optimization limits exceeded')
 if not 30<=a.max_seconds<=7200 or not 8<=a.max_rss_gib<=16 or not 1<=a.validate_every<=32 or not 1<=a.patience<=4 or not 0<=a.min_delta<=.1:raise ValueError('Resource/validation caps invalid')
 if a.pause_after is not None and not 1<=a.pause_after<a.steps:raise ValueError('Pause boundary must precede final step')
 output=Path(a.output).resolve()
 if output.exists():raise FileExistsError('Refusing to overwrite run')
 output.mkdir(parents=True);train=load_split(a.data,a.manifest_sha,'train');validation=load_split(a.data,a.manifest_sha,'validation')
 if {r['family'] for r in train}&{r['family'] for r in validation}:raise ValueError('Family leakage')
 tokenizer=AutoTokenizer.from_pretrained(a.base,local_files_only=True,trust_remote_code=False)
 encoded,tp=encode_rows(tokenizer,train,a.max_length);val_encoded,vp=encode_rows(tokenizer,validation,a.max_length)
 profile={'train':tp,'validation':vp,'manifest_sha256':a.manifest_sha};write_json(output/'token-profile.json',profile)
 if tp['oversized'] or vp['oversized']:raise ValueError('No truncation permitted; inspect token profile')
 if a.profile_only:print(json.dumps({'status':'profile_only','profile':profile}),flush=True);return
 import psutil
 process=psutil.Process();started=time.monotonic();elapsed_before=0.;finished=threading.Event()
 def watchdog():
  while not finished.wait(.25):
   reason='wall_time_limit' if time.monotonic()-started+elapsed_before>a.max_seconds else 'rss_limit' if process.memory_info().rss>a.max_rss_gib*1024**3 else None
   if reason:
    try:write_json(output/'aborted.json',{'reason':reason,'seconds':time.monotonic()-started+elapsed_before})
    finally:os._exit(124)
 threading.Thread(target=watchdog,daemon=True).start();random.seed(a.seed);torch.manual_seed(a.seed)
 if a.device=='cuda':torch.cuda.manual_seed_all(a.seed)
 model,tokenizer,base_hash=load_runtime(a.base,a.device,adapter=a.resume,training=True)
 layers='|'.join(str(i) for i in range(36-a.last_layers,36));target=rf'.*\.layers\.({layers})\.self_attn\.(q_proj|v_proj)$'
 if not a.resume:model=get_peft_model(model,LoraConfig(r=a.rank,lora_alpha=2*a.rank,lora_dropout=.05,target_modules=target,bias='none',task_type='CAUSAL_LM'))
 trainable=[(name,param) for name,param in model.named_parameters() if param.requires_grad]
 if not trainable or any('.lora_' not in name for name,_ in trainable):raise ValueError('Only LoRA parameters may receive gradients')
 expected_count=a.last_layers*a.rank*((2560+4096)+(2560+1024))
 if sum(v.numel() for _,v in trainable)!=expected_count:raise ValueError('Unexpected adapter parameter count')
 optimizer=torch.optim.AdamW([v for _,v in trainable],lr=a.learning_rate,weight_decay=.01);device=model.get_input_embeddings().weight.device
 packages={n:importlib.metadata.version(n) for n in ['torch','transformers','peft','accelerate','gguf','safetensors','bitsandbytes']}
 recipe={k:v for k,v in vars(a).items() if k not in {'output','resume','pause_after','profile_only','base','data','manifest_sha'}}|{'packages':packages,'targets':target,'attention':'sdpa','weight_decay':.01,'base_dtype_policy':'bnb NF4+double quant; nonquantized modules FP32' if a.device=='cuda' else 'BF16'}
 initial_hash=adapter_parameter_hash(model);order=list(range(len(encoded)));random.Random(a.seed).shuffle(order);cursor=0;start_step=0
 report={'version':'actual-lora-v1','status':'running','options':vars(a),'manifest_sha256':a.manifest_sha,'base_report_sha256':base_hash,'base_kind':'BF16 reconstruction from existing Q4_K_M GGUF','device_recipe':a.device,'gpu_base_quantization':'NF4 double-quantized' if a.device=='cuda' else None,'trainable_parameters':expected_count,'initial_adapter_parameter_sha256':initial_hash,'packages':packages,'steps':[],'validation_history':[],'test_data_opened':False,'loss_scope':'assistant-only cross-entropy, not correctness','candidate_checkpoint':None}
 def validation_loss():
  model.eval();total=0.;tokens=0;begin=time.monotonic()
  with torch.inference_mode():
   for row in val_encoded:
    batch=tensor_batch(row,device);n=sum(v!=-100 for v in row['labels'][1:]);loss=model(**batch).loss
    if not torch.isfinite(loss):raise ValueError('Nonfinite validation loss')
    total+=float(loss)*n;tokens+=n;del batch,loss
  return {'loss':total/tokens,'supervised_tokens':tokens,'rows':len(val_encoded),'seconds':time.monotonic()-begin}
 if a.resume:
  state,tensors=read_checkpoint(a.resume,recipe,base_hash,a.manifest_sha,len(encoded))
  expected_hash=json.loads((Path(a.resume)/'runtime_manifest.json').read_text())['trainable_parameter_sha256']
  if adapter_parameter_hash(model)!=expected_hash:raise ValueError('Resumed adapter parameter mismatch')
  report={**state['report'],'options':vars(a),'resume_from':str(Path(a.resume).resolve()),'resume_verified':True,'status':'running'}
  order=state['order'];cursor=state['cursor'];start_step=state['completed_step'];elapsed_before=state['elapsed_active_seconds'];best_loss=state['best_loss'];stale=state['stale'];initial_hash=report['initial_adapter_parameter_sha256']
  restore_optimizer_rng(optimizer,trainable,state,tensors,a.device);del tensors
 else:
  report['initial_validation']=validation_loss();best_loss=report['initial_validation']['loss'];stale=0
 write_json(output/'run.json',report)
 def checkpoint(label,step):
  directory=output/label
  state={'completed_step':step,'order':order,'cursor':cursor,'best_loss':best_loss,'stale':stale,'elapsed_active_seconds':time.monotonic()-started+elapsed_before,'report':report}
  return save_checkpoint(directory,model,optimizer,trainable,state,recipe,base_hash,a.manifest_sha,a.device)
 last_checkpoint_step=-1
 for step in range(start_step+1,a.steps+1):
  if (output/'STOP').exists():report['status']='stopped';break
  model.train();optimizer.zero_grad(set_to_none=True);losses=[];ids=[];step_start=time.monotonic();old_cursor=cursor;old_order=order.copy();stopping=False
  rng_before=(random.getstate(),torch.get_rng_state(),torch.cuda.get_rng_state_all() if a.device=='cuda' else [])
  for micro in range(a.accumulation):
   if (output/'STOP').exists():stopping=True;break
   if cursor==len(order):cursor=0;random.Random(a.seed+step).shuffle(order)
   row=encoded[order[cursor]];cursor+=1;ids.append(row['id']);batch=tensor_batch(row,device);loss=model(**batch).loss
   if not torch.isfinite(loss):raise ValueError('Nonfinite training loss')
   losses.append(float(loss.detach()));(loss/a.accumulation).backward();del batch,loss
  if stopping:
   optimizer.zero_grad(set_to_none=True);cursor=old_cursor;order=old_order
   random.setstate(rng_before[0]);torch.set_rng_state(rng_before[1])
   if a.device=='cuda':torch.cuda.set_rng_state_all(rng_before[2])
   report['status']='stopped';break
  norm=torch.nn.utils.clip_grad_norm_([p for _,p in trainable],1.)
  if not torch.isfinite(norm):raise ValueError('Nonfinite adapter gradient')
  optimizer.step();optimizer.zero_grad(set_to_none=True)
  entry={'step':step,'loss':sum(losses)/len(losses),'pre_clip_gradient_norm':float(norm),'train_ids':ids,'step_seconds':time.monotonic()-step_start,'seconds':time.monotonic()-started+elapsed_before,'rss_bytes':process.memory_info().rss}
  if a.device=='cuda':entry.update(cuda_max_reserved_bytes=torch.cuda.max_memory_reserved(),cuda_max_allocated_bytes=torch.cuda.max_memory_allocated())
  report['steps'].append(entry);print(json.dumps({'stage':'train',**{k:v for k,v in entry.items() if k!='train_ids'}}),flush=True)
  due=step%a.validate_every==0 or step==a.steps
  if due:
   score=validation_loss();report['validation_history'].append({'step':step,**score})
   if score['loss']<best_loss-a.min_delta:
    best_loss=score['loss'];stale=0;report['candidate_checkpoint']=str(output/f'checkpoint-{step:04d}');report['candidate_validation_loss']=best_loss
   else:stale+=1
   report['stale_validation_checks']=stale
  if due or a.pause_after==step:
   checkpoint(f'checkpoint-{step:04d}',step);last_checkpoint_step=step
  write_json(output/'run.json',report)
  if a.pause_after==step:report['status']='paused_at_optimizer_boundary';break
  if due and stale>=a.patience:report['early_stop_reason']='validation_loss_patience';break
 if not report['steps']:raise ValueError('No optimizer step completed')
 final_hash=adapter_parameter_hash(model)
 if final_hash==initial_hash or not any(x['pre_clip_gradient_norm']>0 for x in report['steps']):raise ValueError('No evidence of learned parameter updates')
 report['final_adapter_parameter_sha256']=final_hash;report['adapter_parameters_changed']=True
 completed_step=report['steps'][-1]['step']
 if last_checkpoint_step!=completed_step:checkpoint(f'checkpoint-{completed_step:04d}',completed_step)
 report['last_checkpoint']=str(output/f'checkpoint-{completed_step:04d}')
 if report['status']=='running':report['status']='trained_candidate_pending_quality_gate' if report['candidate_checkpoint'] else 'trained_no_better_loss_candidate'
 report['seconds']=time.monotonic()-started+elapsed_before;write_json(output/'run.json',report);finished.set()
 print(json.dumps({'status':report['status'],'output':str(output),'candidate':report['candidate_checkpoint'],'seconds':report['seconds']}),flush=True)
if __name__=='__main__':main()
