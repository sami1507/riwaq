import {api,apiBase} from './api';
import {localFetch} from './client-session';
export type VisualPlane=Array<[number,number]>;
export type VisualText={id:string;text:string;language:'ar'|'en'|'he'|'es';direction:'rtl'|'ltr'|'auto';font:'arabic'|'latin'|'hebrew';size:number;weight:400|600;x:number;y:number;width:number;height:number;color:string;align:'left'|'center'|'right';protected:true;perspective?:VisualPlane|null};
export type VisualShape={id:string;kind:'rect'|'ellipse';x:number;y:number;width:number;height:number;fill:string};
export type VisualBrief={task:'image'|'poster'|'edit';subject:string;elements:string;medium:string;composition:string;lighting:string;palette:string;material:string;realism:string;instruction_language:'ar'|'en'|'es'|'he';text_language:'ar'|'en'|'es'|'he';visual_prompt:string};
export type VisualSpec={width:number;height:number;background:string;background_asset_id:string|null;layers:VisualText[];shapes:VisualShape[];brief:VisualBrief};
export type VisualDesignSummary={id:string;project_id:string;title:string;version:number;created_at:string;updated_at:string};
export type VisualDesign=VisualDesignSummary&{spec:VisualSpec;provenance:{text_sources:Array<{layer_id:string;text:string;utf8_sha256:string}>;spec_sha256:string;text_assurance:string}};
export type VisualVersion={design_id:string;version:number;title:string;spec:VisualSpec;provenance:VisualDesign['provenance'];created_at:string;byte_size:number};
export type VisualAsset={id:string;project_id:string;filename:string;mime:string;width:number;height:number;byte_size:number;sha256:string;created_at:string;origin:{kind?:string;job_id?:string;design_id?:string;design_version?:number;correction_count?:number;[key:string]:unknown}};
export type VisualAssets={items:VisualAsset[];storage_bytes:number;limit_bytes:number};
export type VisualCodeStatus={available:boolean;configured:boolean;tested:boolean;reason:string|null;formats:Array<'qr'|'code128'>;scope:string;image_id?:string;protocol:string;host_fallback:false};
export type VisualStatus={generation:{available:boolean;experimental:boolean;model:string;prompt_language:string;cloud:boolean;reason?:string|null};render:{available:boolean;engine:string;scope:string;formats:Array<'png'|'jpeg'|'webp'>;ocr:boolean};limits:{seconds:number;rss_bytes:number;active_jobs:number;corrections:number;pixels:number;steps:number;project_bytes:number};unsupported:string[];snapshot_notice:string};
export type VisualJobInput={kind:'generate'|'render'|'edit';design_id:string;version:number;prompt?:string;negative_prompt?:string;seed?:number;steps?:number;width?:number;height?:number;format?:'png'|'jpeg'|'webp';matte?:string;print_dpi?:number;asset_id?:string;mask_rect?:number[]};
export type VisualJob={id:string;project_id:string;kind:VisualJobInput['kind'];status:string;design_id:string;version:number;input:VisualJobInput;progress:{stage?:string;step?:number;total?:number};asset_id?:string|null;report:Record<string,unknown>;error?:string|null;created_at:string;updated_at:string};
export const visualBase=(projectId:string)=>`/projects/${encodeURIComponent(projectId)}/visual`;
export const visualApi={
 codeStatus:(p:string,s?:AbortSignal)=>api<VisualCodeStatus>(visualBase(p)+'/codes/status',{signal:s}),
 code:(p:string,input:{kind:'qr'|'code128';text:string;scale:number;request_key:string},s?:AbortSignal)=>api<VisualAsset>(visualBase(p)+'/codes',{method:'POST',body:JSON.stringify(input),signal:s}),
 status:(p:string,s?:AbortSignal)=>api<VisualStatus>(visualBase(p)+'/status',{signal:s}),
 designs:(p:string,s?:AbortSignal)=>api<VisualDesignSummary[]>(visualBase(p)+'/designs',{signal:s}),
 design:(p:string,id:string,s?:AbortSignal)=>api<VisualDesign>(visualBase(p)+'/designs/'+encodeURIComponent(id),{signal:s}),
 save:(p:string,data:{title:string;spec:VisualSpec},current?:VisualDesign,confirm=false,s?:AbortSignal)=>api<VisualDesign>(visualBase(p)+'/designs'+(current?'/'+encodeURIComponent(current.id):''),{method:current?'PATCH':'POST',body:JSON.stringify({...data,...(current?{expected_version:current.version,confirm_text_changes:confirm}:{})}),signal:s}),
 versions:(p:string,id:string,s?:AbortSignal)=>api<VisualVersion[]>(visualBase(p)+'/designs/'+encodeURIComponent(id)+'/versions',{signal:s}),
 restore:(p:string,current:VisualDesign,from:number,s?:AbortSignal)=>api<VisualDesign>(visualBase(p)+'/designs/'+encodeURIComponent(current.id)+'/restore',{method:'POST',body:JSON.stringify({expected_version:current.version,from_version:from}),signal:s}),
 deleteDesign:(p:string,current:VisualDesign,s?:AbortSignal)=>api(visualBase(p)+'/designs/'+encodeURIComponent(current.id)+`?expected_version=${current.version}&confirm=true`,{method:'DELETE',signal:s}),
 assets:(p:string,s?:AbortSignal)=>api<VisualAssets>(visualBase(p)+'/assets',{signal:s}),
 upload:(p:string,file:File,s?:AbortSignal)=>{const body=new FormData();body.append('file',file);return api<VisualAsset>(visualBase(p)+'/assets',{method:'POST',body,signal:s});},
 deleteAsset:(p:string,id:string,s?:AbortSignal)=>api(visualBase(p)+'/assets/'+encodeURIComponent(id)+'?confirm=true',{method:'DELETE',signal:s}),
 jobs:(p:string,s?:AbortSignal)=>api<VisualJob[]>(visualBase(p)+'/jobs',{signal:s}),
 start:(p:string,input:VisualJobInput,s?:AbortSignal)=>api<VisualJob>(visualBase(p)+'/jobs',{method:'POST',body:JSON.stringify(input),signal:s}),
 cancel:(p:string,id:string,s?:AbortSignal)=>api<VisualJob>(visualBase(p)+'/jobs/'+encodeURIComponent(id)+'/cancel',{method:'POST',signal:s}),
 async blob(p:string,path:string,s?:AbortSignal){const res=await localFetch(apiBase+'/api'+visualBase(p)+path,{signal:s});if(!res.ok)throw Error(`Visual download failed: HTTP ${res.status}`);return res.blob();},
};
export async function visualDataUrl(blob:Blob):Promise<string>{return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(Error('Image could not be read locally'));reader.readAsDataURL(blob);});}
export function saveVisualBlob(blob:Blob,filename:string){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
export const activeVisualJob=(job:VisualJob)=>['queued','running','cancelling'].includes(job.status);
export function blankVisualSpec(language:'ar'|'en'):VisualSpec{return {width:512,height:512,background:'#f6f3e9',background_asset_id:null,layers:[],shapes:[],brief:{task:'poster',subject:'',elements:'',medium:'flat illustration',composition:'',lighting:'',palette:'',material:'',realism:'',instruction_language:language,text_language:language,visual_prompt:''}};}
