import {apiBase} from './api';
import {localFetch} from './client-session';
import type {DocumentReference} from './api';

export type SkillCapability={slug:string;display_name:string;description:string;description_ar?:string;description_en?:string;version:string;status:string;reason?:string;dependencies?:string[];tools?:string[];execution_kind?:string;installed?:boolean;enabled?:boolean;ready?:boolean;tested?:boolean;tested_scope?:string;blockers?:Array<{code:string;dependency?:string;skill?:string}>;warnings?:unknown[];source?:unknown};
export type SkillCatalog={skills:SkillCapability[];policy_version?:string;readiness_scope?:string;release_gate?:Record<string,unknown>;transform_runtime?:{available:boolean;reason?:string;host_fallback:boolean}};
export type SkillRuntime={project_id:string;monitoring:Record<string,unknown>;model:Record<string,unknown>;release:Record<string,unknown>};
export type SkillAudit={project_id:string;run_id:string;events:Array<Record<string,unknown>>;truncated?:boolean};
export type SkillStep={id:string;skill?:string;title:string;status:string;depends_on:string[];output_refs?:string[];error?:string;handoff?:string};
export type SkillArtifact={name:string;mime:string;size:number;sha256?:string};
export type SkillSourceRef={id:string;scope?:'private'|'public'|'user_text';file_id?:string;page?:number|null;line_start?:number|null;line_end?:number|null;document?:DocumentReference|null;title?:string;publisher?:string;url?:string;source_url?:string;language?:string;version_id?:string;checked_at?:string;published_at?:string|null;modified_at?:string|null};
export type SkillEvidence={id:string;scope?:'private'|'public'|'user_text';file_id?:string;text:string;page?:number|null;line_start?:number|null;line_end?:number|null;current?:boolean;notice?:string|null;citation?:SkillSourceRef};
export type SkillUsage={unit:'local_steps';reserved:number;consumed:number|null;limit:number;monetary_cost:null;reason:string};
export type SkillReceipt={operation_id:string;status:string;verification?:string;error?:string; evidence?:string[];seconds?:number;cost_usd?:number;cost_scope?:string;usage?:SkillUsage};
export type SkillCheckpoint={revision:number;resumable:boolean;reason:string};
export type SkillSpeech={text:string;language:string;duration:number;voice_id:string;sha256:string};
export type SkillVoice={id:string;name:string;language:string};
export type SkillVoices={available:boolean;voices:SkillVoice[];reason?:string};
export type SkillCriterion={criterionId:string;requirement:string;verificationMethod:string;evidence:string[];status:string};
export type RequestRecord={version:string;original_request:string;deliverables:string[];constraints:Record<string,unknown>;clarifications:Array<{code:string;message:string}>;resolved_resource:unknown;scope:string};
export type RequestCoverage={checked_deliverables:Record<string,boolean>;gaps:Array<{kind:string;id:string;state:string}>;semantic_quality:string;method:string};
export type RequestPreview={project_id:string;request_record:RequestRecord;effects:'none'};
export type SkillRun={id:string;project_id:string;request:string;status:string;created_at?:string;updated_at?:string;steps:SkillStep[];selected_skills:string[];criteria?:SkillCriterion[];result?:{data_revision_available?:{available:boolean;result_hash:string;table_sha256:string|null;scope:string;maximum_rows:number;maximum_columns:number};report_revision?:{available:boolean;result_hash:string;output_kind:'docx';scope:string};presentation_revision?:{parent_id:string;target_language:string;analysis_keys:string[];analysis_hash:string;result_hash:string;original_artifacts_retained:boolean;scope:string;source:string};interruption_reason?:string;request_record?:RequestRecord;request_coverage?:RequestCoverage;text?:string;artifacts?:SkillArtifact[];evidence?:string[];warnings?:string[];limitations?:string[];layout_review?:string;translation_review?:string;original?:string;source_refs?:SkillSourceRef[];quality_status?:string;structured?:Record<string,unknown>;research_receipt?:Record<string,unknown>;research_kind?:string;retrieval_receipt?:Record<string,unknown>;retrieval_kind?:string;speech?:SkillSpeech;usage?:SkillUsage;summary?:Record<string,unknown>;checkpoint?:SkillCheckpoint;effects?:Array<Record<string,unknown>>;file_matches?:Array<Record<string,unknown>>};receipt?:SkillReceipt;error?:string};
export type SkillArtifactInput={kind:'docx'|'xlsx'|'pptx';payload:Record<string,unknown>};
export type SkillFileRef={kind:'document'|'artifact';id:string;version:number;sha256:string;pinned?:boolean};
export type SkillFile=SkillFileRef&{name:string;folder:string;revision:number;latest_version:number;mime:string;bytes:number;url:string;latest_url:string};
export type SkillWebSource={id:string;name:string;urls:Array<{url:string;title:string}>};
export type SkillRequest={request:string;requestedSkills?:string[];inputs?:{dataReport?:{title:string;language:'ar'|'en';chart:{category:string;value:string;unit?:string}};dataRevision?:{expectedResultHash:string;edits:Array<{cell:string;expected:unknown;value:unknown}>;reason?:string;feedbackId?:string;expectedFeedbackVersion?:number};reportRevision?:{expectedResultHash:string;targetLanguage:'ar'|'en'|'he'};text?:string;targetLanguage?:string;documentIds?:string[];retrievalMode?:'lexical'|'hybrid';artifact?:SkillArtifactInput;artifacts?:SkillArtifactInput[];researchQuestion?:string;researchOptions?:{scopes:('knowledge'|'web')[];allow_network:boolean;require_current:boolean;response_language:'ar'};speech?:{voiceId:string;language:string;rate:number};fileQuery?:string;workspaceId?:string;repositoryPaths?:string[];memoryIds?:string[];memoryExpectedVersions?:Record<string,number>;maxSteps?:number;summaryLanguage?:string;summaryMode?:'extractive'|'generative';sourceRefs?:SkillFileRef[];planningOperation?:Record<string,unknown>;scheduleOperation?:Record<string,unknown>;mailOperation?:Record<string,unknown>;meetingOperation?:Record<string,unknown>;dataOperation?:Record<string,unknown>;documentOperation?:Record<string,unknown>;webOperation?:Record<string,unknown>;folderOperation?:Record<string,unknown>;versionOperation?:Record<string,unknown>;systemOperation?:{operation:string;skills?:string[];taskId?:string;constraints?:Record<string,unknown>;schemaDefinition?:Record<string,unknown>};outputVersion?:{artifactId:string;expectedVersion:number;reason:string}};idempotencyKey:string;previousRunId?:string};
const base=(projectId:string)=>`/projects/${encodeURIComponent(projectId)}/skill-runs`;
const object=(value:unknown):value is Record<string,unknown>=>Boolean(value)&&typeof value==='object'&&!Array.isArray(value);
const validRequestRecord=(value:unknown):value is RequestRecord=>object(value)&&typeof value.version==='string'&&typeof value.original_request==='string'&&strings(value.deliverables)&&object(value.constraints)&&typeof value.scope==='string'&&Array.isArray(value.clarifications)&&value.clarifications.every(item=>object(item)&&typeof item.code==='string'&&typeof item.message==='string');
const strings=(value:unknown)=>Array.isArray(value)&&value.every(item=>typeof item==='string');
const sourceRef=(value:unknown):value is SkillSourceRef=>object(value)&&typeof value.id==='string'&&(value.scope==='public'||value.scope==='user_text'||typeof value.file_id==='string')&&(value.scope===undefined||['private','public','user_text'].includes(String(value.scope)))&&['title','publisher','url','source_url','language','version_id','checked_at','published_at','modified_at'].every(key=>value[key]===undefined||value[key]===null||typeof value[key]==='string')&&['page','line_start','line_end'].every(key=>value[key]===undefined||value[key]===null||typeof value[key]==='number'&&Number.isSafeInteger(value[key])&&value[key]>=0);
const validUsage=(usage:unknown)=>object(usage)&&usage.unit==='local_steps'&&usage.monetary_cost===null&&typeof usage.reason==='string'&&['reserved','limit'].every(key=>typeof usage[key]==='number'&&Number.isSafeInteger(usage[key])&&Number(usage[key])>=0)&&(usage.consumed===null||typeof usage.consumed==='number'&&Number.isSafeInteger(usage.consumed)&&usage.consumed>=0);
export class SkillRequestError extends Error{status:number;constructor(status:number,message:string){super(message);this.name='SkillRequestError';this.status=status;}}
export class SkillContractError extends Error{constructor(message:string){super(message);this.name='SkillContractError';}}
async function checkedResponse(path:string,init:RequestInit={}){
 const response=await localFetch(apiBase+'/api'+path,init);
 if(!response.ok){let message=`HTTP ${response.status}`;try{const value:unknown=await response.json();if(object(value)&&typeof value.detail==='string')message=value.detail;else if(object(value)&&object(value.detail)&&typeof value.detail.message==='string')message=value.detail.message;else if(object(value)&&Array.isArray(value.detail)){const fields=value.detail.filter(object).map(item=>Array.isArray(item.loc)?item.loc.filter(part=>typeof part==='number'||typeof part==='string'&&/^[A-Za-z0-9_-]+$/.test(part)).join('.'): '').filter(Boolean);if(fields.length)message=`Invalid request fields / حقول غير صالحة: ${[...new Set(fields)].slice(0,8).join(', ')}`;}}catch{/* The status is confirmed even when the body is unreadable. */}throw new SkillRequestError(response.status,message);}
 return response;
}
async function json(path:string,init:RequestInit={}){const response=await checkedResponse(path,init);if(response.status===204)return undefined;try{return await response.json() as unknown;}catch{throw new SkillContractError('The skill service returned malformed JSON.');}}
function run(value:unknown,projectId:string,expectedId?:string):SkillRun{
 if(!object(value)||typeof value.id!=='string'||typeof value.project_id!=='string'||typeof value.request!=='string'||typeof value.status!=='string'||!Array.isArray(value.steps)||!Array.isArray(value.selected_skills))throw Error('The skill service returned an invalid task receipt.');
 if(!strings(value.selected_skills)||value.steps.some(step=>!object(step)||typeof step.id!=='string'||typeof step.title!=='string'||typeof step.status!=='string'||!strings(step.depends_on)||step.error!==undefined&&typeof step.error!=='string'))throw Error('The skill service returned an invalid task plan.');
 if(value.error!==undefined&&value.error!==null&&typeof value.error!=='string')throw Error('The skill service returned an invalid task error.');
 if(value.project_id!==projectId||expectedId!==undefined&&value.id!==expectedId)throw new SkillContractError('The task receipt does not match the requested project and operation.');
 if(value.criteria!==undefined&&(!Array.isArray(value.criteria)||value.criteria.some(item=>!object(item)||typeof item.criterionId!=='string'||typeof item.requirement!=='string'||typeof item.verificationMethod!=='string'||typeof item.status!=='string'||!strings(item.evidence))))throw new SkillContractError('The skill service returned invalid acceptance criteria.');
 if(value.result!==undefined&&value.result!==null){
  if(!object(value.result)||value.result.text!==undefined&&typeof value.result.text!=='string')throw Error('The skill service returned an invalid result.');
  for(const key of ['warnings','evidence','limitations'])if(value.result[key]!==undefined&&!strings(value.result[key]))throw Error('The skill service returned invalid result evidence.');
  for(const key of ['interruption_reason','layout_review','translation_review','original','quality_status','research_kind','retrieval_kind'])if(value.result[key]!==undefined&&typeof value.result[key]!=='string')throw Error('The skill service returned invalid result metadata.');
  const dataRevision=value.result.data_revision_available;
  if(dataRevision!==undefined&&(!object(dataRevision)||typeof dataRevision.available!=='boolean'||typeof dataRevision.result_hash!=='string'||!/^[a-f0-9]{64}$/.test(dataRevision.result_hash)||dataRevision.table_sha256!==null&&(typeof dataRevision.table_sha256!=='string'||!/^[a-f0-9]{64}$/.test(dataRevision.table_sha256))||typeof dataRevision.scope!=='string'))throw new SkillContractError('Invalid data report version.');
  const revision=value.result.report_revision;
  if(revision!==undefined&&(!object(revision)||typeof revision.available!=='boolean'||typeof revision.result_hash!=='string'||!/^[a-f0-9]{64}$/.test(revision.result_hash)||revision.output_kind!=='docx'||typeof revision.scope!=='string'))throw new SkillContractError('Invalid saved report version.');
  const revised=value.result.presentation_revision;
  if(revised!==undefined&&(!object(revised)||typeof revised.parent_id!=='string'||!strings(revised.analysis_keys)||!['ar','he','en'].includes(String(revised.target_language))||['analysis_hash','result_hash'].some(k=>typeof revised[k]!=='string'||!/^[a-f0-9]{64}$/.test(String(revised[k])))||revised.original_artifacts_retained!==true||revised.scope!=='narrative_language_only'||revised.source!=='server_saved_result'))throw new SkillContractError('Invalid report revision evidence.');
  if(value.result.request_record!==undefined&&!validRequestRecord(value.result.request_record))throw new SkillContractError('Invalid request understanding record.');
  if(value.result.request_coverage!==undefined){const coverage=value.result.request_coverage;if(!object(coverage)||!object(coverage.checked_deliverables)||Object.values(coverage.checked_deliverables).some(value=>typeof value!=='boolean')||!Array.isArray(coverage.gaps)||coverage.gaps.some(gap=>!object(gap)||['kind','id','state'].some(key=>typeof gap[key]!=='string'))||typeof coverage.method!=='string'||typeof coverage.semantic_quality!=='string')throw new SkillContractError('Invalid request coverage.');}
  if(value.result.effects!==undefined&&(!Array.isArray(value.result.effects)||!value.result.effects.every(object)))throw new SkillContractError('Invalid committed-effects receipt.');
  if(value.result.structured!==undefined&&!object(value.result.structured))throw new SkillContractError('The skill service returned invalid structured results.');
  if(value.result.summary!==undefined&&!object(value.result.summary))throw new SkillContractError('The skill service returned an invalid summary.');
  if(value.result.usage!==undefined&&!validUsage(value.result.usage))throw new SkillContractError('The skill service returned invalid local-work usage.');
  if(value.result.research_receipt!==undefined&&!object(value.result.research_receipt))throw new SkillContractError('The skill service returned an invalid research receipt.');
  if(value.result.retrieval_receipt!==undefined&&!object(value.result.retrieval_receipt))throw new SkillContractError('The skill service returned an invalid retrieval receipt.');
  const checkpoint=value.result.checkpoint;
  if(checkpoint!==undefined&&(!object(checkpoint)||typeof checkpoint.revision!=='number'||!Number.isSafeInteger(checkpoint.revision)||checkpoint.revision<0||typeof checkpoint.resumable!=='boolean'||typeof checkpoint.reason!=='string'))throw new SkillContractError('The skill service returned an invalid checkpoint.');
  const speech=value.result.speech;
  if(speech!==undefined&&(!object(speech)||['text','language','voice_id','sha256'].some(key=>typeof speech[key]!=='string')||typeof speech.sha256!=='string'||!/^[a-f0-9]{64}$/.test(speech.sha256)||typeof speech.duration!=='number'||!Number.isFinite(speech.duration)||speech.duration<=0))throw new SkillContractError('The skill service returned invalid speech evidence.');
  if(value.result.file_matches!==undefined&&(!Array.isArray(value.result.file_matches)||!value.result.file_matches.every(object)))throw new SkillContractError('The skill service returned invalid file matches.');
  if(value.result.source_refs!==undefined&&(!Array.isArray(value.result.source_refs)||!value.result.source_refs.every(sourceRef)))throw new SkillContractError('The skill service returned an invalid evidence reference.');
  if(value.result.artifacts!==undefined&&(!Array.isArray(value.result.artifacts)||value.result.artifacts.some(item=>!object(item)||typeof item.name!=='string'||typeof item.mime!=='string'||typeof item.size!=='number'||!Number.isSafeInteger(item.size)||item.size<0)))throw Error('The skill service returned an invalid artifact reference.');
  if(Array.isArray(value.result.artifacts)&&value.result.artifacts.some(item=>object(item)&&(typeof item.name==='string'&&(!item.name||item.name.length>200||/[\\/\u0000-\u001f]/.test(item.name))||item.sha256!==undefined&&(typeof item.sha256!=='string'||!/^[a-f0-9]{64}$/.test(item.sha256)))))throw new SkillContractError('The skill service returned invalid artifact integrity metadata.');
 }
 if(value.receipt!==undefined&&value.receipt!==null){
  if(!object(value.receipt)||typeof value.receipt.operation_id!=='string'||typeof value.receipt.status!=='string'||value.receipt.evidence!==undefined&&!strings(value.receipt.evidence))throw Error('The skill service returned invalid execution evidence.');
  if(value.receipt.operation_id!==value.id)throw new SkillContractError('The execution receipt belongs to a different operation.');
  for(const key of ['error','verification','cost_scope'])if(value.receipt[key]!==undefined&&value.receipt[key]!==null&&typeof value.receipt[key]!=='string')throw Error('The skill service returned invalid receipt metadata.');
  for(const key of ['seconds','cost_usd'])if(value.receipt[key]!==undefined&&(typeof value.receipt[key]!=='number'||!Number.isFinite(value.receipt[key])||value.receipt[key]<0))throw Error('The skill service returned invalid usage values.');
  const usage=value.receipt.usage;
  if(usage!==undefined&&(!object(usage)||usage.unit!=='local_steps'||usage.monetary_cost!==null||typeof usage.reason!=='string'||['reserved','limit'].some(key=>typeof usage[key]!=='number'||!Number.isSafeInteger(usage[key])||Number(usage[key])<0)||usage.consumed!==null&&(typeof usage.consumed!=='number'||!Number.isSafeInteger(usage.consumed)||usage.consumed<0)))throw new SkillContractError('The skill service returned invalid local-work usage.');
 }
 return value as unknown as SkillRun;
}
export type ReviewedSendInput={accountId:string;draftId:string;expectedRevision:number;expectedPayloadSha256:string;idempotencyKey:string;attachmentsAuthorized:boolean};
export const skillsApi={
 async preview(projectId:string,input:SkillRequest,signal?:AbortSignal):Promise<RequestPreview>{
  const value=await json(`/projects/${encodeURIComponent(projectId)}/skill-request-preview`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input),signal});
  if(!object(value)||value.project_id!==projectId||value.effects!=='none'||!validRequestRecord(value.request_record))throw new SkillContractError('Invalid request preview.');
  return value as RequestPreview;
 },
 async runtime(projectId:string,signal?:AbortSignal):Promise<SkillRuntime>{
  const value=await json(`/projects/${encodeURIComponent(projectId)}/skill-runtime`,{signal});
  if(!object(value)||value.project_id!==projectId||!object(value.monitoring)||!object(value.model)||!object(value.release))throw new SkillContractError('Runtime status does not match this project.');
  return value as SkillRuntime;
 },
 async probe(projectId:string,signal?:AbortSignal):Promise<Record<string,unknown>>{
  const value=await json(`/projects/${encodeURIComponent(projectId)}/skill-runtime/probe`,{method:'POST',signal});
  if(!object(value)||value.project_id!==projectId||!object(value.model)||value.synthetic_only!==true||value.external_calls!==false)throw new SkillContractError('Invalid local synthetic probe receipt.');
  return value.model;
 },
 async audit(projectId:string,runId:string,signal?:AbortSignal):Promise<SkillAudit>{
  const value=await json(base(projectId)+'/'+encodeURIComponent(runId)+'/audit',{signal});
  if(!object(value)||value.project_id!==projectId||value.run_id!==runId||!Array.isArray(value.events)||value.events.some(item=>!object(item)))throw new SkillContractError('Audit receipt does not match this project and operation.');
  return value as SkillAudit;
 },
 async sendReviewed(projectId:string,input:ReviewedSendInput,signal?:AbortSignal){return run(await json(`/projects/${encodeURIComponent(projectId)}/productivity/mail/send`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input),signal}),projectId);},
 async files(projectId:string,signal?:AbortSignal):Promise<SkillFile[]>{
  const value=await json(`/projects/${encodeURIComponent(projectId)}/skill-files`,{signal});
  if(!Array.isArray(value)||value.some(item=>!object(item)||item.kind!=='artifact'||typeof item.id!=='string'||typeof item.sha256!=='string'||!/^[a-f0-9]{64}$/.test(item.sha256)||['name','folder','mime','url','latest_url'].some(key=>typeof item[key]!=='string')||['version','revision','latest_version','bytes'].some(key=>typeof item[key]!=='number'||!Number.isSafeInteger(item[key])||Number(item[key])<0)))throw new SkillContractError('Invalid project output-file list.');
  return value as SkillFile[];
 },
 async webSources(signal?:AbortSignal):Promise<SkillWebSource[]>{
  const value=await json('/knowledge/sources',{signal});if(!Array.isArray(value))throw new SkillContractError('Invalid registered web-source list.');
  return value.filter(object).filter(item=>item.status==='allowed'&&typeof item.id==='string'&&typeof item.name==='string'&&Array.isArray(item.urls)).map(item=>({id:item.id as string,name:item.name as string,urls:(item.urls as unknown[]).flatMap(candidate=>{const url=typeof candidate==='string'?candidate:object(candidate)&&typeof candidate.url==='string'?candidate.url:'';try{const parsed=new URL(url);if(parsed.protocol!=='https:'||parsed.username||parsed.password)return [];}catch{return [];}return [{url,title:object(candidate)&&typeof candidate.title==='string'?candidate.title:url}];})}));
 },
 async voices(projectId:string,signal?:AbortSignal):Promise<SkillVoices>{const value=await json(`/projects/${encodeURIComponent(projectId)}/skill-voices`,{signal});if(!object(value)||typeof value.available!=='boolean'||!Array.isArray(value.voices)||value.voices.some(voice=>!object(voice)||['id','name','language'].some(key=>typeof voice[key]!=='string'))||value.reason!==undefined&&value.reason!==null&&typeof value.reason!=='string')throw new SkillContractError('The service returned invalid voice availability.');return value as unknown as SkillVoices;},
 async catalog(signal?:AbortSignal):Promise<SkillCatalog>{
  const value=await json('/skills/catalog',{signal});
  if(!object(value)||!Array.isArray(value.skills)||value.skills.some(skill=>!object(skill)||typeof skill.slug!=='string'||typeof skill.display_name!=='string'||typeof skill.status!=='string'||typeof skill.description!=='string'||typeof skill.version!=='string'||['description_ar','description_en','reason','execution_kind'].some(key=>skill[key]!==undefined&&skill[key]!==null&&typeof skill[key]!=='string')||['dependencies','tools'].some(key=>skill[key]!==undefined&&!strings(skill[key]))))throw Error('The skill service returned an invalid capability catalog.');
  if(value.skills.some(skill=>['installed','enabled','ready','tested'].some(key=>skill[key]!==undefined&&typeof skill[key]!=='boolean')||skill.blockers!==undefined&&(!Array.isArray(skill.blockers)||skill.blockers.some((item:unknown)=>!object(item)||typeof item.code!=='string'))))throw new SkillContractError('Invalid catalog readiness contract.');
  if(value.transform_runtime!==undefined&&(!object(value.transform_runtime)||typeof value.transform_runtime.available!=='boolean'||(value.transform_runtime.reason!==undefined&&typeof value.transform_runtime.reason!=='string')||value.transform_runtime.host_fallback!==false))throw new SkillContractError('The isolated file-runtime capability is invalid.');
  return value as unknown as SkillCatalog;
 },
 async list(projectId:string,signal?:AbortSignal):Promise<SkillRun[]>{const value=await json(base(projectId),{signal});if(!Array.isArray(value))throw Error('The skill service returned an invalid task history.');return value.map(value=>run(value,projectId));},
 async get(projectId:string,id:string,signal?:AbortSignal){return run(await json(base(projectId)+'/'+encodeURIComponent(id),{signal}),projectId,id);},
 async start(projectId:string,input:SkillRequest,signal?:AbortSignal){
  return run(await json(base(projectId),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input),signal}),projectId);
 },
 async cancel(projectId:string,id:string,signal?:AbortSignal){const value=await json(base(projectId)+'/'+encodeURIComponent(id)+'/cancel',{method:'POST',signal});if(!object(value)||value.id!==id||typeof value.status!=='string')throw new SkillContractError('The stop receipt does not match this task.');return value;},
 async resume(projectId:string,id:string,revision:number,signal?:AbortSignal){return run(await json(base(projectId)+'/'+encodeURIComponent(id)+'/resume',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:revision}),signal}),projectId,id);},
 async remove(projectId:string,id:string,signal?:AbortSignal){const value=await json(base(projectId)+'/'+encodeURIComponent(id),{method:'DELETE',signal});if(!object(value)||value.deleted!==true)throw new SkillContractError('Task deletion was not confirmed.');return value;},
 async evidence(projectId:string,id:string,source:SkillSourceRef,signal?:AbortSignal):Promise<SkillEvidence>{
  const value=await json(base(projectId)+'/'+encodeURIComponent(id)+'/evidence/'+encodeURIComponent(source.id),{signal});
  if(!object(value)||value.id!==source.id||source.scope!=='public'&&value.file_id!==source.file_id||source.scope==='public'&&value.scope!=='public'||typeof value.text!=='string'||value.current!==undefined&&typeof value.current!=='boolean'||value.notice!==undefined&&value.notice!==null&&typeof value.notice!=='string')throw new SkillContractError('The source did not match the requested evidence reference.');
  if(value.citation!==undefined&&(!sourceRef(value.citation)||value.citation.id!==source.id||value.citation.scope!==source.scope))throw new SkillContractError('The evidence citation did not match its authorized source.');
  return value as unknown as SkillEvidence;
 },
 async artifact(projectId:string,id:string,artifact:SkillArtifact,signal?:AbortSignal){
  const response=await checkedResponse(base(projectId)+'/'+encodeURIComponent(id)+'/artifacts/'+encodeURIComponent(artifact.name),{signal});
  const blob=await response.blob();
  if(!blob.size||blob.size!==artifact.size)throw new SkillContractError('The downloaded artifact size does not match the execution receipt.');
  if(artifact.mime&&blob.type.split(';')[0]!==artifact.mime)throw Error('The downloaded artifact type does not match the execution receipt.');
  if(artifact.sha256){const digest=await crypto.subtle.digest('SHA-256',await blob.arrayBuffer()),actual=Array.from(new Uint8Array(digest),value=>value.toString(16).padStart(2,'0')).join('');if(actual!==artifact.sha256)throw new SkillContractError('The downloaded artifact does not match the receipt fingerprint.');}
  return blob;
 },
};
