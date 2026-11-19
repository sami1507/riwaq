import {apiBase} from './api';
import {localFetch} from './client-session';
import {SkillContractError,SkillRequestError} from './skills-api';

export type RecordValue=Record<string,unknown>;
export const isRecord=(value:unknown):value is RecordValue=>Boolean(value)&&typeof value==='object'&&!Array.isArray(value);
export type Calendar={id:string;name:string;zone:string;version:number};
export type Reminder={id:string;text:string;state:string;version:number;zone:string;start_local:string;next_utc?:string|null;missed_policy:string;channel:string;recurrence?:RecordValue|null};
export type MailAccount={id:string;label:string;address:string;mode:string};
export type DraftSummary={id:string;revision:number;subject:string;state:string};
export type ReviewedDraft={id:string;account_id:string;revision:number;state:string;payload_sha256:string;draft:RecordValue};
export type MeetingSource={id:string;version:number;title:string;reference_date:string;zone:string;complete:number|boolean;text?:string};
export type TaskRecord={id:string;title:string;revision:number;state:string;source_id:string;source_changed:boolean;assignee?:string|null;due?:RecordValue|null};
const base=(project:string)=>`/projects/${encodeURIComponent(project)}/productivity`;
async function request(project:string,path:string,method='GET',body?:unknown,signal?:AbortSignal):Promise<unknown>{
 const response=await localFetch(apiBase+'/api'+base(project)+path,{method,signal,...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
 if(!response.ok){let message=`HTTP ${response.status}`;try{const value:unknown=await response.json();if(isRecord(value)&&typeof value.detail==='string')message=value.detail;}catch{/* Keep the confirmed status without reflecting arbitrary request content. */}throw new SkillRequestError(response.status,message);}
 try{return await response.json() as unknown;}catch{throw new SkillContractError('Invalid productivity response JSON.');}
}
function record(value:unknown):RecordValue{if(!isRecord(value))throw new SkillContractError('Invalid productivity result.');return value;}
function records(value:unknown,keys:string[]=['id']):RecordValue[]{if(!Array.isArray(value)||value.some(item=>!isRecord(item)||keys.some(key=>typeof item[key]!=='string')))throw new SkillContractError('Invalid productivity list.');return value as RecordValue[];}
function versioned(value:unknown,keys:string[],revision='version'){const rows=records(value,keys);if(rows.some(row=>!Number.isSafeInteger(row[revision])||Number(row[revision])<1))throw new SkillContractError('Invalid productivity revision.');return rows;}
export const productivityApi={
 async status(project:string,signal?:AbortSignal){return record(await request(project,'/status','GET',undefined,signal));},
 async schedule(project:string,operation:string,options:RecordValue={},signal?:AbortSignal){return record(await request(project,'/schedule','POST',{operation,options},signal));},
 async calendars(project:string,signal?:AbortSignal){const value=await this.schedule(project,'calendar.list',{},signal);return versioned(value.calendars,['id','name','zone']) as Calendar[];},
 async reminders(project:string,signal?:AbortSignal){const value=await this.schedule(project,'reminder.list',{},signal);return versioned(value.reminders,['id','text','state','zone','start_local','missed_policy','channel']) as Reminder[];},
 async accounts(project:string,signal?:AbortSignal){return records(await request(project,'/mail/accounts','GET',undefined,signal),['id','label','address','mode']) as MailAccount[];},
 async createAccount(project:string,label:string,address:string,signal?:AbortSignal){return record(await request(project,'/mail/accounts','POST',{label,address},signal));},
 async contacts(project:string,account:string,signal?:AbortSignal){return records(await request(project,`/mail/accounts/${encodeURIComponent(account)}/contacts`,'GET',undefined,signal),['id','displayName','email']);},
 async saveContact(project:string,account:string,displayName:string,email:string,signal?:AbortSignal){return record(await request(project,`/mail/accounts/${encodeURIComponent(account)}/contacts`,'POST',{displayName,email},signal));},
 async importMail(project:string,accountId:string,rawBase64:string,signal?:AbortSignal){return record(await request(project,'/mail/import','POST',{accountId,rawBase64},signal));},
 async messages(project:string,account:string,signal?:AbortSignal){return versioned(await request(project,`/mail/accounts/${encodeURIComponent(account)}/messages`,'GET',undefined,signal),['id','subject'],'revision');},
 async message(project:string,account:string,id:string,signal?:AbortSignal){const value=record(await request(project,`/mail/accounts/${encodeURIComponent(account)}/messages/${encodeURIComponent(id)}`,'GET',undefined,signal));if(value.id!==id||typeof value.text!=='string')throw new SkillContractError('Invalid message evidence.');return value;},
 async drafts(project:string,account:string,signal?:AbortSignal){return versioned(await request(project,`/mail/accounts/${encodeURIComponent(account)}/drafts`,'GET',undefined,signal),['id','subject','state'],'revision') as DraftSummary[];},
 async draft(project:string,account:string,id:string,signal?:AbortSignal):Promise<ReviewedDraft>{const value=record(await request(project,`/mail/accounts/${encodeURIComponent(account)}/drafts/${encodeURIComponent(id)}`,'GET',undefined,signal));if(value.id!==id||value.account_id!==account||!Number.isSafeInteger(value.revision)||Number(value.revision)<1||typeof value.state!=='string'||typeof value.payload_sha256!=='string'||!/^[a-f0-9]{64}$/.test(value.payload_sha256)||!isRecord(value.draft))throw new SkillContractError('The reviewed draft does not match this account.');return value as ReviewedDraft;},
 async sources(project:string,signal?:AbortSignal){return versioned(await request(project,'/meeting-sources','GET',undefined,signal),['id','title','reference_date','zone']) as MeetingSource[];},
 async source(project:string,id:string,signal?:AbortSignal){const value=record(await request(project,`/meeting-sources/${encodeURIComponent(id)}`,'GET',undefined,signal));if(value.id!==id||typeof value.text!=='string'||!Number.isSafeInteger(value.version))throw new SkillContractError('Invalid meeting source.');return value as MeetingSource;},
 async tasks(project:string,signal?:AbortSignal){return versioned(await request(project,'/tasks','GET',undefined,signal),['id','title','state','source_id'],'revision') as TaskRecord[];},
 async updateTask(project:string,id:string,expectedRevision:number,state:'open'|'completed'|'cancelled',signal?:AbortSignal){const value=record(await request(project,`/tasks/${encodeURIComponent(id)}`,'PATCH',{expectedRevision,state},signal));if(value.id!==id||value.state!==state)throw new SkillContractError('Task update was not confirmed.');return value;},
};
