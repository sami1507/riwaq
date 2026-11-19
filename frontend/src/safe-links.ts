/** Navigation policy for untrusted text, not DNS resolution or network isolation. */
export function safeSourceLink(value:string|undefined):string|undefined{
 if(typeof value!=='string'||!value||/[\u0000-\u0020\u007f\\]/u.test(value))return undefined;
 try{
  const url=new URL(value);
  if(url.protocol!=='https:'||url.username||url.password||(url.port&&url.port!=='443'))return undefined;
  // WHATWG URL canonicalizes decimal, octal and hexadecimal IPv4 spellings.
  const host=url.hostname.toLowerCase().replace(/\.$/,'');
  if(!host.includes('.')||host.includes(':')||/^\d+(?:\.\d+){3}$/u.test(host))return undefined;
  if(['localhost','local','internal'].some(suffix=>host===suffix||host.endsWith('.'+suffix)))return undefined;
  const labels=host.split('.');
  if(host.length>253||labels.some(label=>!/[a-z0-9]/u.test(label)||!/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/u.test(label)))return undefined;
  if(!/^[a-z]/u.test(labels[labels.length-1]))return undefined;
  return url.href;
 }catch{return undefined;}
}
