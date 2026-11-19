// Recheck persisted real-model answer without invoking the model again.
import {chromium,expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {readFile,writeFile} from 'node:fs/promises';import assert from 'node:assert/strict';
const evidence=JSON.parse(await readFile('reports/identity-live.json','utf8')),origin='http://127.0.0.1:8765';const checks=[];
const browser=await chromium.launch({channel:'chrome',headless:true}),page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 for(const theme of ['light','dark'])for(const width of [360,390,768,1280,1440]){
  await page.setViewportSize({width,height:width<500?844:1000});await page.goto(origin);await page.evaluate(({evidence,theme})=>{localStorage.setItem('riwaq.v1.project',evidence.project_id);localStorage.setItem('riwaq.v1.conversation',evidence.conversation_id);localStorage.setItem('riwaq.v1.lang','ar');localStorage.setItem('riwaq.v1.theme',theme)},{evidence,theme});await page.reload();await page.locator('.app[aria-busy="false"]').waitFor();await page.evaluate(()=>document.fonts.ready);await expect(page.locator('html')).toHaveAttribute('data-theme',theme);
  const answer=page.locator('[data-message-id="'+evidence.message_id+'"] .markdown');await expect(answer).toContainText('Pydantic');await answer.scrollIntoViewIfNeeded();
  if(width<761)await expect.poll(()=>page.locator('.sidebar').evaluate(e=>e.getBoundingClientRect().left)).toBeGreaterThanOrEqual(width-1);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert(await answer.evaluate(e=>{const r=e.getBoundingClientRect(),p=document.elementFromPoint(Math.min(r.right-2,Math.max(r.left+2,innerWidth/2)),Math.max(r.top+2,2));return p&&e.contains(p)}),'answer occluded');
  await page.screenshot({path:`reports/identity/answer-${theme}-${width}.png`,fullPage:true,animations:'disabled'});checks.push({theme,width,noOverflow:true,answerUnobscured:true});
 }
 assert.deepEqual(errors,[]);await writeFile('reports/identity-answer-layout.json',JSON.stringify({passed:true,real_saved_answer:evidence.message_id,checks,page_errors:errors,model_invoked:false,animations_settled:true},null,2));console.log(JSON.stringify({passed:true,checks:checks.length}));
}finally{await browser.close()}
