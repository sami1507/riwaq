"""Stream existing Qwen3 GGUF into BF16 shards; no original-BF16 claim or network."""
import argparse,gc,hashlib,json,os,shutil,time,uuid
from pathlib import Path
SOURCE_SHA='85e4a5b7b8ef0e48af0e8658f5aaab9c2324c76c1641493f4d1e25fce54b18b9'
REQUIRED=dict(model_type='qwen3',hidden_size=2560,intermediate_size=9728,num_hidden_layers=36,num_attention_heads=32,num_key_value_heads=8,head_dim=128,vocab_size=151936,rope_theta=5000000,tie_word_embeddings=True)
PROBES=['Hello, world!','رواق يحفظ القرار ومصدره.','لا توجد أدلة كافية.','{"value":null,"source":"[S1]"}','0.1 + 0.2 = 0.3','def add(a, b):\n    return a + b\n','١٢٫٥ × ٢','x != None and y <= 3','العربية English مختلط','<|im_start|>assistant\n']
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(8*1024**2),b''):h.update(block)
 return h.hexdigest()
def dump(path,data):Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def expected_tensors():
 expected={'token_embd.weight':('model.embed_tokens.weight',(151936,2560)),'output_norm.weight':('model.norm.weight',(2560,))}
 specs={'attn_norm.weight':('input_layernorm.weight',(2560,)),'attn_q.weight':('self_attn.q_proj.weight',(4096,2560)),'attn_k.weight':('self_attn.k_proj.weight',(1024,2560)),'attn_v.weight':('self_attn.v_proj.weight',(1024,2560)),'attn_output.weight':('self_attn.o_proj.weight',(2560,4096)),'attn_q_norm.weight':('self_attn.q_norm.weight',(128,)),'attn_k_norm.weight':('self_attn.k_norm.weight',(128,)),'ffn_norm.weight':('post_attention_layernorm.weight',(2560,)),'ffn_gate.weight':('mlp.gate_proj.weight',(9728,2560)),'ffn_up.weight':('mlp.up_proj.weight',(9728,2560)),'ffn_down.weight':('mlp.down_proj.weight',(2560,9728))}
 for i in range(36):
  for gg,(hf,shape) in specs.items():expected[f'blk.{i}.{gg}']=(f'model.layers.{i}.{hf}',shape)
 return expected

def main():
 p=argparse.ArgumentParser();p.add_argument('--gguf',required=True);p.add_argument('--assets',required=True);p.add_argument('--output',required=True);p.add_argument('--shard-mib',type=int,default=512);a=p.parse_args()
 if not 64<=a.shard_mib<=1024:raise ValueError('shard-mib must be64..1024')
 os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false')
 import torch,numpy as np,gguf,psutil
 from safetensors.torch import save_file
 from transformers import AutoTokenizer
 started=time.monotonic();source=Path(a.gguf).resolve();assets=Path(a.assets).resolve();out=Path(a.output).resolve()
 if out.exists():raise FileExistsError('Refusing to overwrite conversion output')
 if not source.is_file() or source.stat().st_size!=2497280480 or sha(source)!=SOURCE_SHA:raise ValueError('Unexpected production GGUF content')
 manifest=json.loads((assets/'assets.json').read_text());revision=manifest['revision']
 if len(revision)!=40 or any(c not in '0123456789abcdef' for c in revision):raise ValueError('Exact official revision required')
 for name,record in manifest['files'].items():
  if (assets/name).parent!=assets or sha(assets/name)!=record['sha256']:raise ValueError('Asset checksum mismatch')
 config=json.loads((assets/'config.json').read_text(encoding='utf-8'))
 for key,value in REQUIRED.items():
  if config.get(key)!=value:raise ValueError(f'Wrong architecture field {key}: {config.get(key)!r}')
 if config.get('architectures')!=['Qwen3ForCausalLM']:raise ValueError('Wrong architecture')
 tokenizer=AutoTokenizer.from_pretrained(str(assets),local_files_only=True,trust_remote_code=False)
 embedded=AutoTokenizer.from_pretrained(str(source.parent),gguf_file=source.name,local_files_only=True,trust_remote_code=False)
 if tokenizer.eos_token_id!=151645 or len(tokenizer)>config['vocab_size']:raise ValueError('Unexpected tokenizer bounds/EOS')
 parity=[]
 for text in PROBES:
  ids=tokenizer.encode(text,add_special_tokens=False)
  if ids!=embedded.encode(text,add_special_tokens=False):raise ValueError('Tokenizer mismatch on fixed parity probe')
  parity.append({'text':text,'ids':ids})
 del embedded
 reader=gguf.GGUFReader(str(source),'r');field=reader.fields['general.architecture']
 if bytes(field.parts[field.data[0]]).decode('utf-8')!='qwen3':raise ValueError('Wrong GGUF architecture')
 expected=expected_tensors();names=[t.name for t in reader.tensors]
 if len(names)!=len(set(names)) or set(names)!=set(expected):raise ValueError('Tensor namespace mismatch')
 stage=out.with_name(out.name+'.incomplete-'+uuid.uuid4().hex[:8]);stage.mkdir(parents=True)
 state={};size=0;total=0;shards=[];weight_map={};records=[];peak=0;process=psutil.Process()
 def flush():
  nonlocal state,size
  if not state:return
  filename=f'model-{len(shards)+1:05d}.safetensors';save_file(state,str(stage/filename),metadata={'format':'pt'})
  for key in state:weight_map[key]=filename
  shards.append({'file':filename,'bytes':(stage/filename).stat().st_size,'sha256':sha(stage/filename)})
  state={};size=0;gc.collect()
 for index,item in enumerate(reader.tensors,1):
  name,shape=expected[item.name];weights=gguf.quants.dequantize(item.data,item.tensor_type)
  if tuple(weights.shape)!=shape or not np.isfinite(weights).all():raise ValueError('Invalid tensor '+item.name)
  if not weights.flags.writeable:weights=weights.copy()
  tensor=torch.from_numpy(np.ascontiguousarray(weights)).to(dtype=torch.bfloat16).contiguous();del weights
  nbytes=tensor.numel()*tensor.element_size()
  if state and size+nbytes>a.shard_mib*1024**2:flush()
  state[name]=tensor;size+=nbytes;total+=nbytes;records.append({'gguf':item.name,'hf':name,'shape':list(shape),'dtype':'bfloat16','bytes':nbytes});del tensor
  peak=max(peak,process.memory_info().rss)
  if index%20==0:print(json.dumps({'stage':'convert','tensors':index,'total':len(reader.tensors),'rss':process.memory_info().rss}),flush=True)
 flush()
 if total!=4022468096*2:raise ValueError('Unexpected unique parameter count')
 dump(stage/'model.safetensors.index.json',{'metadata':{'total_size':total},'weight_map':weight_map})
 config['torch_dtype']='bfloat16';config['_name_or_path']='riwaq-reconstructed-qwen3-4b-instruct-2507';dump(stage/'config.json',config);tokenizer.save_pretrained(stage)
 for filename in ['generation_config.json','LICENSE']:
  if (assets/filename).is_file():shutil.copyfile(assets/filename,stage/filename)
 report={'version':'gguf-reconstruction-v1','status':'complete','source_path':str(source),'source_sha256':SOURCE_SHA,'source_preserved':True,'source_quantization':'Q4_K_M','base_description':'BF16 reconstruction of quantized GGUF, not original BF16 checkpoint','assets_repository':'Qwen/Qwen3-4B-Instruct-2507','assets_revision':revision,'asset_hashes':manifest['files'],'unique_parameters':4022468096,'weight_bytes':total,'tokenizer_probe_parity':parity,'tokenizer_eos_id':tokenizer.eos_token_id,'tensor_records':records,'shards':shards,'seconds':time.monotonic()-started,'peak_sampled_rss_bytes':peak,'sampling_note':'RSS sampled after each tensor; not guaranteed continuous peak'}
 report['runtime_files']={p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in stage.iterdir() if p.is_file() and not p.name.endswith('.safetensors')}
 dump(stage/'reconstruction.json',report);stage.rename(out);print(json.dumps({'status':'complete','output':str(out),'seconds':report['seconds'],'report_sha256':sha(out/'reconstruction.json')}),flush=True)
if __name__=='__main__':main()
