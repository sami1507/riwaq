/** Retry identity only. No prompt, response, credential or health content stored. */
const prefix='riwaq.pending-generation.v1:';
const memory=new Map<string,{fingerprint:string;request_id:string}>();
export async function generationAttempt(projectId:string,conversationId:string,payload:unknown){
 const slot=prefix+projectId+':'+conversationId;
 const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(payload)));
 const fingerprint=Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');
 let previous=memory.get(slot);
 try{const saved=JSON.parse(sessionStorage.getItem(slot)??'null');if(saved&&typeof saved.fingerprint==='string'&&typeof saved.request_id==='string'&&/^[A-Za-z0-9_-]{8,96}$/.test(saved.request_id))previous=saved;}catch{/* In-memory identity still protects this tab. */}
 const attempt=previous?.fingerprint===fingerprint?previous:{fingerprint,request_id:crypto.randomUUID()};
 memory.set(slot,attempt);try{sessionStorage.setItem(slot,JSON.stringify(attempt));}catch{/* Storage can be disabled. */}
 return {...attempt,finish:()=>{
  if(memory.get(slot)?.request_id===attempt.request_id)memory.delete(slot);
  try{const current=JSON.parse(sessionStorage.getItem(slot)??'null');if(current?.request_id===attempt.request_id)sessionStorage.removeItem(slot);}catch{/* No content or fallback persistence. */}
 }};
}
