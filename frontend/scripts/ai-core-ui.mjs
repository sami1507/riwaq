// Actual browser -> owned localhost API -> synthetic persisted tasks; no mocks.
import {chromium,expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url)),base=process.env.RIWAQ_URL;
assert.equal(base,'http://127.0.0.1:8771');
assert.equal(path.resolve(process.env.RIWAQ_DATA_DIR??''),path.resolve(root,'reports/ai-core-20261116/ui-service-data'));
const out=path.join(root,'reports/ai-core-20261116/ui',new Date().toISOString().replaceAll(':','-'));await mkdir(out,{recursive:true});
const report={started_at:new Date().toISOString(),backend_mocked:false,synthetic:true,model_calls:0,native_tested:false,cases:[],page_errors:[],external_requests:[]};
let browser,context,page,stage='pairing';
try{
 browser=await chromium.launch({channel:'chrome',headless:true});report.browser=browser.version();context=await browser.newContext({viewport:{width:1440,height:1000}});page=await context.newPage();page.setDefaultTimeout(15000);
 page.on('pageerror',e=>report.page_errors.push(e.message.slice(0,300)));
 await context.route('**/*',async route=>{const u=new URL(route.request().url());if(/^https?:$/.test(u.protocol)&&u.origin!==base){report.external_requests.push(u.origin+u.pathname);await route.abort('blockedbyclient');}else await route.continue();});
 await context.addInitScript(()=>{if(!sessionStorage.getItem('ai-core-ui-init')){localStorage.setItem('riwaq.v1.lang','en');localStorage.setItem('riwaq.v1.theme','light');sessionStorage.setItem('ai-core-ui-init','1');}});
 await page.goto(base);await expect(page.locator('#riwaq-pairing-code')).toBeVisible();
 let pair=await promisify(execFile)(path.join(root,'.venv/Scripts/python.exe'),['-X','utf8','-B','-c','from backend.local_auth import issue_pairing_code; print(issue_pairing_code())'],{cwd:root,env:{...process.env},windowsHide:true,timeout:15000,maxBuffer:4096});
 let code=pair.stdout.trim();pair.stdout='';pair.stderr='';assert(/^[A-Za-z0-9_-]{43}$/.test(code));await page.locator('#riwaq-pairing-code').fill(code);code='';await page.getByRole('button',{name:'Pair browser',exact:true}).click();await expect(page.locator('.composer textarea')).toBeEnabled();
 const api=async(route,data)=>{const res=await context.request.fetch(base+'/api'+route,{method:data?'POST':'GET',headers:{'X-Riwaq-Client':'local'},...(data?{data}:{})});assert(res.ok(),route+' HTTP '+res.status());return res.json();};
 const project=await api('/projects',{name:'AI core UI synthetic acceptance',instructions:'Synthetic only; no model or external service required.'});report.project_id=project.id;
 await page.evaluate(pid=>{localStorage.setItem('riwaq.v1.project',pid);localStorage.removeItem('riwaq.v1.conversation');},project.id);await page.reload();await expect(page.locator('.composer textarea')).toBeEnabled();stage='problem-mode';await page.locator('.answer-mode input[value="problem"]').check();await page.getByLabel('Math problem',{exact:true}).fill('1200 متر + 0.8 كيلومتر');
 const [calculated]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/generate')&&r.request().method()==='POST'),page.getByRole('button',{name:'Calculate',exact:true}).click()]);
 assert.equal(calculated.status(),200);const packets=(await calculated.text()).trim().split('\n').map(JSON.parse);const answer=packets.findLast(row=>row.type==='done').message;
 assert.equal(answer.model,'riwaq-problem-v1');assert.equal(answer.run.model_calls,0);assert.equal(answer.run.tool_calls,1);assert.match(answer.content,/2000/);assert.match(answer.content,/\(1200\)\*1\+\(0\.8\)\*1000/);report.cases.push('problem_mode_exact_units_saved_without_model');
 const conversation=await api('/conversations/'+packets.find(row=>row.type==='start').conversation_id);assert(conversation.messages.some(row=>row.id===answer.id&&row.content===answer.content));
 await page.getByLabel('Math problem',{exact:true}).fill('100 ترتفع20% ثم تنخفض20%');const [ambiguous]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/generate')&&r.request().method()==='POST'),page.getByRole('button',{name:'Calculate',exact:true}).click()]);assert.equal(ambiguous.status(),422);report.cases.push('ambiguous_percentage_problem_rejected');
 await page.locator('.answer-mode input[value="assistant"]').check();await page.getByRole('button',{name:'Riwaq skills',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'Riwaq skills',exact:true}),request=dialog.getByLabel('Request',{exact:true});
 stage='preview';await request.fill('اكتب 200 كلمة بالضبط، بحد أقصى 50 كلمة');await dialog.getByRole('button',{name:'Review request understanding',exact:true}).click();const preview=dialog.getByRole('status',{name:'Request understanding',exact:true});await expect(preview).toContainText('أي القيدين');assert.equal((await api(`/projects/${project.id}/skill-runs`)).length,0);report.cases.push('preview_has_clarification_and_zero_tasks');
 stage='reject';const [rejected]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/skill-runs')&&r.request().method()==='POST'),dialog.getByRole('button',{name:'Run task',exact:true}).click()]);assert.equal(rejected.status(),422);await expect(dialog.locator('.skills-error')).toContainText('أي القيدين');report.cases.push('server_rejects_contradiction_with_readable_error');
 stage='coverage';await request.fill('حلل هذا الجدول، واكتب ملخصًا، وأنشئ رسمًا وملف نتائج');await expect(preview).toHaveCount(0);
 const [created]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/skill-runs')&&r.request().method()==='POST'),dialog.getByRole('button',{name:'Run task',exact:true}).click()]);assert.equal(created.status(),202);let run=await created.json();
 const deadline=performance.now()+20000;while(['queued','running'].includes(run.status)){assert(performance.now()<deadline);await new Promise(r=>setTimeout(r,100));run=await api(`/projects/${project.id}/skill-runs/${run.id}`);}assert.equal(run.status,'partial');assert.equal(Object.keys(run.result.request_coverage.checked_deliverables).length,4);report.run_id=run.id;
 const coverage=dialog.getByRole('region',{name:'Request coverage',exact:true});await expect(coverage).toBeVisible();for(const name of ['Analysis','Summary','Chart','Results file'])await expect(coverage).toContainText(name+': Completion not proven');report.cases.push('four_outputs_retained_partial_in_api_and_ui');
 stage='status';await dialog.getByRole('button',{name:'Continue this task',exact:true}).click();await request.fill('وين وصلنا؟');const [status]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/skill-runs')&&r.request().method()==='POST'),dialog.getByRole('button',{name:'Run task',exact:true}).click()]);assert.equal(status.status(),202);assert.equal((await status.json()).id,run.id);assert.equal((await api(`/projects/${project.id}/skill-runs`)).length,1);report.cases.push('status_question_preserves_original_task_identity');
 await coverage.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'request-coverage-en.png'),fullPage:true});
 stage='arabic-mobile';await page.evaluate(()=>localStorage.setItem('riwaq.v1.lang','ar'));await page.setViewportSize({width:390,height:844});await page.reload();await page.locator('.mobile-menu').click();await page.getByRole('button',{name:'مهارات رواق',exact:true}).click();
 const arDialog=page.getByRole('dialog');await arDialog.getByLabel('سجل المشروع',{exact:true}).selectOption(run.id);await expect(arDialog.getByRole('region',{name:'تغطية الطلب',exact:true})).toContainText('لم يثبت إنجازه');
 await arDialog.getByRole('region',{name:'تغطية الطلب',exact:true}).scrollIntoViewIfNeeded();report.mobile_overflow=await arDialog.evaluate(e=>e.scrollWidth>e.clientWidth+1);assert.equal(report.mobile_overflow,false);await page.screenshot({path:path.join(out,'request-coverage-ar-mobile.png'),fullPage:true});report.cases.push('arabic_mobile_reopens_saved_coverage_without_overflow');
 assert.deepEqual(report.external_requests,[]);assert.deepEqual(report.page_errors,[]);report.passed=true;
}catch(error){report.passed=false;report.failed_stage=stage;report.error=String(error?.stack??error).replace(/[A-Za-z0-9_-]{40,}/g,'[redacted]').slice(0,2500);if(page)await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});process.exitCode=1;}
finally{if(browser)await browser.close();report.finished_at=new Date().toISOString();await writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({passed:report.passed,stage:report.failed_stage,cases:report.cases,report:path.join(out,'report.json'),error:report.error}));}
