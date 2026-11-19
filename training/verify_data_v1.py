"""Independent checks against prompt/source facts; never executes generated Python."""
import ast,hashlib,json,re,sys
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'training/data-v3'
def nums(text):return [int(x) for x in re.findall(r'(?<![A-Za-z0-9_.])\d+(?![0-9]|\.\d)',text)]
def parsed_expression(text):return ast.parse(text,mode='eval').body

def strict_same(a,b):
 if type(a) is not type(b):return False
 if isinstance(a,dict):return set(a)==set(b) and all(strict_same(a[k],b[k]) for k in a)
 if isinstance(a,list):return len(a)==len(b) and all(strict_same(x,y) for x,y in zip(a,b))
 return a==b

def verify(row):
 p=row['prompt'];s=row['source'];a=row['expected'];f=row['family'];n=nums(p)
 assert strict_same(json.loads(row['messages'][-1]['content']),a)
 sys.path.insert(0,str(ROOT))
 from backend.output_contracts import OutputContract
 contract=OutputContract.model_validate(row['output_contract']);assert contract.check(row['messages'][-1]['content'],1 if s else 0)['schema_valid']
 expected_user=p+(f'\n<untrusted_source_excerpts>\n[S1] {row["source_name"]} lines=1-{len(s.splitlines())}\n{s}\n</untrusted_source_excerpts>' if s else '')
 assert row['messages'][1]['content']==expected_user
 assert [m['role'] for m in row['messages']]==['system','user','assistant']
 assert row['training_permission'] is True and row['redaction_reviewed'] is True
 assert row['license']=='project-original' and row['permission_reference']=='training/DATA_RIGHTS.md'
 assert row['source_group']==f
 if s:
  assert row['source_sha256']==hashlib.sha256(s.encode()).hexdigest()
  assert a['sources']==['[S1]'] and s in row['messages'][1]['content']
 if f=='packing_ceiling':assert (a['boxes']-1)*n[1]<n[0]<=a['boxes']*n[1]
 elif f=='receipt_sum':assert a['total']==sum(n[:3])
 elif f=='cash_change':assert a['change']==n[0]-n[1]
 elif f=='fraction_scale':assert Decimal(a['liters'])==Decimal(n[0])*Decimal('0.75')
 elif f=='centimeter_conversion':assert a['meters']*100==n[0]
 elif f=='boolean_all':assert a['allowed']==(False not in json.loads(re.search(r'\[[^\]]+\]',p)[0]))
 elif f=='implication_counterexample':assert a['counterexample']==(n[2]%n[0]==0 and n[2]%n[1]!=0)
 elif f=='sorted_copy':
  node=parsed_expression(a['expression']);assert isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='sorted' and len(node.args)==1 and node.args[0].id==re.search(r'values_\d+',p)[0] and not node.keywords
 elif f=='dictionary_default':
  node=parsed_expression(a['expression']);assert isinstance(node,ast.Call) and node.func.attr=='get' and node.func.value.id=='mapping' and node.args[0].value==re.search(r'"(k\d+)"',p)[1] and node.args[1].value==n[0]
 elif f=='safe_mean':
  node=parsed_expression(a['expression']);name=re.search(r'x\d+',p)[0];assert isinstance(node,ast.IfExp) and node.test.id==name and isinstance(node.orelse,ast.Constant) and node.orelse.value is None
  assert isinstance(node.body,ast.BinOp) and isinstance(node.body.op,ast.Div) and node.body.left.func.id=='sum' and node.body.right.func.id=='len' and node.body.left.args[0].id==node.body.right.args[0].id==name and len(node.body.left.args)==len(node.body.right.args)==1 and not node.body.left.keywords and not node.body.right.keywords
 elif f=='mutator_return':assert a=={'value':None,'executed':False} and '.append(' in p
 elif f=='source_limit':assert a['limit']==int(re.search(r'للدفعة (\d+)',s)[1])
 elif f=='source_flag':assert a['enabled']==('مفعلة.' in s)
 elif f=='source_missing':assert a['date'] is None and 'لا يذكر' in s
 elif f=='version_pick':assert a['limit']==int(re.search(r'الإصدار 1\.0: الحد (\d+)',s)[1])
 elif f=='unit_agreement':assert a['conflict']==(Decimal(n[0])!=Decimal(n[1])/100)
 elif f=='unresolved_conflict':
  values=[int(x) for x in re.findall(r'السعة (\d+)',s)];assert a['conflict']==(len(set(values))>1) and a['chosen'] is None and 'لا توجد وسيلة ترجيح' in s
 elif f=='decision_pending':assert a=={'status':'proposal','implemented':False} and 'لم يؤكد الاعتماد' in p
 elif f=='decision_done':assert a=={'approved':True,'implemented':False} and 'اعتمد المستخدم' in p and 'لم يسجل دليل تنفيذ' in p
 elif f=='false_premise':assert a['product']==n[0]*n[1] and a['premise_correct']==(n[0]*n[1]==n[2])
 elif f=='proposal_execution':assert a=={'kind':'proposal','executed':False,'measurement':None} and 'لم تشغل أي أداة' in p
 elif f=='weighted_invoice':
  counts=re.search(r'فيها (\d+) قطع بسعر (\d+).*و(\d+) قطع بسعر (\d+)',p);x,y,z,w=map(int,counts.groups());assert a=={'total':sum([y]*x+[w]*z),'count':len([0]*x+[0]*z)}
 elif f=='exclusive_or':
  bits=re.findall(r'(?<![A-Za-z])(true|false)(?![A-Za-z])',p);assert a['accept']==(bits.count('true')==1)
 elif f=='set_union_copy':
  node=parsed_expression(a['expression']);assert isinstance(node,ast.BinOp) and isinstance(node.op,ast.BitOr) and node.left.id==re.search(r'left_\d+',p)[0] and node.right.id==re.search(r'right_\d+',p)[0]
 elif f=='two_stage_evidence':
  plan=re.search(r'الخطة المعتمدة: ([^.]+)',s)[1];limit=int(re.search(re.escape(plan)+r': (\d+) طلب',s)[1]);assert a['plan']==plan and a['limit']==limit
 elif f=='unknown_unit':assert a=={'comparable':False,'conflict':None} and 'مجهولة' in p
 elif f=='disjoint_time_windows':assert a['same_time'] is False and a['conflict'] is False and 'عام 2023' in s and 'عام 2025' in s and 'كل قيمة تصف عامها فقط' in s
 elif f=='linear_equation':
  parts=re.search(r'(\d+)\*x \+ (\d+) = (\d+)',p);coef,offset,total=map(int,parts.groups());assert a['x']*coef+offset==total==a['substitution']
 elif f=='exactly_two_predicates':
  bits=json.loads(re.search(r'\[[^\]]+\]',p)[0]);count=len([b for b in bits if b is True]);assert a=={'accepted':count==2,'true_count':count}
 elif f=='range_boundary':
  limits=re.search(r'من (\d+) إلى (\d+)',p);first,last=map(int,limits.groups());node=parsed_expression(a['expression']);assert isinstance(node,ast.Call) and node.func.id=='range' and len(node.args)==2 and [x.value for x in node.args]==[first,last+1] and a['executed'] is False
 elif f=='joined_version_evidence':
  version=re.search(r'يستخدم الإصدار ([\d.]+)\.',s)[1];line=next(x for x in s.splitlines() if x.startswith(version+':'));m=re.search(r'الخطة ([^،]+)، حدها (\d+)',line);assert a['version']==version and a['plan']==m[1] and a['limit']==int(m[2])
 elif f=='different_population':assert a['same_entity'] is False and a['conflict'] is False and 'كل تعداد خاص بمدرسته' in s
 elif f=='inconsistent_constraints':assert n[0]>n[1] and a=={'feasible':False,'value':None}
 else:raise AssertionError('Unknown family')

def main():
 manifest=json.loads((DATA/'manifest.json').read_text(encoding='utf-8'));seen={};families={};normalized={};counts={};errors=[]
 for split in ['train','validation','test']:
  file=DATA/(split+'.jsonl');assert hashlib.sha256(file.read_bytes()).hexdigest()==manifest['splits'][split]['sha256']
  rows=[json.loads(line) for line in file.read_text(encoding='utf-8').splitlines()];counts[split]=len(rows)
  for row in rows:
   try:verify(row)
   except Exception as error:errors.append({'id':row['id'],'type':type(error).__name__,'message':str(error)[:100]})
   full=hashlib.sha256(row['messages'][1]['content'].encode()).hexdigest();assert full not in seen,'duplicate prompt';seen[full]=split
   assert families.setdefault(row['family'],split)==split,'family leakage'
   # Replacing numbers and collapsing whitespace detects near-identical numerical variants across splits.
   key=re.sub(r'\s+',' ',re.sub(r'\d+(?:\.\d+)?','#',row['messages'][1]['content'])).strip()
   assert normalized.setdefault(key,split)==split,'normalized prompt leakage'
 report={'version':'data-verification-v3','counts':counts,'families':len(families),'passed':sum(counts.values())-len(errors),'errors':errors,'private_content_used':False,'human_review':False,'scope':'All exact target values checked against prompt/source facts or AST shape without executing generated code; cross-split exact/numeric-normalized/family checks. Semantic paraphrase detection remains engineering review, not a universal leakage guarantee.'}
 (ROOT/'reports/training-v3-data.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=True))
 if errors:raise SystemExit(1)
if __name__=='__main__':main()
