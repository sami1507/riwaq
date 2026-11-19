// Canonical local SVG -> browser raster -> PNG/ICO, without font or drawing forks.
import {chromium} from '../tests/browser/node_modules/@playwright/test/index.mjs';
import {readFile,writeFile,mkdir,copyFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
const root=resolve(import.meta.dirname,'..');
const brand=resolve(root,'frontend/public/brand'),desktop=resolve(root,'desktop/src-tauri/icons');
const report=resolve(root,'reports/identity-20261116');
const geometryBytes=await readFile(resolve(root,'frontend/src/brand-geometry.json'));
const geometry=JSON.parse(geometryBytes);
const svg=await readFile(resolve(brand,'riwaq-symbol-light.svg'),'utf8');
assert.equal((svg.match(/<path\b/g)||[]).length,1,'Canonical symbol must have one native path');
assert(svg.includes(`d="${geometry.path}"`)&&svg.includes(`viewBox="${geometry.viewBox}"`),'Canonical SVG differs from geometry');
assert(!/<(?:script|image|foreignObject|text)\b|(?:href|onload)=/i.test(svg),'Symbol must be a passive path');
const sizes=[16,24,32,48,64,128,256];
for(const folder of [brand,desktop,report])await mkdir(folder,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true});
const records=[];
try{
 const page=await browser.newPage({viewport:{width:300,height:300},deviceScaleFactor:1});
 const requests=[];await page.route('**/*',route=>{requests.push(route.request().url());return route.abort();});
 for(const size of sizes){
  for(const badge of [false,true]){
   const name=badge?`icon-${size}.png`:`icon-transparent-${size}.png`;
   const path=resolve(brand,name);
   const markup=svg.replace(/<\?xml[^>]*\?>/,'').replace('<svg ',`<svg width="${size}" height="${size}" `);
   await page.setContent(`<html><head><style>html,body{margin:0;background:transparent}#icon{width:${size}px;height:${size}px;${badge?`background:${geometry.palette.ivory};border-radius:${size*.2}px`:''}}svg{display:block}</style></head><body><div id="icon">${markup}</div></body></html>`);
   await page.locator('#icon').screenshot({path,omitBackground:true});
   const png=await readFile(path);assert(png.readUInt32BE(16)===size&&png.readUInt32BE(20)===size,'Unexpected PNG dimensions');
   assert.equal(png[25],6,'Expected RGBA PNG');
   records.push({file:`frontend/public/brand/${name}`,size,background:badge?'ivory rounded badge':'transparent',sha256:createHash('sha256').update(png).digest('hex')});
  }
 }
 assert.deepEqual(requests,[],'Local export made an external request');
 for(const size of [32,128,256])await copyFile(resolve(brand,`icon-${size}.png`),resolve(desktop,`${size}x${size}.png`));
 await copyFile(resolve(brand,'icon-256.png'),resolve(desktop,'icon.png'));
 // ICO container with existing PNG frames. 32px first per Tauri's Windows advice.
 const icoSizes=[32,16,24,48,64,128,256],frames=await Promise.all(icoSizes.map(size=>readFile(resolve(brand,`icon-${size}.png`))));
 const header=Buffer.alloc(6+icoSizes.length*16);header.writeUInt16LE(1,2);header.writeUInt16LE(icoSizes.length,4);let offset=header.length;
 for(let i=0;i<icoSizes.length;i++){
  const index=6+i*16,size=icoSizes[i];header[index]=size===256?0:size;header[index+1]=size===256?0:size;
  header.writeUInt16LE(1,index+4);header.writeUInt16LE(32,index+6);header.writeUInt32LE(frames[i].length,index+8);header.writeUInt32LE(offset,index+12);offset+=frames[i].length;
 }
 const ico=Buffer.concat([header,...frames]);await writeFile(resolve(desktop,'icon.ico'),ico);
 await writeFile(resolve(report,'icon-export.json'),JSON.stringify({selectedDirection:geometry.selected_direction,geometrySha256:createHash('sha256').update(geometryBytes).digest('hex'),source:'frontend/public/brand/riwaq-symbol-light.svg',browser:browser.version(),externalRequests:requests,icoFrames:icoSizes,icoSha256:createHash('sha256').update(ico).digest('hex'),records},null,2));
 console.log(JSON.stringify({generatedPngs:records.length,icoFrames:icoSizes,output:desktop,networkRequests:0}));
}finally{await browser.close();}
