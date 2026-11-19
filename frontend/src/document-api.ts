import {localFetch} from './client-session';
import {api,apiBase} from './api';
import type {DocumentReference,FileScope} from './api';
import type {Lang} from './ui';
export type Box=[number,number,number,number];
export type Locator={page?:number;sheet?:string;slide?:number;paragraph?:number|string;table?:number|string;cells?:string[]};
export type DocumentCell={address:string;row:number;column:number|string;value:string;kind?:string;formula?:string|null;cached_value?:string|number|null;bbox?:Box|null};
export type DocumentElement={id:string;type:string;text_raw:string;text:string;revision:number;bbox:Box|null;locator:Locator;quality:Record<string,unknown>;cells?:DocumentCell[];warnings?:string[]};
export type DocumentPage={number:number;width:number;height:number;kind:string;status:string;method?:string;language?:string;warnings?:string[];elements:DocumentElement[]};
export type PageSummary=Omit<DocumentPage,'elements'|'method'|'language'>;
export type DocumentJob={id:string;status:string;pages_done:number;page_count:number;error?:string|null;stage?:string};
export type DocumentInfo={id:string;filename:string;mime?:string;format?:string;version:number;sha256:string;status:string;page_count:number;pages_done:number;job:DocumentJob|null;job_id?:string;pages?:PageSummary[];warnings?:string[];error?:string|null};
export type DocumentCapabilities={formats:Array<{extension:string;status:string;detail:string}>;ocr:{available:boolean;languages:string[];engine?:string;experimental:boolean};vision:{available:boolean;model?:string;experimental:boolean};limits:Record<string,number>;notice:string};
export type ElementVersion={revision:number;text:string;text_raw:string;created_at:string;method:string};
export type CellReference={file_id:string;version:number;page:number;element_id:string;revision:number;address:string;value:string};
export type TableResult={result:{exact:string;decimal:string;decimal_exact:boolean;unit:string;operation:string};cellrefs:CellReference[];scope:{row_start:number;row_end:number;column:string;all_requested_cells:boolean};warnings:string[]};
export type DocumentComparison={kind:'structural_difference';left:{id:string;filename:string;version:number;sha256:string};right:{id:string;filename:string;version:number;sha256:string};coverage:{left:{selected:number;processed:number;failed:number;partial:number};right:{selected:number;processed:number;failed:number;partial:number}};differences:Array<{location:string;before:string;after:string;left:DocumentReference|null;right:DocumentReference|null}>;total_differences:number;truncated:boolean;warnings:string[]};
export type DocumentVision={result:{transcribed_text:string;observations:string[];inferences:string[];uncertain:string[]};source:{project_id:string;file_id:string;filename:string;sha256:string;document_version:number;page:number;bbox:Box|null;preview_sha256:string;region_sha256:string};model:string;digest:string;processing:'local';experimental:true;metrics:Record<string,unknown>;notice:string};
export type DocumentLocation={file_id:string;page?:number;element_id?:string;version?:number;revision?:number;address?:string;key?:string};
const enc=encodeURIComponent;
export const documentPath=(projectId:string,fileId?:string)=>`/projects/${enc(projectId)}/documents${fileId?'/'+enc(fileId):''}`;
export const documentsApi={
 list:(pid:string,signal?:AbortSignal)=>api<DocumentInfo[]>(documentPath(pid),{signal}),
 capabilities:(signal?:AbortSignal)=>api<DocumentCapabilities>('/document-capabilities',{signal}),
 get:(pid:string,fid:string,signal?:AbortSignal)=>api<DocumentInfo>(documentPath(pid,fid),{signal}),
 page:(pid:string,fid:string,n:number,signal?:AbortSignal)=>api<DocumentPage>(documentPath(pid,fid)+`/pages/${n}`,{signal}),
 upload:(pid:string,file:File,languages:string[])=>{const form=new FormData();form.append('file',file);form.append('languages',languages.join('+'));return api<DocumentInfo>(documentPath(pid),{method:'POST',body:form});},
 control:(pid:string,fid:string,action:'resume'|'cancel')=>api<DocumentInfo>(documentPath(pid,fid)+'/'+action,{method:'POST'}),
 remove:(pid:string,fid:string)=>api(`/projects/${enc(pid)}/files/${enc(fid)}`,{method:'DELETE'}),
 correct:(pid:string,fid:string,eid:string,text:string,revision:number)=>api<DocumentElement>(documentPath(pid,fid)+`/elements/${enc(eid)}`,{method:'PATCH',body:JSON.stringify({text,expected_revision:revision})}),
 history:(pid:string,fid:string,eid:string,signal?:AbortSignal)=>api<ElementVersion[]>(documentPath(pid,fid)+`/elements/${enc(eid)}/versions`,{signal}),
 compare:(pid:string,left:FileScope,right:FileScope)=>api<DocumentComparison>(documentPath(pid)+'/compare',{method:'POST',body:JSON.stringify({left,right})}),
 vision:(pid:string,fid:string,n:number,input:{question:string;bbox?:Box|null})=>api<DocumentVision>(documentPath(pid,fid)+`/pages/${n}/vision`,{method:'POST',body:JSON.stringify(input)}),
 stopVision:()=>api('/media/stop',{method:'POST'}),
 calculate:(pid:string,fid:string,input:{element_id:string;operation:'sum'|'mean'|'count';column:string;row_start:number;row_end:number;unit:string})=>api<TableResult>(documentPath(pid,fid)+'/table-calculate',{method:'POST',body:JSON.stringify(input)})
};
export async function documentBlob(projectId:string,fileId:string,part:'original'|number,signal?:AbortSignal){
 const path=documentPath(projectId,fileId)+(part==='original'?'/original':`/pages/${part}/preview`);
 const response=await localFetch(apiBase+'/api'+path,{headers:{'X-Riwaq-Client':'local'},signal});
 if(!response.ok){let message=`HTTP ${response.status}`;try{const data=await response.json();message=typeof data.detail==='string'?data.detail:JSON.stringify(data.detail??data);}catch{/* preserve status */}throw Error(message);}
 const blob=await response.blob();if(typeof part==='number'&&blob.type.split(';')[0]!=='image/png')throw Error('The preview response was not a PNG image.');return blob;
}
export function downloadBlob(blob:Blob,filename:string){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=filename.replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').slice(0,180)||'document';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
export function validBox(box:unknown):box is Box{return Array.isArray(box)&&box.length===4&&box.every(x=>typeof x==='number'&&Number.isFinite(x)&&x>=0&&x<=1)&&box[2]>box[0]&&box[3]>box[1];}
export function documentLocationLabel(ref:Pick<DocumentReference,'page'|'locator'|'mime'>,lang:Lang){const ar=lang==='ar',loc=ref.locator??{},parts:string[]=[];
 if(ref.mime==='application/pdf')parts.push(`${ar?'صفحة':'Page'} ${ref.page}`);
 if(loc.sheet)parts.push(`${ar?'ورقة':'Sheet'} ${loc.sheet}`);
 if(loc.slide!==undefined)parts.push(`${ar?'شريحة':'Slide'} ${loc.slide}`);
 if(loc.paragraph!==undefined)parts.push(`${ar?'فقرة':'Paragraph'} ${loc.paragraph}`);
 if(loc.table!==undefined)parts.push(`${ar?'جدول':'Table'} ${loc.table}`);
 if(loc.cells?.length)parts.push(`${ar?'خلايا':'Cells'} ${loc.cells.join(', ')}`);
 return parts.join(' · ')||`${ar?'وحدة':'Unit'} ${ref.page}`;
}
