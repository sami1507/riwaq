// Actual browser -> authenticated local API -> limited worker -> saved message.
import {chromium,expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url)),base=process.env.RIWAQ_URL;
assert.equal(base,'http://127.0.0.1:8773');
assert.equal(path.resolve(process.env.RIWAQ_DATA_DIR??''),path.resolve(root,'reports/math-reasoning-20261116/ui-service-data'));
const out=path.join(root,'reports/math-reasoning-20261116/ui',new Date().toISOString().replaceAll(':','-'));
await mkdir(out,{recursive:true});
const report={started_at:new Date().toISOString(),synthetic:true,backend_mocked:false,transport_mocked:false,native_tested:false,cases:[],messages:[],page_errors:[],external_requests:[]};
let browser,context,page,stage='pairing';
try{
 browser=await chromium.launch({channel:'chrome',headless:true});report.browser=browser.version();
 context=await browser.newContext({viewport:{width:1440,height:1000}});page=await context.newPage();page.setDefaultTimeout(20000);
 page.on('pageerror',e=>report.page_errors.push(e.message.slice(0,300)));
 await context.route('**/*',async route=>{const u=new URL(route.request().url());if(/^https?:$/.test(u.protocol)&&u.origin!==base){report.external_requests.push(u.origin+u.pathname);await route.abort('blockedbyclient');}else await route.continue();});
 await context.addInitScript(()=>{if(!sessionStorage.getItem('math-ui-init')){localStorage.setItem('riwaq.v1.lang','en');localStorage.setItem('riwaq.v1.theme','light');sessionStorage.setItem('math-ui-init','1');}});
 await page.goto(base);await expect(page.locator('#riwaq-pairing-code')).toBeVisible();
 const pairing=await promisify(execFile)(path.join(root,'.venv/Scripts/python.exe'),['-X','utf8','-B','-c','from backend.local_auth import issue_pairing_code; print(issue_pairing_code())'],{cwd:root,env:{...process.env},windowsHide:true,timeout:15000,maxBuffer:4096});
 let code=pairing.stdout.trim();pairing.stdout='';pairing.stderr='';assert(/^[A-Za-z0-9_-]{43}$/.test(code));await page.locator('#riwaq-pairing-code').fill(code);code='';
 await page.getByRole('button',{name:'Pair browser',exact:true}).click();await expect(page.locator('.composer textarea')).toBeEnabled();
 const api=async(route,data)=>{const response=await context.request.fetch(base+'/api'+route,{method:data?'POST':'GET',headers:{'X-Riwaq-Client':'local'},...(data?{data}:{})});assert(response.ok(),route+': '+response.status());return response.json();};
 const project=await api('/projects',{name:'Math acceptance synthetic'});report.project_id=project.id;
 await page.evaluate(pid=>{localStorage.setItem('riwaq.v1.project',pid);localStorage.removeItem('riwaq.v1.conversation');},project.id);await page.reload();
 await page.locator('input[name="riwaq-answer-mode"][value="problem"]').check();
 const input=page.locator('.composer textarea');let buttonLabel='Calculate';
 async function submit(text){
  await input.fill(text);
  const [response]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/generate')&&r.request().method()==='POST'),page.getByRole('button',{name:buttonLabel,exact:true}).click()]);
  assert.equal(response.status(),200,await response.text());
  const events=(await response.text()).trim().split('\n').map(JSON.parse),done=events.at(-1);assert.equal(done.type,'done');
  const message=done.message;assert.equal(message.status,'complete');assert.equal(message.run.model_calls,0);assert.equal(message.run.tool_calls,1);
  await expect(page.locator(`[data-message-id="${message.id}"]`)).toBeVisible();await expect(input).toBeEnabled();report.messages.push(message);
  const check=message.run.checks.find(c=>['solve_math','solve_problem'].includes(c.kind));assert(check);return {message,data:JSON.parse(check.details)};
 }
 stage='examples';await page.locator('.math-input-help summary').click();await page.getByRole('button',{name:'Constrained equation',exact:true}).click();
 assert.equal(await input.inputValue(),'solve x^2=4; where: x<0');
 let result=await submit(await input.inputValue());assert.deepEqual(result.data.result.solutions,['-2']);report.cases.push('equation_example_constrained_original_verified');
 stage='domain';result=await submit('حل (x^2-1)/(x-1)=2 على الأعداد الحقيقية.');assert.equal(result.data.problem_status,'no_solution');report.cases.push('original_arabic_denominator_exclusion');
 stage='precision';result=await submit('احسب 9007199254740993 + 1 على الأعداد الصحيحة.');assert.equal(result.data.result.value,'9007199254740994');assert(result.message.content.includes('9007199254740994'));report.cases.push('large_integer_preserved_in_browser');
 stage='logic';result=await submit('هل تلزم P من P->Q وQ؟');assert.equal(result.data.result.entails,false);report.cases.push('countermodel_saved_without_model_call');
 stage='root';result=await submit('root cos(x)-x; bounds: 0,1; tolerance: 1/1000000000000');assert.equal(result.data.result.unique,true);assert(Math.abs(Number(result.data.result.value)-0.7390851332151607)<=1e-12);report.cases.push('numerical_root_error_bound_visible');await page.locator('.math-input-help summary').click();await page.locator(`[data-message-id="${result.message.id}"] pre`).first().scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'root-en-desktop.png'),fullPage:true});
 stage='ambiguity';result=await submit('1/2x');assert.equal(result.data.problem_status,'ambiguous');assert.equal(result.data.result.readings.length,2);report.cases.push('ambiguous_input_requires_grouping');
 const cid=await page.evaluate(()=>localStorage.getItem('riwaq.v1.conversation'));assert(cid);report.conversation_id=cid;
 const before=await api('/conversations/'+cid);assert(before.messages.length>=12);
 stage='mobile-reopen';await page.evaluate(()=>localStorage.setItem('riwaq.v1.lang','ar'));await page.setViewportSize({width:390,height:844});await page.reload();
 await expect(page.locator(`[data-message-id="${result.message.id}"]`)).toBeVisible();await page.locator(`[data-message-id="${result.message.id}"] pre`).first().scrollIntoViewIfNeeded();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(out,'ambiguity-ar-mobile.png'),fullPage:true});
 const after=await api('/conversations/'+cid);assert.deepEqual(after.messages,before.messages);report.cases.push('arabic_mobile_reopen_same_saved_results_no_overflow');
 stage='arabic-result';await page.locator('input[name="riwaq-answer-mode"][value="problem"]').check();buttonLabel='احسب';result=await submit('حل ٢س + ٣ = ١١ على الأعداد الحقيقية.');assert.deepEqual(result.data.result.solutions,['4']);assert(result.message.content.includes('الأعداد الحقيقية'));await page.locator(`[data-message-id="${result.message.id}"] pre`).first().scrollIntoViewIfNeeded();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(out,'equation-ar-mobile.png'),fullPage:true});report.cases.push('arabic_result_and_ltr_equation_mobile');report.result_languages=['en','ar'];
 assert.deepEqual(report.page_errors,[]);assert.deepEqual(report.external_requests,[]);report.passed=true;
}catch(error){report.passed=false;report.failed_stage=stage;report.error=String(error?.stack??error).slice(0,1800);if(page)await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});process.exitCode=1;}
finally{await browser?.close();report.finished_at=new Date().toISOString();await writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({out,passed:report.passed,stage:report.failed_stage,cases:report.cases,error:report.error}));}
