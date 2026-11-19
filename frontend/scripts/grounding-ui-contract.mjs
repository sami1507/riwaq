// MOCK UI CONTRACT ONLY. All API requests intercepted; no inference or app-data writes.
import {chromium,expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const origin='http://127.0.0.1:8765';
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:1280,height:900}});
const dialog=page.getByRole('dialog');
const project={id:'ui-p',name:'UI fixture',instructions:''};
const citation={id:'ui-source',scope:'public',filename:'Fixture evidence',title:'Fixture evidence',text:'Synthetic evidence for UI testing.',url:'https://example.org/fixture',version:'test',line_start:1,line_end:1,license:'Test fixture'};
const assistant=(id,content,status='complete',citations=[citation])=>({id,role:'assistant',content,status,citations});
const conversation={id:'ui-c',project_id:project.id,title:'UI contract fixture',messages:[
{id:'question',role:'user',content:'Fixture question',status:'complete'},
assistant('cutoff','Partial answer [S1]','interrupted'),assistant('missing','Answer without a reference'),
assistant('invalid','Answer with invalid reference [S99]'),assistant('valid','Answer with reference [S1]'),
assistant('ordinary','Ordinary answer','complete',[])]};
let settings={mode:'local',provider:'ollama',model:'embedding-only'};
let models=[{name:'embedding-only',chat_capable:false},{name:'qwen3:4b-instruct',chat_capable:true},{name:'alternate-chat',chat_capable:true},{name:'unknown-capabilities'}];
const attempts=[],unexpected=[],external=[],errors=[];
page.on('pageerror',e=>errors.push(e.message));
await page.addInitScript(()=>{localStorage.setItem('riwaq.v1.project','ui-p');localStorage.setItem('riwaq.v1.conversation','ui-c');if(!localStorage.getItem('riwaq.v1.lang'))localStorage.setItem('riwaq.v1.lang','en');});
await page.route('**/*',async route=>{
 const request=route.request(),url=new URL(request.url());
 if(url.origin!==origin){external.push(url.href);return route.abort();}
 if(!url.pathname.startsWith('/api/'))return route.continue();
 const path=url.pathname.slice(4),json=value=>route.fulfill({json:value});
 if(path.endsWith('/generate')||path==='/conversations'&&request.method()==='POST'){
  attempts.push(path);return route.fulfill({status:409,json:{detail:'Synthetic source changed before generation'}});
 }
 if(path==='/settings'&&request.method()==='PATCH'){settings={...settings,...request.postDataJSON()};return json(settings);}
 if(request.method()!=='GET'){unexpected.push(request.method()+' '+path);return route.abort();}
 if(path==='/settings')return json(settings);
 if(path==='/status')return json({version:'ui-test',...settings,ollama:{available:true,models},capabilities:{},providers:{openai:{configured:true,tested:false}}});
 if(path==='/projects')return json([project]);
 if(path==='/conversations')return json([conversation]);
 if(path==='/conversations/ui-c')return json(conversation);
 if(path==='/projects/ui-p/state')return json({events:[]});
 if(/^\/projects\/ui-p\/(files|events|artifacts)$/.test(path))return json([]);
 unexpected.push('GET '+path);return route.fulfill({status:404,json:{detail:'Unmocked route'}});
});
const message=id=>page.locator('[data-message-id="'+id+'"]');
const composer=()=>page.locator('.composer textarea');
const ready=async()=>{await page.locator('.app[aria-busy="false"]').waitFor();await message('cutoff').waitFor();};
const openSettings=async(name='Settings')=>page.getByRole('button',{name,exact:true}).click();
const closeSettings=async()=>dialog.getByRole('button',{name:'Close / إغلاق',exact:true}).click();
async function check(){
 await expect(message('cutoff').locator('.message-status')).toHaveText('Incomplete response');
 for(const id of ['missing','invalid'])await expect(message(id).locator('.knowledge-answer-notice')).toBeVisible();
 for(const id of ['valid','ordinary','cutoff'])await expect(message(id).locator('.knowledge-answer-notice')).toHaveCount(0);
}
try{
 await page.goto(origin);await ready();await check();await page.reload();await ready();await check();
 await composer().fill('Keep this unsent question');
 await expect(page.getByRole('button',{name:'Send',exact:true})).toBeDisabled();
 await composer().press('Enter');await page.waitForTimeout(200);
 await expect(composer()).toHaveValue('Keep this unsent question');assert.equal(attempts.length,0);
 await expect(message('missing').getByRole('button',{name:'Retry',exact:true})).toBeDisabled();
 await openSettings();
 const select=dialog.getByRole('combobox').nth(1);
 assert.deepEqual(await select.locator('option:enabled').evaluateAll(options=>options.map(o=>o.value)),['qwen3:4b-instruct','alternate-chat']);
 await expect(select.locator('option[value="embedding-only"]')).toBeDisabled();
 await dialog.getByRole('button',{name:'Local only',exact:true}).click();
 await expect(select).toHaveValue('qwen3:4b-instruct');
 await closeSettings();
 await composer().press('Enter');
 await expect(page.locator('.composer')).not.toHaveClass(/generating/);
 await expect(composer()).toHaveValue('Keep this unsent question');
 assert.equal(attempts.length,1);
 await page.screenshot({path:'reports/grounding-ui-en.png',fullPage:true});
 await openSettings();await dialog.getByRole('combobox').last().selectOption('ar');await closeSettings();
 await expect(message('cutoff').locator('.message-status')).toHaveText('رد غير مكتمل');
 await expect(message('missing').locator('.knowledge-answer-notice')).toContainText('لم يربط النموذج');
 await page.screenshot({path:'reports/grounding-ui-ar.png',fullPage:true});
 await openSettings('الإعدادات');await dialog.getByRole('combobox').last().selectOption('en');await closeSettings();
 settings={mode:'hybrid',provider:'ollama',model:'alternate-chat'};
 await page.reload();await ready();await openSettings();
 await dialog.getByRole('button',{name:'Local only',exact:true}).click();
 await expect(dialog.getByRole('combobox').nth(1)).toHaveValue('alternate-chat');await closeSettings();
 models=[{name:'embedding-only',chat_capable:false}];
 settings={mode:'hybrid',provider:'openai',model:'cloud-fixture'};
 await page.reload();await ready();await openSettings();
 await dialog.getByRole('button',{name:'Local only',exact:true}).click();
 await expect(dialog.locator('.mode-selector .selected')).toHaveText('Local only');await closeSettings();
 await composer().fill('Still unsent');await expect(page.getByRole('button',{name:'Send',exact:true})).toBeDisabled();
 await composer().press('Enter');await page.waitForTimeout(200);await expect(composer()).toHaveValue('Still unsent');
 assert.equal(attempts.length,1);assert.deepEqual(unexpected,[]);assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
 const report={passed:true,mocked_ui_contract_only:true,real_model_test:false,real_api_mutations:false,checks:['interrupted-after-reload','missing-and-invalid-citation-notice','valid-marker-not-proof-of-support','model-capability-filter','keyboard-and-retry-gated','preflight-draft-restored','privacy-switch-without-model','bilingual']};
 await writeFile('reports/grounding-ui-contract.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser.close();}
