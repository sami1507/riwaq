import {localFetch} from './client-session';
import {api,apiBase} from './api';
import type {Citation} from './api';
export type VoiceMode='conversation'|'transcription'|'translation';
export type LocalVoice={id:string;name:string;language:string;languages?:string[];local:boolean;gender?:string;tested?:boolean};
export type VoiceLanguage={tag:string;label_en:string;label_ar:string;engine_code?:string;kind?:string;model_token_present?:boolean;stt_available?:boolean;measured?:boolean;dialect_verified?:boolean;tts_voice_ids?:string[]};
export type VoiceStatus={stt:{available:boolean;reason?:string;model?:string};tts:{available:boolean;reason?:string;voices?:LocalVoice[]};voices?:LocalVoice[];languages:VoiceLanguage[];limits:Record<string,number>;modes?:string[];language_notice?:string};
export type VoiceConfig={mode:VoiceMode;input_language:string;reply_language:string;voice_id:string|null;rate:number;volume:number;use_files:boolean;segmented_speech?:boolean};
export type VoiceAudioPart={index:number;epoch:number;state:'ready';text:string;start:number;end:number;sha256:string;duration:number;ready_seconds:number;synthesis_seconds:number};
export type VoiceTurn={id:string;epoch:number;state:string;origin:'microphone'|'upload'|'typed';transcript_original:string;transcript:string;detected_language:string|null;language_probability?:number|null;segments?:Array<{start:number;end:number;text:string}>;reply:string;spoken_text:string;citations:Citation[];options?:VoiceConfig;quality_notice?:string;audio_ready:boolean;audio_parts?:VoiceAudioPart[];audio_parts_complete?:boolean;speech_delivery?:{parts_total:number};audio_duration:number|null;playback:{played_seconds:number;completed:boolean};error:string|null;timings:Record<string,unknown>};
export type VoiceSession=VoiceConfig&{id:string;epoch:number;state:string;turns:VoiceTurn[];expires_at:number;active?:boolean;token?:string};
export type PlaybackReceipt={expected_epoch:number;played_seconds:number;completed:boolean};
export const voicePath=(pid:string,sid?:string)=>`/projects/${encodeURIComponent(pid)}/voice${sid?'/sessions/'+encodeURIComponent(sid):''}`;
const auth=(token:string)=>({'X-Riwaq-Voice-Token':token});
export const voiceApi={
 status:(pid:string,signal?:AbortSignal)=>api<VoiceStatus>(voicePath(pid)+'/status',{signal,cache:'no-store'}),
 create:(pid:string,config:VoiceConfig,signal?:AbortSignal)=>api<VoiceSession>(voicePath(pid)+'/sessions',{method:'POST',body:JSON.stringify(config),signal}),
 session:(pid:string,sid:string,token:string,signal?:AbortSignal)=>api<VoiceSession>(voicePath(pid,sid),{headers:auth(token),signal,cache:'no-store'}),
 configure:(pid:string,sid:string,token:string,config:VoiceConfig,signal?:AbortSignal)=>api<VoiceSession>(voicePath(pid,sid),{method:'PATCH',headers:auth(token),body:JSON.stringify(config),signal}),
 transcribe:(pid:string,sid:string,token:string,file:File,language:string,epoch:number,origin:'microphone'|'upload',signal?:AbortSignal)=>{const form=new FormData();form.append('file',file);form.append('language',language);form.append('expected_epoch',String(epoch));form.append('turn_key',crypto.randomUUID());form.append('origin',origin);return api<VoiceTurn>(voicePath(pid,sid)+'/transcribe',{method:'POST',headers:auth(token),body:form,signal});},
 submit:(pid:string,sid:string,token:string,input:{turn_id?:string;text:string;turn_key:string;expected_epoch:number},signal?:AbortSignal)=>api<VoiceTurn>(voicePath(pid,sid)+'/turns',{method:'POST',headers:auth(token),body:JSON.stringify(input),signal}),
 interrupt:(pid:string,sid:string,token:string,input:PlaybackReceipt&{turn_id?:string},signal?:AbortSignal)=>api<VoiceSession>(voicePath(pid,sid)+'/interrupt',{method:'POST',headers:auth(token),body:JSON.stringify(input),signal}),
 playback:(pid:string,sid:string,token:string,tid:string,receipt:PlaybackReceipt,signal?:AbortSignal)=>api(voicePath(pid,sid)+'/turns/'+encodeURIComponent(tid)+'/playback',{method:'POST',headers:auth(token),body:JSON.stringify({expected_epoch:receipt.expected_epoch,played_seconds:receipt.played_seconds,completed:receipt.completed}),signal}),
 save:(pid:string,sid:string,token:string,title:string,signal?:AbortSignal)=>api<{conversation_id:string;saved_turns:number}>(voicePath(pid,sid)+'/save',{method:'POST',headers:auth(token),body:JSON.stringify({confirm:true,...(title?{title}:{})}),signal}),
 end:(pid:string,sid:string,token:string,signal?:AbortSignal)=>api<{ended:boolean}>(voicePath(pid,sid)+'/end',{method:'POST',headers:auth(token),signal,keepalive:true}),
};
export async function voiceAudioBlob(pid:string,sid:string,token:string,tid:string,signal?:AbortSignal){const response=await localFetch(apiBase+'/api'+voicePath(pid,sid)+'/turns/'+encodeURIComponent(tid)+'/audio',{headers:{...auth(token),'X-Riwaq-Client':'local'},signal,cache:'no-store'});if(!response.ok){let message=`HTTP ${response.status}`;try{const error=await response.json();message=typeof error.detail==='string'?error.detail:JSON.stringify(error.detail??error);}catch{/* keep status */}throw Error(message);}return response.blob();}
