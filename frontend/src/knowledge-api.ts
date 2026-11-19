import { api } from './api';

export type SourceStatus = 'candidate' | 'verifying' | 'allowed' | 'link_only' | 'suspended';
export type SourceLicense = {name:string;url:string;storage_allowed:boolean;redistribution_allowed:boolean;attribution:string;reviewed_at:string;evidence:string;sha256?:string;training_allowed?:false};
export type KnowledgeSource = {
 id:string;name:string;base_url:string;allowed_hosts:string[];urls:Array<string|{url:string;title?:string;language?:string;version?:string;original_url?:string}>;status:SourceStatus;license:SourceLicense;rights_confirmed:boolean;
 domain?:string;language?:string;content_type?:string;access_method?:string;version?:string;owner?:string;update_policy?:{interval_days:number};fetch_limits?:{delay_seconds:number;max_documents:number};priority?:number;allowed_prefixes?:string[];pack?:string;training_allowed?:boolean;
 created_at?:string;updated_at?:string;license_change_pending?:boolean;license_sha256?:string;pending_license_sha256?:string;pending_license_text?:string;
};
export type KnowledgePack = {id:string;name:string;description?:string;source_ids:string[];status?:string};
export type KnowledgeDocument = {published_at?:string|null;modified_at?:string|null;fetched_at?:string|null;version_id?:string;publisher?:string|null;id:string;source_id:string;url:string;title:string;version:string;sha256:string;byte_size:number;etag?:string;last_modified?:string;created_at:string;updated_at:string;license:SourceLicense|string};
export type KnowledgeChunk = {published_at?:string|null;modified_at?:string|null;fetched_at?:string|null;checked_at?:string|null;version_id?:string;sha256?:string;publisher?:string|null;id:string;document_id:string;source_id:string;scope:'public';title:string;filename?:string;url:string;version:string;license:SourceLicense|string;page?:number;line_start:number;line_end:number;text:string;score:number};
export type KnowledgePreview = {sources:KnowledgeSource[];documents:Array<{source_id:string;url:string;state:string;reason:string}>;document_count:number;budget_remaining_bytes:number;notice:string};
export type KnowledgeJob = {
 id:string;status:'queued'|'running'|'cancelled'|'completed'|'partial'|'failed'|'paused';source_ids:string[];max_documents:number;completed_documents:number;downloaded_bytes:number;started_at?:string;updated_at?:string;error?:string;
 discovered_links?:number;fetched_files?:number;accepted_documents?:number;indexed_chunks?:number;failed_sources?:number;
 items?:Array<{url:string;source_id:string;status:string;error?:string;document_id?:string}>;errors?:Array<{url?:string;source_id?:string;error?:string;message?:string}|string>;
};
export type KnowledgeStatus = {
 sources:number;documents:number;chunks:number;downloaded_bytes:number;budget_bytes:number;budget_remaining_bytes:number;active_job:KnowledgeJob|string|null;
 discovered_links?:number;fetched_files?:number;accepted_documents?:number;indexed_chunks?:number;failed_sources?:number;storage_bytes?:number;storage_measurement?:string;
 embedding:{configured:boolean;model?:string;available:boolean;indexed_vectors?:number;current_chunks?:number;dimensions?:number};limits:{max_sources:number;max_documents:number;max_file_bytes:number;max_job_seconds:number};
};
export function knowledge<T>(path:string,init:RequestInit={}){return api<T>(`/knowledge${path}`,init);}
export {safeSourceLink} from './safe-links';
export function formatBytes(value:number|undefined){if(value===undefined)return '—';if(value<1024)return `${value} B`;if(value<1024*1024)return `${(value/1024).toFixed(1)} KB`;if(value<1024*1024*1024)return `${(value/(1024*1024)).toFixed(1)} MB`;return `${(value/(1024*1024*1024)).toFixed(2)} GB`;}
export function sourceStatusLabel(status:string,ar:boolean){const labels:Record<string,[string,string]>={available:['تضم مصادر مسموحة','Contains allowed sources'],candidate:['مرشّح للمراجعة','Candidate'],verifying:['قيد المراجعة','Under review'],allowed:['مسموح بالاستيراد','Import allowed'],link_only:['رابط فقط','Link only'],suspended:['معلّق','Suspended'],queued:['في الانتظار','Queued'],running:['جارٍ الاستيراد','Importing'],cancelled:['أُلغي','Cancelled'],completed:['مكتمل','Completed'],partial:['مكتمل جزئيًا','Partial'],failed:['فشل','Failed'],paused:['متوقف','Paused'],eligible:['مؤهل للاستيراد','Eligible'],blocked:['محظور','Blocked'],pending:['في الانتظار','Pending'],fetched:['جُلب','Fetched'],accepted:['مقبول','Accepted'],indexed:['مفهرس','Indexed'],skipped:['متجاوز','Skipped'],unchanged:['لم يتغير','Unchanged']};return labels[status]?.[ar?0:1]??status;}
