import {useEffect,useReducer,useRef} from 'react';
import type {SetStateAction} from 'react';
import {api} from './api';
import type {AnswerMode} from './api';

type Contents=Record<AnswerMode,string>;
type Saved={version:number;contents:Contents};
type Entry=Saved&{project:string;conversation:string;loaded:boolean;dirty:boolean;error:''|'load'|'save'|'conflict';flight:Promise<void>|null;loadFlight:Promise<void>|null;timer?:ReturnType<typeof setTimeout>};
const empty=():Contents=>({assistant:'',calculator:'',problem:''});
const same=(a:Contents,b:Contents)=>a.assistant===b.assistant&&a.calculator===b.calculator&&a.problem===b.problem;

/** Project/owner authority lives on the server; no draft text enters web storage. */
export function useComposerDrafts(project:string,conversation:string){
 const entries=useRef(new Map<string,Entry>()),alive=useRef(true),active=useRef('');const [,redraw]=useReducer(n=>n+1,0);
 const key=JSON.stringify([project,conversation]);active.current=key;
 const get=(p:string,c:string)=>{const k=JSON.stringify([p,c]);let value=entries.current.get(k);if(!value){value={project:p,conversation:c,version:0,contents:empty(),loaded:false,dirty:false,error:'',flight:null,loadFlight:null};entries.current.set(k,value);}return value;};
 const current=get(project,conversation),notify=()=>{if(alive.current)redraw();};
 const path=(e:Entry)=>`/projects/${e.project}/composer-draft`;
 async function flushEntry(e:Entry):Promise<void>{
  clearTimeout(e.timer);
  if(e.flight){await e.flight;return flushEntry(e);}
  if(!e.loaded||!e.dirty)return;
  if(e.error)throw Error(e.error);
  e.flight=(async()=>{
   while(e.dirty){const sent={...e.contents};
    try{const saved=await api<Saved>(path(e),{method:'PATCH',body:JSON.stringify({conversation_id:e.conversation||null,expected_version:e.version,contents:sent})});e.version=saved.version;e.dirty=!same(e.contents,sent);}
    catch(error){e.error=error instanceof Error&&/draft changed|saved draft changed|overwritten/i.test(error.message)?'conflict':'save';throw error;}
   }
  })();notify();
  try{await e.flight;}finally{e.flight=null;notify();}
 }
 async function load(e:Entry){
  if(e.loadFlight)return e.loadFlight;
  e.loadFlight=(async()=>{try{const saved=await api<Saved>(path(e)+(e.conversation?`?conversation_id=${encodeURIComponent(e.conversation)}`:''));e.version=saved.version;e.contents=saved.contents;e.loaded=true;e.dirty=false;e.error='';}
   catch{e.error='load';}finally{e.loadFlight=null;notify();}})();return e.loadFlight;
 }
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;for(const e of entries.current.values())clearTimeout(e.timer);};},[]);
 useEffect(()=>{if(project&&!current.loaded&&!current.error)void load(current);return()=>{if(current.dirty&&!current.error)void flushEntry(current).catch(()=>{});};},[key]);
 useEffect(()=>{const before=(event:BeforeUnloadEvent)=>{if([...entries.current.values()].some(e=>e.dirty||e.flight)){event.preventDefault();event.returnValue='';}};window.addEventListener('beforeunload',before);return()=>window.removeEventListener('beforeunload',before);},[]);
 function set(mode:AnswerMode,action:SetStateAction<string>){const e=entries.current.get(active.current);if(!e?.loaded)return;const next=typeof action==='function'?action(e.contents[mode]):action;if(next===e.contents[mode])return;e.contents={...e.contents,[mode]:next};e.dirty=true;clearTimeout(e.timer);if(!e.error)e.timer=setTimeout(()=>void flushEntry(e).catch(()=>{}),250);notify();}
 async function flush(){const e=entries.current.get(active.current);if(!e?.loaded||e.error)throw Error('Draft is not saved. Resolve the draft notice before sending.');await flushEntry(e);}
 async function transfer(target:string){
  const e=entries.current.get(active.current);if(!e||e.conversation)throw Error('Only a new-conversation draft can be transferred');
  await flushEntry(e);if(e.error)throw Error('The draft could not be saved');
  const moved={...e.contents};
  const saved=await api<Saved>(path(e)+'/transfer',{method:'POST',body:JSON.stringify({expected_version:e.version,target_conversation_id:target})});
  // Text typed during the transfer remains the next draft in the new scope.
  const latest={...e.contents},changed=!same(latest,moved);clearTimeout(e.timer);
  const next=get(e.project,target);Object.assign(next,saved,{contents:changed?latest:saved.contents,loaded:true,dirty:changed,error:''});e.version++;e.contents=empty();e.dirty=false;
  if(changed)next.timer=setTimeout(()=>void flushEntry(next).catch(()=>{}),250);notify();
 }
 const status=!project?'idle':current.error||(!current.loaded?'loading':current.flight||current.dirty?'saving':'saved');
 return {contents:current.loaded?current.contents:empty(),ready:current.loaded,status,set,flush,transfer,
  retry:async()=>{current.error='';notify();if(!current.loaded)await load(current);else await flushEntry(current).catch(()=>{});},
  reload:async()=>{clearTimeout(current.timer);if(current.flight)await current.flight.catch(()=>{});await load(current);},
 };
}
