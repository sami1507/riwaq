import {localFetch} from './client-session';
import {apiBase} from './api';
export type CodingAuthority='read_only'|'propose'|'apply_local';
export type CodingMode='explain'|'review'|'diagnose'|'edit'|'tests'|'docs'|'dependency';
export type CodingWorkspace={id:string;project_id:string;grant_id:string;root:string;label:string;created_at:string};
export type CodingFile={path:string;size:number};
export type CodingReference={path:string;sha256:string;line_start:number;line_end:number};
export type CodingSymbol={name:string;qualified_name?:string;kind:string;line_start:number;line_end:number;method:string};
export type ContextFile=CodingFile&{sha256:string;language?:string;analysis_status?:string;symbols?:CodingSymbol[];imports?:Array<{module:string;names?:string[];line_start:number;line_end:number;method:string;resolved:boolean}>;dependencies?:unknown[];evidence?:Array<{line_start:number;line_end:number;text:string}>};
export type CodingContext={files?:ContextFile[];tree?:unknown;context_text?:string;context_bytes?:number;bindings?:Array<{path:string;sha256:string}>;warnings?:string[];git?:{head?:unknown;status_complete?:boolean;notice?:string;[key:string]:unknown}};
export type CodingRead={path:string;sha256:string;content:string|null;size:number;blocked_reason?:string|null;findings?:Array<{kind?:string;code?:string;line?:number}>};
export type CodingCheckRequest={request_key?:string;runner:'python_unittest'|'node_test';targets:string[];timeout_seconds:number;memory_mb:number};
export type CodingValidation={status:string;executed:boolean|null;tests_passed:boolean|null;test_count?:number|null;exit_code?:number|null;reason_code?:string;reason_ar?:string;reason_en?:string;runner?:string;targets?:string[];stdout?:string;stderr?:string;isolation_verified?:boolean;limits_enforced?:boolean;[key:string]:unknown};
export type CodingTask={id:string;workspace_id:string;project_id:string;revision:number;status:string;can_resume:boolean;can_undo:boolean;can_apply:boolean;error_code?:string|null;mode:CodingMode;authority:CodingAuthority;language:string;prompt:string;acceptance:string;selected_paths:string[];created_at:string;updated_at:string;answer:string;refs:CodingReference[];context:CodingContext|null;proposal:{state:'none'|'proposed'|'applied'|'undone'|'conflict'|'recovery_required';edits:Array<{path:string;original_sha:string;new_sha:string;diff:string}>;conflicts:Array<string|{path?:string;reason?:string;message?:string}>};validation:CodingValidation;events:Array<{time:string;stage:string;message:string}>;error:string|null;model:string|null;metrics?:{output_chars?:number;[key:string]:unknown}};
export type CodingStatus={local_only:boolean;modes:Array<{id:string;label_ar:string;label_en:string;available:boolean;reason?:string}>;languages:Array<{id:string;read:string;analyze:string;edit:string;format:boolean;build:boolean;test:boolean;run:boolean;note?:string}>;limits:Record<string,number>;isolation:{mode?:string;execution:{available:boolean;reason_ar?:string;reason_en?:string;reason_code?:string};declared_runners?:string[];notice?:string}};
export type CodingTaskInput={request_key:string;mode:CodingMode;authority:CodingAuthority;language:string;prompt:string;selected_paths:string[];acceptance:string;test_request?:CodingCheckRequest};
export class CodingApiError extends Error{status:number;payload:unknown;constructor(status:number,payload:unknown){const data=payload as {detail?:unknown;message?:string};super(typeof data?.detail==='string'?data.detail:data?.message??`HTTP ${status}`);this.status=status;this.payload=payload;}}
const base=(pid:string)=>`/projects/${encodeURIComponent(pid)}/coding`;
const ws=(pid:string,wid:string)=>base(pid)+'/workspaces/'+encodeURIComponent(wid);
const task=(pid:string,tid:string)=>base(pid)+'/tasks/'+encodeURIComponent(tid);
async function request<T>(path:string,init:RequestInit={}):Promise<T>{const headers=new Headers(init.headers);headers.set('X-Riwaq-Client','local');if(init.body)headers.set('Content-Type','application/json');const response=await localFetch(apiBase+'/api'+path,{...init,headers,cache:'no-store'});if(response.status===204)return undefined as T;let data:unknown;try{data=await response.json();}catch(error){if(error instanceof DOMException&&error.name==='AbortError')throw error;if(response.ok)throw new CodingApiError(response.status,{detail:'Invalid JSON response from the local coding service.'});data={detail:`HTTP ${response.status}`};}if(!response.ok)throw new CodingApiError(response.status,data);return data as T;}
const json=(value:unknown)=>JSON.stringify(value);
export const codingApi={
 status:(pid:string,signal?:AbortSignal)=>request<CodingStatus>(base(pid)+'/status',{signal}),
 workspaces:(pid:string,signal?:AbortSignal)=>request<CodingWorkspace[]>(base(pid)+'/workspaces',{signal}),
 grant:(pid:string,path:string,label:string,signal?:AbortSignal)=>request<CodingWorkspace>(base(pid)+'/workspaces',{method:'POST',body:json({path,...(label?{label}:{})}),signal}),
 revoke:(pid:string,wid:string,signal?:AbortSignal)=>request(ws(pid,wid),{method:'DELETE',signal}),
 files:(pid:string,wid:string,signal?:AbortSignal)=>request<{files:CodingFile[];truncated:boolean}>(ws(pid,wid)+'/files',{signal}),
 read:(pid:string,wid:string,path:string,expectedSha?:string,signal?:AbortSignal)=>request<CodingRead>(ws(pid,wid)+'/read?path='+encodeURIComponent(path)+(expectedSha?'&expected_sha='+encodeURIComponent(expectedSha):''),{signal}),
 inspect:(pid:string,wid:string,selected_paths:string[],query:string,signal?:AbortSignal)=>request<CodingContext>(ws(pid,wid)+'/inspect',{method:'POST',body:json({selected_paths,query}),signal}),
 tasks:(pid:string,wid:string,signal?:AbortSignal)=>request<CodingTask[]>(ws(pid,wid)+'/tasks',{signal}),
 createTask:(pid:string,wid:string,input:CodingTaskInput,signal?:AbortSignal)=>request<CodingTask>(ws(pid,wid)+'/tasks',{method:'POST',body:json(input),signal}),
 task:(pid:string,tid:string,signal?:AbortSignal)=>request<CodingTask>(task(pid,tid),{signal}),
 action:(pid:string,tid:string,action:'stop'|'resume'|'apply'|'undo',revision:number,signal?:AbortSignal)=>request<CodingTask>(task(pid,tid)+'/'+action,{method:'POST',body:json(action==='resume'?{request_key:crypto.randomUUID()}:action==='stop'?{}:{confirm:true,expected_revision:revision}),signal}),
 checks:async(pid:string,tid:string,input:CodingCheckRequest,signal?:AbortSignal)=>{try{return await request<CodingValidation>(task(pid,tid)+'/checks',{method:'POST',body:json(input),signal});}catch(error){if(error instanceof CodingApiError&&error.status===503){const data=error.payload as {status?:string;detail?:CodingValidation;validation?:CodingValidation};const receipt=data.validation??data.detail??data;if(typeof receipt==='object'&&receipt?.status==='blocked')return receipt as CodingValidation;}throw error;}},
};
