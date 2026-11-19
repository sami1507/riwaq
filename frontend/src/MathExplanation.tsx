import {useState} from 'react';
import type {RunReceipt} from './api';
import type {Lang} from './ui';
import './education.css';

const object=(v:unknown):v is Record<string,unknown>=>Boolean(v)&&typeof v==='object'&&!Array.isArray(v);
const strings=(v:unknown):string[]=>Array.isArray(v)?v.filter((x):x is string=>typeof x==='string').slice(0,40):[];
type Step={equation:string;reason_ar:string;reason_en:string};
type Point={x:number;y:number};

function read(run?:RunReceipt){
 if(!run||run.origin!=='local_run'||run.status!=='complete')return null;
 const check=run.checks.find(c=>c.kind==='solve_math'&&c.status==='pass'&&c.method==='deterministic');
 if(!check||check.details.length>19500)return null;
 try{const record:unknown=JSON.parse(check.details);if(!object(record)||!object(record.result)||!object(record.request)||record.execution_status!=='completed')return null;return record as Record<string,unknown>&{result:Record<string,unknown>;request:Record<string,unknown>};}catch{return null;}
}

function Plot({series,ar}:{series:Point[][];ar:boolean}){
 const points=series.flat();if(!points.length)return null;
 const xs=points.map(p=>p.x),ys=points.map(p=>p.y),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys,0),ymax=Math.max(...ys,0);
 const x=(n:number)=>42+456*(n-xmin)/(xmax-xmin||1),y=(n:number)=>220-190*(n-ymin)/(ymax-ymin||1);
 return <figure className="math-plot"><svg viewBox="0 0 540 260" role="img" aria-label={ar?'رسم من نقاط محسوبة؛ كل فرع منفصل':'Plot from computed points; branches remain separate'}>
  <path d={`M42 30V220H498`} fill="none" stroke="currentColor"/>
  {xmin<0&&xmax>0&&<path d={`M${x(0)} 30V220`} stroke="currentColor" strokeDasharray="4 4"/>}
  {ymin<0&&ymax>0&&<path d={`M42 ${y(0)}H498`} stroke="currentColor" strokeDasharray="4 4"/>}
  {series.map((s,i)=><polyline key={i} points={s.map(p=>`${x(p.x)},${y(p.y)}`).join(' ')} fill="none" stroke="currentColor" strokeWidth="2"/>)}
  <text x="500" y="240">x</text><text x="12" y="25">f(x)</text><text x="42" y="240">{xmin}</text><text x="460" y="240">{xmax}</text><text x="4" y="42">{Number(ymax.toPrecision(3))}</text><text x="4" y="217">{Number(ymin.toPrecision(3))}</text>
 </svg><figcaption>{ar?'الخطوط تصل نقاطًا محسوبة داخل كل فرع فقط؛ لا تثبت استمرارية الدالة أو قيمة لها عند الانقطاع.':'Lines connect computed samples within each branch only; they do not establish continuity or a value at a discontinuity.'}</figcaption>
 <details><summary>{ar?'النقاط المحسوبة':'Computed points'}</summary><table><thead><tr><th>{ar?'الفرع':'Branch'}</th><th>x</th><th>f(x)</th></tr></thead><tbody>{series.flatMap((s,i)=>s.map((p,j)=><tr key={`${i}-${j}`}><td>{i+1}</td><td dir="ltr">{p.x}</td><td dir="ltr">{p.y}</td></tr>))}</tbody></table></details></figure>;
}

export default function MathExplanation({run,lang}:{run?:RunReceipt;lang:Lang}){
 const [level,setLevel]=useState(()=>{const requested=read(run)?.request.explanation_level;return requested==='detailed'||requested==='technical'?requested:'simple';}),[verify,setVerify]=useState(false),[alternative,setAlternative]=useState(false),[extra,setExtra]=useState(false);
 const r=read(run),ar=lang==='ar',t=(a:string,b:string)=>ar?a:b;
 if(!r)return null;
 const steps:Step[]=Array.isArray(r.result.teaching_steps)?r.result.teaching_steps.filter((s):s is Step=>object(s)&&['equation','reason_ar','reason_en'].every(k=>typeof s[k]==='string')).slice(0,12):[];
 const series:Point[][]=Array.isArray(r.result.series)?r.result.series.slice(0,12).map(s=>{
  const raw=object(s)?s.points:s;return Array.isArray(raw)?raw.slice(0,100).flatMap(p=>{
   if(!object(p)||!['string','number'].includes(typeof p.x)||!['string','number'].includes(typeof p.y))return [];const x=Number(p.x),y=Number(p.y);return Number.isFinite(x)&&Number.isFinite(y)?[{x,y}]:[];
  }):[];
 }).filter(s=>s.length>0):[];
 const risk=r.request.operation==='risk_change',inequality=r.request.operation==='inequality';
 const example=object(r.result.teaching_example)?r.result.teaching_example:null;
 const exampleSteps:Step[]=example&&Array.isArray(example.teaching_steps)?example.teaching_steps.filter((s):s is Step=>object(s)&&['equation','reason_ar','reason_en'].every(k=>typeof s[k]==='string')).slice(0,12):[];
 if(!steps.length&&!series.length&&!risk)return null;
 const conditions=strings(r.conditions),checks=Array.isArray(r.verification)?r.verification.filter(object):[];
 return <section className="math-explanation" aria-label={t('شرح النتيجة المتحققة','Explain the checked result')}>
  <h3>{t('افهم النتيجة','Understand the result')}</h3>
  <div className="education-actions" role="group" aria-label={t('مستوى الشرح','Explanation level')}>{[['simple','مبسط','Simple'],['detailed','مفصل','Detailed'],['technical','جامعي / تقني','Technical']].map(([id,a,e])=><button type="button" key={id} aria-pressed={level===id} onClick={()=>setLevel(id)}>{t(a,e)}</button>)}</div>
  <p>{t('المجال','Domain')}: <bdi>{String(r.request.domain??'real')}</bdi> · {t('نوع النتيجة','Result type')}: <bdi>{String(r.result_kind)}</bdi></p>
  {conditions.length>0&&<p>{t('الشروط التي تبقى في كل مستوى: ','Conditions retained at every level: ')}{conditions.map((c,i)=><span key={i}><bdi>{c}</bdi>{i<conditions.length-1?'؛ ':''}</span>)}</p>}
  {steps.length>0&&<><p>{inequality?t('المتباينة تقارن كميتين. الضرب أو القسمة في سالب يعكس اتجاه المقارنة.','An inequality compares two quantities. Multiplying or dividing by a negative number reverses its direction.'):t('الرمز يمثل العدد المجهول. نحافظ على المساواة بإجراء العملية نفسها على الطرفين.','The symbol represents the unknown number. Applying the same operation to both sides preserves equality.')}</p>
   {level==='simple'?<><pre dir="ltr">{steps.at(-1)?.equation}</pre><p>{ar?steps.at(-1)?.reason_ar:steps.at(-1)?.reason_en}</p></>:<ol>{steps.map((s,i)=><li key={i}><pre dir="ltr">{s.equation}</pre><p>{ar?s.reason_ar:s.reason_en}</p></li>)}</ol>}
   {level==='technical'&&<p>{t('التحويلات هنا مكافئة في المجال المعروض. القسمة مشروعة فقط على مقدار غير صفري؛ التعويض يختبر المرشح في العلاقة الأصلية، ولا يثبت وحده اكتمال حلول مسألة عامة.','These transformations are equivalent in the displayed domain. Division requires a nonzero divisor. Substitution checks a candidate in the original relation; it alone does not establish completeness for a general problem.')}</p>}
   <button type="button" onClick={()=>setAlternative(v=>!v)} aria-expanded={alternative}>{t('لم أفهم — تمثيل آخر','Another explanation')}</button>
   {alternative&&<p>{inequality?t('على خط الأعداد، الأصغر يقع يسار الأكبر. الضرب في عدد سالب يعكس ترتيبهما حول الصفر، لذلك تنعكس الإشارة؛ راجع العملية العددية في الخطوات.','On a number line, the smaller number lies to the left. Multiplying by a negative reverses their order around zero, so the inequality reverses; see the computed step.'):t('تخيّل طرفي المعادلة ككفتي ميزان متعادل: إذا أزلت المقدار نفسه من الكفتين يبقى التوازن. وبعد إزالة الحد المضاف، قسّم الكميتين إلى العدد نفسه من الأجزاء. هذا تشبيه للمساواة؛ الخطوات والمعادلة الأصلية أعلاه هي التحقق الرياضي.','Imagine an equal balance: removing the same amount from both pans keeps it balanced. After removing the added term, split both amounts into the same number of equal parts. This is an analogy for equality; the equations above provide the mathematical check.')}</p>}
   {exampleSteps.length>0&&<><button type="button" onClick={()=>setExtra(v=>!v)} aria-expanded={extra}>{t('مثال إضافي','Another example')}</button>{extra&&<div className="math-example"><p>{t('مثال قريب حُسب وتُحقق منه بصورة منفصلة؛ ليس تغييرًا لجواب المسألة الأصلية.','A related example computed and checked separately; it does not change the original answer.')}</p><ol>{exampleSteps.map((s,i)=><li key={i}><pre dir="ltr">{s.equation}</pre><p>{ar?s.reason_ar:s.reason_en}</p></li>)}</ol>{object(example?.substitution)&&<pre dir="ltr">{JSON.stringify(example.substitution,null,2)}</pre>}</div>}</>}
  </>}
  {series.length>0&&<Plot series={series} ar={ar}/>}
  {risk&&<><p>{t('نقارن السكان أنفسهم خلال الفترة نفسها. هذه أرقام تعليمية افتراضية وليست احتمالًا شخصيًا.','Compare the same population over the same period. These are hypothetical educational values, not a personal probability.')}</p><p>{t('الفترة: ','Period: ')}{String(r.result.period)} · {t('المقام: ','Denominator: ')}<bdi>{String(r.result.denominator)}</bdi></p><dl><dt>{t('الانخفاض المطلق','Absolute reduction')}</dt><dd dir="ltr">{String(r.result.absolute_reduction)}</dd><dt>{t('الانخفاض بالنقاط المئوية','Reduction in percentage points')}</dt><dd dir="ltr">{String(r.result.percentage_points)}</dd><dt>{t('الانخفاض النسبي بالمئة','Relative percent reduction')}</dt><dd dir="ltr">{r.result.relative_percent===null?t('غير معرّف لأن الخطر الأصلي صفر','Undefined because the original risk is zero'):String(r.result.relative_percent)+'%'}</dd></dl>{level!=='simple'&&<p>{t('الفرق المطلق يطرح الخطر الجديد من القديم. الانخفاض النسبي يقسم هذا الفرق على الخطر القديم، لذلك قد تبدو نسبته كبيرة رغم صغر الفرق المطلق. لا يعني الانخفاض زوال الخطر.','The absolute difference subtracts the new risk from the old risk. Relative reduction divides that difference by the old risk, so it can look large while the absolute difference is small. Reduction does not mean the risk disappeared.')}</p>}</>}
  <button type="button" onClick={()=>setVerify(v=>!v)} aria-expanded={verify}>{t('تحقق من الحل','Check the solution')}</button>
  {verify&&<><p>{t('هذه الفحوص نُفذت مع النتيجة المحفوظة؛ فتحها لا يعيد الحساب. زر إعادة المحاولة يحسب الطلب مجددًا.','These checks ran with the stored result; opening them does not rerun computation. Retry recomputes the request.')}</p><ul>{checks.map((c,i)=><li key={i}><bdi>{String(c.check)}: {String(c.status)}</bdi> — {String(c.detail)}</li>)}</ul>{object(r.result.substitution)&&<pre dir="ltr">{JSON.stringify(r.result.substitution,null,2)}</pre>}</>}
  <p>{steps.length&&!inequality?t('اختياري: كيف تشرح سبب إجراء العملية نفسها على الطرفين بكلماتك؟','Optional: how would you explain applying the same operation to both sides?'):t('اختياري: كيف تشرح الفكرة أو أحد شروطها بكلماتك؟','Optional: how would you explain the idea or one of its conditions?')} {t('لا يلزم الإجابة للمتابعة، ولا يُحفظ اختيار المستوى كتفضيل دائم.','You can continue without answering; the selected level is not saved as a lasting preference.')}</p>
 </section>;
}
