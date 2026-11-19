// Read-only real UI inspection of the already saved live test conversation.
import {chromium,expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {readFile,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const proof=JSON.parse(await readFile('reports/grounding-live-browser.json','utf8'));
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:390,height:844}});
const mutations=[],errors=[];
page.on('request',request=>{if(!['GET','HEAD','OPTIONS'].includes(request.method()))mutations.push(request.url());});
page.on('pageerror',error=>errors.push(error.message));
await page.addInitScript(({project,conversation})=>{localStorage.setItem('riwaq.v1.project',project);localStorage.setItem('riwaq.v1.conversation',conversation);localStorage.setItem('riwaq.v1.lang','ar');},{project:proof.project_id,conversation:proof.conversation_id});
try{
 await page.goto('http://127.0.0.1:8765');
 await page.locator('.app[aria-busy="false"]').waitFor();
 await expect(page.locator('.message.assistant .markdown')).toContainText('Pydantic');
 await page.screenshot({path:'reports/grounding-live-mobile.png',fullPage:true,animations:'disabled'});
 const bounds=await page.locator('.sidebar').boundingBox();
 assert(bounds.x>=390||bounds.x+bounds.width<=0,'Closed sidebar stays outside mobile viewport');
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.locator('.mobile-menu').click();
 await expect(page.locator('.sidebar')).toHaveClass(/open/);
 await page.locator('.sidebar-scrim').click();
 await expect(page.locator('.sidebar')).not.toHaveClass(/open/);
 assert.deepEqual(mutations,[]);assert.deepEqual(errors,[]);
 proof.mobile_visual_recheck={passed:true,viewport:{width:390,height:844},sidebar_closed_outside_viewport:true,menu_open_close:true,api_mutations:0,note:'Captured from a fresh mobile page with CSS transitions settled. Initial desktop-to-mobile capture was during a sidebar transition.'};
 await writeFile('reports/grounding-live-browser.json',JSON.stringify(proof,null,2));
 console.log(JSON.stringify(proof.mobile_visual_recheck));
}finally{await browser.close();}
