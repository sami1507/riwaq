"""Metadata-only schema correction; all prompts/targets and split assignments preserved."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'training/data-v2';OUT=ROOT/'training/data-v3'
NULL_FIELDS={'source_missing':{'date':['string','null']},'unresolved_conflict':{'chosen':['number','null']},'proposal_execution':{'measurement':['number','null']},'unknown_unit':{'conflict':['boolean','null']},'inconsistent_constraints':{'value':['number','null']}}
def main():
 if OUT.exists():raise SystemExit('Sealed v3 exists')
 parent=json.loads((SOURCE/'manifest.json').read_text());OUT.mkdir();manifest={**parent,'version':'sft-original-v3','replaces':'v2 schemas for unknown values were too permissive; model messages and gold answers unchanged','parent_manifest_sha256':hashlib.sha256((SOURCE/'manifest.json').read_bytes()).hexdigest(),'splits':{},'model_messages_unchanged':True,'schema_change':'Declared nullable field types follow each user-requested type; no expected values encoded'}
 for split in ['train','validation','test']:
  path=SOURCE/(split+'.jsonl');assert hashlib.sha256(path.read_bytes()).hexdigest()==parent['splits'][split]['sha256']
  rows=[json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()]
  for row in rows:
   for field,types in NULL_FIELDS.get(row['family'],{}).items():row['output_contract']['schema']['properties'][field]={'type':types}
  target=OUT/(split+'.jsonl');target.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows),encoding='utf-8')
  manifest['splits'][split]={**parent['splits'][split],'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
 manifest['generator_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(manifest['splits']))
if __name__=='__main__':main()
