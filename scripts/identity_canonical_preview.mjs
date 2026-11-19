import {chromium} from '../tests/browser/node_modules/@playwright/test/index.mjs';
import {readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const root=resolve(import.meta.dirname,'..'),out=resolve(root,'reports/identity-20261116');
const image=async name=>'data:image/svg+xml;base64,'+(await readFile(resolve(root,'frontend/public/brand',name))).toString('base64');
const cards=[];
for(const variant of ['light','dark','mono','black','white']){
 cards.push(`<section class="${variant}"><h2>${variant}</h2><img class="symbol" src="${await image('riwaq-symbol-'+variant+'.svg')}"/><img class="lockup" src="${await image('riwaq-lockup-'+variant+'.svg')}"/><img class="lockup" src="${await image('riwaq-bilingual-'+variant+'.svg')}"/></section>`);
}
const html=`<!doctype html><meta charset="utf-8"><title>Selected canonical B exports</title><style>*{box-sizing:border-box}html,body{margin:0}body{padding:24px;font-family:Arial;color:#172b2a;background:#e5e7df}h1{font-size:24px;font-weight:500}main{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}section{text-align:center;background:#f5f1e8;border:1px solid #c4cbbf;padding:16px 5px}h2{font-size:16px;font-weight:400}img{display:block;margin:auto}.symbol{width:80px;height:80px;margin-bottom:30px}.lockup{width:210px;margin-top:22px}.dark,.white{background:#172b2a;color:#f5f1e8}.black{background:white}.mono{background:#e8e8e8}footer{font-size:13px;line-height:1.5;margin-top:24px}</style><h1>Riwaq — selected B / canonical native vector exports</h1><main>${cards.join('')}</main><footer>Same geometry and native outlines across variants. Top: symbol; middle: Arabic lockup; bottom: bilingual lockup. White version is shown on ink background. Captured from local files without network requests.</footer>`;
await writeFile(resolve(out,'canonical-exports-preview.html'),html);
const browser=await chromium.launch({channel:'chrome',headless:true});
try{const page=await browser.newPage({viewport:{width:1200,height:620},deviceScaleFactor:1});let external=0;await page.route('**/*',route=>{external++;return route.abort()});await page.setContent(html);await page.locator('img').evaluateAll(images=>Promise.all(images.map(image=>image.decode())));await page.screenshot({path:resolve(out,'canonical-exports-preview.png'),fullPage:true});if(external)throw Error('Unexpected request');console.log(resolve(out,'canonical-exports-preview.png'));}finally{await browser.close();}
