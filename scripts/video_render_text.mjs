/** Literal timed-text rasterizer. No images generated, no external browser requests. */
import {chromium} from '../tests/browser/node_modules/@playwright/test/index.mjs';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const [input,output]=process.argv.slice(2);
if(!input||!output)throw Error('Input/output paths required');
const raw=await readFile(input,'utf8');if(Buffer.byteLength(raw)>256*1024)throw Error('Text input exceeds bound');
const job=JSON.parse(raw);if(!Array.isArray(job.texts)||job.texts.length>24)throw Error('Too many text layers');
const coverage=JSON.parse(await readFile(join(root,'frontend/public/fonts/visual-coverage.json'),'utf8'));
const fontData=[];
for(const entry of Object.values(coverage)){
 const bytes=await readFile(join(root,'frontend/public/fonts',entry.file));
 if(createHash('sha256').update(bytes).digest('hex')!==entry.sha256)throw Error('Font checksum mismatch');
 fontData.push({...entry,data:'data:font/woff2;base64,'+bytes.toString('base64')});
}
const engine=await readFile(join(root,'frontend/src/designEngine.mjs'),'utf8');
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run']});
const blocked=[];const reports=[];
try{
 const context=await browser.newContext();
 await context.route('**/*',route=>{
  if(route.request().url()==='http://127.0.0.1:8765/riwaq-video-text')return route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><body></body>'});
  blocked.push(route.request().url());return route.abort();
 });
 const page=await context.newPage();await page.goto('http://127.0.0.1:8765/riwaq-video-text');
 await page.addScriptTag({content:engine.replace(/^export (?=(?:async )?function )/gm,'')+'\nwindow.renderDesign=renderDesign;'});
 await page.evaluate(async entries=>{
  for(const entry of entries){const face=new FontFace(entry.family,'url('+entry.data+')',{weight:String(entry.weight),style:'normal'});await face.load();document.fonts.add(face);}
 },fontData);
 await mkdir(output,{recursive:true});
 for(let index=0;index<job.texts.length;index++){
  const original=job.texts[index];const {kind,start,end,...style}=original;
  const layer={...style,protected:true};
  const result=await page.evaluate(async({width,height,layer,coverage})=>{
   const canvas=document.createElement('canvas');
   const {report}=await window.renderDesign(canvas,{width,height,background:'transparent',layers:[layer],shapes:[]},{},coverage);
   return {report,image:report.success?canvas.toDataURL('image/png'):null};
  },{width:job.width,height:job.height,layer,coverage});
  reports.push({id:original.id,start,end,kind,...result.report});
  if(!result.image)throw Error('Text layout rejected for '+original.id+'; inspect text-report.json');
  const bytes=Buffer.from(result.image.split(',')[1],'base64');if(bytes.length>6*1024**2)throw Error('Text PNG exceeds6MiB');
  await writeFile(join(output,`text-${String(index).padStart(3,'0')}.png`),bytes,{flag:'wx'});
 }
 const report={success:true,source_unchanged:reports.every(r=>r.source_unchanged),font_bytes_verified:true,
  renderer_sha256:createHash('sha256').update(engine).digest('hex'),font_manifest_sha256:createHash('sha256').update(JSON.stringify(coverage)).digest('hex'),
  fonts:fontData.map(({data,ranges,...entry})=>entry),layers:reports,blocked_network_requests:blocked,
  notice:'Exact Unicode source, font coverage and geometry checks; not an independent visual quality review. Temporal layer overlap is permitted and needs inspection.'};
 await writeFile(join(output,'text-report.json'),JSON.stringify(report));
 console.log(JSON.stringify({stage:'text','success':true,step:reports.length,total:job.texts.length}));
}catch(error){
 await mkdir(output,{recursive:true});await writeFile(join(output,'text-report.json'),JSON.stringify({success:false,error:String(error),layers:reports,blocked_network_requests:blocked}));
 throw error;
}finally{await browser.close();}
