import {localFetch} from './client-session';
import {api,apiBase} from './api';
export type VideoAsset={id:string;filename:string;kind:'image'|'video'|'audio';mime:string;width:number|null;height:number|null;duration:number|null;has_audio:boolean;bytes:number;sha256:string;origin:{type:string;rights:string;training_allowed:false}};
export type VideoClip={id:string;asset_id:string;start:number;duration:number;muted:boolean;gain:number;motion:'none'|'pan_zoom'};
export type VideoText={id:string;kind:'title'|'subtitle';text:string;start:number;end:number;x:number;y:number;width:number;height:number;language:'ar'|'en'|'he'|'es';direction:'rtl'|'ltr'|'auto';font:'arabic'|'latin'|'hebrew';size:number;weight:400|600;color:string;protected?:true;align:'left'|'center'|'right'};
export type VideoAudio={id:string;asset_id:string;start:number;source_start:number;duration:number;gain:number;role:'narration'|'music'|'effect'};
export type VideoSpec={width:number;height:number;fps:24|30;background:string;clips:VideoClip[];texts:VideoText[];audio:VideoAudio[];brief:{goal:string;audience:string;style:string;voice_language:string;text_language:string;reference_notes:string}};
export type VideoTimelineSummary={id:string;project_id:string;title:string;version:number;created_at:string;updated_at:string};
export type VideoTimeline={id:string;project_id?:string;title:string;version:number;spec:VideoSpec;created_at:string;updated_at:string};
export type VideoVersion={version:number;title:string;sha256?:string;created_at:string};
export type VideoJob={id:string;status:string;version:number;timeline_id:string;progress:{stage?:string;step?:number;total?:number;frame?:number;frames?:number;total_frames?:number;processed_seconds?:number;duration?:number;[key:string]:unknown}|null;report:Record<string,unknown>|null;error:string|null;created_at:string;updated_at:string;outdated:boolean};
export type VideoCapability={available:boolean;reason?:string;engine?:string;model?:string;experimental?:boolean};
export type VideoStatus={render:VideoCapability;generation?:VideoCapability;arabic_tts?:VideoCapability;limits?:Record<string,unknown>;notice?:string};
const enc=encodeURIComponent;
export const videoPath=(pid:string)=>`/projects/${enc(pid)}/video`;
export const videoApi={
 status:(pid:string,signal?:AbortSignal)=>api<VideoStatus>(videoPath(pid)+'/status',{signal}),
 assets:(pid:string,signal?:AbortSignal)=>api<VideoAsset[]>(videoPath(pid)+'/assets',{signal}),
 upload:(pid:string,file:File,license:string,signal?:AbortSignal)=>{const data=new FormData();data.append('file',file);if(license)data.append('license',license);return api<VideoAsset>(videoPath(pid)+'/assets',{method:'POST',body:data,signal});},
 removeAsset:(pid:string,id:string,signal?:AbortSignal)=>api(videoPath(pid)+'/assets/'+enc(id)+'?confirm=true',{method:'DELETE',signal}),
 timelines:(pid:string,signal?:AbortSignal)=>api<VideoTimelineSummary[]>(videoPath(pid)+'/timelines',{signal}),
 timeline:(pid:string,id:string,signal?:AbortSignal)=>api<VideoTimeline>(videoPath(pid)+'/timelines/'+enc(id),{signal}),
 save:(pid:string,id:string|null,input:{title:string;spec:VideoSpec;expected_version?:number},signal?:AbortSignal)=>api<VideoTimeline>(videoPath(pid)+'/timelines'+(id?'/'+enc(id):''),{method:id?'PATCH':'POST',body:JSON.stringify(input),signal}),
 versions:(pid:string,id:string,signal?:AbortSignal)=>api<VideoVersion[]>(videoPath(pid)+'/timelines/'+enc(id)+'/versions',{signal}),
 restore:(pid:string,id:string,expected:number,version:number,signal?:AbortSignal)=>api<VideoTimeline>(videoPath(pid)+'/timelines/'+enc(id)+'/restore',{method:'POST',body:JSON.stringify({expected_version:expected,from_version:version}),signal}),
 removeTimeline:(pid:string,id:string,version:number,signal?:AbortSignal)=>api(videoPath(pid)+'/timelines/'+enc(id)+`?confirm=true&expected_version=${version}`,{method:'DELETE',signal}),
 jobs:(pid:string,signal?:AbortSignal)=>api<VideoJob[]>(videoPath(pid)+'/jobs',{signal}),
 job:(pid:string,id:string,signal?:AbortSignal)=>api<VideoJob>(videoPath(pid)+'/jobs/'+enc(id),{signal}),
 render:(pid:string,id:string,version:number,key:string,signal?:AbortSignal)=>api<VideoJob>(videoPath(pid)+'/jobs',{method:'POST',body:JSON.stringify({timeline_id:id,version,idempotency_key:key}),signal}),
 cancel:(pid:string,id:string,signal?:AbortSignal)=>api<VideoJob>(videoPath(pid)+'/jobs/'+enc(id)+'/cancel',{method:'POST',signal}),
};
export async function videoBlob(pid:string,suffix:string,signal?:AbortSignal){const response=await localFetch(apiBase+'/api'+videoPath(pid)+suffix,{headers:{'X-Riwaq-Client':'local'},signal});if(!response.ok){let message=`HTTP ${response.status}`;try{const value=await response.json();message=typeof value.detail==='string'?value.detail:JSON.stringify(value.detail??value);}catch{/* keep status */}throw Error(message);}return response.blob();}
export function saveVideoBlob(blob:Blob,name:string){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name.replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').slice(0,180);a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
export const initialVideoSpec=(language:'ar'|'en'):VideoSpec=>({width:640,height:360,fps:24,background:'#172B2A',clips:[],texts:[],audio:[],brief:{goal:'',audience:'',style:'',voice_language:language,text_language:language,reference_notes:''}});
export const videoDuration=(spec:VideoSpec)=>spec.clips.reduce((total,clip)=>total+clip.duration,0);
