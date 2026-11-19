import {useCallback,useEffect,useRef,useState} from 'react';
import {skillsApi} from './skills-api';
import type {SkillAudit,SkillRuntime} from './skills-api';

const record=(value:unknown):Record<string,unknown>=>value!==null&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{};
const valueText=(value:unknown,missing:string)=>typeof value==='string'||typeof value==='number'?String(value):missing;
const errorText=(value:unknown)=>value instanceof Error?value.message:'Runtime request failed.';

export default function SkillRuntimePanel({projectId,ar,selectedRunId}:{projectId:string;ar:boolean;selectedRunId?:string}){
 const [snapshot,setSnapshot]=useState<SkillRuntime|null>(null),[audit,setAudit]=useState<SkillAudit|null>(null);
 const [error,setError]=useState(''),[busy,setBusy]=useState('');
 const [now,setNow]=useState(Date.now);
 const pending=useRef<AbortController|null>(null),version=useRef(0);
 const t=(arabic:string,english:string)=>ar?arabic:english;
 const missing=t('غير متاح','Not available');
 const label=(value:unknown)=>({ready:t('جاهز للفحص المحدد','Ready for the recorded check'),limited:t('دعم محدود','Limited support'),unknown:t('غير معلوم','Unknown'),unverified:t('غير متحقق','Not verified'),stale:t('بيانات قديمة','Stale data'),degraded:t('متدهور','Degraded'),unavailable:t('غير متاح','Unavailable'),observed:t('مرصود','Observed'),blocked:t('غير معتمد','Not approved'),approved_local:t('معتمد للفحوص المحلية فقط','Approved for local checks only'),passed_fixed_probe:t('نجح الاختبار الاصطناعي المحدد','Passed the fixed synthetic probe'),unsupported_by_adapter:t('غير مدعوم بهذا الاتصال','Unsupported by this adapter')}[String(value)]??valueText(value,missing));
 const load=useCallback(async()=>{
  pending.current?.abort();const abort=new AbortController();pending.current=abort;const ticket=++version.current;
  setBusy('refresh');setError('');setSnapshot(null);setAudit(null);
  try{const result=await skillsApi.runtime(projectId,abort.signal);if(!abort.signal.aborted&&version.current===ticket)setSnapshot(result);}
  catch(problem){if(!abort.signal.aborted&&version.current===ticket)setError(errorText(problem));}
  finally{if(!abort.signal.aborted&&version.current===ticket)setBusy('');}
 },[projectId]);
 useEffect(()=>{void load();return()=>{version.current++;pending.current?.abort();};},[load]);
 useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),5000);return()=>clearInterval(timer);},[]);
 async function action(kind:'probe'|'audit'){
  if(busy||kind==='audit'&&!selectedRunId)return;
  pending.current?.abort();const abort=new AbortController();pending.current=abort;const ticket=++version.current;setBusy(kind);setError('');
  if(kind==='audit')setAudit(null);
  try{
   if(kind==='probe'){
    const model=await skillsApi.probe(projectId,abort.signal);
    if(!abort.signal.aborted&&version.current===ticket)setSnapshot(old=>old?{...old,model}:null);
   }else{
    const result=await skillsApi.audit(projectId,selectedRunId!,abort.signal);
    if(!abort.signal.aborted&&version.current===ticket)setAudit(result);
   }
  }catch(problem){if(!abort.signal.aborted&&version.current===ticket){setError(errorText(problem));if(kind==='probe')setSnapshot(null);else setAudit(null);}}
  finally{if(!abort.signal.aborted&&version.current===ticket)setBusy('');}
 }
 const monitoring=snapshot?.monitoring??{},database=record(monitoring.database),worker=record(monitoring.worker),queue=record(monitoring.queue),timing=record(monitoring.timing);
 const model=snapshot?.model??{},declared=record(model.declared),measured=record(model.measured),release=snapshot?.release??{};
 const observed=Date.parse(String(monitoring.observed_at??''));
 const oldSnapshot=!Number.isFinite(observed)||now-observed>60000||observed-now>60000;
 const workerTick=Date.parse(String(worker.last_tick??''));
 const workerLimit=typeof worker.stale_after_seconds==='number'?worker.stale_after_seconds:15;
 const workerState=worker.state==='ready'&&(oldSnapshot||!Number.isFinite(workerTick)||now-workerTick>workerLimit*1000)?'stale':worker.state;
 const rows=[['text',t('نص','Text')],['images',t('صور','Images')],['tools',t('أدوات','Tools')],['structured_output',t('نتيجة منظمة','Structured output')],['audio',t('صوت','Audio')]];
 return <section className="skills-runtime" aria-label={t('حالة التشغيل','Runtime status')} aria-busy={Boolean(busy)}>
  <div className="skills-card-actions"><button className="button subtle" disabled={Boolean(busy)} onClick={()=>void load()}>{t('تحديث حالة التشغيل','Refresh runtime status')}</button><span role="status">{busy?t('جارٍ الفحص…','Checking…'):t('الفحص لا يرسل رسائل ولا يغيّر الملفات.','Checks do not send messages or change files.')}</span></div>
  <p className="skills-hint">{t('الجاهزية تخص الفحص الظاهر ووقته. اختبار محلي أو استجابة اتصال لا يثبت جودة كل لغة أو وصول إشعار أو إذن إرسال.','Readiness applies to the recorded check and time. A local check or connection response does not certify every language, notification delivery, or permission to send.')}</p>
  {error&&<p className="skills-error" role="alert">{error}</p>}
  {!snapshot?<p data-runtime-empty="true">{busy?t('بانتظار نتائج فعلية.','Waiting for actual results.'):t('لا توجد حالة حديثة متحققة.','No current verified status.')}</p>:<>
   <p className="skills-hint">{t('آخر رصد: ','Last observed: ')}<bdi>{valueText(monitoring.observed_at,missing)}</bdi></p>
   {oldSnapshot&&<p className="skills-notice" role="status">{t('مرّ أكثر من دقيقة على الرصد أو لم يصل توقيت صالح. حدّث الفحص قبل الاعتماد على الحالة.','The snapshot is over one minute old or lacks a valid timestamp. Refresh before relying on its state.')}</p>}
   <div className="skills-catalog">
    <article className="skills-card" data-runtime="database"><h3>{t('التخزين','Storage')}</h3><p>{label(oldSnapshot&&database.state==='ready'?'stale':database.state)}</p><small>{valueText(database.check,missing)}</small></article>
    <article className="skills-card" data-runtime="worker"><h3>{t('العامل الخلفي','Background worker')}</h3><p>{label(workerState)}</p><small>{t('آخر نشاط: ','Last activity: ')}<bdi>{valueText(worker.last_tick,missing)}</bdi></small><p className="skills-hint">{t('نشاط العامل لا يثبت وصول إشعار إلى جهاز.','Worker activity does not prove notification delivery to a device.')}</p></article>
    <article className="skills-card" data-runtime="queue"><h3>{t('العمل المنتظر','Queued work')}</h3><p>{label(queue.state)}</p><p>{t('عدد المهام: ','Tasks: ')}{queue.state==='observed'?valueText(queue.queued,missing):missing}</p><p>{t('عمر أقدم مهمة، ثانية: ','Oldest task age, seconds: ')}{queue.state==='observed'?valueText(queue.oldest_queued_age_seconds,missing):missing}</p></article>
    <article className="skills-card" data-runtime="timing"><h3>{t('زمن المهام المحفوظ','Recorded task duration')}</h3><p>{label(timing.state)}</p><p>{t('عدد العينات: ','Samples: ')}{valueText(timing.samples,missing)}</p><p>{t('الوسيط، ثانية: ','Median, seconds: ')}{timing.state==='observed'?valueText(timing.median,missing):missing}</p></article>
   </div>
   <article className="skills-card" data-runtime="model"><h3>{t('قدرات النموذج المحلي','Local model capabilities')}</h3><p><bdi>{valueText(model.model,missing)}</bdi> · {label(model.status)}</p><p className="skills-hint">{t('الإعلان من بيانات المزود؛ العمود الثاني يبين ما اختُبر فعلًا. اختبار JSON الصغير لا يقيس جودة الإجابة أو الإسناد.','Declared support comes from provider metadata; measured support records actual checks. The small JSON probe does not evaluate answer quality or grounding.')}</p><div className="skills-table-scroll"><table className="skills-result-table"><thead><tr><th>{t('القدرة','Capability')}</th><th>{t('الدعم المعلن','Declared support')}</th><th>{t('الفحص المقاس','Measured check')}</th></tr></thead><tbody>{rows.map(([key,title])=><tr key={key}><th>{title}</th><td>{declared[key]===true?t('معلن','Declared'):declared[key]===false?t('غير مدعوم','Unsupported'):missing}</td><td>{label(measured[key]??'unverified')}</td></tr>)}</tbody></table></div><p className="skills-hint">{t('آخر فحص اتصال: ','Connection checked: ')}<bdi>{valueText(model.checked_at,missing)}</bdi></p><button className="button subtle" disabled={Boolean(busy)} onClick={()=>void action('probe')}>{t('اختبر النموذج ببيانات اصطناعية محلية','Run a local synthetic probe')}</button><p className="skills-hint">{t('طلب JSON ثابت، دون ملفاتك؛ حد الناتج 64 رمزًا ومهلة 45 ثانية.','One fixed JSON request, without your files; output limit 64 tokens and deadline 45 seconds.')}</p></article>
   <article className="skills-card" data-runtime="release"><h3>{t('اعتماد النسخة','Release evidence')}</h3><p>{label(release.state??'unverified')}</p><p className="skills-hint">{t('لا يمنح الاعتماد المحلي إذن نشر أو إثبات اتصال حي.','Local approval does not authorize publishing or prove a live integration.')}</p>{Array.isArray(release.reasons)&&release.reasons.length>0&&<ul>{release.reasons.filter((reason):reason is string=>typeof reason==='string').map(reason=><li key={reason}><bdi>{reason}</bdi></li>)}</ul>}</article>
   <details><summary>{t('تفاصيل الفحص للمطور','Developer check details')}</summary><pre dir="ltr">{JSON.stringify(snapshot,null,2)}</pre></details>
  </>}
  <article className="skills-card"><h3>{t('سجل المهمة المحددة','Selected task audit')}</h3><p className="skills-hint">{selectedRunId?t('سجل محدود للقرارات والحالات، بلا نص الرسائل.','Limited decision and state metadata, without message bodies.'):t('اختر مهمة من سجل المهام أولًا.','Select a task from task history first.')}</p><button className="button subtle" disabled={Boolean(busy)||!selectedRunId} onClick={()=>void action('audit')}>{t('اقرأ سجل المهمة','Read task audit')}</button>{audit&&audit.run_id===selectedRunId&&<><p>{t('الأحداث: ','Events: ')}{audit.events.length}{audit.truncated?t(' (عرض محدود)',' (limited view)'):''}</p><details><summary>{t('الأحداث المسجلة','Recorded events')}</summary><pre dir="ltr">{JSON.stringify(audit.events,null,2)}</pre></details></>}</article>
 </section>;
}
