import {api,apiBase} from './api';
import {localFetch} from './client-session';
export type GuardFile={id:string;filename:string;sha256:string};
export type GuardResult={id:string;status:string;created_at?:string;content?:string;decision_id?:string;policy?:unknown;resources?:unknown;model?:unknown;error?:string|null};
export type GuardOverview={policy_version:string;local_owner_only:true;personal_memory_gate:'blocked';cloud_generation:'quarantined';files:GuardFile[];results:GuardResult[]};
export type GuardDecision={id:string;decision:string;reason:string;operation:string;policy_version:string;created_at:string};
export type GuardEgressDenial={reason:string;decision_id:string};
const base=(projectId:string)=>`/projects/${encodeURIComponent(projectId)}/guard`;
export const guardApi={
 overview:(pid:string,signal?:AbortSignal)=>api<GuardOverview>(base(pid),{signal}),
 summarize:(pid:string,fileId:string,key:string,signal?:AbortSignal)=>api<GuardResult>(base(pid)+'/summaries',{method:'POST',body:JSON.stringify({file_id:fileId,idempotency_key:key}),signal}),
 result:(pid:string,id:string,signal?:AbortSignal)=>api<GuardResult>(base(pid)+'/results/'+encodeURIComponent(id),{signal}),
 cancel:(pid:string,id:string,signal?:AbortSignal)=>api<{id:string;cancel_requested:boolean}>(base(pid)+'/results/'+encodeURIComponent(id)+'/cancel',{method:'POST',signal}),
 decision:(id:string,signal?:AbortSignal)=>api<GuardDecision>('/guard/decisions/'+encodeURIComponent(id),{signal}),
 async egressCheck(pid:string,id:string,signal?:AbortSignal):Promise<GuardEgressDenial>{
  const response=await localFetch(apiBase+'/api'+base(pid)+'/results/'+encodeURIComponent(id)+'/egress-check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({destination:'https://example.invalid/upload'}),signal});
  const data:unknown=await response.json();const detail=data&&typeof data==='object'?(data as {detail?:unknown}).detail:undefined;
  if(response.status===403&&detail&&typeof detail==='object'&&typeof (detail as GuardEgressDenial).reason==='string'&&typeof (detail as GuardEgressDenial).decision_id==='string')return detail as GuardEgressDenial;
  throw Error(response.ok?'The egress check did not return a deny decision.':`Egress check could not be verified (HTTP ${response.status}).`);
 },
};
