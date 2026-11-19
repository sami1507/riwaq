// Real browser -> FastAPI -> local Ollama -> saved conversation -> reload.
import {chromium,expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const origin='http://127.0.0.1:8765';
async function api(path,method='GET',body){
 const response=await fetch(origin+'/api'+path,{method,headers:{'X-Riwaq-Client':'local',...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
 assert(response.ok,method+' '+path+' '+response.status);
 return response.json();
}
const before=await api('/settings');
assert.equal(before.mode,'local');assert.equal(before.provider,'ollama');
const project=await api('/projects','POST',{name:'تحقق فعلي • جودة المعرفة 2026-11-13',instructions:''});
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[],external=[];
page.on('pageerror',error=>errors.push(error.message));
page.on('request',request=>{if(!request.url().startsWith(origin)&&!request.url().startsWith('data:')&&!request.url().startsWith('blob:'))external.push(request.url());});
await page.addInitScript(id=>{localStorage.setItem('riwaq.v1.project',id);localStorage.removeItem('riwaq.v1.conversation');localStorage.setItem('riwaq.v1.lang','ar');},project.id);
try{
 await page.goto(origin);
 await page.locator('.app[aria-busy="false"]').waitFor();
 await page.locator('.source-toggle.public').click();
 await page.evaluate(()=>{
  window.__sawLiveText=false;
  new MutationObserver(()=>{
   if(document.querySelector('.composer.generating')&&Array.from(document.querySelectorAll('.message.assistant .markdown')).some(element=>element.textContent.trim().length>10))window.__sawLiveText=true;
  }).observe(document.body,{subtree:true,childList:true,characterData:true});
 });
 const question='في FastAPI، ما نوع النموذج الذي نستخدمه لتعريف حقول جسم الطلب؟ أجب بجملة واحدة مع مرجع.';
 await page.locator('.composer textarea').fill(question);
 const generated=page.waitForResponse(response=>response.url().endsWith('/generate')&&response.request().method()==='POST',{timeout:200000});
 await page.getByRole('button',{name:'إرسال',exact:true}).click();
 assert.equal((await generated).status(),200);
 await expect(page.locator('.message.assistant')).toHaveCount(1,{timeout:200000});
 await expect(page.locator('.composer')).not.toHaveClass(/generating/,{timeout:200000});
 const conversations=await api('/conversations?project_id='+project.id);
 assert.equal(conversations.length,1);
 const stored=await api('/conversations/'+conversations[0].id);
 const answer=stored.messages.at(-1);
 assert.equal(answer.status,'complete');assert(answer.citations.length>0);
 assert(/Pydantic/i.test(answer.content));assert(/\[S\d+\]/.test(answer.content));
 const observed=await page.evaluate(()=>window.__sawLiveText);
 const rendered=await page.locator('.message.assistant .markdown').innerText();
 await page.screenshot({path:'reports/grounding-live-desktop.png',fullPage:true});
 // Init script removes the selection on navigation, so explicitly reopen after reload.
 await page.reload();await page.locator('.app[aria-busy="false"]').waitFor();
 await page.locator('.conversation-item>button').filter({hasText:conversations[0].title}).first().click();
 await expect(page.locator('[data-message-id="'+answer.id+'"]')).toBeVisible();
 assert.equal(await page.locator('.message.assistant .markdown').innerText(),rendered);
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.screenshot({path:'reports/grounding-live-mobile.png',fullPage:true});
 assert.deepEqual(await api('/settings'),before);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 const report={passed:true,real_model:true,mocked_api:false,project_id:project.id,conversation_id:stored.id,question,answer:answer.content,citations:answer.citations,streamed_text_observed_before_completion:observed,reloaded_equal:true,mode:before.mode,provider:before.provider,model:before.model,external_requests:external,page_errors:errors};
 await writeFile('reports/grounding-live-browser.json',JSON.stringify(report,null,2));
 console.log(JSON.stringify({passed:true,real_model:true,answer:answer.content,stream_observed:observed,citations:answer.citations.length,reloaded_equal:true}));
}finally{await browser.close();}
