import {useEffect,useRef,useState} from 'react';
import {apiBase} from './api';
import {localFetch} from './client-session';
import {voicePath} from './voice-api';
import type {VoiceTurn} from './voice-api';
import type {Lang} from './ui';

export default function VoicePartPlayer({projectId,sessionId,token,turn,lang,disabled,volume}:{projectId:string;sessionId:string;token:string;turn:VoiceTurn;lang:Lang;disabled:boolean;volume:number}){
 const [loaded,setLoaded]=useState<{index:number;url:string}|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const request=useRef<AbortController|null>(null),url=useRef(''),player=useRef<HTMLAudioElement>(null),mounted=useRef(true);
 const t=(ar:string,en:string)=>lang==='ar'?ar:en;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;request.current?.abort();player.current?.pause();if(url.current)URL.revokeObjectURL(url.current);};},[]);
 useEffect(()=>{if(player.current)player.current.volume=volume/100;},[volume,loaded]);
 useEffect(()=>{const node=player.current;return()=>{if(node){node.pause();node.removeAttribute('src');node.load();}};},[loaded]);
 useEffect(()=>{if(disabled){request.current?.abort();player.current?.pause();if(url.current)URL.revokeObjectURL(url.current);url.current='';setLoaded(null);}},[disabled]);
 async function load(index:number){
  const part=turn.audio_parts?.find(item=>item.index===index);if(!part||disabled)return;
  request.current?.abort();const controller=new AbortController();request.current=controller;setBusy(true);setError('');
  try{
   const response=await localFetch(apiBase+'/api'+voicePath(projectId,sessionId)+'/turns/'+encodeURIComponent(turn.id)+'/audio-parts/'+index,{headers:{'X-Riwaq-Voice-Token':token,'X-Riwaq-Client':'local'},signal:controller.signal,cache:'no-store'});
   if(!response.ok)throw Error(t('لم يعد الجزء متاحًا؛ حدّث الجلسة.','The part is no longer available; refresh the session.'));
   const bytes=await response.arrayBuffer();
   if(bytes.byteLength>8*1024*1024)throw Error('Speech part exceeds size limit');
   const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
   if(response.headers.get('X-Riwaq-Voice-Epoch')!==String(turn.epoch)||digest!==part.sha256)throw Error(t('تغير الجزء الصوتي؛ أعد تحميل الجلسة.','Speech part changed; reload the session.'));
   if(controller.signal.aborted||!mounted.current)return;
   player.current?.pause();if(url.current)URL.revokeObjectURL(url.current);
   url.current=URL.createObjectURL(new Blob([bytes],{type:'audio/wav'}));setLoaded({index,url:url.current});
  }catch(e){if(mounted.current&&!controller.signal.aborted)setError(e instanceof Error?e.message:String(e));}
  finally{if(mounted.current&&request.current===controller)setBusy(false);}
 }
 return <section className="voice-audio" aria-label={t('أجزاء صوتية مؤكدة','Confirmed speech parts')}>
  <p>{t('أجزاء جاهزة: ','Ready parts: ')}{turn.audio_parts?.length??0}/{turn.speech_delivery?.parts_total??0} · {turn.audio_parts_complete?t('اكتمل تجهيز الأجزاء','All parts prepared'):t('تجهيز جزئي','Partially prepared')}</p>
  <div className="voice-actions">{turn.audio_parts?.map(part=><button className="text-button" key={part.index} disabled={disabled||busy} onClick={()=>void load(part.index)}>{t('حمّل الجزء ','Load part ')}{part.index+1}</button>)}</div>
  {loaded&&<><audio ref={player} key={loaded.url} src={loaded.url} controls preload="metadata" aria-label={t('تشغيل جزء الرد','Response part playback')}/><pre dir="auto">{turn.audio_parts?.find(part=>part.index===loaded.index)?.text}</pre></>}
  {error&&<p role="alert">{error}</p>}
  <p className="voice-hint">{t('كل جزء من نص الرد المكتمل. تشغيل جزء لا يعني اكتمال الاستماع للرد؛ الإيقاف أو سحب المصدر يبطل الأجزاء.','Each part comes from the completed reply text. Playing one part does not establish full-response listening; interruption or source withdrawal invalidates the parts.')}</p>
 </section>;
}
