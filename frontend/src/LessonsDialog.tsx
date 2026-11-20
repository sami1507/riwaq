import { useEffect, useState } from 'react';
import { BookOpen, Pencil, Plus, Trash2 } from 'lucide-react';
import { api } from './api';
import { Dialog } from './ui';
import type { Lang } from './ui';

type Lesson={id:string;title:string;content:string;enabled:boolean;uses:number;updated_at:string};
export type LessonSeed={title:string;content:string};

// "Teach Riwaq": the user's own notebook of lessons. Everything stays on this device; the local model recalls from it.
export default function LessonsDialog({lang,seed,onClose}:{lang:Lang;seed:LessonSeed|null;onClose:()=>void}){
 const ar=lang==='ar';
 const [items,setItems]=useState<Lesson[]|null>(null);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 const [editing,setEditing]=useState<string|null>(null);const [title,setTitle]=useState(seed?.title??'');const [content,setContent]=useState(seed?.content??'');const [query,setQuery]=useState('');
 async function load(){try{setItems(await api<Lesson[]>('/lessons'));}catch(e){setError(e instanceof Error?e.message:String(e));}}
 useEffect(()=>{void load();},[]);
 function reset(){setEditing(null);setTitle('');setContent('');}
 async function run(action:()=>Promise<unknown>,after?:()=>void){setBusy(true);setError('');try{await action();after?.();await load();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}}
 const save=()=>run(()=>editing?api(`/lessons/${editing}`,{method:'PATCH',body:JSON.stringify({title:title.trim(),content:content.trim()})}):api('/lessons',{method:'POST',body:JSON.stringify({title:title.trim(),content:content.trim()})}),reset);
 const shown=(items??[]).filter(item=>!query.trim()||(item.title+' '+item.content).toLowerCase().includes(query.trim().toLowerCase()));
 return <Dialog title={ar?'علّم رواق':'Teach Riwaq'} onClose={onClose} wide><div className="form-body lessons">
  <p className="workspace-safety"><BookOpen size={17}/>{ar?'دروسك تُحفظ على هذا الجهاز فقط. عند سؤال مشابه يستعيدها رواق ويجيب منها، ويضع علامة على الجواب. الدرس الخاطئ سيتكرر، فراجع ما تحفظه.':'Your lessons stay on this device only. On a similar question Riwaq recalls them, answers from them, and marks the answer. A wrong lesson will be repeated, so review what you save.'}</p>
  <label className="field-label">{ar?'السؤال أو العنوان':'Question or title'}<input value={title} maxLength={400} onChange={e=>setTitle(e.target.value)} placeholder={ar?'مثال: متى موعد صيانة الضاغط؟':'Example: When is the compressor serviced?'} dir="auto"/></label>
  <label className="field-label">{ar?'الجواب الصحيح أو المعلومة':'The correct answer or information'}<textarea value={content} maxLength={4000} rows={5} onChange={e=>setContent(e.target.value)} dir="auto"/></label>
  {error&&<p className="form-error" role="alert">{error}</p>}
  <div className="modal-actions">{editing&&<button className="button subtle" onClick={reset}>{ar?'إلغاء التعديل':'Cancel edit'}</button>}<button className="button primary" disabled={busy||!title.trim()||!content.trim()} onClick={()=>void save()}><Plus size={15}/>{editing?(ar?'حفظ التعديل':'Save changes'):(ar?'احفظ الدرس':'Save lesson')}</button></div>
  <div className="lessons-head"><strong>{ar?`دفتر الدروس (${items?.length??0})`:`Lesson notebook (${items?.length??0})`}</strong><input value={query} onChange={e=>setQuery(e.target.value)} placeholder={ar?'ابحث في الدروس':'Search lessons'} aria-label={ar?'ابحث في الدروس':'Search lessons'} dir="auto"/></div>
  {items===null?<p className="lessons-empty">{ar?'جارٍ التحميل…':'Loading…'}</p>:shown.length===0?<p className="lessons-empty">{items.length===0?(ar?'لا دروس بعد. أضف أول درس من الأعلى، أو اضغط «احفظ كدرس» تحت أي جواب.':'No lessons yet. Add the first one above, or press "Save as lesson" under any answer.'):(ar?'لا نتائج.':'No results.')}</p>:
  <ul className="lessons-list">{shown.map(item=><li key={item.id} className={item.enabled?'':'off'}>
   <div className="lesson-text"><strong dir="auto">{item.title}</strong><p dir="auto">{item.content}</p><small>{ar?`استُخدم ${item.uses} مرة`:`Used ${item.uses} times`}{item.enabled?'':(ar?' · موقوف':' · paused')}</small></div>
   <div className="lesson-actions"><button disabled={busy} title={ar?'تعديل':'Edit'} aria-label={ar?'تعديل':'Edit'} onClick={()=>{setEditing(item.id);setTitle(item.title);setContent(item.content);}}><Pencil size={14}/></button>
   <button disabled={busy} className="lesson-toggle" onClick={()=>void run(()=>api(`/lessons/${item.id}`,{method:'PATCH',body:JSON.stringify({enabled:!item.enabled})}))}>{item.enabled?(ar?'أوقف':'Pause'):(ar?'فعّل':'Enable')}</button>
   <button disabled={busy} title={ar?'حذف':'Delete'} aria-label={ar?'حذف':'Delete'} onClick={()=>{if(window.confirm(ar?'حذف هذا الدرس نهائيًا؟':'Delete this lesson permanently?'))void run(()=>api(`/lessons/${item.id}`,{method:'DELETE'}),()=>{if(editing===item.id)reset();});}}><Trash2 size={14}/></button></div>
  </li>)}</ul>}
 </div></Dialog>;
}
