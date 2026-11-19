import {chromium} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {mkdir,writeFile} from 'node:fs/promises';
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
await page.addInitScript(()=>{localStorage.setItem('riwaq.v1.lang','ar');localStorage.setItem('riwaq.v1.theme','light');localStorage.removeItem('riwaq.v1.conversation');});
try{await page.goto('http://127.0.0.1:8765');await page.locator('.app[aria-busy="false"]').waitFor();await page.evaluate(()=>document.fonts.ready);await mkdir('reports/identity',{recursive:true});await page.screenshot({path:'reports/identity/first-desktop.png',fullPage:true});console.log(JSON.stringify(await page.evaluate(()=>({title:document.title,font:getComputedStyle(document.body).fontFamily,bg:getComputedStyle(document.body).backgroundColor,overflow:document.documentElement.scrollWidth>innerWidth}))))}finally{await browser.close()}
