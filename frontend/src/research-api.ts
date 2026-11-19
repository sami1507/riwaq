import {api} from './api';
import type {Citation} from './api';
export type KnowledgeFilters={source_id?:string;domain?:string;language?:string;version?:string};
export type ResearchScope='web'|'knowledge'|'files';
export type ResearchOptions=KnowledgeFilters&{scopes:ResearchScope[];response_language:string;allow_network:boolean;require_current:boolean};
export type ResearchDraft={enabled:boolean;scopes:ResearchScope[];response_language:string;allow_network:boolean;require_current:boolean};
export const initialResearch:ResearchDraft={enabled:false,scopes:['knowledge'],response_language:'ar',allow_network:false,require_current:false};
export type ResearchEvent={kind:string;status:string;at:string;detail:string};
export type ResearchReceipt={version:'research-v1';status:string;events:ResearchEvent[];notices:string[];provider:'approved_catalogue'|'saved_index';job_id?:string;documents_read:number;downloaded_bytes:number;network_requests:number;model_calls:number;seconds:number;validation?:{status:string;errors:string[];checked_claims:number;semantic_support:'not_certified'};model?:string};
export type ResearchStatus={active:boolean;receipt?:ResearchReceipt};
export type ResearchEvidence={citation:Citation;excerpt:string;current:boolean;notice?:string};
export function cleanKnowledgeFilters(filters:KnowledgeFilters):KnowledgeFilters{return Object.fromEntries((['source_id','domain','language','version'] as const).filter(key=>typeof filters[key]==='string'&&filters[key]!.trim()).map(key=>[key,filters[key]!.trim()]));}
export function researchOptions(draft:ResearchDraft,filters:KnowledgeFilters,local:boolean,ar:boolean):ResearchOptions|undefined{
 if(!draft.enabled)return undefined;
 const scopes=[...new Set(draft.scopes)];
 if(!scopes.length)throw Error(ar?'اختر نطاقًا واحدًا على الأقل للبحث.':'Select at least one research scope.');
 const language=draft.response_language.trim();
 if(language.length>40||! /^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$/.test(language))throw Error(ar?'أدخل رمز لغة الرد مثل ar أو en أو es أو he.':'Enter a response language tag such as ar, en, es, or he.');
 if(scopes.includes('web')&&(local||!draft.allow_network))throw Error(ar?(local?'القراءة الحية متوقفة في وضع محلي فقط. أزل نطاق الشبكة أو غيّر الوضع صراحة.':'فعّل السماح بقراءة المصادر المعتمدة قبل الإرسال.'):(local?'Live reading is blocked in Local only mode. Remove the network scope or explicitly change mode.':'Authorize reading approved sources before sending.'));
 return {...cleanKnowledgeFilters(filters),scopes,response_language:language,allow_network:scopes.includes('web')&&draft.allow_network,require_current:draft.require_current};
}
export const researchApi={
 status:(conversationId:string,signal?:AbortSignal)=>api<ResearchStatus>(`/conversations/${encodeURIComponent(conversationId)}/research-status`,{signal,cache:'no-store'}),
 evidence:(projectId:string,messageId:string,index:number,signal?:AbortSignal)=>api<ResearchEvidence>(`/projects/${encodeURIComponent(projectId)}/research/evidence/${encodeURIComponent(messageId)}/${index}`,{signal,cache:'no-store'}),
};
