import {forwardRef,useEffect,useImperativeHandle,useState} from 'react';
import {Plus,Trash2,RefreshCw} from 'lucide-react';
import {documentsApi} from './document-api';
import {skillsApi} from './skills-api';
import type {SkillFile,SkillFileRef,SkillRequest,SkillRun,SkillWebSource} from './skills-api';

type Inputs=NonNullable<SkillRequest['inputs']>;
export type TransformHandle={build:()=>{inputs:Inputs;requestedSkills:string[]}};
type Props={projectId:string;ar:boolean;selected:SkillRun|null;requestedSkill?:string;onOperationChange?:()=>void};
type Source=SkillFileRef&{name:string;pages?:number;revision?:number;folder?:string;latest_version?:number};
type Pair={name:string;value:string};
type Operation={key:string;slug:string;ar:string;en:string;group:'document'|'data'|'files'|'web'|'synthesis'};
export const transformOperations:Operation[]=[
 ['synthesis','cross-document-synthesis','دمج أدلة الوثائق','Combine document evidence','synthesis'],
 ['pdf-create','pdf-creation','إنشاء PDF','Create PDF','document'],['pdf-form-fill','pdf-form-filling','تعبئة نموذج PDF','Fill PDF form','document'],
 ['pdf-merge','pdf-merge-split','دمج صفحات PDF','Merge PDF pages','document'],['pdf-split','pdf-merge-split','استخراج صفحات PDF','Extract PDF pages','document'],
 ['pdf-redact','pdf-redaction','تنقيح مناطق PDF','Redact PDF regions','document'],['docx-edit','docx-editing','تعديل فقرة Word','Edit Word paragraph','document'],
 ['docx-compare','document-comparison','مقارنة مستندَي Word','Compare Word documents','document'],['template-fill','template-population','تعبئة قالب','Populate template','document'],
 ['convert','file-conversion','تحويل صيغة ملف','Convert file format','document'],['table-extract','document-table-extraction','استخراج جدول','Extract table','document'],
 ['folders','folder-organization','تنظيم ملفات النتائج','Organize output files','files'],['versions','artifact-versioning','إصدارات ملف نتيجة','Output file versions','files'],
 ['read','spreadsheet-reading','قراءة مصنف','Read workbook','data'],['formulas','formula-debugging','تعديل صيغة محددة','Edit a specific formula','data'],
 ['clean','spreadsheet-cleaning','تنظيف عمود','Clean a column','data'],['csv','csv-import-export','تحويل CSV','Convert CSV','data'],
 ['join','table-joins','ربط جدولين','Join two tables','data'],['aggregate','pivot-analysis','تجميع وحساب','Group and aggregate','data'],
 ['chart','spreadsheet-charts','رسم في مصنف','Workbook chart','data'],['profile','data-profiling','فحص جودة البيانات','Profile data quality','data'],
 ['explore','exploratory-analysis','تحليل استكشافي','Explore data','data'],['stats','descriptive-statistics','إحصاء وصفي','Descriptive statistics','data'],
 ['browser-navigation','browser-navigation','قراءة موقع مسجّل','Read registered site','web'],
 ['page-content-extraction','page-content-extraction','استخراج صفحة مسجّلة','Extract registered page','web'],
 ['web-form-filling','web-form-filling','تجهيز نموذج موقع','Prepare site form','web'],
].map(([key,slug,ar,en,group])=>({key,slug,ar,en,group:group as Operation['group']}));
const object=(v:unknown):v is Record<string,unknown>=>Boolean(v)&&typeof v==='object'&&!Array.isArray(v);
const ref=(item:Source):SkillFileRef=>({kind:item.kind,id:item.id,version:item.version,sha256:item.sha256});
function pairsObject(rows:Pair[]){const value:Record<string,string>=Object.create(null);for(const row of rows){if(!row.name.trim())throw Error('Each field needs a name / كل حقل يحتاج اسمًا');if(Object.hasOwn(value,row.name))throw Error('Duplicate field / حقل مكرر');value[row.name]=row.value;}return value;}
function pageNumbers(text:string){const parts=text.split(',').map(x=>x.trim());if(!parts.length||parts.some(x=>!/^\d+$/.test(x)||Number(x)<1||Number(x)>200))throw Error('Use explicit physical page numbers: 1,2,3 / استخدم أرقام صفحات فعلية');return parts.map(Number);}
function repeatsUnknownSubmission(candidate:unknown,sourceId:string,url:string,pageSha:unknown,destination:string,fields:Record<string,string>){
 if(!object(candidate)||candidate.source_id!==sourceId||candidate.requested_url!==url||candidate.sha256!==pageSha||!object(candidate.submission)||candidate.submission.status!=='unknown'||!object(candidate.form_draft)||candidate.form_draft.destination!==destination||!Array.isArray(candidate.form_draft.fields))return false;
 const prior=candidate.form_draft.fields.filter(object);
 return prior.length===Object.keys(fields).length&&prior.every(field=>typeof field.name==='string'&&Object.hasOwn(fields,field.name)&&fields[field.name]===field.value);
}

export function TransformResult({value,ar}:{value:unknown;ar:boolean}){
 const t=(a:string,b:string)=>ar?a:b;
 if(!object(value))return <p>{t('لا توجد نتيجة منظّمة قابلة للعرض.','No structured result is available.')}</p>;
 const output=object(value.output)?value.output:value;
 const draft=object(value.form_draft)?value.form_draft:null,submission=object(value.submission)?value.submission:null;
 const rows=draft&&Array.isArray(draft.fields)?draft.fields.filter(object):[];
 const table=object(output.table)&&Array.isArray(output.table.columns)&&Array.isArray(output.table.rows)?output.table:null;
 const columns=table?(table.columns as unknown[]).filter((x):x is string=>typeof x==='string'):[];
 const dataRows=table?(table.rows as unknown[]).filter(Array.isArray):[];
 return <section className="skills-transform-result">
  {typeof value.title==='string'&&<h4 dir="auto">{value.title}</h4>}
  {typeof value.url==='string'&&<p dir="ltr">{value.url}</p>}
  {typeof value.text==='string'&&<div className="skills-output" dir="auto">{value.text}</div>}
  {typeof value.notice==='string'&&<p className="skills-hint" dir="auto">{value.notice}</p>}
  {value.truncated===true&&<p className="skills-hint">{t('المحتوى المعروض محدود؛ لم تُقرأ كل التفاصيل ضمن هذا الناتج.','The displayed content is bounded; this output does not cover every detail.')}</p>}
  {draft&&<><h4>{t('قيم النموذج للمراجعة','Form values for review')}</h4><p>{submission?.status==='unknown'?t('قد يكون الطلب وصل؛ النتيجة مجهولة ولا تجوز إعادته تلقائيًا.','The request may have arrived; the outcome is unknown and must not be automatically retried.'):submission?t('أُرسل طلب؛ أثره في الموقع لم يُثبت.','A request was sent; its effect on the site is not established.'):t('مسودة محلية؛ لم تُرسل قيم النموذج.','Local draft; no form values have been sent.')}</p><p dir="ltr">{String(draft.destination??'')}</p><dl>{rows.map((row,i)=><div key={i}><dt><bdi>{String(row.name??'')}</bdi></dt><dd dir="auto">{String(row.value??'')}</dd></div>)}</dl></>}
  {submission&&<p className="skills-hint">HTTP {String(submission.http_status??'—')} · {t('استلام الرد لا يثبت نجاح الإجراء أو حفظه. لا إعادة إرسال تلقائية.','A response does not prove the action succeeded or persisted. No automatic resubmission.')}</p>}
  {table&&<><h4>{t('معاينة البيانات','Data preview')}</h4><p className="skills-hint">{t(`عرض ${Math.min(dataRows.length,20)} من ${dataRows.length} صفًا. الناقص مميز عن الصفر والنص الفارغ.`,`Showing ${Math.min(dataRows.length,20)} of ${dataRows.length} rows. Missing values are distinct from zero and empty text.`)}</p><div className="skills-table-scroll"><table className="skills-result-table"><thead><tr>{columns.map((name,i)=><th key={i} scope="col" dir="auto">{name}</th>)}</tr></thead><tbody>{dataRows.slice(0,20).map((row,i)=><tr key={i}>{columns.map((_,j)=><td key={j} dir="auto">{row[j]===null||row[j]===undefined?<span className="skills-hint">{t('ناقص','Missing')}</span>:row[j]===''?<span className="skills-hint">{t('نص فارغ','Empty text')}</span>:String(row[j])}</td>)}</tr>)}</tbody></table></div></>}
  {Array.isArray(value.changes)&&<><h4>{t('التغييرات','Changes')}</h4><ul>{value.changes.filter(object).map((change,i)=><li key={i} dir="auto">{Object.entries(change).filter(([,v])=>typeof v==='string'||typeof v==='number').map(([k,v])=>`${k}: ${String(v)}`).join(' · ')}</li>)}</ul></>}
  {Array.isArray(value.versions)&&<p>{t('الإصدارات المحفوظة: ','Saved versions: ')}{value.versions.filter(object).map(x=>String(x.version??'')).join(' · ')}</p>}
  <details><summary>{t('تفاصيل الفحص والمصدر · للمراجعة التقنية','Verification and source details · technical review')}</summary><pre className="skills-json" dir="ltr">{JSON.stringify(value,null,2)}</pre></details>
 </section>;
}

export default forwardRef<TransformHandle,Props>(function SkillTransforms({projectId,ar,selected,requestedSkill,onOperationChange},handle){
 const t=(a:string,b:string)=>ar?a:b;
 const [op,setOp]=useState(transformOperations.find(x=>x.slug===requestedSkill)?.key??'pdf-create');
 const operation=transformOperations.find(x=>x.key===op)!;
 const [sources,setSources]=useState<Source[]>([]),[outputs,setOutputs]=useState<SkillFile[]>([]),[sourceError,setSourceError]=useState(''),[loading,setLoading]=useState(false),[refresh,setRefresh]=useState(0);
 const [ids,setIds]=useState<string[]>([]),[pages,setPages]=useState<Record<string,string>>({}),[allowRepeat,setAllowRepeat]=useState(false);
 const [title,setTitle]=useState(''),[content,setContent]=useState(''),[pairs,setPairs]=useState<Pair[]>([{name:'',value:''}]);
 const [paragraph,setParagraph]=useState(1),[expected,setExpected]=useState(''),[replacement,setReplacement]=useState('');
 const [targetFormat,setTargetFormat]=useState('txt'),[reflow,setReflow]=useState(false),[raster,setRaster]=useState(false),[region,setRegion]=useState({page:1,x0:0.1,y0:0.1,x1:0.5,y1:0.2});
 const [sheet,setSheet]=useState(0),[header,setHeader]=useState(1),[delimiter,setDelimiter]=useState(','),[outputFormat,setOutputFormat]=useState('json');
 const [column,setColumn]=useState(''),[extraColumn,setExtraColumn]=useState(''),[unit,setUnit]=useState(''),[recordUnit,setRecordUnit]=useState('');
 const [cleaning,setCleaning]=useState('trim'),[cleanValue,setCleanValue]=useState(''),[cell,setCell]=useState('A1'),[formula,setFormula]=useState(''),[aggregate,setAggregate]=useState('sum'),[chart,setChart]=useState('bar');
 const [advanced,setAdvanced]=useState(''),[folderAction,setFolderAction]=useState('preview'),[folder,setFolder]=useState(''),[newName,setNewName]=useState(''),[planHash,setPlanHash]=useState('');
 const [versionAction,setVersionAction]=useState('list'),[restoreVersion,setRestoreVersion]=useState(1);
 const [webSources,setWebSources]=useState<SkillWebSource[]>([]),[webError,setWebError]=useState(''),[webSource,setWebSource]=useState(''),[webUrl,setWebUrl]=useState(''),[network,setNetwork]=useState(false);
 const [webSnapshot,setWebSnapshot]=useState<Record<string,unknown>|null>(null),[formIndex,setFormIndex]=useState(0),[submit,setSubmit]=useState(false);
 const chosen=ids.map(id=>sources.find(x=>x.id===id)).filter((x):x is Source=>Boolean(x));
 const file=outputs.find(x=>x.id===ids[0]);
 const structured=selected?.result?.structured??{};
 const currentWeb=Object.values(structured).find(value=>object(value)&&value.mode==='static-http') as Record<string,unknown>|undefined;
 const currentFolder=structured['folder-organization'];
 useEffect(()=>{if(!selected){setWebSnapshot(null);setSubmit(false);setPlanHash('');setFolderAction('preview');}},[selected]);
 useEffect(()=>{const next=transformOperations.find(x=>x.slug===requestedSkill);if(next)changeOperation(next.key);},[requestedSkill]);
 useEffect(()=>{
  const abort=new AbortController();setLoading(true);setSourceError('');
  Promise.allSettled([documentsApi.list(projectId,abort.signal),skillsApi.files(projectId,abort.signal)]).then(([documents,files])=>{
   if(abort.signal.aborted)return;const values:Source[]=[];const errors:string[]=[];
   if(documents.status==='fulfilled')values.push(...documents.value.map(d=>({kind:'document' as const,id:d.id,version:d.version,sha256:d.sha256,name:d.filename,pages:d.page_count})));else errors.push(String(documents.reason));
   if(files.status==='fulfilled'){setOutputs(files.value);values.push(...files.value);}else{setOutputs([]);errors.push(String(files.reason));}
   setSources(values);setIds(old=>old.filter(id=>values.some(x=>x.id===id)));setSourceError(errors.join(' · '));setLoading(false);
  });return()=>abort.abort();
 },[projectId,refresh]);
 useEffect(()=>{
  if(operation.group!=='web')return;const abort=new AbortController();setWebError('');
  skillsApi.webSources(abort.signal).then(values=>{if(!abort.signal.aborted)setWebSources(values);}).catch(e=>{if(!abort.signal.aborted){setWebSources([]);setWebError(String(e));}});
  return()=>abort.abort();
 },[operation.group,projectId]);
 function changeOperation(value:string){setOp(value);setIds([]);setSubmit(false);setWebSnapshot(null);setPlanHash('');setFolderAction('preview');setAdvanced('');setOutputFormat(value==='csv'?'csv':'json');}
 function choose(id:string,checked:boolean){setIds(old=>checked?[...old,id]:old.filter(x=>x!==id));setPlanHash('');}
 function useObservedPage(){
  if(!currentWeb||currentWeb.source_id!==webSource||currentWeb.requested_url!==webUrl)return;
  setWebSnapshot(currentWeb);const draft=object(currentWeb.form_draft)?currentWeb.form_draft:null;
  setFormIndex(draft&&typeof draft.form_index==='number'?draft.form_index:0);
  const prior=draft&&Array.isArray(draft.fields)?draft.fields.filter(object).filter(x=>typeof x.name==='string'&&typeof x.value==='string').map(x=>({name:String(x.name),value:String(x.value)})):[];
  setPairs(prior.length?prior:[{name:'',value:''}]);setSubmit(false);
 }
 const forms=webSnapshot&&Array.isArray(webSnapshot.forms)?webSnapshot.forms.filter(object):[];
 const observedForm=forms[formIndex];
 const fields=observedForm&&Array.isArray(observedForm.fields)?observedForm.fields.filter(object):[];
 const destination=webSnapshot&&object(webSnapshot.form_draft)&&webSnapshot.form_draft.form_index===formIndex?String(webSnapshot.form_draft.destination??''):'';
 useImperativeHandle(handle,()=>({build(){
  function fail(a:string,b:string):never{throw Error(t(a,b));}
  const inputs:Inputs={};
  if(operation.group==='web'){
   if(!network||!webSource||!webSources.find(x=>x.id===webSource)?.urls.some(x=>x.url===webUrl))fail('اختر مصدرًا مسجّلًا واسمح بقراءة عنوانه.','Select a registered source and explicitly allow reading its URL.');
   const value:Record<string,unknown>={sourceId:webSource,url:webUrl,allowNetwork:network};
   if(op==='web-form-filling'){
    if(!webSnapshot||webSnapshot.source_id!==webSource||webSnapshot.requested_url!==webUrl)fail('اقرأ الصفحة أولًا، ثم استخدم حقولها المرصودة.','Read the page first, then use its observed fields.');
    value.formIndex=formIndex;value.expectedPageSha256=webSnapshot.sha256;value.fields=pairsObject(pairs);value.submit=submit;
    if(submit){if(!destination)fail('جهّز مسودة النموذج أولًا لتراجع وجهة الإرسال.','Prepare the draft first to review the submission destination.');if([webSnapshot,currentWeb].some(candidate=>repeatsUnknownSubmission(candidate,webSource,webUrl,webSnapshot.sha256,destination,value.fields as Record<string,string>)))fail('نتيجة إرسال هذه القيم إلى الوجهة نفسها مجهولة. تحقق من أثر الطلب السابق قبل إعادته.','The submission of these values to the same destination has an unknown outcome. Check the prior request before repeating it.');value.expectedDestination=destination;}
   }
   inputs.webOperation=value;
  }else if(operation.group==='files'){
   if(!file||ids.length!==1)fail('اختر ملف نتيجة واحدًا.','Select one output file.');
   if(op==='versions')inputs.versionOperation={action:versionAction,artifactId:file.id,...(versionAction==='restore'?{version:restoreVersion,expectedVersion:file.latest_version}:{})};
   else{if(!newName.trim())fail('حدد اسم الملف المطلوب.','Enter the requested file name.');inputs.folderOperation={action:folderAction,moves:[{artifactId:file.id,expectedRevision:file.revision,name:newName,folder}],...(folderAction==='apply'?{expectedPlanSha256:planHash}:{})};if(folderAction==='apply'&&!planHash)fail('اعرض الخطة أولًا واستخدم بصمتها بعد المراجعة.','Preview and review the plan before applying it.');}
  }else if(operation.group==='synthesis'){
   if(chosen.length<2||chosen.some(x=>x.kind!=='document'))fail('اختر وثيقتين معالجتين على الأقل.','Choose at least two processed documents.');inputs.documentIds=chosen.map(x=>x.id);
  }else{
   if(op!=='pdf-create'&&!chosen.length)fail('اختر الملفات المطلوبة.','Choose the required source files.');
   if(chosen.length!==ids.length)fail('تغيرت قائمة الملفات؛ حدّثها وأعد الاختيار.','The file list changed; refresh and select again.');
   inputs.sourceRefs=chosen.map(ref);
   if(operation.group==='document'){
    let options:Record<string,unknown>={};
    if(op==='pdf-create'){if(!title.trim()||!content.trim())fail('أدخل عنوان PDF ونصه المعتمد.','Enter a PDF title and approved text.');options={title,paragraphs:content.split(/\n\s*\n/).filter(Boolean),direction:ar?'rtl':'ltr'};}
    if(op==='pdf-merge'||op==='pdf-split')options={selections:chosen.map(x=>({sourceId:x.id,pages:pageNumbers(pages[x.id]??'')})),allowRepeatedPages:allowRepeat};
    if(op==='pdf-form-fill'||op==='template-fill')options={values:pairsObject(pairs)};
    if(op==='pdf-redact'){if(!raster)fail('راجع فقدان النص القابل للبحث ووافق على إعادة بناء الصفحات المنقحة.','Review loss of searchable text and explicitly allow raster reconstruction.');options={regions:[region],allowRasterization:true};}
    if(op==='docx-edit')options={edits:[{paragraph,expectedText:expected,replacement}]};
    if(op==='docx-compare')options={ignoreWhitespace:false};
    if(op==='convert')options={format:targetFormat,allowReflow:reflow,direction:ar?'rtl':'ltr'};
    if(op==='table-extract')options={tableIndex:paragraph};
    if(advanced.trim()){const parsed:unknown=JSON.parse(advanced);if(!object(parsed))fail('الخيارات المتقدمة يجب أن تكون كائنًا.','Advanced options must be an object.');options=parsed;}
    inputs.documentOperation={operation:op,sourceIds:chosen.map(x=>x.id),options};
   }else{
    if(chosen.length!==1)fail('اختر ملف بيانات واحدًا لهذا النموذج.','Choose one data file for this form.');
    let value:Record<string,unknown>={operation:op,sourceId:chosen[0].id,source_format:chosen[0].name.toLowerCase().endsWith('.csv')?'csv':'xlsx',sheet_index:sheet,header_row:header,delimiter,format:outputFormat};
    if(op==='clean'){if(!column.trim())fail('حدد العمود المطلوب.','Specify the target column.');value.cleaning=[{operation:cleaning,column,...(cleaning==='rename'||cleaning==='fill_missing'?{value:cleanValue}:{})}];}
    if(op==='formulas'){if(!formula.trim())fail('أدخل صيغة محددة للمراجعة.','Enter an explicit formula for review.');value.edits=[{cell,formula}];value.format='xlsx';}
    if(op==='aggregate'){if(!recordUnit.trim())fail('حدد ما يمثله الصف.','Specify what each record represents.');value.aggregate={group_by:extraColumn.split(',').map(x=>x.trim()).filter(Boolean),value_column:column,operation:aggregate,record_unit:recordUnit};}
    if(op==='chart'){if(!title||!unit||!column||!extraColumn)fail('حدد عنوان الرسم ووحدته وأعمدته.','Specify chart title, unit and columns.');value.chart={kind:chart,category:column,values:extraColumn.split(',').map(x=>x.trim()).filter(Boolean),title,unit};value.format='xlsx';}
    if(['stats','explore'].includes(op)){value.columns=column.split(',').map(x=>x.trim()).filter(Boolean);if(recordUnit)value.record_unit=recordUnit;}
    if(op==='join'&&!advanced.trim())fail('الربط يحتاج مخطط مفاتيح وجدول الطرف الثاني في الخيارات المتقدمة.','Joining currently needs explicit keys and the right-hand table in advanced options.');
    if(advanced.trim()){const parsed:unknown=JSON.parse(advanced);if(!object(parsed))fail('الخيارات المتقدمة يجب أن تكون كائنًا.','Advanced options must be an object.');value={...value,...parsed,operation:op,sourceId:chosen[0].id};}
    inputs.dataOperation=value;
   }
  }
  return {inputs,requestedSkills:[operation.slug]};
 }}));
 function field(label:string,value:string,set:(v:string)=>void,limit=200){return <label className="field-label">{label}<input className="input" dir="auto" aria-label={label} value={value} maxLength={limit} onChange={e=>set(e.target.value)}/></label>;}
 function pairsEditor(){return <section aria-label={t('قيم الحقول','Field values')}><p className="skills-hint">{t('الأسماء والقيم حرفية؛ الفراغ قيمة فارغة ولا يُستبدل بصفر.','Names and values are literal; an empty value is not zero.')}</p>{pairs.map((pair,i)=><div className="skills-pair" key={i}><input className="input" aria-label={t(`اسم الحقل ${i+1}`,`Field ${i+1} name`)} value={pair.name} maxLength={200} onChange={e=>{setSubmit(false);setPairs(old=>old.map((p,n)=>n===i?{...p,name:e.target.value}:p));}}/><input className="input" dir="auto" aria-label={t(`قيمة الحقل ${i+1}`,`Field ${i+1} value`)} value={pair.value} maxLength={2000} onChange={e=>{setSubmit(false);setPairs(old=>old.map((p,n)=>n===i?{...p,value:e.target.value}:p));}}/><button type="button" className="icon-button" aria-label={t('إزالة الحقل','Remove field')} disabled={pairs.length===1} onClick={()=>{setSubmit(false);setPairs(old=>old.filter((_,n)=>n!==i));}}><Trash2 size={14}/></button></div>)}<button type="button" className="text-button" disabled={pairs.length>=24} onClick={()=>{setSubmit(false);setPairs(old=>[...old,{name:'',value:''}]);}}><Plus size={14}/>{t('حقل','Field')}</button></section>;}
 return <section className="skills-transforms" aria-label={t('عمليات الملفات والبيانات والويب','File, data and web operations')}>
  <label className="field-label">{t('العملية المطلوبة','Requested operation')}<select className="input" aria-label={t('العملية المطلوبة','Requested operation')} value={op} onChange={e=>{changeOperation(e.target.value);onOperationChange?.();}}>{transformOperations.map(x=><option key={x.key} value={x.key}>{ar?x.ar:x.en}</option>)}</select></label>
  <p className="skills-hint">{t('يُحفظ الناتج مستقلًا مع إصدار وبصمة مصدره. نجاح التنفيذ لا يثبت سلامة المظهر في كل برنامج.','The result is saved separately with its source version and fingerprint. Execution success does not establish layout in every application.')}</p>
  {operation.group!=='web'&&op!=='pdf-create'&&<fieldset className="skills-source-choices"><legend>{t('الملفات المتاحة في المشروع','Available project files')}</legend>{loading&&<p>{t('جارٍ قراءة القائمة…','Loading files…')}</p>}{sourceError&&<p className="skills-error">{sourceError}</p>}{sources.filter(x=>operation.group==='files'?x.kind==='artifact':operation.group==='synthesis'?x.kind==='document':true).map(source=><label className="skills-check" key={source.kind+source.id}><input type="checkbox" checked={ids.includes(source.id)} disabled={ids.length>=8&&!ids.includes(source.id)} onChange={e=>choose(source.id,e.target.checked)}/><span><bdi>{source.name}</bdi><small>{source.kind==='artifact'?t('نتيجة محفوظة','Saved output'):t('ملف مرفوع','Uploaded document')} · v{source.version}</small></span></label>)}<button type="button" className="text-button" onClick={()=>setRefresh(n=>n+1)}><RefreshCw size={14}/>{t('تحديث الملفات','Refresh files')}</button></fieldset>}
  {op==='pdf-create'&&<>{field(t('عنوان الملف','File title'),title,setTitle)}<label className="field-label">{t('النص المعتمد','Approved document text')}<textarea className="input" dir="auto" aria-label={t('النص المعتمد','Approved document text')} value={content} maxLength={16000} rows={6} onChange={e=>setContent(e.target.value)}/></label></>}
  {['pdf-merge','pdf-split'].includes(op)&&<>{chosen.map(source=><div key={source.id}>{field(t(`صفحات ${source.name}`,`Pages in ${source.name}`),pages[source.id]??'',v=>setPages(old=>({...old,[source.id]:v})),800)}</div>)}<p className="skills-hint">{t('الأرقام فعلية وتبدأ من 1؛ مثال: 3,1,2 يحفظ هذا الترتيب.','Physical page numbers start at1; 3,1,2 preserves that order.')}</p><label className="skills-check"><input type="checkbox" checked={allowRepeat} onChange={e=>setAllowRepeat(e.target.checked)}/>{t('السماح بتكرار صفحة صراحةً','Explicitly allow repeated pages')}</label></>}
  {['pdf-form-fill','template-fill'].includes(op)&&pairsEditor()}
  {op==='pdf-redact'&&<><p className="skills-hint">{t('التنقيح يعيد بناء صفحات نقطية؛ يفقد النص القابل للبحث والروابط. افحص الناتج بصريًا. الإحداثيات نسبة من الصفحة 0–1.','Redaction reconstructs raster pages, losing searchable text and links. Review visually. Coordinates are page fractions0–1.')}</p>{Object.entries(region).map(([key,value])=><label className="field-label" key={key}>{key}<input className="input" type="number" aria-label={`Redaction ${key}`} min={key==='page'?1:0} max={key==='page'?20:1} step={key==='page'?1:0.01} value={value} onChange={e=>setRegion(old=>({...old,[key]:Number(e.target.value)}))}/></label>)}<label className="skills-check"><input type="checkbox" checked={raster} onChange={e=>setRaster(e.target.checked)}/>{t('أوافق على إعادة البناء وفقد خصائص النص المذكورة','I accept reconstruction and the stated loss of text features')}</label></>}
  {['docx-edit','table-extract'].includes(op)&&<label className="field-label">{op==='docx-edit'?t('رقم الفقرة من1','Paragraph number from1'):t('رقم الجدول من1','Table number from1')}<input className="input" type="number" min={1} max={op==='docx-edit'?5000:100} value={paragraph} onChange={e=>setParagraph(Number(e.target.value))}/></label>}
  {op==='docx-edit'&&<>{field(t('النص الحالي المتوقع','Expected current text'),expected,setExpected,4000)}{field(t('النص البديل','Replacement text'),replacement,setReplacement,4000)}</>}
  {op==='docx-compare'&&<p className="skills-hint">{t('اختر ملفين بالترتيب: القديم ثم الجديد. المقارنة حرفية وليست إثباتًا لتكافؤ المعنى.','Choose two files in order: old then new. Comparison is literal, not proof of semantic equivalence.')}</p>}
  {op==='convert'&&<><label className="field-label">{t('الصيغة المطلوبة','Target format')}<select className="input" value={targetFormat} onChange={e=>setTargetFormat(e.target.value)}><option value="txt">TXT</option><option value="pdf">PDF</option><option value="csv">CSV</option></select></label><label className="skills-check"><input type="checkbox" checked={reflow} onChange={e=>setReflow(e.target.checked)}/>{t('أسمح بإعادة تدفق المحتوى عند حاجة التحويل','Allow content reflow where the conversion requires it')}</label></>}
  {operation.group==='data'&&<><label className="field-label">{t('رقم الورقة من0','Sheet index from0')}<input className="input" type="number" min={0} max={199} value={sheet} onChange={e=>setSheet(Number(e.target.value))}/></label><label className="field-label">{t('صف العناوين من1','Header row from1')}<input className="input" type="number" min={1} max={1000} value={header} onChange={e=>setHeader(Number(e.target.value))}/></label><label className="field-label">{t('فاصل CSV','CSV delimiter')}<select className="input" value={delimiter} onChange={e=>setDelimiter(e.target.value)}><option value=",">,</option><option value=";">;</option><option value={'\t'}>Tab</option><option value="|">|</option></select></label><label className="field-label">{t('صيغة الناتج','Output format')}<select className="input" value={outputFormat} onChange={e=>setOutputFormat(e.target.value)}><option value="json">JSON</option><option value="xlsx">XLSX</option><option value="csv">CSV</option></select></label></>}
  {['clean','aggregate','chart','stats','explore'].includes(op)&&field(t(op==='chart'?'عمود الفئات':'الأعمدة أو العمود المطلوب',op==='chart'?'Category column':'Target column or columns'),column,setColumn,500)}
  {op==='clean'&&<><label className="field-label">{t('قاعدة التنظيف الصريحة','Explicit cleaning rule')}<select className="input" value={cleaning} onChange={e=>setCleaning(e.target.value)}><option value="trim">{t('إزالة المسافات الطرفية','Trim boundary whitespace')}</option><option value="parse_number">{t('تحويل رقم بفاصل عشري نقطة','Parse number with decimal point')}</option><option value="rename">{t('تسمية العمود','Rename column')}</option><option value="fill_missing">{t('تعبئة الناقص بنص محدد','Fill missing with explicit text')}</option></select></label>{['rename','fill_missing'].includes(cleaning)&&field(t('القيمة النصية المعتمدة','Approved literal text'),cleanValue,setCleanValue)}</>}
  {op==='formulas'&&<>{field(t('عنوان الخلية','Cell address'),cell,setCell,10)}{field(t('الصيغة البديلة','Replacement formula'),formula,setFormula,1000)}<p className="skills-hint">{t('تعاد حساب الصيغ المدعومة فقط. الصيغ غير المدعومة لا تُعرض كمتحققة.','Only the declared supported formulas are recalculated. Unsupported formulas remain unverified.')}</p></>}
  {['aggregate','chart'].includes(op)&&field(op==='chart'?t('أعمدة القيم بفواصل','Value columns, comma separated'):t('أعمدة التجميع بفواصل','Grouping columns, comma separated'),extraColumn,setExtraColumn,500)}
  {['aggregate','stats','explore'].includes(op)&&field(t('ما الذي يمثله الصف؟','What does one record represent?'),recordUnit,setRecordUnit)}
  {op==='aggregate'&&<label className="field-label">{t('الحساب','Calculation')}<select className="input" value={aggregate} onChange={e=>setAggregate(e.target.value)}><option value="sum">{t('المجموع','Sum')}</option><option value="mean">{t('المتوسط','Mean')}</option><option value="count">{t('العدد','Count')}</option><option value="distinct">{t('قيم مميزة','Distinct values')}</option></select></label>}
  {op==='chart'&&<>{field(t('عنوان الرسم','Chart title'),title,setTitle)}{field(t('الوحدة','Unit'),unit,setUnit)}<select className="input" aria-label={t('نوع الرسم','Chart type')} value={chart} onChange={e=>setChart(e.target.value)}><option value="bar">{t('أعمدة','Bars')}</option><option value="line">{t('خط','Line')}</option></select></>}
  {op==='folders'&&<>{field(t('اسم الملف الجديد','New file name'),newName,v=>{setNewName(v);setPlanHash('');setFolderAction('preview');},180)}{field(t('المجلد المنطقي','Logical folder'),folder,v=>{setFolder(v);setPlanHash('');setFolderAction('preview');},240)}<p className="skills-hint">{t('تنظيم داخل المشروع فقط؛ لا نقل لملفات الجهاز ولا تغيير للمشاركة.','Project organization only: no host-file moves or sharing changes.')}</p><select className="input" aria-label={t('مرحلة التنظيم','Organization action')} value={folderAction} onChange={e=>setFolderAction(e.target.value)}><option value="preview">{t('معاينة الخطة','Preview plan')}</option><option value="apply">{t('تطبيق الخطة المراجعة','Apply reviewed plan')}</option></select>{object(currentFolder)&&typeof currentFolder.plan_sha256==='string'&&<button type="button" className="text-button" onClick={()=>{setPlanHash(currentFolder.plan_sha256 as string);setFolderAction('apply');}}>{t('راجعت الخطة المعروضة؛ استخدمها للتطبيق','I reviewed the displayed plan; use it to apply')}</button>}</>}
  {op==='versions'&&<><select className="input" aria-label={t('إجراء الإصدار','Version action')} value={versionAction} onChange={e=>setVersionAction(e.target.value)}><option value="list">{t('عرض الإصدارات','List versions')}</option><option value="restore">{t('استعادة نسخة كإصدار جديد','Restore as a new version')}</option></select>{versionAction==='restore'&&<label className="field-label">{t('الإصدار المراد استعادته','Version to restore')}<input className="input" type="number" min={1} value={restoreVersion} onChange={e=>setRestoreVersion(Number(e.target.value))}/></label>}</>}
  {operation.group==='web'&&<>{webError&&<p className="skills-error">{webError}</p>}<label className="field-label">{t('المصدر المعتمد','Approved source')}<select className="input" value={webSource} onChange={e=>{setWebSource(e.target.value);setWebUrl('');setWebSnapshot(null);setSubmit(false);}}><option value="">{t('اختر المصدر','Choose source')}</option>{webSources.map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select></label><label className="field-label">{t('الصفحة المسجّلة','Registered page')}<select className="input" value={webUrl} onChange={e=>{setWebUrl(e.target.value);setWebSnapshot(null);setSubmit(false);}}><option value="">{t('اختر الصفحة','Choose page')}</option>{webSources.find(x=>x.id===webSource)?.urls.map(x=><option key={x.url} value={x.url}>{x.title}</option>)}</select></label><label className="skills-check"><input type="checkbox" checked={network} onChange={e=>{setNetwork(e.target.checked);setSubmit(false);}}/>{t('أسمح بقراءة هذا العنوان العام في الوضع الهجين','Allow reading this public URL in hybrid mode')}</label><p className="skills-hint">{t('قراءة HTML ثابتة؛ لا تسجيل دخول ولا JavaScript ولا هوية حساب متحققة. لا تُرسل قيم النموذج أثناء المعاينة.','Static HTML only: no login, JavaScript or verified account identity. Form values are not sent during drafting.')}</p>{op==='web-form-filling'&&<>{currentWeb?.source_id===webSource&&currentWeb.requested_url===webUrl&&<button type="button" className="text-button" onClick={useObservedPage}>{t('استخدم الصفحة المرصودة الحالية','Use the current observed page')}</button>}{webSnapshot&&<><label className="field-label">{t('النموذج المرصود','Observed form')}<select className="input" value={formIndex} onChange={e=>{setFormIndex(Number(e.target.value));setSubmit(false);setPairs([{name:'',value:''}]);}}>{forms.map((_,i)=><option key={i} value={i}>{i+1}</option>)}</select></label><p className="skills-hint">{t('الحقول المرصودة: ','Observed fields: ')}{fields.map(x=>String(x.name)).join(' · ')}</p>{pairsEditor()}{destination&&<><p><bdi>{destination}</bdi></p><label className="skills-check"><input type="checkbox" checked={submit} onChange={e=>setSubmit(e.target.checked)}/>{t('أفوّض إرسال القيم الظاهرة إلى هذه الوجهة مرة واحدة. استلام HTTP لا يثبت إتمام العملية.','Authorize sending these displayed values once to this destination. An HTTP response does not prove the intended effect.')}</label></>}</>}</>}</>}
  {['document','data'].includes(operation.group)&&<details><summary>{t('خيارات متقدمة للصيغة','Advanced format options')}</summary><p className="skills-hint">{t('لخيارات البنية المتخصصة؛ تُتحقق على الخادم ولا تمنح صلاحيات إضافية.','For specialized structured options; the server validates them and they grant no additional permissions.')}</p><textarea className="input" dir="ltr" aria-label={t('خيارات العملية المتقدمة','Advanced operation options')} value={advanced} maxLength={50000} rows={4} onChange={e=>setAdvanced(e.target.value)}/></details>}
 </section>;
});
