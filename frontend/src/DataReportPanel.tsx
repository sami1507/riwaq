import {useEffect,useRef,useState} from 'react';
import {api} from './api';
import {skillsApi,SkillRequestError} from './skills-api';
import type {SkillFile,SkillRequest,SkillRun} from './skills-api';

const object=(v:unknown):v is Record<string,unknown>=>Boolean(v)&&typeof v==='object'&&!Array.isArray(v);
type Props={projectId:string;ar:boolean;selected:SkillRun|null;busy:boolean;onBusy:(value:boolean)=>void;onRun:(value:SkillRun)=>void};

export default function DataReportPanel({projectId,ar,selected,busy,onBusy,onRun}:Props){
 const t=(a:string,b:string)=>ar?a:b;
 const [files,setFiles]=useState<SkillFile[]>([]),[source,setSource]=useState('');
 const [title,setTitle]=useState(''),[category,setCategory]=useState(''),[valueColumn,setValueColumn]=useState(''),[unit,setUnit]=useState('');
 const [language,setLanguage]=useState<'ar'|'en'>(ar?'ar':'en');
 const [cell,setCell]=useState(''),[value,setValue]=useState(''),[valueType,setValueType]=useState<'number'|'text'|'missing'>('number');
 const [feedback,setFeedback]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[uncertain,setUncertain]=useState(false);
 const pending=useRef<SkillRequest|null>(null),abort=useRef<AbortController|null>(null),alive=useRef(true),running=useRef(false);
 const feedbackKey=useRef(crypto.randomUUID());
 const record=selected?.result?.structured?.['exploratory-analysis'];
 const output=object(record)&&object(record.output)?record.output:null;
 const table=output&&object(output.table)&&Array.isArray(output.table.columns)&&Array.isArray(output.table.rows)?output.table:null;
 const columns=table?(table.columns as unknown[]).filter((x):x is string=>typeof x==='string'):[];
 const rows=table?(table.rows as unknown[]).filter(Array.isArray):[];
 const indices=/^([A-L])(\d+)$/.exec(cell);
 const before=indices?rows[Number(indices[2])-2]?.[indices[1].charCodeAt(0)-65]:undefined;
 const revision=selected?.result?.data_revision_available;
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;abort.current?.abort();};},[]);
 useEffect(()=>{const controller=new AbortController();skillsApi.files(projectId,controller.signal).then(all=>{
  if(!controller.signal.aborted)setFiles(all.filter(file=>file.name.endsWith('.xlsx')||file.name.endsWith('.csv')));
 }).catch(e=>{if(!controller.signal.aborted)setError(String(e));});return()=>controller.abort();},[projectId,selected?.id,selected?.status]);
 useEffect(()=>{setCell('');setValue('');setFeedback(false);feedbackKey.current=crypto.randomUUID();},[selected?.id]);
 async function start(request:SkillRequest){
  pending.current=request;setUncertain(false);const controller=new AbortController();abort.current=controller;
  try{const run=await skillsApi.start(projectId,request,controller.signal);if(alive.current&&!controller.signal.aborted){pending.current=null;onRun(run);setNotice(t('سُجلت المهمة؛ انتظر نتيجة الخادم وملفاته المتحققة.','Task recorded. Wait for the server result and checked files.'));}}
  catch(e){if(alive.current&&!controller.signal.aborted){if(e instanceof SkillRequestError){pending.current=null;}else setUncertain(true);setError(String(e));}}
 }
 async function perform(work:()=>Promise<void>){if(busy||running.current)return;running.current=true;onBusy(true);setError('');setNotice('');try{await work();}catch(e){if(alive.current)setError(String(e));}finally{running.current=false;if(alive.current)onBusy(false);}}
 function create(){void perform(async()=>{
  const chosen=files.find(file=>file.id===source);
  if(!chosen||!title.trim()||!category.trim()||!valueColumn.trim())throw Error(t('اختر المصدر واكتب العنوان وعمود الفئة والقيمة.','Choose a source, title, category column and value column.'));
  if(!unit.trim())throw Error(t('حدد وحدة القيم للرسم؛ لم تبدأ أي أداة ولم تُفترض وحدة.','Specify the chart value unit. No tool has started and no unit was assumed.'));
  await start({request:t('أنشئ التحليل والملخص والجدول والرسم والتقرير من المصدر المحدد','Create analysis, summary, table, chart and report from the selected source'),requestedSkills:['exploratory-analysis'],idempotencyKey:crypto.randomUUID(),inputs:{
   sourceRefs:[{kind:chosen.kind,id:chosen.id,version:chosen.version,sha256:chosen.sha256}],
   dataOperation:{operation:'explore',sourceId:chosen.id,source_format:chosen.name.endsWith('.csv')?'csv':'xlsx',columns:[valueColumn.trim()]},
   dataReport:{title:title.trim(),language,chart:{category:category.trim(),value:valueColumn.trim(),unit}}
  }});
 });}
 function correct(recompute=false){void perform(async()=>{
  if(!selected||!revision?.available)throw Error(t('اختر تقرير بيانات محفوظًا.','Choose a saved data report.'));
  let replacement:unknown=null;
  if(!recompute){
   if(!cell||before===undefined)throw Error(t('اختر خلية من الجدول المحفوظ.','Choose a cell from the saved table.'));
   replacement=valueType==='missing'?null:valueType==='text'?value:Number(value);
   if(valueType==='number'&&(!value.trim()||!Number.isFinite(replacement as number)))throw Error(t('أدخل عددًا صالحًا أو اختر قيمة مفقودة.','Enter a valid number or choose a missing value.'));
  }
  const spec:NonNullable<NonNullable<SkillRequest['inputs']>['dataRevision']>={expectedResultHash:revision.result_hash,edits:recompute?[]:[{cell,expected:before,value:replacement}]};
  if(feedback){
   const base=`/projects/${encodeURIComponent(projectId)}/learning`;
   const target=await api<{kind:'tool';id:string;version:string}>(`${base}/targets/tool/${encodeURIComponent(selected.id)}`);
   const saved=await api<{id:string;version:number}>(`${base}/feedback`,{method:'POST',body:JSON.stringify({idempotency_key:feedbackKey.current,target:{kind:target.kind,id:target.id,version:target.version},signal:'correction',dimensions:['input','correctness'],reason:'User-declared correction and dependent data output regeneration',scope:'project',purposes:['current_task']})});
   spec.feedbackId=saved.id;spec.expectedFeedbackVersion=saved.version;
  }
  if(!alive.current)return;
  await start({request:t('صحح البيانات وأعد حساب جميع المخرجات التابعة','Correct data and recompute all dependent outputs'),previousRunId:selected.id,idempotencyKey:crypto.randomUUID(),inputs:{dataRevision:spec}});
 });}
 return <details className="skills-limits" data-testid="data-report-panel">
  <summary>{t('تقرير بيانات مترابط وتصحيح نتائجه','Linked data report and corrections')}</summary>
  <p className="skills-hint">{t('ينشئ تحليلًا وجدولًا ورسمًا في مصنف وتقرير Word. يحفظ الصفر منفصلًا عن المفقود، ويحتفظ بالإصدارات السابقة عند التصحيح. حتى100صف و12عمودًا، بقيم رقمية صريحة.','Creates analysis, a table, an editable chart workbook and Word report. Zero remains distinct from missing. Corrections retain prior versions. Up to 100 rows and 12 columns, with explicit numeric values.')}</p>
  {error&&<p role="alert" className="skills-error">{error}</p>}{notice&&<p role="status">{notice}</p>}
  <fieldset disabled={busy||uncertain}>
   <label className="field-label">{t('مصدر التقرير','Report source')}<select className="input" aria-label={t('مصدر التقرير','Report source')} value={source} onChange={e=>setSource(e.target.value)}><option value="">{t('اختر مصنفًا أو CSV محفوظًا','Choose a saved workbook or CSV')}</option>{files.map(file=><option key={file.id} value={file.id}>{file.name} · v{file.version} · {file.id.slice(0,8)}</option>)}</select></label>
   <label className="field-label">{t('عنوان تقرير البيانات','Data report title')}<input className="input" value={title} maxLength={180} onChange={e=>setTitle(e.target.value)}/></label>
   <label className="field-label">{t('عمود الفئات','Category column')}<input className="input" value={category} maxLength={100} onChange={e=>setCategory(e.target.value)}/></label>
   <label className="field-label">{t('عمود القيم العددية','Numeric value column')}<input className="input" value={valueColumn} maxLength={100} onChange={e=>setValueColumn(e.target.value)}/></label>
   <label className="field-label">{t('الوحدة','Unit')}<input className="input" value={unit} maxLength={100} onChange={e=>setUnit(e.target.value)}/></label>
   <label className="field-label">{t('لغة تقرير البيانات','Data report language')}<select className="input" aria-label={t('لغة تقرير البيانات','Data report language')} value={language} onChange={e=>setLanguage(e.target.value as 'ar'|'en')}><option value="ar">العربية</option><option value="en">English</option></select></label>
   <button type="button" className="button" onClick={create}>{t('إنشاء حزمة التقرير','Create linked report')}</button>
   {revision?.available&&table&&<section aria-label={t('تصحيح التقرير المحفوظ','Correct saved report')}>
    <h4>{t('تصحيح التقرير المحدد','Correct selected report')}</h4>
    <label className="field-label">{t('الخلية المراد تصحيحها','Cell to correct')}<select className="input" aria-label={t('الخلية المراد تصحيحها','Cell to correct')} value={cell} onChange={e=>{setCell(e.target.value);setValue('');}}><option value="">{t('اختر خلية','Choose a cell')}</option>{rows.flatMap((row,i)=>columns.map((name,j)=><option key={`${i}-${j}`} value={`${String.fromCharCode(65+j)}${i+2}`}>{String.fromCharCode(65+j)}{i+2} · {name} · {row[j]===null?t('ناقص','Missing'):String(row[j])}</option>))}</select></label>
    <p>{t('القيمة المحفوظة: ','Saved value: ')}<output>{before===undefined?'—':before===null?t('ناقص','Missing'):String(before)}</output></p>
    <label className="field-label">{t('نوع القيمة الجديدة','New value type')}<select className="input" aria-label={t('نوع القيمة الجديدة','New value type')} value={valueType} onChange={e=>setValueType(e.target.value as typeof valueType)}><option value="number">{t('عدد','Number')}</option><option value="text">{t('نص','Text')}</option><option value="missing">{t('مفقود','Missing')}</option></select></label>
    {valueType!=='missing'&&<label className="field-label">{t('القيمة المصححة','Corrected value')}<input className="input" value={value} maxLength={1000} inputMode={valueType==='number'?'decimal':'text'} onChange={e=>setValue(e.target.value)}/></label>}
    <label className="skills-check"><input type="checkbox" checked={feedback} onChange={e=>setFeedback(e.target.checked)}/>{t('اربط التصحيح بملاحظة لهذه المهمة فقط','Link correction to feedback for this task only')}</label>
    <p className="skills-hint">{t('التعديل تصريح منك؛ التحقق يثبت إعادة الحساب وحفظ المخرجات، ولا يثبت صحة قيمة المصدر أو ينشئ درسًا عامًا.','The edit is your declaration. Verification covers recalculation and saved outputs; it does not authenticate source facts or create a global lesson.')}</p>
    <div className="skills-card-actions"><button type="button" className="button" onClick={()=>correct()}>{t('تصحيح وإعادة بناء النتائج','Correct and rebuild outputs')}</button><button type="button" className="button subtle" onClick={()=>correct(true)}>{t('إعادة حساب الجدول المحفوظ','Recompute saved table')}</button></div>
   </section>}
  </fieldset>
  {uncertain&&<div role="status"><p>{t('لم يتأكد وصول الرد؛ استعد الطلب نفسه دون إنشاء عملية جديدة.','The response was not confirmed. Recover the same request without creating another operation.')}</p><button type="button" className="button subtle" disabled={busy} onClick={()=>void perform(async()=>{if(pending.current)await start(pending.current);})}>{t('استعادة الطلب نفسه','Recover same request')}</button></div>}
 </details>;
}
