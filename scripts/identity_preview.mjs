// Render only local, controlled vector identity assets. No external resources.
import {chromium} from '../tests/browser/node_modules/@playwright/test/index.mjs';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const root=resolve(import.meta.dirname,'..');
const out=resolve(root,'reports/identity-20261116');
await mkdir(out,{recursive:true});
const asset=async name=>'data:image/svg+xml;base64,'+(await readFile(resolve(root,'frontend/public/brand/directions',name))).toString('base64');
const wordmark=await asset('wordmark.svg');
const cards=[];
for(const [id,label] of [['a','A · ق / wordmark'],['b','B · Open return'],['c','C · Three rhythms']]){
 const symbol=await asset(id+'-symbol.svg'),lockup=await asset(id+'-lockup.svg');
 cards.push(`<section><h2>${label}</h2><div class="hero"><img width="96" height="96" src="${symbol}"/></div><div class="word"><img src="${wordmark}"/></div><p>Same native outline wordmark</p><div class="lock"><img width="260" height="130" src="${lockup}"/></div><p>Same 260×130 lockup / font66 position</p><div class="sizes">${[16,24,32].map(s=>`<span><img src="${symbol}" width="${s}" height="${s}"/><small>${s}px</small></span>`).join('')}</div><div class="sizes">${[16,24,32].map(s=>`<span><i style="width:${s}px;height:${s}px"><img src="${symbol}" width="${s}" height="${s}"/></i><small>badge ${s}</small></span>`).join('')}</div></section>`);
}
const html=`<!doctype html><meta charset="utf-8"><title>Riwaq identity A/B/C comparison</title><style>*{box-sizing:border-box}body{margin:0;padding:32px;background:#f5f1e8;color:#172b2a;font:16px Arial,sans-serif}h1{font-weight:500;font-size:28px;margin:0 0 12px}header p{margin:0 0 26px;max-width:900px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}section{background:#fffdf7;border:1px solid #c8cdc2;padding:22px 18px;text-align:center}h2{font:500 18px Arial;margin:0 0 26px}.hero{height:112px;display:flex;align-items:center;justify-content:center}.word img{height:90px;width:210px;margin:22px 0 8px}.lock{background:white;border:1px solid #e5e8df;margin:18px auto 0;width:278px}p{font-size:12px;color:#51625c;line-height:1.5}.sizes{display:flex;align-items:center;justify-content:center;gap:36px;height:88px}.sizes span{display:flex;align-items:center;justify-content:center;flex-direction:column;gap:10px;width:52px}.sizes i{display:block;border-radius:24%;background:#f5f1e8;box-shadow:0 0 0 1px #d6ded6}.sizes small{font:11px Arial;white-space:nowrap}.sizes img{display:block}footer{margin-top:24px;font-size:12px;line-height:1.6;color:#51625c}</style><header><h1>Riwaq / رواق — three vector directions</h1><p>One palette and one browser-shaped IBM Plex wordmark. B retains the open arcade and adds a compact inward return. Small icons are shown at actual CSS pixels; lower row uses the proposed ivory application badge.</p></header><div class="grid">${cards.join('')}</div><footer>Native SVG paths, no live font dependency in the mark. A extracts the existing Arabic ق glyph; C uses three independent grid strokes. This is a visual comparison, not a trademark clearance.</footer>`;
await writeFile(resolve(out,'vector-directions-preview.html'),html);
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const page=await browser.newPage({viewport:{width:1100,height:950},deviceScaleFactor:1});
 const requests=[];await page.route('**/*',route=>{requests.push(route.request().url());return route.abort();});
 await page.setContent(html);await page.locator('img').evaluateAll(images=>Promise.all(images.map(image=>image.decode())));
 await page.screenshot({path:resolve(out,'vector-directions-preview.png'),fullPage:true});
 if(requests.length)throw Error('Unexpected external request');
 await writeFile(resolve(out,'vector-preview-validation.json'),JSON.stringify({browser:browser.version(),rendered:'vector-directions-preview.png',externalRequests:requests,viewBox:'48×48',lockupViewBox:'260×130',cssPixelSizes:[16,24,32],status:'rendered; visual review follows'},null,2));
 console.log(resolve(out,'vector-directions-preview.png'));
}finally{await browser.close();}
