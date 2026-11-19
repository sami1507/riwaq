import {chromium} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..'),out=join(root,'reports/renderer-regression');
const engine=await readFile(join(root,'frontend/src/designEngine.mjs'),'utf8');
const coverage=JSON.parse(await readFile(join(root,'frontend/public/fonts/visual-coverage.json'),'utf8'));
const fontData=await Promise.all(Object.values(coverage).map(async entry=>{const bytes=await readFile(join(root,'frontend/public/fonts',entry.file));if(createHash('sha256').update(bytes).digest('hex')!==entry.sha256)throw Error('Font bytes mismatch');return {...entry,data:'data:font/woff2;base64,'+bytes.toString('base64')};}));
const arabic=JSON.parse(await readFile(join(root,'reports/visual-first-arabic-input.json'),'utf8')).spec;
const literal='المعنى محفوظ كما كُتب.\r\nCafé / Cafe\u0301  19,95 €';
const expectedHash=createHash('sha256').update(literal,'utf8').digest('hex');
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run']});
const requests=[];
try{
 const context=await browser.newContext(),page=await context.newPage();
 await context.route('**/*',route=>{if(route.request().url()==='http://127.0.0.1:8765/renderer-regression')return route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><body></body>'});requests.push(route.request().url());return route.abort();});
 await page.goto('http://127.0.0.1:8765/renderer-regression');
 await page.addScriptTag({content:engine.replace(/^export (?=(?:async )?function )/gm,'')+'\nwindow.renderDesign=renderDesign;'});
 const result=await page.evaluate(async({coverage,fontData,arabic,literal,expectedHash})=>{
  for(const entry of fontData){const face=new FontFace(entry.family,'url('+entry.data+')',{weight:String(entry.weight),style:'normal'});await face.load();document.fonts.add(face);}
  const checks=[],reports={};
  const check=(name,pass,details)=>checks.push({name,pass,details});
  const canvas=()=>{const c=document.createElement('canvas');document.body.append(c);return c;};
  const makeLayer=(extra={})=>({id:'first',text:'WWWWWWWWWWWWWWWW\nI',language:'en',direction:'ltr',font:'latin',size:32,weight:400,x:48,y:48,width:700,height:210,color:'#172b2a',align:'left',protected:true,...extra});
  const makeSpec=layers=>({width:1024,height:768,background:'#ffffff',background_asset_id:null,layers,shapes:[]});
  const run=async spec=>(await window.renderDesign(canvas(),spec,{},coverage)).report;
  const ar=await run(arabic);reports.arabic=ar;
  check('Arabic mixed text sidebearings fit without changing source',ar.success&&ar.source_unchanged&&ar.layers.every(l=>l.overflow.length===0),ar.layers.map(l=>({id:l.id,overflow:l.overflow,lines:l.lines.map(x=>({x:x.x,advance:x.advance,ink_bounds:x.ink_bounds}))})));
  const first=await run(makeSpec([makeLayer()]));const secondY=48+first.layers[0].line_height;
  const gap=await run(makeSpec([makeLayer(),makeLayer({id:'second',text:'SIDE',x:350,y:secondY,width:220,height:110})]));reports.gap=gap;
  const [a,b]=gap.layers.map(l=>l.ink_bounds);const unionIntersects=Math.min(a.right,b.right)>Math.max(a.left,b.left)&&Math.min(a.bottom,b.bottom)>Math.max(a.top,b.top);
  check('Vacant multiline rectangle does not count as ink overlap',unionIntersects&&gap.success&&gap.overlap.length===0,{unionIntersects,overlap:gap.overlap});
  const collision=await run(makeSpec([makeLayer(),makeLayer({id:'second',text:'WWWW'})]));reports.collision=collision;
  check('Real text overlap blocks checked export',!collision.success&&collision.overlap.length===1,collision.overlap);
  const wide=await run(makeSpec([makeLayer({text:'مستمر'.repeat(12),language:'ar',direction:'rtl',font:'arabic',width:70,height:600})]));reports.wide=wide;
  check('Overwide word is rejected rather than split',!wide.success&&wide.layers[0].overflow.some(x=>x.code==='word_too_wide'),{error:wide.layers[0].layout_error,overflow:wide.layers[0].overflow});
  const exact=await run(makeSpec([makeLayer({text:literal,language:'ar',direction:'auto',font:'arabic',width:928,height:600})]));reports.exact=exact;
  const lineText=exact.layers[0].lines.map(x=>x.text+x.separator).join('');
  check('CRLF, NFD accent and repeated spaces retain exact UTF8 hash',exact.success&&lineText===literal&&exact.layers[0].source_text_sha256===expectedHash&&exact.layers[0].reconstructed_text_sha256===expectedHash,{expectedHash,sourceHash:exact.layers[0].source_text_sha256,reconstructedHash:exact.layers[0].reconstructed_text_sha256});
  const raceCanvas=canvas();raceCanvas.width=123;raceCanvas.height=123;raceCanvas.getContext('2d').fillStyle='#1155aa';raceCanvas.getContext('2d').fillRect(0,0,123,123);
  const previousPixel=[...raceCanvas.getContext('2d').getImageData(0,0,1,1).data];
  const originalLoad=document.fonts.load.bind(document.fonts);let release,intercepted=false;
  const gate=new Promise(resolve=>{release=resolve;});
  Object.defineProperty(document.fonts,'load',{configurable:true,value:async(...args)=>{if(!intercepted){intercepted=true;await gate;}return originalLoad(...args);}});
  let old,invalidError='';
  try{const pending=window.renderDesign(raceCanvas,makeSpec([makeLayer()]),{},coverage);try{await window.renderDesign(raceCanvas,{...makeSpec([makeLayer()]),width:1},{},coverage);}catch(e){invalidError=e.message;}release();old=(await pending).report;}finally{release();delete document.fonts.load;}
  const currentPixel=[...raceCanvas.getContext('2d').getImageData(0,0,1,1).data];
  check('Invalid newest request cancels pending old render',invalidError==='invalid_canvas'&&old.cancelled&&raceCanvas.width===123&&String(previousPixel)===String(currentPixel),{invalidError,cancelled:old.cancelled,width:raceCanvas.width,currentPixel});
  const q=[[400,300],[1000,260],[960,650],[450,600]],planeCanvas=canvas(),planeSpec=makeSpec([makeLayer({perspective:q})]);planeSpec.background='transparent';
  const plane=(await window.renderDesign(planeCanvas,planeSpec,{},coverage)).report;reports.perspective=plane;
  const rgba=planeCanvas.getContext('2d').getImageData(0,0,1024,768).data;let count=0,minX=1024,minY=768,maxX=0,maxY=0;
  for(let y=0;y<768;y++)for(let x=0;x<1024;x++)if(rgba[(y*1024+x)*4+3]>10){count++;minX=Math.min(minX,x);minY=Math.min(minY,y);maxX=Math.max(maxX,x);maxY=Math.max(maxY,y);}
  check('Perspective moves actual ink into the destination plane and preserves source',plane.success&&plane.source_unchanged&&count>400&&minX>=400&&minY>=260&&maxX<=1000&&maxY<=650&&plane.layers[0].perspective.grid===24,{count,minX,minY,maxX,maxY});
  const invalidPlanes=[[[0,0],[100,100],[0,100],[100,0]],[[0,0],[0,0],[0,0],[0,0]],[[0,0],[2000,0],[2000,100],[0,100]]];let rejected=0;
  for(const perspective of invalidPlanes)try{await run(makeSpec([makeLayer({perspective})]));}catch(e){if(e.message==='invalid_text_plane')rejected++;}
  check('Crossed, degenerate and out-of-canvas planes are rejected',rejected===invalidPlanes.length,{rejected});
  return {checks,reports,browser:navigator.userAgent,limitations:['Structural/rendering regression checks only; no OCR or human typography certification.','Font bytes verified before loading; no model calls.']};
 },{coverage,fontData,arabic,literal,expectedHash});
 result.blocked_network_requests=requests;result.renderer_sha256=createHash('sha256').update(engine).digest('hex');
 await mkdir(out,{recursive:true});await writeFile(join(out,'report.json'),JSON.stringify(result,null,2));
 const failed=result.checks.filter(x=>!x.pass);console.log(JSON.stringify({passed:result.checks.length-failed.length,failed:failed.length,checks:result.checks,network_requests:requests}));if(failed.length||requests.length)process.exitCode=1;
}finally{await browser.close();}
