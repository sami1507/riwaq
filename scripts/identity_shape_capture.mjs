// Development-only deterministic shaping capture. No network, user files or UI.
import {chromium} from '../tests/browser/node_modules/@playwright/test/index.mjs';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createHash} from 'node:crypto';

const root=resolve(import.meta.dirname,'..');
const output=resolve(root,'reports/identity-20261116/shaping');
await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true});
const cases=[{name:'arabic',text:'رواق',file:'IBMPlexSansArabic-SemiBold.woff2',weight:600,direction:'rtl',x:2100},
             {name:'latin',text:'Riwaq',file:'IBMPlexSans-Medium.woff2',weight:500,direction:'ltr',x:100}];
const records=[];
try{
 for(const item of cases){
  const font=await readFile(resolve(root,'frontend/public/fonts',item.file));
  const page=await browser.newPage({viewport:{width:3200,height:1200}});
  const blocked=[];await page.route('**/*',route=>{blocked.push(route.request().url());return route.abort();});
  await page.setContent(`<html><head><style>@page{size:3200px 1200px;margin:0}html,body{margin:0;padding:0}@font-face{font-family:IdentityFont;src:url(data:font/woff2;base64,${font.toString('base64')}) format('woff2');font-weight:${item.weight}}svg{display:block}</style></head><body><svg xmlns="http://www.w3.org/2000/svg" width="3200" height="1200"><text x="${item.x}" y="850" font-size="1000" font-weight="${item.weight}" font-family="IdentityFont" direction="${item.direction}" text-anchor="start">${item.text}</text></svg></body></html>`);
  await page.evaluate(()=>document.fonts.ready);
  if(!await page.evaluate(weight=>document.fonts.check(weight+' 1000px IdentityFont'),item.weight))throw Error('Local font not ready');
  const metrics=await page.locator('text').evaluate(element=>{const box=element.getBBox();return {box:{x:box.x,y:box.y,width:box.width,height:box.height},characters:Array.from(element.textContent).map((text,index)=>{const start=element.getStartPositionOfChar(index),end=element.getEndPositionOfChar(index);return {text,start:{x:start.x,y:start.y},end:{x:end.x,y:end.y}}})};});
  await page.pdf({path:resolve(output,item.name+'.pdf'),preferCSSPageSize:true,printBackground:true,displayHeaderFooter:false,margin:{top:0,bottom:0,left:0,right:0}});
  await page.screenshot({path:resolve(output,item.name+'-browser.png'),omitBackground:true});
  records.push({...item,font_sha256:createHash('sha256').update(font).digest('hex'),metrics,external_requests:blocked});
  if(blocked.length)throw Error('Unexpected resource request during local shaping capture');
  await page.close();
 }
 await writeFile(resolve(output,'capture.json'),JSON.stringify({browser:browser.version(),method:'Chrome native shaping to glyph-positioned PDF; fonts embedded from pinned local WOFF2',records},null,2));
 console.log(JSON.stringify({output,records:records.map(r=>({name:r.name,characters:r.metrics.characters,box:r.metrics.box})),browser:browser.version()}));
}finally{await browser.close();}
