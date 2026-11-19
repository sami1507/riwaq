# Package the rejected research adapter and provenance, excluding large weights/private data.
import argparse,hashlib,json,re,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--runtime',default='D:/RiwaqRuntime/training-v1');p.add_argument('--output',required=True);p.add_argument('--label',default='v1-rejected');a=p.parse_args()
if not re.fullmatch(r'[A-Za-z0-9._-]+',a.label):raise ValueError('Unsafe label')
runtime=Path(a.runtime).resolve()
recipe=json.loads((runtime/'download-plan.json').read_text(encoding='utf-8-sig'))
if recipe.get('security_update'):raise ValueError('Historical experiment packaging requires its preserved original recipe; the current runtime was security-updated.')
output=Path(a.output).resolve();output.mkdir(parents=True,exist_ok=True)
selection=json.loads((ROOT/'reports/training-lora-selection.json').read_text(encoding='utf-8'))
if selection['decision']!='retain_base' or selection['candidate_status']!='rejected':raise ValueError('This packager is scoped to the rejected experiment')
files={}
def add(path,name):
 path=Path(path)
 if not path.is_file():raise FileNotFoundError(path)
 if name in files:raise ValueError('Duplicate archive path')
 files[name]=path
for path in (runtime/'runs/sft64-v3/checkpoint-0064').iterdir():
 if path.is_file():add(path,'checkpoint-0064/'+path.name)
for path in (runtime/'assets').iterdir():
 if path.is_file():add(path,'assets/'+path.name)
for path in (runtime/'runs/sft64-v3').iterdir():
 if path.is_file() and path.suffix=='.json':add(path,'run/'+path.name)
for phase in ['validation-v1','heldout-v1']:
 for path in (runtime/'evaluation'/phase).iterdir():
  if path.is_file() and (path.suffix in {'.json','.jsonl','.log'}):add(path,'evaluation/'+phase+'/'+path.name)
add(runtime/'reconstructed-base/reconstruction.json','base/reconstruction.json')
add(runtime/'download-plan.json','provenance/download-plan.json')
for name in ['training-downloads.json','training-lora-selection.json','training-lora-selection.json.heldout-started.json','training-lora-retention-review.json','training-supervisor.json']:
 add(ROOT/'reports'/name,'provenance/'+name)
for name in ['requirements-runtime.lock.txt','DATA_RIGHTS.md','README.md','retention-v1.json']:
 add(ROOT/'training'/name,'training/'+name)
for path in (ROOT/'training/data-v3').iterdir():
 if path.is_file():add(path,'training/data-v3/'+path.name)
for path in (ROOT/'training').glob('*.py'):add(path,'training/'+path.name)
for name in ['evaluate_lora_validation.py','evaluate_lora_heldout.py','test_training_supervisor.py']:add(ROOT/'scripts'/name,'scripts/'+name)
add(ROOT/'docs/TRAINING_RESULTS.md','TRAINING_RESULTS.md')
entries={}
for name,path in sorted(files.items()):
 payload=path.read_bytes();entries[name]={'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
if sum(x['bytes'] for x in entries.values())>100*1024**2:raise ValueError('Unexpected artifact growth')
readme='''# Riwaq QLoRA experiment v1 — REJECTED / مرفوض
This is an actual trained research adapter, NOT the production model.
Validation12/44→13/44 with three lost arithmetic answers failed the frozen gate.
Heldout11/46→11/46. Original Ollama model remains active; do not automatically install this adapter.
Base: BF16 reconstruction from existing Q4_K_M Qwen3-4B-Instruct-2507, then NF4.
Use with the exact verified reconstruction manifest/shards only, not original BF16 or an arbitrary GGUF.
Large base weights, wheel cache, environment and user database are NOT included.
They remain in D:/RiwaqRuntime/training-v1; see training/README.md for replay/rollback.
Official Qwen Apache2 license and pinned tokenizer assets are in assets/.
Dataset is project-original authorized procedural material; no private chats or production knowledge.
checkpoint-0064/README.md is PEFT's untouched generated template; this file and TRAINING_RESULTS.md are the actual experiment card.
Optimizer/RNG and state are supplied for compatible resume, not a promise to extend steps or improve quality.
Checksums are integrity checks against accidental changes, not a signature or permission to trust unknown archives.
'''
manifest={'status':'rejected_research_artifact','production_model_unchanged':True,'base_weights_included':False,'private_database_included':False,'files':entries}
archive=output/f'Riwaq-QLoRA-experiment-{a.label}.zip'
with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as z:
 for name,path in sorted(files.items()):z.write(path,'Riwaq-QLoRA/'+name)
 z.writestr('Riwaq-QLoRA/README.md',readme)
 z.writestr('Riwaq-QLoRA/manifest.json',json.dumps(manifest,indent=2))
with zipfile.ZipFile(archive) as z:
 if z.testzip() is not None:raise ValueError('Corrupt archive')
 for name,item in entries.items():
  if hashlib.sha256(z.read('Riwaq-QLoRA/'+name)).hexdigest()!=item['sha256']:raise ValueError('Archive content mismatch')
result={'name':archive.name,'bytes':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'status':manifest['status'],'files':len(entries)+2,'large_base_weights_included':False,'verified':True}
(output/f'manifest-training-artifact-{a.label}.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
