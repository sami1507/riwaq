import {useState} from 'react';
import {Check,CircleAlert,FileText,Languages} from 'lucide-react';
import {Dialog,Logo} from './ui';
import {useAppearance} from './preferences';
import CitationCard from './CitationCard';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
export const mixedSample=`# مِسَاحَةٌ لِفَهْمِ الفِكْرَةِ

هٰذِهِ فِقْرَةٌ عَرَبِيَّةٌ بِالتَّشْكِيلِ: نُرَاجِعُ المَعْنَى، وَنُحَافِظُ عَلَى وُضُوحِ القِرَاءَةِ. يربط المشروع TypeScript مع API بإصدار v2.1، دون تغيير أسماء الدوال أو القيم عند النسخ.

1. اقرأ الملف \`customer-project-configuration-with-a-very-long-english-filename.ts\`.
2. راجع [الوثائق الرسمية](https://www.typescriptlang.org/docs/).

| البند | العدد | الإصدار |
| --- | ---: | --- |
| طلبات API | 1234 | v2.1 |
| ملفات المشروع | ٢٣ | 2026-11-13 |

\`\`\`typescript
// مثال عربي — لا يُنفّذ داخل المعرض
const version = "v2.1";
function greet(name: string): string {
  return \`مرحبًا \${name}\`;
}
\`\`\`

> المصادر المتاحة لا تكفي لتأكيد هذه المعلومة.
`;
export default function DevGallery(){const [dialog,setDialog]=useState(false);const {dark,setTheme}=useAppearance();return <main className="dev-gallery" dir="rtl" lang="ar"><header><Logo/><h1>رواق · معرض المكونات</h1><p>للتطوير فقط. الأمثلة التالية لا تُضاف إلى المحادثات أو قاعدة المعرفة.</p><button className="button subtle" onClick={()=>setTheme(dark?'light':'dark')}>{dark?'الوضع الفاتح':'الوضع الداكن'}</button></header><section><h2>العلامة والطباعة</h2><img alt="رواق" src={dark?'/brand/riwaq-wordmark-dark.svg':'/brand/riwaq-wordmark-light.svg'} width="260" height="100"/><div className="icon-samples">{[16,24,32].map(size=><figure key={size}><img src={dark?'/brand/riwaq-symbol-dark.svg':'/brand/riwaq-symbol-light.svg'} width={size} height={size} alt={`رمز رواق ${size}`} /><figcaption>{size}px</figcaption></figure>)}</div><p>وضوح للفكرة. خطوة للعمل.</p></section><section><h2>اللون والحالة</h2><div className="swatches"><div style={{background:'var(--surface)'}}>سطح القراءة</div><div style={{background:'var(--accent)',color:'var(--color-on-brand)'}}>الفعل الرئيسي</div><div style={{color:'var(--color-error)'}}><CircleAlert/> تعذّر الاتصال</div><div style={{color:'var(--color-success)'}}><Check/> محفوظ</div></div><div className="studio-actions"><button className="button primary" onClick={()=>setDialog(true)}>افتح الحوار</button><button className="button subtle"><Languages/> جسر المصطلحات</button><button className="button subtle"><FileText/> من الفكرة إلى ملف</button><button className="button primary" disabled>غير متاح</button></div></section><section><div className="markdown" dir="auto"><ReactMarkdown remarkPlugins={[remarkGfm]}>{mixedSample}</ReactMarkdown></div><CitationCard index={0} lang="ar" citation={{scope:'private',filename:'typography-fixture.md',line_start:1,line_end:8,text:'مثال عرض فقط، وليس وثيقة مستوردة.'}}/></section><section><label className="field-label">المسودة<textarea className="input" dir="auto" defaultValue="شرح TypeScript مع API v2.1"/></label><p role="status">جارٍ انتظار مزود النموذج…</p><p className="inline-error" role="alert">تعذّر استيراد الملف. جرّب مجددًا أو اطّلع على السبب.</p></section>{dialog&&<Dialog title="حوار تجريبي" onClose={()=>setDialog(false)}><div className="form-body"><label className="field-label">عنوان<input className="input"/></label><p>اختبر Tab وShift Tab وEscape، ثم عُد إلى زر فتح الحوار.</p><button className="button primary" onClick={()=>setDialog(false)}>إغلاق</button></div></Dialog>}</main>}
