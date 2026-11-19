import {chromium,expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {writeFile,mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const base=process.env.RIWAQ_URL||'http://127.0.0.1:8765',out=new URL('../../reports/calculator-ui/',import.meta.url);
await mkdir(out,{recursive:true});
const headers={'X-Riwaq-Client':'local','Content-Type':'application/json'};
async function api(path,init={}){const response=await fetch(base+'/api'+path,{...init,headers});assert(response.ok,`${path}: ${response.status} ${await response.clone().text()}`);return response.json();}
const initialSettings=await api('/settings');
const project=await api('/projects',{method:'POST',body:JSON.stringify({name:'QA explicit calculator '+new Date().toISOString(),instructions:'Synthetic calculator UI test project only.'})});
const browser=await chromium.launch({channel:'chrome',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
const report={project_id:project.id,real_backend:true,model_responses_mocked:false,status_fixture_only:false,cases:[],layouts:[],page_errors:[],external_requests:[]};
const payloads=[];
page.on('pageerror',e=>report.page_errors.push(e.message));
page.on('request',r=>{const url=new URL(r.url());if(/^https?:/.test(url.protocol)&&url.origin!==new URL(base).origin)report.external_requests.push(r.url());if(url.pathname.endsWith('/generate')&&r.method()==='POST')payloads.push(r.postDataJSON());});
await context.addInitScript(pid=>{if(!sessionStorage.getItem('calculatorQAInit')){localStorage.setItem('riwaq.v1.project',pid);localStorage.removeItem('riwaq.v1.conversation');localStorage.setItem('riwaq.v1.lang','en');localStorage.setItem('riwaq.v1.theme','light');sessionStorage.setItem('calculatorQAInit','1');}},project.id);
const mode=value=>page.locator(`.answer-mode input[value="${value}"]`);
const composer=()=>page.locator('.composer textarea');
async function submit(action,label,value,expectedLanguage='en'){
 const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/generate')&&r.request().method()==='POST'),action()]);
 const raw=await response.text();assert.equal(response.status(),200,raw);
 const events=raw.trim().split('\n').map(JSON.parse),start=events.find(e=>e.type==='start'),done=events.findLast(e=>e.type==='done');
 assert(start&&done&&events.some(e=>e.type==='delta'));const message=done.message;
 assert.equal(message.status,'complete');assert.equal(message.model,'riwaq-calculator-v1');assert.equal(message.run.policy_version,'calculator-v1');assert.equal(message.run.model_calls,0);assert.equal(message.run.tool_calls,1);
 assert.equal(message.run.request_options.answer_mode,'calculator');assert.equal(message.run.request_options.result_language,expectedLanguage);
 assert.equal(message.retrieval_options.answer_mode,'calculator');assert.equal(message.retrieval_options.result_language,expectedLanguage);
 assert.equal(message.retrieval_options.use_files,false);assert.equal(message.retrieval_options.use_knowledge,false);assert.equal(message.citations.length,0);
 assert(message.content.includes(`**${value}**`),message.content);
 assert(message.content.includes(expectedLanguage==='ar'?'النتيجة الدقيقة':'Exact result'));
 const body=response.request().postDataJSON();const allowed=['content','answer_mode','result_language','retry_message_id','edit_message_id'];assert(Object.keys(body).every(k=>allowed.includes(k)),JSON.stringify(body));assert.equal(body.answer_mode,'calculator');assert.equal(body.result_language,expectedLanguage);
 const node=page.locator(`[data-message-id="${message.id}"]`);await expect(node).toBeVisible();await expect(node.locator('.calculator-result')).toHaveCount(0);await expect(node.locator('.message-meta strong')).toHaveText(expectedLanguage==='ar'?'حاسبة محلية':'Local calculator');
 const saved=await api('/conversations/'+start.conversation_id);const savedMessage=saved.messages.find(m=>m.id===message.id);assert.equal(savedMessage.content,message.content);assert.equal(savedMessage.run.model_calls,0);const user=saved.messages.findLast(m=>m.role==='user');assert.equal(user.retrieval_options.answer_mode,'calculator');
 await expect(page.locator('.send-button.stop')).toHaveCount(0);
 const result={label,conversation_id:saved.id,message_id:message.id,user_id:user.id,value,language:expectedLanguage,model_calls:0,payload:body};report.cases.push(result);return result;
}
try{
 await page.goto(base);await expect(mode('assistant')).toBeChecked();
 await composer().fill('Keep this chat draft with the numbers 6 * 7.');await expect(mode('assistant')).toBeChecked();
 await page.getByRole('checkbox',{name:'Enable experimental reasoning tools'}).check();
 await page.getByRole('checkbox',{name:'Specified JSON format · advanced'}).check();await page.getByRole('textbox',{name:'JSON schema',exact:true}).fill('{');
 await page.getByRole('button',{name:'Private project files',exact:true}).click();await page.getByRole('button',{name:'Use public knowledge',exact:true}).click();
 await mode('calculator').check();await expect(page.locator('.reasoning-controls')).toHaveCount(0);await expect(page.locator('.model-selector')).toHaveCount(0);await expect(page.locator('.composer-knowledge-summary')).toHaveCount(0);
 await composer().fill('(19.95 * 3) * 1.17 - 10');
 await mode('assistant').check();await expect(composer()).toHaveValue('Keep this chat draft with the numbers 6 * 7.');await expect(page.getByRole('textbox',{name:'JSON schema',exact:true})).toHaveValue('{');
 await mode('calculator').check();await expect(composer()).toHaveValue('(19.95 * 3) * 1.17 - 10');
 const first=await submit(()=>page.getByRole('button',{name:'Calculate',exact:true}).click(),'decimal_exact','60.0245');await expect(composer()).toHaveValue('');
 await mode('assistant').check();await expect(composer()).toHaveValue('Keep this chat draft with the numbers 6 * 7.');await expect(page.getByRole('textbox',{name:'JSON schema',exact:true})).toHaveValue('{');
 const retry=await submit(()=>page.locator(`[data-message-id="${first.message_id}"]`).getByRole('button',{name:'Retry',exact:true}).click(),'retry_from_chat_with_invalid_schema','60.0245');assert.notEqual(retry.conversation_id,first.conversation_id);
 await expect(composer()).toHaveValue('Keep this chat draft with the numbers 6 * 7.');await expect(page.getByRole('textbox',{name:'JSON schema',exact:true})).toHaveValue('{');
 await page.locator(`[data-message-id="${retry.user_id}"]`).getByRole('button',{name:'Edit & branch',exact:true}).click();
 await page.getByRole('dialog').getByLabel('Edit arithmetic expression',{exact:true}).fill('(٢٠ + ٢٢)');
 const edited=await submit(()=>page.getByRole('dialog').getByRole('button',{name:'Create branch',exact:true}).click(),'edit_preserves_calculator','42');assert.notEqual(edited.conversation_id,retry.conversation_id);
 await page.reload();await expect(page.locator(`[data-message-id="${edited.message_id}"] .markdown`)).toContainText('Exact result: 42');await expect(page.locator(`[data-message-id="${edited.message_id}"] .calculator-result`)).toHaveCount(0);
 report.restored=true;
 await mode('calculator').check();
 for(const text of ['1 / 0','Please compute 6 * 7','1 m + 2 m']){
  const before=await api('/conversations/'+edited.conversation_id);await composer().fill(text);
  const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/generate')&&r.request().method()==='POST'),page.getByRole('button',{name:'Calculate',exact:true}).click()]);
  assert.equal(response.status(),422,await response.text());await expect(page.locator('.send-button.stop')).toHaveCount(0);await expect(composer()).toHaveValue(text);const after=await api('/conversations/'+edited.conversation_id);assert.equal(after.messages.length,before.messages.length);report.cases.push({label:'rejected_expression',expression:text,status:422,draft_retained:true,messages_unchanged:true});
 }
 await composer().fill('0.1 + 0.2');await submit(()=>composer().press('Enter'),'enter_decimal','0.3');
 await composer().fill('1 / 3');await submit(()=>page.getByRole('button',{name:'Calculate',exact:true}).click(),'exact_fraction','1/3');
 await page.evaluate(()=>{localStorage.setItem('riwaq.v1.lang','ar');localStorage.setItem('riwaq.v1.theme','dark');});await page.reload();await mode('calculator').check();
 await composer().fill('(١٩٫٩٥ * ٣) * ١٫١٧ - ١٠');await submit(()=>page.getByRole('button',{name:'احسب',exact:true}).click(),'arabic_digits_and_result','60.0245','ar');
 // Only the status indicator is controlled below. All arithmetic requests still reach the real backend.
 await page.route('**/api/status',async route=>{const actual=await route.fetch(),data=await actual.json();data.ollama={...data.ollama,available:false,models:[]};for(const provider of Object.values(data.providers??{}))provider.configured=false;await route.fulfill({response:actual,json:data});});report.status_fixture_only=true;
 await page.reload();await composer().fill('6 * 7');await expect(mode('assistant')).toBeChecked();await expect(page.getByRole('button',{name:'إرسال',exact:true})).toBeDisabled();
 await mode('calculator').check();await composer().fill('3 * 14');const unavailable=await submit(()=>composer().press('Enter'),'unavailable_model_ui_real_calculation','42','ar');
 await mode('assistant').check();await expect(composer()).toHaveValue('6 * 7');await submit(()=>page.locator(`[data-message-id="${unavailable.message_id}"]`).getByRole('button',{name:'إعادة المحاولة',exact:true}).click(),'unavailable_model_retry','42','ar');
 for(const width of [360,390,768,1280,1440])for(const theme of ['light','dark']){
  const lang=width<1000?'ar':'en';await page.setViewportSize({width,height:900});await page.evaluate(({lang,theme})=>{localStorage.setItem('riwaq.v1.lang',lang);localStorage.setItem('riwaq.v1.theme',theme);},{lang,theme});await page.reload();await mode('calculator').check();
  const layout=await page.evaluate(()=>{const nodes=['.answer-mode','.composer textarea','.send-button'].map(selector=>{const e=document.querySelector(selector),r=e.getBoundingClientRect();return {selector,left:r.left,right:r.right,bottom:r.bottom,top:r.top,client:e.clientWidth,scroll:e.scrollWidth};});return {overflow:document.documentElement.scrollWidth>innerWidth,conversation_height:document.querySelector('.conversation-scroll').clientHeight,nodes,viewport:innerWidth,height:innerHeight};});
  assert(!layout.overflow);assert(layout.conversation_height>120);assert(layout.nodes.every(r=>r.left>=0&&r.right<=width&&r.top>=0&&r.bottom<=900&&r.scroll<=r.client+1),JSON.stringify(layout));report.layouts.push({width,theme,lang,...layout});
  if(width===390||width===1440)await page.screenshot({path:fileURLToPath(new URL(`${width}-${lang}-${theme}.png`,out)),fullPage:true,animations:'disabled'});
 }
 assert.deepEqual(await api('/settings'),initialSettings);assert.equal(report.page_errors.length,0);assert.equal(report.external_requests.length,0);report.request_count=payloads.length;report.passed=true;
}catch(error){report.error=String(error);await page.screenshot({path:fileURLToPath(new URL('failure.png',out)),fullPage:true}).catch(()=>{});throw error;}
finally{await writeFile(new URL('report.json',out),JSON.stringify(report,null,2));await browser.close();}
console.log(JSON.stringify({passed:report.passed,cases:report.cases.length,layouts:report.layouts.length,status_fixture_only:report.status_fixture_only,model_responses_mocked:false}));
