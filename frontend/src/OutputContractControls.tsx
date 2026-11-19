import {useEffect,useId,useState} from 'react';
import type {GenerateOutputContract} from './api';
import type {Lang} from './ui';
import './components/FactLedger.css';
export type OutputContract=GenerateOutputContract;
export type ContractDraft={enabled:boolean;schemaText:string;citationField:string};
export const initialContract:ContractDraft={enabled:false,schemaText:'',citationField:''};
export function contractDraft(contract?:OutputContract|null):ContractDraft{return contract?{enabled:true,schemaText:JSON.stringify(contract.schema,null,2),citationField:contract.citation_field??''}:{...initialContract};}
export function parseContract(value:ContractDraft,localProvider:boolean,lang:Lang):OutputContract|undefined{
 if(!value.enabled)return undefined;
 const t=(ar:string,en:string)=>lang==='ar'?ar:en;
 if(!localProvider)throw new Error(t('تنسيق JSON المحدد يتطلب المزود المحلي. اختره أو ألغِ خيار التنسيق.','The specified JSON format requires the local provider. Select it or disable the format option.'));
 if(!value.schemaText.trim())throw new Error(t('اكتب مخطط JSON أو ألغِ خيار التنسيق.','Enter a JSON schema or disable the format option.'));
 if(new TextEncoder().encode(value.schemaText).length>8192)throw new Error(t('حجم المخطط يتجاوز ٨ كيبيبايت.','The schema exceeds 8 KiB.'));
 let schema:unknown;
 try{schema=JSON.parse(value.schemaText);}catch{throw new Error(t('صياغة JSON غير صالحة. لم تُرسل الرسالة.','Invalid JSON syntax. The message was not sent.'));}
 if(!schema||typeof schema!=='object'||Array.isArray(schema)||(schema as Record<string,unknown>).type!=='object')throw new Error(t('يجب أن يكون نوع جذر المخطط object.','The schema root must have type object.'));
 const citationField=value.citationField.trim();
 return {schema:schema as Record<string,unknown>,...(citationField?{citation_field:citationField}:{})};
}
export default function OutputContractControls({value,onChange,lang,disabled,localProvider,error}:{value:ContractDraft;onChange:(value:ContractDraft)=>void;lang:Lang;disabled:boolean;localProvider:boolean;error?:string}){
 const id=useId(),t=(ar:string,en:string)=>lang==='ar'?ar:en;
 const [expanded,setExpanded]=useState(true);
 useEffect(()=>{if(error)setExpanded(true);},[error]);
 const patch=(next:Partial<ContractDraft>)=>onChange({...value,...next});
 return <div className="output-contract"><label className="studio-confirm"><input type="checkbox" checked={value.enabled} disabled={disabled||(!localProvider&&!value.enabled)} onChange={e=>{patch({enabled:e.target.checked});setExpanded(true);}}/><span>{t('تنسيق JSON محدد · متقدم','Specified JSON format · advanced')}</span></label>{value.enabled&&<details open={expanded} onToggle={e=>setExpanded(e.currentTarget.open)}><summary>{t('مخطط الإجابة','Answer schema')}</summary><fieldset disabled={disabled||!localProvider}><legend className="sr-only">{t('إعداد مخطط الإجابة','Answer schema settings')}</legend><label className="field-label" htmlFor={`${id}-schema`}>{t('مخطط JSON','JSON schema')}<textarea id={`${id}-schema`} className="input" dir="ltr" rows={6} spellCheck={false} value={value.schemaText} aria-invalid={Boolean(error)} aria-describedby={`${id}-hint${error?` ${id}-error`:''}`} onChange={e=>patch({schemaText:e.target.value})}/></label><button type="button" className="text-button" onClick={()=>patch({schemaText:JSON.stringify({type:'object',properties:{answer:{type:'string'}},required:['answer'],additionalProperties:false},null,2),citationField:''})}>{t('إدراج مثال قابل للتعديل','Insert editable example')}</button><label className="field-label" htmlFor={`${id}-citations`}>{t('اسم حقل الاستشهادات · اختياري','Citation field name · optional')}<input id={`${id}-citations`} className="input" dir="ltr" maxLength={80} value={value.citationField} onChange={e=>patch({citationField:e.target.value})}/></label><p id={`${id}-hint`} className="studio-hint">{t('حقل الاستشهادات، إن حُدّد، مصفوفة نصوص في الجذر لمعرّفات [S#] المرفقة فعلًا. تُقبل المصفوفة الفارغة. اجتياز المخطط يفحص البنية؛ لا يثبت صحة القيم أو دعم المصدر لها.','If specified, the citation field is a root array of strings for actual attached [S#] identifiers. An empty array is allowed. Passing the schema checks structure; it does not prove factual accuracy or source support.')}</p><p className="studio-hint">{t('جذر object؛ ٨ كيبيبايت، عمق ٥، ٦٤ عقدة، ٢٠ خاصية كحد أقصى. يتحقق الخادم من الحدود والكلمات المدعومة.','Object root; maximum 8 KiB, depth 5, 64 nodes and 20 properties. The server validates limits and supported keywords.')}</p><details><summary>{t('الكلمات المدعومة','Supported keywords')}</summary><p className="studio-hint" dir="ltr">type, properties, required, additionalProperties: false, items, enum, minItems, maxItems, minLength, maxLength, minimum, maximum. Types: object, array, string, number, integer, boolean, null; nullable type lists. No $ref, oneOf or pattern.</p></details></fieldset></details>}{error&&<p id={`${id}-error`} className="knowledge-answer-notice" role="alert">{error}</p>}{!localProvider&&<p className="studio-hint">{t('هذا الخيار للنموذج المحلي فقط. ألغِه قبل إرسال طلب سحابي.','This option requires the local model. Disable it before sending a cloud request.')}</p>}</div>;
}
