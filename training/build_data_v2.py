"""Deduplicated revision; v1 is retained as rejected data audit, never silently edited."""
from pathlib import Path
import hashlib,json
import build_data_v1 as base
OUT=base.ROOT/'training/data-v2'
def main():
 if OUT.exists():raise SystemExit('Sealed v2 exists')
 rows=base.make_rows();seen={};kept=[];removed=[]
 for row in rows:
  key=hashlib.sha256(row['messages'][1]['content'].encode()).hexdigest()
  if key in seen:
   assert seen[key]['split']==row['split'],'Cross-split duplicate requires redesign'
   removed.append({'removed':row['id'],'kept':seen[key]['id']});continue
  seen[key]=row;kept.append(row)
 for i in range(25):
  total=31+i*7;capacity=3+(i%8);expected={'boxes':(total+capacity-1)//capacity}
  prompt=f'نريد تعبئة {total} قطعة في علب، تتسع كل علبة لعدد أقصاه {capacity} قطع. ما أقل عدد علب يشمل جميع القطع؟ أعد كائن JSON فقط، boxes عدد صحيح.'
  row={'id':f'train-packing_ceiling-{i:03d}','split':'train','family':'packing_ceiling','source_group':'packing_ceiling','license':'project-original','training_permission':True,'permission_reference':'training/DATA_RIGHTS.md','redaction_reviewed':True,'generated':True,'generator':'deterministic authored templates, no language model','language':'ar','category':'arithmetic','prompt':prompt,'source':'','source_name':'','source_sha256':None,'output_contract':{'schema':base.schema(expected)},'calculation':None,'expected':expected,'verification':{'method':'integer minimal capacity proof','human_review':False,'scope':'exact synthetic task'},'messages':[{'role':'system','content':base.SYSTEM},{'role':'user','content':prompt},{'role':'assistant','content':json.dumps(expected,separators=(',',':'))}]}
  kept.append(row)
 OUT.mkdir();manifest={'version':'sft-original-v2','created':'2026-11-14','rights':base.RIGHTS,'private_data':False,'public_knowledge_imported':False,'generated_by_model':False,'human_reviewed':False,'scope':'Targeted Arabic structured answers; procedural variants are not independent knowledge discoveries.','replaces':'v1 rejected by independent duplicate-prompt check; v1 reports preserved as diagnostics','duplicates_removed':removed,'splits':{},'family_assignment':{},'acceptance':{'code':'validation exact gain>=3; lost correct<=1; restored100%; p95<=90s','adapter':'validation exact gain>=2; lost correct<=1; no observed nonstructured retention regression; otherwise keep base','test':'single comparison after validation selection, never tune on test results'}}
 for split in ['train','validation','test']:
  selected=[x for x in kept if x['split']==split];path=OUT/(split+'.jsonl');path.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in selected),encoding='utf-8')
  manifest['splits'][split]={'count':len(selected),'families':len({x['family'] for x in selected}),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()};manifest['family_assignment'][split]=sorted({x['family'] for x in selected})
 manifest['generator_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();manifest['parent_generator_sha256']=hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest()
 (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(manifest['splits']))
if __name__=='__main__':main()
