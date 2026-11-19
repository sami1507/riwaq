import {apiBase,localFetch} from './client-session';
export {apiBase} from './client-session';
import type {KnowledgeFilters,ResearchOptions,ResearchReceipt} from './research-api';
export type FileScope={file_id:string;pages?:number[]};
export type DocumentReference={file_id:string;version:number;element_id:string;revision:number;page:number;bbox?:[number,number,number,number]|null;mime?:string;locator?:{page?:number;sheet?:string;slide?:number;paragraph?:number|string;table?:number|string;cells?:string[]}};
export type Project = {id:string;name:string;instructions:string;created_at:string};
export type Citation = {publisher?:string|null;language?:string|null;published_at?:string|null;modified_at?:string|null;fetched_at?:string|null;checked_at?:string|null;version_id?:string;sha256?:string;document?:DocumentReference;id?:string;file_name?:string;filename?:string;page?:number;line_start?:number;line_end?:number;content?:string;text?:string;scope?:'public'|'private';title?:string;url?:string;version?:string;document_id?:string;source_id?:string;provenance?:'stored_excerpt';license?:string|{name:string;url?:string;attribution?:string;evidence?:string}};
export type AnswerMode='assistant'|'calculator'|'problem';
export type RetrievalOptions={research?:ResearchOptions;knowledge_filters?:KnowledgeFilters;file_scope?:FileScope[];use_files:boolean;use_knowledge:boolean;answer_mode?:AnswerMode;result_language?:'ar'|'en'};
export type HistoryCompaction={counting?:'exact_tokenizer'|'utf8_byte_upper_bound';policy:string;history_messages:number;user_turns_preserved:number;assistant_turns:number;assistant_turns_abbreviated:number;assistant_turns_omitted:number;affected_turns:{turn:number;action:'abbreviated'|'omitted'}[];latest_assistant_reply_full:boolean};
export type Message = {context?:HistoryCompaction;research?:ResearchReceipt;retrieval_options?:RetrievalOptions;id:string;role:'user'|'assistant';content:string;status:string;model?:string;created_at?:string;citations?:Citation[];run?:RunReceipt};
export type Conversation = {id:string;project_id:string;title:string;updated_at?:string;messages?:Message[]};
export type Settings = {mode:'local'|'hybrid';provider:'ollama'|'openai'|'anthropic';model:string;[key:string]:unknown};
export type Status = {mode:string;model:string;version:string;ollama:{available:boolean;models:Array<{name:string;size?:number;chat_capable?:boolean}>};capabilities:Record<string,unknown>;providers?:Record<string,{configured:boolean;tested:boolean}>};
export type ProjectFile = {id:string;name?:string;filename?:string;size?:number;byte_size?:number;chunks?:number;chunk_count?:number;status?:string};
export type Artifact = {id:string;title:string;content?:string;version?:number;updated_at?:string};
export type Memory = {id:string;content:string;scope:string;project_id?:string;version:number;updated_at:string};
export async function api<T>(path:string,init:RequestInit={}):Promise<T>{
 const headers=new Headers(init.headers); headers.set('X-Riwaq-Client','local');
 if(init.body && !(init.body instanceof FormData)) headers.set('Content-Type','application/json');
 const response=await localFetch(`${apiBase}/api${path}`,{...init,headers});
 if(!response.ok){let message=`HTTP ${response.status}`;try{const err=await response.json();message=typeof err.detail==='string'?err.detail:typeof err.detail?.message==='string'?err.detail.message:JSON.stringify(err.detail ?? err);}catch{/* preserve status */}throw new Error(message);}
 if(response.status===204)return undefined as T;
 return response.json();
}
export async function downloadConversation(id:string,format:'markdown'|'json'){
 const response=await localFetch(`${apiBase}/api/conversations/${id}/export?format=${format}`,{headers:{'X-Riwaq-Client':'local'}});
 if(!response.ok)throw new Error(`Export failed: ${response.status}`);
 const url=URL.createObjectURL(await response.blob());const link=document.createElement('a');link.href=url;link.download=`riwaq-${id}.${format==='markdown'?'md':'json'}`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

export type GenerateOutputContract={schema:Record<string,unknown>;citation_field?:string};
export type FactBinding={id:string;version:number;payload_sha256:string};
export type Depth='auto'|'quick'|'analytical'|'research';
export type RunStage={id:string;kind:string;status:string};
export type RunCheck={id:string;kind:string;status:string;method:string;summary:string;details:string};
export type RunReceipt={id:string;version:string;requested_depth:Depth;request_options?:{research?:ResearchOptions;knowledge_filters?:KnowledgeFilters;file_scope?:FileScope[];answer_mode?:AnswerMode;result_language?:'ar'|'en';use_files?:boolean;use_knowledge?:boolean;calculation?:{expression:string}|null;review?:boolean;reasoning_enabled?:boolean|null;output_contract?:GenerateOutputContract|null;fact_ids?:string[]};effective_depth:Exclude<Depth,'auto'>;status:string;policy_version:string;goal:string;hypothesis?:{success_metric:string;constraints:string}|null;assumptions:string[];stages:RunStage[];checks:RunCheck[];model_calls:number;tool_calls:number;reserved_tokens:number;actual_input_tokens:number;actual_output_tokens:number;ceilings:Record<string,number>;seconds:number;monetary_cost_usd:number;remaining:string[];origin:'local_run'|'imported_snapshot';fact_bindings?:FactBinding[]};

export function messageAnswerMode(message:Message|undefined):AnswerMode{return message?.retrieval_options?.answer_mode??message?.run?.request_options?.answer_mode??'assistant';}
export function messageResultLanguage(message:Message|undefined,fallback:'ar'|'en'):'ar'|'en'{return message?.retrieval_options?.result_language??message?.run?.request_options?.result_language??fallback;}
