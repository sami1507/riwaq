import {chromium} from '../tests/browser/node_modules/@playwright/test/index.mjs';
import {readFile,writeFile,mkdir,stat} from 'node:fs/promises';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..'),input=process.argv[2],output=process.argv[3];
if(!input||!output)throw Error('Input/output paths required');
// One bounded normalized 16.5 MB asset, base64 overhead, and a 128 KiB design.
const MAX_INPUT=23_000_000; if((await stat(input)).size>MAX_INPUT)throw Error('Render input limit');
const raw=await readFile(input,'utf8');if(Buffer.byteLength(raw)>MAX_INPUT)throw Error('Render input limit');
const job=JSON.parse(raw);if(!['png','jpeg','webp'].includes(job.format))throw Error('Unsupported format');
if(job.format==='jpeg'&&!/^#[0-9a-f]{6}$/i.test(job.matte||''))throw Error('JPEG requires an explicit matte');
const coverage=JSON.parse(await readFile(join(root,'frontend/public/fonts/visual-coverage.json'),'utf8')),fontData=[];
for(const entry of Object.values(coverage)){
 const bytes=await readFile(join(root,'frontend/public/fonts',entry.file));
 if(createHash('sha256').update(bytes).digest('hex')!==entry.sha256)throw Error('Font checksum mismatch');
 fontData.push({...entry,data:'data:font/woff2;base64,'+bytes.toString('base64')});
}
const engine=await readFile(join(root,'frontend/src/designEngine.mjs'),'utf8'),browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run']});
let requests=[];
try{
 const context=await browser.newContext(),page=await context.newPage();
 await context.route('**/*',route=>{if(route.request().url()==='http://127.0.0.1:8765/riwaq-fixed-render')return route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><body></body>'});requests.push(route.request().url());return route.abort();});
 await page.goto('http://127.0.0.1:8765/riwaq-fixed-render');
 await page.addScriptTag({content:engine.replace(/^export (?=(?:async )?function )/gm,'')+'\nwindow.renderDesign=renderDesign;'});
 const result=await page.evaluate(async ({job,coverage,fontData})=>{
  for(const entry of fontData){const face=new FontFace(entry.family,'url('+entry.data+')',{weight:String(entry.weight),style:'normal'});await face.load();document.fonts.add(face);}
  const canvas=document.createElement('canvas');document.body.append(canvas);const {report}=await window.renderDesign(canvas,job.spec,job.assets||{},coverage);
  if(!report.success)return {report,image:null,mime:null};
  if(job.format==='jpeg'){const c=document.createElement('canvas');c.width=canvas.width;c.height=canvas.height;const cx=c.getContext('2d');cx.fillStyle=job.matte;cx.fillRect(0,0,c.width,c.height);cx.drawImage(canvas,0,0);canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height);canvas.getContext('2d').drawImage(c,0,0);}
  const requested='image/'+job.format,blob=await new Promise(resolve=>canvas.toBlob(resolve,requested,.95));
  if(!blob||blob.type!==requested)throw Error('Requested encoder unavailable; no silent PNG fallback');
  const image=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsDataURL(blob);});
  return {report,image,mime:blob.type,browser:navigator.userAgent};
 },{job,coverage,fontData});
 await mkdir(output,{recursive:true});result.report.export_format=job.format;result.report.matte=job.matte||null;result.report.blocked_network_requests=requests;result.report.renderer_sha256=createHash('sha256').update(engine).digest('hex');result.report.font_bytes_verified=true;
 if(result.image){const bytes=Buffer.from(result.image.split(',')[1],'base64');if(bytes.length>16_500_000)throw Error('Export exceeds normalized asset limit');await writeFile(join(output,'image.'+job.format),bytes);}
 await writeFile(join(output,'report.json'),JSON.stringify({...result,image:undefined},null,2));
 console.log(JSON.stringify({stage:'rendered',success:result.report.success,format:job.format}));
 if(!result.report.success)process.exitCode=2;
}finally{await browser.close();}
