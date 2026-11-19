/** Local browser pairing. Credentials remain in the HttpOnly server cookie. */
export const apiBase=(window as unknown as {__TAURI_INTERNALS__?:unknown}).__TAURI_INTERNALS__?'http://127.0.0.1:8765':'';
type Listener=()=>void;
const listeners=new Set<Listener>();
const protectedRequests=new Set<AbortController>();
let locked=false;let sessionRevision=0;let statusProbe:{revision:number;promise:Promise<boolean>}|null=null;
export class LocalAuthenticationError extends Error{constructor(){super('Browser pairing is required. The original request was not retried.');this.name='LocalAuthenticationError';}}
export class PairingRequestError extends Error{status:number;constructor(status:number){super(`Local pairing service returned HTTP ${status}.`);this.status=status;}}
export function onAuthenticationRequired(listener:Listener){listeners.add(listener);return()=>{listeners.delete(listener);};}
function abortProtectedRequests(){for(const controller of protectedRequests)controller.abort(new LocalAuthenticationError());protectedRequests.clear();}
function requirePairing(){sessionRevision++;locked=true;statusProbe=null;abortProtectedRequests();for(const listener of listeners)listener();}
function localApiUrl(input:string){
 const base=new URL(apiBase||window.location.origin);const url=new URL(input,window.location.origin);
 if(url.origin!==base.origin||!url.pathname.startsWith('/api/')||url.username||url.password)throw Error('Only local API requests are permitted by this transport.');
 return url.href;
}
export async function localFetch(input:string,init:RequestInit={}):Promise<Response>{
 if(locked)throw new LocalAuthenticationError();
 const url=localApiUrl(input),headers=new Headers(init.headers);headers.set('X-Riwaq-Client','local');
 const requestRevision=sessionRevision,controller=new AbortController();protectedRequests.add(controller);
 const cancel=()=>controller.abort(init.signal?.reason);
 if(init.signal?.aborted)cancel();else init.signal?.addEventListener('abort',cancel,{once:true});
 let cleaned=false;const cleanup=()=>{if(cleaned)return;cleaned=true;protectedRequests.delete(controller);init.signal?.removeEventListener('abort',cancel);};
 const stale=()=>locked||requestRevision!==sessionRevision;
 let response:Response;
 try{
  response=await fetch(url,{...init,headers,signal:controller.signal,credentials:'include',cache:'no-store',redirect:'error'});
  if(stale()){await response.body?.cancel().catch(()=>{});throw new LocalAuthenticationError();}
  if(response.status===401){
   if(!statusProbe||statusProbe.revision!==requestRevision){const probe={revision:requestRevision,promise:Promise.resolve(false)};probe.promise=pairingStatus().then(status=>status.authenticated).catch(()=>false).finally(()=>{if(statusProbe===probe)statusProbe=null;});statusProbe=probe;}
   const authenticated=await statusProbe.promise;
   if(stale()){await response.body?.cancel().catch(()=>{});throw new LocalAuthenticationError();}
   if(!authenticated){requirePairing();await response.body?.cancel().catch(()=>{});throw new LocalAuthenticationError();}
   // A subordinate service may reject its own credentials. Never replay the request.
  }
 }catch(error){cleanup();throw error;}
 if(!response.body){cleanup();return response;}
 const reader=response.body.getReader();
 // Keep the abort binding through body consumption, including NDJSON and downloads.
 const body=new ReadableStream<Uint8Array>({
  async pull(target){try{if(stale())throw new LocalAuthenticationError();const part=await reader.read();if(stale())throw new LocalAuthenticationError();if(part.done){cleanup();target.close();}else target.enqueue(part.value);}catch(error){cleanup();await reader.cancel(error).catch(()=>{});target.error(error);}},
  async cancel(reason){controller.abort(reason);cleanup();await reader.cancel(reason).catch(()=>{});},
 });
 return new Response(body,{status:response.status,statusText:response.statusText,headers:response.headers});
}
async function pairingRequest(path:string,init:RequestInit={}){
 const headers=new Headers(init.headers);headers.set('X-Riwaq-Client','local');if(init.body)headers.set('Content-Type','application/json');
 const response=await fetch(localApiUrl(`${apiBase}/api/auth/${path}`),{...init,headers,credentials:'include',cache:'no-store',redirect:'error'});
 if(!response.ok)throw new PairingRequestError(response.status);
 return response;
}
export async function pairingStatus(signal?:AbortSignal):Promise<{authenticated:boolean;paired?:boolean}>{
 const response=await pairingRequest('status',{signal});const data:unknown=await response.json();
 if(!data||typeof data!=='object'||typeof (data as {authenticated?:unknown}).authenticated!=='boolean')throw Error('Invalid local authentication status.');
 return data as {authenticated:boolean;paired?:boolean};
}
export async function pairBrowser(pairingCode:string,signal?:AbortSignal){
 await pairingRequest('login',{method:'POST',body:JSON.stringify({pairing_code:pairingCode}),signal});
 const status=await pairingStatus(signal);if(!status.authenticated)throw new PairingRequestError(401);
 acceptAuthenticatedSession();
}
export function acceptAuthenticatedSession(){sessionRevision++;statusProbe=null;abortProtectedRequests();locked=false;}
export async function logoutBrowser(){try{await pairingRequest('logout',{method:'POST'});}catch(error){if(!(error instanceof PairingRequestError&&error.status===401))throw error;}requirePairing();}
