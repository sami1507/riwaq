/** Versioned backup envelope. See MDN SubtleCrypto.deriveKey and AesGcmParams.
 * Passphrases and derived keys stay in this JS session; only the envelope leaves it. */
export type EncryptedBackup={version:1;kdf:'PBKDF2-SHA256';iterations:310000;salt:string;nonce:string;ciphertext:string};
const iterations=310000;const maxBytes=3*1024*1024;
function encode(bytes:Uint8Array){let value='';for(let i=0;i<bytes.length;i+=32768)value+=String.fromCharCode(...bytes.subarray(i,i+32768));return btoa(value);}
function decode(value:string){if(typeof value!=='string'||value.length>4*1024*1024)throw new Error('Invalid backup size');return Uint8Array.from(atob(value),character=>character.charCodeAt(0));}
async function key(passphrase:string,salt:Uint8Array<ArrayBuffer>){
 if(!globalThis.crypto?.subtle)throw new Error('WebCrypto is unavailable. Open the app on localhost or HTTPS.');
 if(passphrase.length<12)throw new Error('Use a backup passphrase of at least 12 characters.');
 const material=await crypto.subtle.importKey('raw',new TextEncoder().encode(passphrase),'PBKDF2',false,['deriveKey']);
 return crypto.subtle.deriveKey({name:'PBKDF2',hash:'SHA-256',salt,iterations},material,{name:'AES-GCM',length:256},false,['encrypt','decrypt']);
}
export async function encryptSnapshot(snapshot:unknown,passphrase:string):Promise<EncryptedBackup>{
 const plain=new TextEncoder().encode(JSON.stringify(snapshot));if(plain.length>maxBytes-16)throw new Error('Project snapshot exceeds the 3 MB encrypted backup limit.');
 const salt=crypto.getRandomValues(new Uint8Array(16));const nonce=crypto.getRandomValues(new Uint8Array(12));
 const derived=await key(passphrase,salt);const ciphertext=await crypto.subtle.encrypt({name:'AES-GCM',iv:nonce,tagLength:128},derived,plain);
 return {version:1,kdf:'PBKDF2-SHA256',iterations,salt:encode(salt),nonce:encode(nonce),ciphertext:encode(new Uint8Array(ciphertext))};
}
export async function decryptSnapshot(envelope:EncryptedBackup,passphrase:string):Promise<unknown>{
 if(envelope?.version!==1||envelope.kdf!=='PBKDF2-SHA256'||envelope.iterations!==iterations)throw new Error('Unsupported backup encryption format.');
 const salt=decode(envelope.salt),nonce=decode(envelope.nonce),ciphertext=decode(envelope.ciphertext);
 if(salt.length!==16||nonce.length!==12||ciphertext.length<16||ciphertext.length>maxBytes)throw new Error('Invalid encrypted backup.');
 const derived=await key(passphrase,salt);
 try{const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:nonce,tagLength:128},derived,ciphertext);return JSON.parse(new TextDecoder().decode(plain));}catch{throw new Error('The backup passphrase is incorrect or the encrypted backup is damaged.');}
}
