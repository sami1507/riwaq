import {useEffect,useRef,useState} from 'react';
import {visualApi,saveVisualBlob,type VisualAsset,type VisualCodeStatus} from './visual-api';

export default function VisualCodesPanel({projectId,ar,onCreated}:{projectId:string;ar:boolean;onCreated:()=>void}){
 const [kind,setKind]=useState<'qr'|'code128'>('qr'),[text,setText]=useState(''),[status,setStatus]=useState<VisualCodeStatus|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[asset,setAsset]=useState<VisualAsset|null>(null),[preview,setPreview]=useState('');
 const alive=useRef(true),active=useRef<AbortController|null>(null),retry=useRef<{signature:string;key:string}|null>(null);
 const t=(a:string,e:string)=>ar?a:e;
 useEffect(()=>{alive.current=true;const controller=new AbortController();visualApi.codeStatus(projectId,controller.signal).then(value=>{if(!controller.signal.aborted)setStatus(value);}).catch(e=>{if(!controller.signal.aborted)setError(String(e));});return()=>{alive.current=false;controller.abort();active.current?.abort();};},[projectId]);
 useEffect(()=>{const controller=new AbortController();let url='';setPreview('');if(asset)visualApi.blob(projectId,`/assets/${encodeURIComponent(asset.id)}/content`,controller.signal).then(blob=>{if(!controller.signal.aborted){url=URL.createObjectURL(blob);setPreview(url);}}).catch(e=>{if(!controller.signal.aborted)setError(String(e));});return()=>{controller.abort();if(url)URL.revokeObjectURL(url);};},[projectId,asset]);
 async function create(){
  if(active.current||!status?.available)return;
  const signature=JSON.stringify([kind,text]);if(retry.current?.signature!==signature)retry.current={signature,key:crypto.randomUUID()};
  const controller=new AbortController();active.current=controller;setBusy(true);setError('');
  try{const result=await visualApi.code(projectId,{kind,text,scale:4,request_key:retry.current.key},controller.signal);if(alive.current&&!controller.signal.aborted){setAsset(result);retry.current=null;onCreated();}}
  catch(e){if(alive.current)setError(controller.signal.aborted?t('أُوقف الطلب. لم تُعلن صورة جديدة؛ حدّث قائمة الصور للتحقق من أي حفظ سبق الإيقاف.','Request stopped. No new image claimed; refresh assets to check any save preceding cancellation.'):String(e));}
  finally{active.current=null;if(alive.current)setBusy(false);}
 }
 async function download(){if(!asset)return;try{const blob=await visualApi.blob(projectId,`/assets/${encodeURIComponent(asset.id)}/content`);if(alive.current)saveVisualBlob(blob,asset.filename);}catch(e){if(alive.current)setError(String(e));}}
 return <details className="visual-export" data-testid="visual-codes-panel"><summary>{t('رمز QR أو Code128 قابل للمسح','Scannable QR or Code128')}</summary>
  <p className="visual-hint">{t('يُحفظ النص كما كتبته، ويُفك PNG الناتج بقارئ مستقل قبل الحفظ. لا يُفتح الرابط أو يُرسل النص إلى خدمة. افحص المسح مجددًا بعد تغيير المقاس أو الطباعة.','Literal text is preserved and the generated PNG is decoded independently before saving. Links are not opened and text is not sent to a service. Scan again after resizing or printing.')}</p>
  {!status&&<p role="status">{t('جارٍ فحص العامل…','Checking the worker…')}</p>}{status&&!status.available&&<p role="status">{t('عامل الرموز المتحقق غير مهيأ.','The verified code worker is not configured.')}</p>}
  <fieldset disabled={busy||!status?.available}><legend className="sr-only">{t('مدخل الرمز','Code input')}</legend>
   <label className="field-label">{t('نوع الرمز','Code type')}<select aria-label={t('نوع الرمز','Code type')} className="input" value={kind} onChange={e=>setKind(e.target.value as 'qr'|'code128')}><option value="qr">QR · UTF-8</option><option value="code128">Code128 · ASCII</option></select></label>
   <label className="field-label">{t('النص الحرفي للرمز','Literal code text')}<textarea aria-label={t('النص الحرفي للرمز','Literal code text')} className="input" dir="auto" rows={2} maxLength={512} value={text} onChange={e=>setText(e.target.value)}/></label>
   <p className="visual-hint">{kind==='qr'?t('حتى 512 بايت UTF-8، دون محارف تحكم.','Up to 512 UTF-8 bytes, without control characters.'):t('حتى 80 محرف ASCII قابلًا للطباعة. الأصفار البادئة محفوظة.','Up to 80 printable ASCII characters. Leading zeros are preserved.')}</p>
   <button type="button" className="button primary" disabled={!text.trim()} onClick={()=>void create()}>{t('إنشاء الرمز والتحقق منه','Create and verify code')}</button>
  </fieldset>
  {busy&&<><p role="status">{t('جارٍ الإنشاء وفك الرمز…','Generating and decoding…')}</p><button type="button" className="button subtle" onClick={()=>active.current?.abort()}>{t('إيقاف إنشاء الرمز','Stop code creation')}</button></>}
  {error&&<p role="alert" className="inline-error">{error}</p>}
  {error&&!busy&&<button type="button" className="button subtle" disabled={!status?.available||!text.trim()} onClick={()=>{retry.current=null;void create();}}>{t('بدء محاولة جديدة','Start a new attempt')}</button>}
  {asset&&<div className="visual-asset">{preview&&<img src={preview} alt={t('الرمز المحفوظ المتحقق','Saved verified code')} style={{maxWidth:'100%',height:'auto',imageRendering:'pixelated'}}/>}<p role="status">{t('فُك الرمز وطابقت حمولته المدخل، وحُفظ PNG.','Decoded payload matched the input and PNG was saved.')}</p><small>{asset.width} × {asset.height} · PNG</small><button type="button" className="button subtle" onClick={()=>void download()}>{t('تنزيل الرمز المتحقق','Download verified code')}</button></div>}
  <p className="visual-hint">{t('هذا لا يثبت قبول ألوان الطباعة أو النص داخل مشهد مولد.','This does not certify print color profiles or text inside a generated scene.')}</p>
 </details>;
}
