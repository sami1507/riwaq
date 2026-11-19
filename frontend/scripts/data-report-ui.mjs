// Real Chrome → local API → pinned data-v4 Docker → saved/downloaded files.
// The only API-only preparation is a synthetic source workbook; report creation
// and correction are driven through the same controls the user sees.
import {chromium,expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url)),base=process.env.RIWAQ_URL;
assert.equal(base,'http://127.0.0.1:8776');
assert.equal(path.resolve(process.env.RIWAQ_DATA_DIR??''),path.resolve(root,'reports/closure-20261116/data-revision/ui-service-data'));
const out=path.join(root,'reports/closure-20261116/data-revision/ui',new Date().toISOString().replaceAll(':','-'));
await mkdir(out,{recursive:true});
const report={started_at:new Date().toISOString(),synthetic:true,api_mocked:false,worker_mocked:false,native_office_rendered:false,cases:[],page_errors:[]};
let browser,page,stage='pairing';
try{
 browser=await chromium.launch({channel:'chrome',headless:true});report.browser=browser.version();
 const context=await browser.newContext({viewport:{width:1440,height:1000}});page=await context.newPage();page.setDefaultTimeout(30000);
 page.on('pageerror',e=>report.page_errors.push(e.message.slice(0,300)));
 await context.route('**/*',async route=>{const url=new URL(route.request().url());if(/^https?:$/.test(url.protocol)&&url.origin!==base)await route.abort();else await route.continue();});
 await context.addInitScript(()=>{if(!sessionStorage.getItem('data-report-init')){localStorage.setItem('riwaq.v1.lang','en');sessionStorage.setItem('data-report-init','1');}});
 await page.goto(base);await expect(page.locator('#riwaq-pairing-code')).toBeVisible();
 const pairing=await promisify(execFile)(path.join(root,'.venv/Scripts/python.exe'),['-X','utf8','-B','-c','from backend.local_auth import issue_pairing_code; print(issue_pairing_code())'],{cwd:root,env:{...process.env},windowsHide:true,timeout:15000});
 let code=pairing.stdout.trim();pairing.stdout='';pairing.stderr='';await page.locator('#riwaq-pairing-code').fill(code);code='';await page.getByRole('button',{name:'Pair browser',exact:true}).click();await expect(page.locator('.composer textarea')).toBeEnabled();
 const api=async(route,data)=>{const response=await context.request.fetch(base+'/api'+route,{method:data?'POST':'GET',headers:{'X-Riwaq-Client':'local'},...(data?{data}:{})});assert(response.ok(),route+': '+response.status()+' '+await response.text());return response.json();};
 const project=await api('/projects',{name:'Data report acceptance synthetic'});report.project_id=project.id;
 const prefix=`/projects/${project.id}`;
 async function completed(id,status='completed'){
  let value;const deadline=Date.now()+100000;
  do{value=await api(prefix+'/skill-runs/'+id);if(!['queued','running','cancelling'].includes(value.status))break;await new Promise(r=>setTimeout(r,150));}while(Date.now()<deadline);
  assert.equal(value.status,status,JSON.stringify({status:value.status,error:value.error}));return value;
 }
 stage='source-fixture';
 const source=await completed((await api(prefix+'/skill-runs',{request:'Create the specified source workbook',requestedSkills:['spreadsheet-reading'],idempotencyKey:'ui-source-'+Date.now(),inputs:{dataOperation:{operation:'read',format:'xlsx',table:{columns:['order','amount'],rows:[['A',0],['B',10],['C',20],['D',null]]}}}})).id);
 const sourceId=source.result.artifacts[0].artifact_id;
 await page.evaluate(pid=>{localStorage.setItem('riwaq.v1.project',pid);localStorage.removeItem('riwaq.v1.conversation');},project.id);await page.reload();
 await page.getByRole('button',{name:'Run a skill',exact:true}).click();
 const dialog=page.getByRole('dialog');const panel=dialog.getByTestId('data-report-panel');
 await panel.locator('summary').click();
 await panel.getByLabel('Report source',{exact:true}).selectOption(sourceId);
 await panel.getByLabel('Data report title',{exact:true}).fill('Actual linked report');
 await panel.getByLabel('Category column',{exact:true}).fill('order');
 await panel.getByLabel('Numeric value column',{exact:true}).fill('amount');
 await panel.getByLabel('Data report language',{exact:true}).selectOption('ar');
 await panel.getByRole('button',{name:'Create linked report',exact:true}).click();
 await expect(panel.getByRole('alert')).toHaveText('Error: Specify the chart value unit. No tool has started and no unit was assumed.');
 await panel.getByLabel('Unit',{exact:true}).fill('value units');
 report.cases.push('missing_required_unit_reported_before_tool_without_invented_unit');
 async function submit(button){const [response]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/skill-runs')&&r.request().method()==='POST'),button.click()]);assert.equal(response.status(),202,await response.text());return (await response.json()).id;}
 stage='create-linked-report';
 const first=await completed(await submit(panel.getByRole('button',{name:'Create linked report',exact:true})));
 assert.equal(first.result.structured['exploratory-analysis'].output.summary.statistics.amount.mean,'10');
 assert.equal(first.result.artifacts.length,3);report.initial_run_id=first.id;
 await expect(panel.getByRole('heading',{name:'Correct selected report',exact:true})).toBeVisible();
 await panel.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'created-report-desktop.png'),fullPage:true});
 report.cases.push('UI_created_analysis_table_chart_workbook_word_preserving_zero_missing');
 stage='correct-linked-report';
 await panel.getByLabel('Cell to correct',{exact:true}).selectOption('B4');
 await expect(panel.locator('output')).toHaveText('20');
 await panel.getByLabel('Corrected value',{exact:true}).fill('30');
 await panel.getByLabel('Link correction to feedback for this task only',{exact:true}).check();
 const corrected=await completed(await submit(panel.getByRole('button',{name:'Correct and rebuild outputs',exact:true})));
 report.corrected_run_id=corrected.id;
 const stats=corrected.result.structured['exploratory-analysis'].output.summary.statistics.amount;
 assert.equal(stats.sum,'40');assert.equal(stats.mean,'13.33333333333333333333333333333333');assert.equal(stats.numeric_count,3);assert.equal(stats.missing,1);
 assert.deepEqual(corrected.result.artifacts.map(a=>a.artifact_id).sort(),first.result.artifacts.map(a=>a.artifact_id).sort());
 assert(corrected.result.artifacts.every(a=>a.version===2));
 const feedback=await api(prefix+'/learning/feedback/'+corrected.result.data_revision.feedback_result.id);
 assert.equal(feedback.status,'verified');assert.equal(feedback.verification.corrected_output.run_id,corrected.id);assert.equal(feedback.verification.objective_error_proven,false);
 await expect(panel.locator('fieldset')).toBeEnabled();
 for(const artifact of corrected.result.artifacts){
  const [download]=await Promise.all([page.waitForEvent('download'),dialog.getByRole('button',{name:artifact.name,exact:true}).click()]);
  const file=path.join(out,artifact.name);await download.saveAs(file);
  assert.equal(createHash('sha256').update(await readFile(file)).digest('hex'),artifact.sha256);
 }
 report.cases.push('UI_cell_correction_recomputed_all_three_saved_versions_and_feedback');
 stage='invalid-value-rejected';
 await panel.getByLabel('Cell to correct',{exact:true}).selectOption('B4');
 await panel.getByLabel('New value type',{exact:true}).selectOption('text');
 await panel.getByLabel('Corrected value',{exact:true}).fill('abc');
 const failed=await completed(await submit(panel.getByRole('button',{name:'Correct and rebuild outputs',exact:true})),'failed');
 assert.equal(failed.result.artifacts.length,0);
 await expect(dialog.locator('.skills-run .skills-error').first()).toBeVisible();
 const files=await api(prefix+'/skill-files');assert(corrected.result.artifacts.every(a=>files.find(f=>f.id===a.artifact_id)?.version===2));
 report.cases.push('invalid_numeric_correction_failed_without_advancing_any_output_version');
 stage='reopen-rtl-mobile';
 await dialog.getByRole('button',{name:'Close / إغلاق',exact:true}).click();
 await page.evaluate(()=>localStorage.setItem('riwaq.v1.lang','ar'));await page.setViewportSize({width:390,height:844});await page.reload();
 await page.getByRole('button',{name:'تنفيذ مهارة',exact:true}).click();
 await dialog.getByLabel('سجل المشروع',{exact:true}).selectOption(corrected.id);
 await panel.locator('summary').click();
 await expect(panel.getByRole('heading',{name:'تصحيح التقرير المحدد',exact:true})).toBeVisible();
 await panel.getByLabel('الخلية المراد تصحيحها',{exact:true}).selectOption('B4');await expect(panel.locator('output')).toHaveText('30');
 assert.equal(await dialog.locator('.skills-content').getAttribute('dir'),'rtl');
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
 await panel.getByRole('heading',{name:'تصحيح التقرير المحدد',exact:true}).scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'corrected-report-rtl-mobile.png'),fullPage:true});
 report.cases.push('reopened_corrected_version_rtl_mobile_no_page_overflow');
 stage='personal-memory-gate-and-legacy-deletion';
 await dialog.getByRole('button',{name:'Close / إغلاق',exact:true}).click();
 const legacyId='closure-ui-legacy-'+Date.now();
 await promisify(execFile)(path.join(root,'.venv/Scripts/python.exe'),['-X','utf8','-B','-c',
  'import sys; from backend.db import get_db, now; c=get_db(); db=c.__enter__(); db.execute("INSERT INTO memories VALUES(?,?,NULL,?,?)",(sys.argv[1],"preference","SYNTHETIC legacy UI deletion fixture",now())); c.__exit__(None,None,None)',legacyId],
  {cwd:root,env:{...process.env},windowsHide:true,timeout:15000});
 const priorEntries=await api('/personal-memory');assert(priorEntries.some(e=>e.id===legacyId&&e.status==='legacy_read_only'));
 await page.evaluate(()=>localStorage.setItem('riwaq.v1.lang','en'));await page.setViewportSize({width:1440,height:1000});await page.reload();
 await page.getByRole('button',{name:/^Memory/}).click();
 await page.getByRole('button',{name:'Review personal preferences',exact:true}).click();
 const personal=page.getByTestId('personal-memory-panel');
 await expect(personal.getByText('New saves and use in answers are currently blocked.',{exact:true})).toBeVisible();
 await expect(personal.getByRole('button',{name:'Save explicitly',exact:true})).toBeDisabled();
 const legacy=personal.locator('article').filter({hasText:'SYNTHETIC legacy UI deletion fixture'});
 await expect(legacy).toContainText('Legacy — review only');
 await page.screenshot({path:path.join(out,'personal-memory-gated-legacy.png'),fullPage:true});
 await legacy.getByRole('button',{name:'Delete',exact:true}).click();
 await expect(legacy).toHaveCount(0);
 const remaining=await api('/personal-memory');assert(!remaining.some(e=>e.id===legacyId));
 const policy=await api('/personal-memory/settings');assert.equal(policy.gate.available,false);
 report.personal_memory={legacy_fixture_id:legacyId,new_saves_blocked:true,save_disabled:true,legacy_deleted_via_ui:true,api_read_confirmed_absent:true,gate_enabled_by_test:false,fixture_inserted_directly_for_legacy_read_delete_only:true};
 assert.deepEqual(report.page_errors,[]);report.passed=true;
}catch(error){report.passed=false;report.failed_stage=stage;report.error=String(error).slice(0,1600);if(page)await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});throw error;}
finally{report.finished_at=new Date().toISOString();await writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));await browser?.close();console.log(JSON.stringify({output:out,passed:report.passed,cases:report.cases.length}));}
