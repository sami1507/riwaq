"""Original, deterministic SFT corpus. No user conversations, scraped text or model answers.
Families are assigned to partitions BEFORE instantiation. Never change a sealed version.
"""
from pathlib import Path
from decimal import Decimal
import hashlib,json,re
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'training/data-v1'
RIGHTS='Original procedural examples authored for the Riwaq task; training explicitly authorized by the user on 2026-11-14. No third-party or private content.'
SYSTEM='أنت رواق. أجب مباشرة وبالعربية الواضحة. ميّز الدليل والافتراض والاقتراح. لا تدّعِ تنفيذًا لم يحدث. التزم بالشكل المطلوب واستخدم [S1] للدليل المرفق فقط.'
TRAIN=['receipt_sum','cash_change','fraction_scale','centimeter_conversion','boolean_all','implication_counterexample','sorted_copy','dictionary_default','safe_mean','mutator_return','source_limit','source_flag','source_missing','version_pick','unit_agreement','unresolved_conflict','decision_pending','decision_done','false_premise','proposal_execution']
VALID=['weighted_invoice','exclusive_or','set_union_copy','two_stage_evidence','unknown_unit','disjoint_time_windows']
TEST=['linear_equation','exactly_two_predicates','range_boundary','joined_version_evidence','different_population','inconsistent_constraints']
assert not(set(TRAIN)&set(VALID) or set(TRAIN)&set(TEST) or set(VALID)&set(TEST))
def schema(expected):
 if isinstance(expected,dict):return {'type':'object','properties':{k:schema(v) for k,v in expected.items()},'required':list(expected),'additionalProperties':False}
 if isinstance(expected,list):return {'type':'array','items':schema(expected[0]) if expected else {'type':'string'},'maxItems':20}
 if expected is None:return {'type':['string','null']}
 if type(expected) is bool:return {'type':'boolean'}
 if type(expected) is int:return {'type':'integer'}
 if type(expected) is float:return {'type':'number'}
 return {'type':'string'}
def case(f,i):
 a=i+3;b=(i*7)%19+2;c=(i*11)%13+1
 source='';category='arithmetic';calc=None
 if f=='receipt_sum':
  prompt=f'احسب مجموع فاتورة فيها {a} و{b} و{c} شيكل. المطلوب قيمة total عددية فقط.';out={'total':a+b+c};calc=f'{a}+{b}+{c}'
 elif f=='cash_change':
  paid=100+i*10;price=a*2+b
  prompt=f'دُفع {paid} مقابل سلعة سعرها {price}. ما الباقي؟ استخدم change عددًا.';out={'change':paid-price};calc=f'{paid}-{price}'
 elif f=='fraction_scale':
  prompt=f'مقدار حجمه {a*4} لترًا، نحتاج ثلاثة أرباعه. أعد liters عددًا.';out={'liters':a*3};calc=f'{a*4}*3/4'
 elif f=='centimeter_conversion':
  prompt=f'طول قطعة {a*100} سنتيمتر. حوّله إلى متر؛ الحقل meters عدد.';out={'meters':a};calc=f'{a*100}/100'
 elif f=='boolean_all':
  flags=[bool(i%2),bool(i%3),bool(i%5)];prompt=f'أعد allowed قيمة منطقية تكون true فقط إذا كانت كل القيم صحيحة: {json.dumps(flags)}.';out={'allowed':all(flags)};category='logic'
 elif f=='implication_counterexample':
  prompt=f'ادعاء: كل عدد يقبل القسمة على {a} يقبل القسمة على {a*2}. هل العدد {a} يدحضه؟ أعد counterexample قيمة منطقية.';out={'counterexample':True};category='logic'
 elif f=='sorted_copy':
  prompt=f'Python: القائمة values_{i} يجب فرز نسخة منها دون تغيير الأصل. أعطني التعبير فقط في expression، دون ادعاء التنفيذ.';out={'expression':f'sorted(values_{i})'};category='code'
 elif f=='dictionary_default':
  prompt=f'Python: أريد قراءة المفتاح "k{i}" من mapping وإرجاع {a} عند غيابه دون تعديل القاموس. أعد expression نصًا.';out={'expression':f'mapping.get("k{i}", {a})'};category='code'
 elif f=='safe_mean':
  prompt=f'لدالة متوسط، القائمة x{i} قد تكون فارغة. أعطني expression يعيد None إن فرغت، وإلا sum/len. لا تنفّذ كودًا.';out={'expression':f'sum(x{i}) / len(x{i}) if x{i} else None'};category='code'
 elif f=='mutator_return':
  prompt=f'في Python: data=[{a},{b}]; result=data.append({c}). ما result؟ أعد value: null وexecuted: false لأن المطلوب تحليل لا تشغيل.';out={'value':None,'executed':False};category='code'
 elif f=='source_limit':
  source=f'مرجع اصطناعي للتدريب، خدمة نخلة-{i}.\nالحد الأقصى للدفعة {a*4} عنصرًا.';prompt=f'ما حد الدفعة في نخلة-{i} وفق الدليل؟ أعد limit عددًا وsources قائمة مراجع.';out={'limit':a*4,'sources':['[S1]']};category='evidence'
 elif f=='source_flag':
  enabled=i%2==0;source=f'مرجع اصطناعي: أداة سطر-{i}.\nميزة الأرشفة '+('مفعلة' if enabled else 'معطلة')+'.';prompt=f'هل الأرشفة مفعلة في سطر-{i}؟ أعد enabled منطقيًا وsources.';out={'enabled':enabled,'sources':['[S1]']};category='evidence'
 elif f=='source_missing':
  source=f'مرجع اصطناعي: مكتبة شرفة-{i} تدعم النصوص.\nلا يذكر هذا المرجع موعد الإصدار.';prompt=f'ما موعد إصدار شرفة-{i} وفق هذا المصدر وحده؟ أعد date:null إذا غاب، وsources.';out={'date':None,'sources':['[S1]']};category='missing'
 elif f=='version_pick':
  source=f'مرجع اصطناعي: مكتبة حجر-{i}.\nالإصدار 1.0: الحد {a}.\nالإصدار 2.0: الحد {b+30}.';prompt=f'مشروعي يستخدم حجر-{i} 1.0. أعد limit وفق إصداري وsources، دون افتراض ترقية.';out={'limit':a,'sources':['[S1]']};category='version'
 elif f=='unit_agreement':
  prompt=f'قياسان دقيقان للقطعة نفسها في الوقت نفسه: {a} متر و{a*100} سنتيمتر. هل يتعارضان؟ أعد conflict منطقيًا.';out={'conflict':False};category='conflict'
 elif f=='unresolved_conflict':
  source=f'سجلان اصطناعيان عن الكيان نفسه والإصدار والوقت والوحدة نفسها.\nالمصدر أ: السعة {a} لترًا.\nالمصدر ب: السعة {a+2} لترًا.\nلا توجد وسيلة ترجيح هنا.';prompt='هل توجد قيمتان متعارضتان؟ أعد conflict منطقيًا، chosen:null عند غياب الترجيح، وsources.';out={'conflict':True,'chosen':None,'sources':['[S1]']};category='conflict'
 elif f=='decision_pending':
  prompt=f'في المشروع مسار-{i} اقترح المستخدم اعتماد SQLite لكنه لم يؤكد الاعتماد. أعد status بأحد الرموز proposal/adopted/implemented وفق الحدث، وimplemented منطقيًا.';out={'status':'proposal','implemented':False};category='context'
 elif f=='decision_done':
  prompt=f'في المشروع دليل-{i} اعتمد المستخدم قرارًا، ولم يسجل دليل تنفيذ أو تأكيد إتمام. أعد approved وimplemented منطقيين.';out={'approved':True,'implemented':False};category='context'
 elif f=='false_premise':
  prompt=f'يقول المستخدم إن {a}×{b} يساوي {a*b+1}. صحح القيمة في product، واجعل premise_correct منطقيًا. أعد JSON فقط.';out={'product':a*b,'premise_correct':False};calc=f'{a}*{b}'
 elif f=='proposal_execution':
  prompt=f'اقترحت خطة لاختبار الخدمة رقم {i}، ولم تشغل أي أداة أو تجربة. أعد kind برمز proposal أو experiment، وexecuted منطقيًا، وmeasurement عددًا أو null بحسب ما حدث.';out={'kind':'proposal','executed':False,'measurement':None};category='execution'
 elif f=='weighted_invoice':
  prompt=f'طلبية فيها {a} قطع بسعر {b} للقطعة و{c} قطع بسعر 7. أعد total وcount عددين. يمكنك فحص التعبير `{a}*{b}+{c}*7` للمجموع.';out={'total':a*b+c*7,'count':a+c};calc=f'{a}*{b}+{c}*7'
 elif f=='exclusive_or':
  x=i%2==0;y=i%3==0;prompt=f'شرط الاختيار: واحدة فقط من {str(x).lower()} و{str(y).lower()} صحيحة. أعد accept منطقيًا.';out={'accept':x!=y};category='logic'
 elif f=='set_union_copy':
  prompt=f'Python: أعد expression لاتحاد المجموعتين left_{i} وright_{i} دون تعديلهما، باستخدام العامل |.';out={'expression':f'left_{i} | right_{i}'};category='code'
 elif f=='two_stage_evidence':
  source=f'مرجع اصطناعي: خدمة عبور-{i}.\nالخطة المعتمدة: فضية.\nفضية: {a*6} طلبًا.\nذهبية: {b*10} طلبًا.';prompt=f'ما حد الخطة المعتمدة لعبور-{i}؟ أعد plan وlimit وsources.';out={'plan':'فضية','limit':a*6,'sources':['[S1]']};category='evidence'
 elif f=='unknown_unit':
  prompt=f'رصدان للخاصية نفسها: القيمة {a} والقيمة {b+30}، لكن وحدة الرصد الثاني مجهولة. هل تكفي القيم وحدها لإثبات التعارض؟ أعد comparable منطقيًا وconflict منطقيًا أو null عندما لا تكفي المعطيات.';out={'comparable':False,'conflict':None};category='missing'
 elif f=='disjoint_time_windows':
  source=f'سجل اصطناعي: جهاز فلك-{i}.\nعام 2023: السعة {a} لترًا.\nعام 2025: السعة {a+4} لترًا.\nكل قيمة تصف عامها فقط.';prompt=f'هل اختلاف قيمتي فلك-{i} يثبت تعارضًا في الزمن نفسه؟ أعد same_time وconflict منطقيين وsources قائمة مراجع.';out={'same_time':False,'conflict':False,'sources':['[S1]']};category='conflict'
 elif f=='linear_equation':
  prompt=f'حل المعادلة {a}*x + {b} = {a*c+b}. أعد x عددًا وsubstitution عدد الطرف الأيسر بعد التعويض.';out={'x':c,'substitution':a*c+b};category='arithmetic'
 elif f=='exactly_two_predicates':
  xs=[i%2==0,i%3==0,i%4==0];prompt=f'المطلوب صحة شرط وجود قيمتين صحيحتين بالضبط في {json.dumps(xs)}. أعد accepted منطقيًا وtrue_count عددًا.';out={'accepted':sum(xs)==2,'true_count':sum(xs)};category='logic'
 elif f=='range_boundary':
  prompt=f'Python: نريد المرور على الأعداد الصحيحة من {a} إلى {a+b} شاملًا الحدين. أعد expression باستعمال range بخطوة افتراضية، وexecuted:false.';out={'expression':f'range({a}, {a+b+1})','executed':False};category='code'
 elif f=='joined_version_evidence':
  source=f'مرجع اصطناعي: برنامج قوس-{i}.\nالمشروع الفعلي يستخدم الإصدار 3.2.\n3.2: الخطة وردية، حدها {a*3}.\n4.0: الخطة زرقاء، حدها {b*8}.';prompt=f'لقوس-{i} أعد version وplan وlimit من إعداد المشروع الفعلي، وsources.';out={'version':'3.2','plan':'وردية','limit':a*3,'sources':['[S1]']};category='version'
 elif f=='different_population':
  source=f'مرجع اصطناعي: مدرسة ألف: عدد الطلبة {a*10}.\nمدرسة باء: عدد الطلبة {b*10}.\nكل تعداد خاص بمدرسته في اليوم نفسه.';prompt='هل الرقمان متعارضان بشأن الكيان نفسه؟ أعد same_entity وconflict منطقيين وsources قائمة مراجع.';out={'same_entity':False,'conflict':False,'sources':['[S1]']};category='conflict'
 elif f=='inconsistent_constraints':
  prompt=f'أريد عددًا صحيحًا أكبر من {a+5} وأقل من {a} في الوقت نفسه. أعد feasible منطقيًا وvalue:null إن تعذر.';out={'feasible':False,'value':None};category='missing'
 else:raise AssertionError(f)
 contract=schema(out)
 # Unknown fields must allow uncertainty; this schema never encodes the correct value.
 for k,v in out.items():
  if v is None:contract['properties'][k]={'type':['string','number','boolean','null']}
 prompt+='\nأعد كائن JSON فقط بالمفاتيح والأنواع المطلوبة دون شرح إضافي.'
 return prompt,out,source,category,calc,contract

def make_rows():
 rows=[]
 for split,families,count in [('train',TRAIN,25),('validation',VALID,8),('test',TEST,8)]:
  for family in families:
   for i in range(count):
    prompt,target,source,category,calculation,contract=case(family,i)
    uid=f'{split}-{family}-{i:03d}'
    source_name=f'{family}-{i:03d}.txt'
    user=prompt+(f'\n<untrusted_source_excerpts>\n[S1] {source_name} lines=1-{len(source.splitlines())}\n{source}\n</untrusted_source_excerpts>' if source else '')
    response=json.dumps(target,ensure_ascii=False,separators=(',',':'))
    rows.append({'id':uid,'split':split,'family':family,'source_group':family,'license':'project-original','training_permission':True,'permission_reference':'training/DATA_RIGHTS.md','redaction_reviewed':True,'generated':True,'generator':'deterministic authored templates, no language model','language':'ar','category':category,'prompt':prompt,'source':source,'source_name':source_name,'source_sha256':hashlib.sha256(source.encode()).hexdigest() if source else None,'output_contract':{'schema':contract,**({'citation_field':'sources'} if source else {})},'calculation':calculation,'expected':target,'verification':{'method':'trusted deterministic generator + independent verifier','human_review':False,'scope':'exact synthetic task; not real-world truth'},'messages':[{'role':'system','content':SYSTEM},{'role':'user','content':user},{'role':'assistant','content':response}]})
 return rows

def main():
 if OUT.exists():raise SystemExit('Sealed data directory exists; refusing overwrite')
 rows=make_rows();OUT.mkdir(parents=True)
 manifest={'version':'sft-original-v1','created':'2026-11-14','rights':RIGHTS,'private_data':False,'public_knowledge_imported':False,'generated_by_model':False,'human_reviewed':False,'scope':'Targeted Arabic structured responses and source/decision boundaries; 32 authored task families, not broad knowledge training.','splits':{},'acceptance':{'code':'validation exact gain>=3/48, no more than1 lost correct; restored100%; p95<=90s','adapter':'validation exact gain>=2/48 and no more than1 lost correct, no observed nonstructured retention regression; otherwise retain base','test':'single final comparison after selection; no tuning on test results'},'family_assignment':{'train':TRAIN,'validation':VALID,'test':TEST}}
 for split in ['train','validation','test']:
  selected=[x for x in rows if x['split']==split];path=OUT/(split+'.jsonl')
  path.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in selected),encoding='utf-8')
  manifest['splits'][split]={'count':len(selected),'families':len({x['family'] for x in selected}),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
 manifest['generator_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
 (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(manifest,ensure_ascii=True))
if __name__=='__main__':main()
