import type {Lang} from './ui';

const examples = [
  ['خطوات معادلة','Equation steps','حل 2*x+3=11','solve 2*x+3=11'],
  ['رسم بفروع منفصلة','Separate plot branches','ارسم 1/x على [-10,10]','plot 1/x on [-10,10]'],
  ['خطر مطلق ونسبي','Absolute and relative risk','{"operation":"risk_change","before":"2","after":"1","denominator":"1000","period":"سنة","scope":"hypothetical"}','{"operation":"risk_change","before":"2","after":"1","denominator":"1000","period":"one year","scope":"hypothetical"}'],
  ['حل معادلة بقيد','Constrained equation','حل x^2=4؛ بشرط: x<0','solve x^2=4; where: x<0'],
  ['الحلول المركبة','Complex roots','حل x^2+1=0؛ المجال: complex','solve x^2+1=0; domain: complex'],
  ['نظام معادلات','Equation system','حل النظام x+y=3 | x-y=1؛ المتغيرات: x,y','system x+y=3 | x-y=1; variables: x,y'],
  ['مشتقة','Derivative','اشتق x^3*sin(x)','differentiate x^3*sin(x)'],
  ['تكامل محدد','Definite integral','تكامل x^2؛ الحدود: 0,1','integrate x^2; bounds: 0,1'],
  ['جذر عددي','Numerical root','جذر عددي cos(x)-x؛ الحدود: 0,1؛ الدقة: 1/1000000000000','root cos(x)-x; bounds: 0,1; tolerance: 1/1000000000000'],
  ['قيم مفقودة وصفر','Missing values and zero','إحصاء [0,10,20,null]','statistics [0,10,20,null]'],
] as const;

export default function MathInputHelp({lang,disabled,onChoose,capability}:{lang:Lang;disabled:boolean;onChoose:(value:string)=>void;capability?:unknown}){
  const ar=lang==='ar';
  const unavailable=Boolean(capability&&typeof capability==='object'&&'available' in capability&&capability.available===false);
  return <details className="math-input-help">
    <summary>{ar?'صيغ الرياضيات والمجال':'Math notation and domain'}</summary>
    {unavailable&&<p role="status">{ar?'محرك المعادلات غير متاح في هذه البيئة. تبقى الحاسبة الأساسية مستقلة؛ راجع إعداد المحرك قبل المسائل المتقدمة.':'The equation engine is unavailable in this environment. The basic calculator remains separate; configure the engine before advanced problems.'}</p>}
    <p>{ar?'اختر مثالًا لتعديله قبل الحساب. الضرب صريح بعلامة *، والأس ^، والدوال sin وcos وln وsqrt وabs. المجال الافتراضي حقيقي؛ اذكر المركب أو الصحيح عند الحاجة.':'Choose an example to edit before calculating. Use * for multiplication, ^ for powers, and sin, cos, ln, sqrt or abs. The default domain is real; specify complex or integer when needed.'}</p>
    <div className="math-examples">{examples.map(([arabic,english,a,e])=><button key={english} type="button" disabled={disabled} onClick={()=>onChoose(ar?a:e)}>{ar?arabic:english}</button>)}</div>
    <p>{ar?'الأقواس مهمة: -2^2 = -4، و(-2)^2 = 4. الكتابة 1/2x ملتبسة. لا يشمل هذا المسار قراءة معادلة من صورة أو تفسير كل مسألة لفظية.':'Parentheses matter: -2^2 = -4, while (-2)^2 = 4. The notation 1/2x is ambiguous. This path does not read equations from images or interpret every word problem.'}</p>
  </details>;
}
