import {useEffect, useRef, useState} from 'react';
import {api, type Project} from './api';
import {Dialog} from './ui';
import './conversation-library.css';

export type LibraryConversation = {id:string;project_id:string;title:string;version:number;archived:boolean;updated_at:string};
type Detail = LibraryConversation & {move:{eligible:boolean;reason:string|null;message:string}};
type Props = {projectId:string;projects:Project[];lang:'ar'|'en';onClose:()=>void;onOpen:(id:string,projectId:string)=>void;onChanged:(item:LibraryConversation)=>void|Promise<void>};

export default function ConversationLibrary({projectId,projects,lang,onClose,onOpen,onChanged}:Props){
 const ar=lang==='ar';
 const t=(arabic:string,english:string)=>ar?arabic:english;
 const path=`/projects/${encodeURIComponent(projectId)}/conversation-library`;
 const [query,setQuery]=useState('');
 const [filter,setFilter]=useState<'active'|'archived'|'all'>('active');
 const [items,setItems]=useState<LibraryConversation[]>([]);
 const [selected,setSelected]=useState<Detail|null>(null);
 const [title,setTitle]=useState('');
 const [target,setTarget]=useState('');
 const [ack,setAck]=useState(false);
 const [loading,setLoading]=useState(true);
 const [detailLoading,setDetailLoading]=useState(false);
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState('');
 const [notice,setNotice]=useState('');
 const [revision,setRevision]=useState(0);
 const detailRequest=useRef<AbortController|null>(null);
 const busyRef=useRef(false);
 const mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;detailRequest.current?.abort();};},[]);
 useEffect(()=>{
  const controller=new AbortController();
  setLoading(true);setError('');
  const timer=setTimeout(()=>{
   api<{items:LibraryConversation[]}>(`${path}?${new URLSearchParams({q:query,archived:filter})}`,{signal:controller.signal})
    .then(result=>{if(!controller.signal.aborted)setItems(result.items);})
    .catch(reason=>{if(!controller.signal.aborted)setError(String(reason.message??reason));})
    .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
  },180);
  return()=>{clearTimeout(timer);controller.abort();};
 },[path,query,filter,revision]);

 async function select(item:LibraryConversation){
  if(busyRef.current)return;
  detailRequest.current?.abort();const controller=new AbortController();detailRequest.current=controller;
  setSelected(null);setDetailLoading(true);setError('');setNotice('');setAck(false);setTarget('');
  try{
   const detail=await api<Detail>(`${path}/${encodeURIComponent(item.id)}`,{signal:controller.signal});
   if(!controller.signal.aborted){setSelected(detail);setTitle(detail.title);}
  }catch(reason){if(!controller.signal.aborted)setError(String((reason as Error).message));}
  finally{if(!controller.signal.aborted)setDetailLoading(false);}
 }

 async function mutate(kind:'rename'|'archive'|'move'){
  if(!selected||busyRef.current)return;
  busyRef.current=true;setBusy(true);setError('');setNotice('');
  const original=selected;
  try{
   const moving=kind==='move';
   const body=moving?{expected_version:original.version,target_project_id:target,acknowledge_context_change:ack}:
    {expected_version:original.version,...(kind==='rename'?{title:title.trim()}:{archived:!original.archived})};
   const result=await api<LibraryConversation>(`${path}/${encodeURIComponent(original.id)}${moving?'/move':''}`,{method:moving?'POST':'PATCH',body:JSON.stringify(body)});
   if(mounted.current){
    setRevision(value=>value+1);
    setNotice(moving?t('نُقلت المحادثة. ستستخدم الطلبات المقبلة سياق المشروع الجديد.','Conversation moved. Future requests use the new project context.'):
     kind==='rename'?t('حُفظ الاسم الجديد.','New title saved.'):
     result.archived?t('أُرشفت المحادثة. يمكنك استعادتها من قائمة المؤرشفة.','Conversation archived. Restore it from the archived list.'):
     t('استُعيدت المحادثة إلى القائمة النشطة.','Conversation restored to the active list.'));
    if(moving){setSelected(null);setAck(false);}else{setSelected({...original,...result});setTitle(result.title);}
   }
   // Parent refresh failure must not turn a committed mutation into "not saved".
   try{await onChanged(result);}catch{if(mounted.current)setError(t('تم الحفظ، لكن تحديث القائمة الرئيسية تعذر. أعد فتحها.','Saved, but the main list could not refresh. Reopen it.'));}
  }catch(reason){
   if(mounted.current)setError(String((reason as Error).message));
   // Keep the unsaved title and the reviewed version. Never silently retry a
   // conflict with a newer version; the user must select and review it again.
  }finally{busyRef.current=false;if(mounted.current)setBusy(false);}
 }

 const destinations=projects.filter(project=>project.id!==projectId);
 return <Dialog title={t('تنظيم المحادثات','Organize conversations')} wide className="conversation-library" onClose={onClose}>
  <div dir={ar?'rtl':'ltr'}>
   <p className="library-hint">{t('البحث في عناوين ورسائل المشروع الحالي المتاحة لك. الأرشفة تخفي المحادثة من القائمة النشطة وتحفظ محتواها.','Search titles and available messages in this project. Archiving keeps content and removes the conversation from the active list.')}</p>
   <div className="library-toolbar">
    <label>{t('بحث المحادثات','Search conversations')}<input type="search" value={query} maxLength={300} onChange={event=>setQuery(event.target.value)} disabled={busy} placeholder={t('عنوان أو نص رسالة','Title or message text')}/></label>
    <label>{t('عرض','Show')}<select value={filter} disabled={busy} onChange={event=>setFilter(event.target.value as typeof filter)}><option value="active">{t('النشطة','Active')}</option><option value="archived">{t('المؤرشفة','Archived')}</option><option value="all">{t('الكل','All')}</option></select></label>
    <button type="button" disabled={busy||loading} onClick={()=>setRevision(value=>value+1)}>{t('تحديث','Refresh')}</button>
   </div>
   {error&&<p role="alert" className="library-error">{error}</p>}
   {notice&&<p role="status" className="library-notice">{notice}</p>}
   <div className="library-layout">
    <section className="library-list" aria-label={t('نتائج المحادثات','Conversation results')} aria-busy={loading}>
     {loading?<p role="status">{t('جارٍ تحميل المحادثات…','Loading conversations…')}</p>:items.length===0?<p>{t('لا توجد محادثات مطابقة في هذا العرض.','No conversations match this view.')}</p>:<ul>{items.map(item=><li key={item.id}>
      <button type="button" disabled={busy} aria-pressed={selected?.id===item.id} onClick={()=>void select(item)}>
       <strong dir="auto">{item.title}</strong><small>{item.archived?t('مؤرشفة','Archived'):t('نشطة','Active')}</small>
      </button>
     </li>)}</ul>}
     {!loading&&items.length===200&&<p className="library-hint">{t('تظهر أول 200 نتيجة. ضيّق البحث للوصول إلى غيرها.','Showing the first 200 matches. Narrow your search for more.')}</p>}
    </section>
    <section className="library-editor" aria-label={t('إجراءات المحادثة','Conversation actions')} aria-busy={detailLoading||busy}>
     {detailLoading?<p role="status">{t('جارٍ فحص المحادثة…','Checking conversation…')}</p>:!selected?<p className="library-hint">{t('اختر محادثة لتغيير اسمها أو أرشفتها أو فحص إمكانية نقلها.','Choose a conversation to rename, archive or check whether it can move.')}</p>:<>
      <h3 dir="auto">{selected.title}</h3>
      <button type="button" disabled={busy} onClick={()=>onOpen(selected.id,selected.project_id)}>{t('فتح المحادثة','Open conversation')}</button>
      <form onSubmit={event=>{event.preventDefault();void mutate('rename');}}>
       <label>{t('اسم المحادثة','Conversation title')}<input dir="auto" value={title} maxLength={200} disabled={busy} onChange={event=>setTitle(event.target.value)}/></label>
       <button type="submit" className="primary" disabled={busy||!title.trim()||title.trim()===selected.title}>{t('حفظ الاسم','Save title')}</button>
      </form>
      <button type="button" disabled={busy} onClick={()=>void mutate('archive')}>{selected.archived?t('استعادة من الأرشيف','Restore from archive'):t('أرشفة المحادثة','Archive conversation')}</button>
      <hr/>
      <h4>{t('النقل إلى مشروع آخر','Move to another project')}</h4>
      <p className="library-hint">{t('يتغير سياق الطلبات المقبلة إلى تعليمات المشروع الجديد ومصادره. يُنقل سجل المحادثة المؤهل فقط؛ لا تُنقل ملفات أو ذكريات أو صلاحيات. المحادثات المرتبطة بمصادر أو مخرجات أو سجل توليد تبقى في مشروعها لحماية هذه الروابط.','Future requests use the destination project instructions and sources. Only eligible conversation history moves; files, memories and permissions do not. Conversations bound to sources, outputs or generation history stay in their project to preserve those links.')}</p>
      {!selected.move.eligible?<p className="library-blocked">{selected.move.message}</p>:destinations.length===0?<p>{t('أنشئ مشروعًا آخر أولًا لتتمكن من النقل.','Create another project before moving.')}</p>:<form onSubmit={event=>{event.preventDefault();void mutate('move');}}>
       <label>{t('المشروع الجديد','Destination project')}<select value={target} disabled={busy} onChange={event=>{setTarget(event.target.value);setAck(false);}}><option value="">{t('اختر مشروعًا','Choose a project')}</option>{destinations.map(project=><option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
       <label className="library-ack"><input type="checkbox" checked={ack} disabled={busy||!target} onChange={event=>setAck(event.target.checked)}/><span>{t('أفهم تغير السياق وأن الملفات والذاكرة لا تُنقل.','I understand that context changes and files and memory do not move.')}</span></label>
       <button type="submit" disabled={busy||!target||!ack}>{t('نقل المحادثة','Move conversation')}</button>
      </form>}
      {busy&&<p role="status">{t('جارٍ حفظ التغيير…','Saving change…')}</p>}
     </>}
    </section>
   </div>
  </div>
 </Dialog>;
}
