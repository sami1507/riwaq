// Actual browser -> authenticated local API -> fixed worker -> SQLite feedback.
// Only the final storage-transport failure is deliberately simulated and labelled.
import {chromium,expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url)),base=process.env.RIWAQ_URL;
assert.equal(base,'http://127.0.0.1:8774');
assert.equal(path.resolve(process.env.RIWAQ_DATA_DIR??''),path.resolve(root,'reports/learning-20261116/ui-service-data'));
const out=path.join(root,'reports/learning-20261116/ui',new Date().toISOString().replaceAll(':','-'));
await mkdir(out,{recursive:true});
const report={started_at:new Date().toISOString(),synthetic:true,backend_mocked:false,native_tested:false,
  training_started:false,transport_failure_mocked:true,cases:[],page_errors:[],external_requests:[]};
let browser,context,page,stage='pairing';
try{
 browser=await chromium.launch({channel:'chrome',headless:true});report.browser=browser.version();
 context=await browser.newContext({viewport:{width:1440,height:1000}});page=await context.newPage();page.setDefaultTimeout(20000);
 page.on('pageerror',e=>report.page_errors.push(e.message.slice(0,300)));
 await context.route('**/*',async route=>{const u=new URL(route.request().url());if(/^https?:$/.test(u.protocol)&&u.origin!==base){report.external_requests.push(u.origin+u.pathname);await route.abort('blockedbyclient');}else await route.continue();});
 await context.addInitScript(()=>{if(!sessionStorage.getItem('learning-ui-init')){localStorage.setItem('riwaq.v1.lang','en');localStorage.setItem('riwaq.v1.theme','light');sessionStorage.setItem('learning-ui-init','1');}});
 await page.goto(base);await expect(page.locator('#riwaq-pairing-code')).toBeVisible();
 const pairing=await promisify(execFile)(path.join(root,'.venv/Scripts/python.exe'),['-X','utf8','-B','-c','from backend.local_auth import issue_pairing_code; print(issue_pairing_code())'],{cwd:root,env:{...process.env},windowsHide:true,timeout:15000,maxBuffer:4096});
 let code=pairing.stdout.trim();pairing.stdout='';pairing.stderr='';assert(/^[A-Za-z0-9_-]{43}$/.test(code));await page.locator('#riwaq-pairing-code').fill(code);code='';
 await page.getByRole('button',{name:'Pair browser',exact:true}).click();await expect(page.locator('.composer textarea')).toBeEnabled();
 const api=async(route,data)=>{const response=await context.request.fetch(base+'/api'+route,{method:data?'POST':'GET',headers:{'X-Riwaq-Client':'local'},...(data?{data}:{})});assert(response.ok(),route+': '+response.status());return response.json();};
 const project=await api('/projects',{name:'Learning acceptance synthetic'});report.project_id=project.id;
 const learning=`/projects/${project.id}/learning`;
 await page.evaluate(pid=>{localStorage.setItem('riwaq.v1.project',pid);localStorage.removeItem('riwaq.v1.conversation');},project.id);await page.reload();
 stage='actual-math';await page.locator('input[name="riwaq-answer-mode"][value="problem"]').check();await page.locator('.composer textarea').fill('2+2');
 const [mathResponse]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/generate')&&r.request().method()==='POST'),page.getByRole('button',{name:'Calculate',exact:true}).click()]);
 assert.equal(mathResponse.status(),200);const events=(await mathResponse.text()).trim().split('\n').map(JSON.parse),message=events.at(-1).message;
 assert.equal(message.status,'complete');assert.equal(message.run.model_calls,0);assert.equal(message.run.tool_calls,1);
 const mid=message.id;report.message_id=mid;report.original_content=message.content;
 const cid=await page.evaluate(()=>localStorage.getItem('riwaq.v1.conversation'));report.conversation_id=cid;
 await expect(page.locator(`[data-message-id="${mid}"]`)).toBeVisible();
 await page.locator(`[data-message-id="${mid}"]`).getByRole('button',{name:'Not helpful or correct',exact:true}).click();
 const dialog=page.getByRole('dialog'),panel=dialog.locator('.learning-panel');
 await expect(dialog.getByRole('heading',{name:'Feedback and lessons',exact:true})).toBeVisible();
 async function mutation(suffix,method,button){
  const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname.endsWith(suffix)&&r.request().method()===method),button.click()]);
  assert(response.ok(),suffix+' '+response.status()+': '+await response.text());return response.json();
 }
 const save=()=>mutation('/learning/feedback','POST',dialog.getByRole('button',{name:'Save feedback',exact:true}));
 stage='negative-vote';const vote=await save();assert.equal(vote.status,'recorded');assert.equal(vote.reason,'');assert.deepEqual(vote.correction,{});
 await expect(panel.locator(`[data-feedback-id="${vote.id}"]`)).toContainText('Saved; not checked');report.cases.push('negative_vote_saved_without_invented_correction');
 stage='unreviewed-example';await dialog.getByLabel('Feedback type',{exact:false}).selectOption('example');await dialog.getByLabel('Reason or preference',{exact:false}).fill('Example claim for review: 2+2=5.');
 const example=await save();assert.equal(example.signal,'example');assert.equal(example.status,'recorded');assert.deepEqual(example.correction,{});
 const examplePanel=panel.locator(`[data-feedback-id="${example.id}"]`);await expect(examplePanel).toContainText('Saved; not checked');await expect(examplePanel.getByRole('button',{name:'Verify correction',exact:true})).toHaveCount(0);await expect(examplePanel.getByRole('button',{name:'Save candidate',exact:true})).toBeDisabled();
 const persistedExample=await api(learning+'/feedback/'+example.id);assert.equal(persistedExample.status,'recorded');assert.deepEqual(persistedExample.correction,{});assert.deepEqual(persistedExample.verification,{});assert.equal((await api(learning+'/candidates')).length,0);
 await expect(dialog.getByText('Example saved without review. Evidence and an appropriate check are required before treating it as a correction or creating a lesson.',{exact:true})).toBeVisible();await dialog.getByText('Example saved without review. Evidence and an appropriate check are required before treating it as a correction or creating a lesson.',{exact:true}).scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'unreviewed-example-en.png'),fullPage:true});report.cases.push('submitted_example_stays_unreviewed_without_preference_verification_or_lesson');
 stage='wrong-correction';await dialog.getByLabel('Feedback type',{exact:false}).selectOption('correction');await dialog.getByLabel('Reason or preference',{exact:false}).fill('');await dialog.getByLabel('Proposed correction',{exact:true}).fill('5');
 const wrong=await save();await expect(panel.locator(`[data-feedback-id="${wrong.id}"]`)).toBeVisible();
 const disputed=await mutation(`/feedback/${wrong.id}/verify`,'POST',dialog.getByRole('button',{name:'Verify correction',exact:true}));
 assert.equal(disputed.status,'disputed');assert.equal(disputed.verification.corrected_output.value,'4');assert.equal(disputed.verification.worker_calls,1);
 await expect(panel.locator('.learning-answer')).toHaveText('Verified result: 4');await page.screenshot({path:path.join(out,'wrong-correction-en.png'),fullPage:true});report.cases.push('wrong_2_plus_2_assertion_disputed_actual_worker_retains_4');
 stage='preference';await dialog.getByLabel('Feedback type',{exact:false}).selectOption('preference');await dialog.getByLabel('Reason or preference',{exact:false}).fill('Use a formal tone for this conversation only.');
 const preference=await save();assert.deepEqual(preference.purposes,['current_task']);
 const checked=await mutation(`/feedback/${preference.id}/verify`,'POST',dialog.getByRole('button',{name:'Verify correction',exact:true}));assert.equal(checked.status,'verified');assert.equal(checked.verification.objective_verified,false);
 await dialog.getByLabel('Rule',{exact:false}).selectOption('tone');await dialog.getByLabel('Value',{exact:false}).selectOption('formal');
 const candidate=await mutation(`/feedback/${preference.id}/candidates`,'POST',dialog.getByRole('button',{name:'Save candidate',exact:true}));
 assert.equal(candidate.scope,'conversation');assert.equal(candidate.conversation_id,cid);
 const candidateRow=panel.locator(`[data-candidate-id="${candidate.id}"]`);
 await expect(candidateRow).toBeVisible();
 stage='evaluation';await mutation(`/candidates/${candidate.id}/evaluate`,'POST',candidateRow.getByRole('button',{name:'Evaluate candidate',exact:true}));await expect(candidateRow).toContainText('Evaluated; not adopted');
 stage='candidate-edit';await candidateRow.getByRole('button',{name:'Edit candidate',exact:true}).click();await candidateRow.getByLabel('Value',{exact:false}).selectOption('friendly');
 const edited=await mutation(`/candidates/${candidate.id}`,'PUT',candidateRow.getByRole('button',{name:'Save changes',exact:true}));assert.equal(edited.version,2);assert.equal(edited.status,'proposed');
 await expect(candidateRow.getByRole('button',{name:'Adopt in scope',exact:true})).toBeDisabled();report.cases.push('candidate_edit_preserves_scope_invalidates_prior_evaluation');
 stage='reevaluate-adopt';await mutation(`/candidates/${candidate.id}/evaluate`,'POST',candidateRow.getByRole('button',{name:'Evaluate candidate',exact:true}));
 await expect(candidateRow.getByRole('button',{name:'Adopt in scope',exact:true})).toBeEnabled();
 const released=await mutation(`/candidates/${candidate.id}/activate`,'POST',candidateRow.getByRole('button',{name:'Adopt in scope',exact:true}));assert.equal(released.status,'active');
 await expect(candidateRow).toContainText('Active in scope');report.cases.push('scoped_preference_checked_evaluated_and_adopted_actual_api_sqlite');
 report.candidate_id=candidate.id;report.release_id=released.id;
 await page.screenshot({path:path.join(out,'active-en-desktop.png'),fullPage:true});
 stage='reopen-mobile';await dialog.getByRole('button',{name:'Close / إغلاق',exact:true}).click();await page.evaluate(()=>localStorage.setItem('riwaq.v1.lang','ar'));await page.setViewportSize({width:390,height:844});await page.reload();
 await expect(page.locator(`[data-message-id="${mid}"]`)).toBeVisible();
 await page.locator(`[data-message-id="${mid}"]`).getByRole('button',{name:'غير مفيد أو تصحيح',exact:true}).click();
 await expect(panel.locator(`[data-candidate-id="${candidate.id}"]`)).toContainText('معتمد في نطاقه');
 assert.equal(await panel.getAttribute('dir'),'rtl');
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
 assert.equal(await panel.evaluate(e=>e.scrollWidth>e.clientWidth+1),false);
 await panel.locator(`[data-candidate-id="${candidate.id}"]`).scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'active-ar-mobile.png'),fullPage:true});
 const saved=await api('/conversations/'+cid);assert.equal(saved.messages.find(m=>m.id===mid).content,message.content);report.cases.push('reopen_rtl_mobile_current_state_no_overflow_original_preserved');
 stage='rollback';const rolled=await mutation(`/releases/${released.id}/rollback`,'POST',panel.locator(`[data-candidate-id="${candidate.id}"]`).getByRole('button',{name:'تراجع عن التحسين',exact:true}));assert.equal(rolled.status,'rolled_back');
 await expect(panel.locator(`[data-candidate-id="${candidate.id}"]`)).toContainText('أُلغي التحسين');assert.equal((await api(learning+'/releases')).some(r=>r.id===released.id&&r.status==='active'),false);report.cases.push('rollback_ui_matches_persisted_release');
 stage='simulated-storage-transport-failure';
 const before=await api(learning+'/feedback');
 await page.route('**/learning/feedback',async route=>{if(route.request().method()==='POST')await route.abort('failed');else await route.continue();});
 await dialog.getByRole('button',{name:'احفظ الملاحظة',exact:true}).click();await expect(dialog.getByRole('alert')).toBeVisible();
 assert.equal((await api(learning+'/feedback')).length,before.length);await expect(dialog.getByText('حُفظت الملاحظة على هذا الرد؛ لم تُعتمد قاعدة أو يُدرّب نموذج.',{exact:true})).toHaveCount(0);
 await page.screenshot({path:path.join(out,'simulated-storage-failure-ar-mobile.png'),fullPage:true});await page.unroute('**/learning/feedback');report.cases.push('simulated_transport_abort_shows_failure_no_false_saved_feedback');
 assert.deepEqual(report.page_errors,[]);assert.deepEqual(report.external_requests,[]);report.passed=true;
}catch(error){report.passed=false;report.failed_stage=stage;report.error=String(error?.stack??error).slice(0,2000);if(page)await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});process.exitCode=1;}
finally{await browser?.close();report.finished_at=new Date().toISOString();await writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({out,passed:report.passed,stage:report.failed_stage,cases:report.cases,error:report.error}));}
