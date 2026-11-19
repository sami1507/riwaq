// Synthetic browser/API/Docker/Ollama integration. No mocks, production data or downloads.
import {chromium,expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url)),base=process.env.RIWAQ_URL;
assert.equal(base,'http://127.0.0.1:8772');
assert.equal(path.resolve(process.env.RIWAQ_DATA_DIR??''),path.resolve(root,'reports/ai-core-continuation-20261116/ui-service-data'));
const out=path.join(root,'reports/ai-core-continuation-20261116/ui',new Date().toISOString().replaceAll(':','-'));await mkdir(out,{recursive:true});
const report={started_at:new Date().toISOString(),backend_mocked:false,transport_mocked:false,synthetic:true,native_tested:false,cases:[],runs:[],page_errors:[],external_requests:[]};
let browser,context,page,stage='pair';
try{
 browser=await chromium.launch({channel:'chrome',headless:true});report.browser=browser.version();context=await browser.newContext({viewport:{width:1440,height:1000}});page=await context.newPage();page.setDefaultTimeout(20000);
 page.on('pageerror',e=>report.page_errors.push(e.message.slice(0,300)));
 await context.route('**/*',async route=>{const u=new URL(route.request().url());if(/^https?:$/.test(u.protocol)&&u.origin!==base){report.external_requests.push(u.origin+u.pathname);await route.abort('blockedbyclient');}else await route.continue();});
 await context.addInitScript(()=>{if(!sessionStorage.getItem('revision-ui-init')){localStorage.setItem('riwaq.v1.lang','en');localStorage.setItem('riwaq.v1.theme','light');sessionStorage.setItem('revision-ui-init','1');}});
 await page.goto(base);await expect(page.locator('#riwaq-pairing-code')).toBeVisible();
 let pair=await promisify(execFile)(path.join(root,'.venv/Scripts/python.exe'),['-X','utf8','-B','-c','from backend.local_auth import issue_pairing_code; print(issue_pairing_code())'],{cwd:root,env:{...process.env},windowsHide:true,timeout:15000,maxBuffer:4096});
 let code=pair.stdout.trim();pair.stdout='';pair.stderr='';assert(/^[A-Za-z0-9_-]{43}$/.test(code));await page.locator('#riwaq-pairing-code').fill(code);code='';await page.getByRole('button',{name:'Pair browser',exact:true}).click();await expect(page.locator('.composer textarea')).toBeEnabled();
 const api=async(route,data,method=data?'POST':'GET')=>{const res=await context.request.fetch(base+'/api'+route,{method,headers:{'X-Riwaq-Client':'local'},...(data?{data}:{})});assert(res.ok(),route+' HTTP '+res.status());return res.json();};
 const settings={mode:'local',provider:'ollama',model:'qwen3:4b-instruct',context_size:8192,max_tokens:1024,temperature:0,seed:42,local_fallback_models:[]};await api('/settings',settings,'PATCH');report.settings=settings;
 const project=await api('/projects',{name:'AI core report revision synthetic acceptance'});report.project_id=project.id;
 const poll=async rid=>{const end=performance.now()+120000;let run;do{run=await api(`/projects/${project.id}/skill-runs/${rid}`);if(!['queued','running','cancelling'].includes(run.status))return run;assert(performance.now()<end,'task deadline');await new Promise(r=>setTimeout(r,500));}while(true);};
 stage='actual-analysis';const old=await poll((await api(`/projects/${project.id}/skill-runs`,{request:'احسب إحصاء الطلبات المرجعية',requestedSkills:['descriptive-statistics'],idempotencyKey:crypto.randomUUID(),inputs:{dataOperation:{operation:'stats',table:{columns:['amount'],rows:[[10],[0],[20],[null]]},columns:['amount'],record_unit:'order'}}})).id);
 assert.equal(old.status,'completed');const analysis=JSON.stringify(old.result.structured);assert.equal(old.result.structured['descriptive-statistics'].execution_receipt.exit_code,0);report.runs.push(old);report.cases.push('actual_api_docker_statistics_saved');
 await page.evaluate(pid=>{localStorage.setItem('riwaq.v1.project',pid);localStorage.removeItem('riwaq.v1.conversation');},project.id);await page.reload();await page.getByRole('button',{name:'Riwaq skills',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'Riwaq skills',exact:true});await dialog.getByLabel('Project history',{exact:true}).selectOption(old.id);
 const translated=[];
 for(const language of ['en','he']){
  const sourceId=language==='he'?translated[0].run_id:old.id;stage='revision-'+language;await dialog.getByLabel('Project history',{exact:true}).selectOption(sourceId);await dialog.getByLabel('Revised report language',{exact:true}).selectOption(language);
  const [created]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/skill-runs')&&r.request().method()==='POST'),dialog.getByRole('button',{name:'Create translated copy',exact:true}).click()]);assert.equal(created.status(),202);const run=await poll((await created.json()).id);
  report.runs.push(run);assert.equal(run.status,'completed',JSON.stringify(run.steps));assert.deepEqual(run.selected_skills,['docx-authoring']);assert.equal(run.result.presentation_revision.presentation_method,'localized_saved_analysis');assert(!run.result.text.includes('__RW_LITERAL_'));if(language==='en')assert.match(run.result.text,/total: 30; mean: 10/);assert.equal(run.result.presentation_revision.parent_id,sourceId);assert.equal(run.result.presentation_revision.analysis_source_run_id,old.id);
  assert.equal(JSON.stringify((await api(`/projects/${project.id}/skill-runs/${old.id}`)).result.structured),analysis);
  const artifact=run.result.artifacts.find(a=>a.mime==='application/vnd.openxmlformats-officedocument.wordprocessingml.document');assert(artifact);
  const response=await context.request.get(`${base}/api/projects/${project.id}/skill-runs/${run.id}/artifacts/${artifact.name}`,{headers:{'X-Riwaq-Client':'local'}});assert.equal(response.status(),200);const saved=path.join(out,`report-${language}.docx`);await writeFile(saved,await response.body());
  const inspect=await promisify(execFile)(path.join(root,'.venv/Scripts/python.exe'),['-X','utf8','-B','-c','from zipfile import ZipFile; from xml.etree import ElementTree as E; import sys,json; z=ZipFile(sys.argv[1]); r=E.fromstring(z.read("word/document.xml")); ns={"w":"http://schemas.openxmlformats.org/wordprocessingml/2006/main"}; t=" ".join(x.text or "" for x in r.findall(".//w:t",ns)); assert len(r.findall(".//w:tbl",ns))==2; assert "30" in t and "10" in t and "null" in t; print(json.dumps({"text":t,"tables":2},ensure_ascii=False))',saved],{cwd:root,windowsHide:true,timeout:15000});
  translated.push({run_id:run.id,language,result_text:run.result.text,artifact_sha256:artifact.sha256,inspection:JSON.parse(inspect.stdout)});report.cases.push(`revision_${language}_reuses_analysis_saves_editable_tables`);
  await expect(dialog.locator('.skills-status').first()).toContainText('review',{timeout:15000});await page.screenshot({path:path.join(out,`report-${language}.png`),fullPage:true});
 }
 report.translated=translated;
 const history=await api(`/projects/${project.id}/skill-runs`);assert.equal(history.length,3);assert.equal(history.filter(r=>r.selected_skills.includes('descriptive-statistics')).length,1);report.cases.push('one_analysis_two_presentation_runs_original_unchanged');
 stage='freeform-translation';const prose=await poll((await api(`/projects/${project.id}/skill-runs`,{request:'أنشئ تقرير Word اصطناعي',requestedSkills:['docx-authoring'],idempotencyKey:crypto.randomUUID(),inputs:{text:'المجموع 30 والمتوسط 10.'}})).id);
 const rewritten=await poll((await api(`/projects/${project.id}/skill-runs`,{request:'غيّر لغة التقرير المحفوظ إلى الإنجليزية',previousRunId:prose.id,idempotencyKey:crypto.randomUUID(),inputs:{reportRevision:{expectedResultHash:prose.result.report_revision.result_hash,targetLanguage:'en'}}})).id);
 report.runs.push(prose,rewritten);assert.equal(rewritten.status,'completed',JSON.stringify(rewritten.steps));assert.match(rewritten.result.text,/30/);assert.match(rewritten.result.text,/10/);assert(!rewritten.result.text.includes('__RW_LITERAL_'));assert(!/[\u0600-\u06ff\u0590-\u05ff]/.test(rewritten.result.text));report.freeform_translation={text:rewritten.result.text,method:rewritten.result.presentation_revision.presentation_method,quality:'assistant semantic review pending',transport_retries:'not instrumented in this UI probe'};report.cases.push('actual_local_freeform_translation_has_numbers_and_no_internal_markers');
 stage='arabic-mobile';await page.evaluate(()=>localStorage.setItem('riwaq.v1.lang','ar'));await page.setViewportSize({width:390,height:844});await page.reload();await page.locator('.mobile-menu').click();await page.getByRole('button',{name:'مهارات رواق',exact:true}).click();const arabic=page.getByRole('dialog',{name:'مهارات رواق',exact:true});await arabic.getByLabel('سجل المشروع',{exact:true}).selectOption(old.id);await expect(arabic.getByLabel('لغة نسخة التقرير',{exact:true})).toBeVisible();await arabic.getByLabel('لغة نسخة التقرير',{exact:true}).scrollIntoViewIfNeeded();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await page.screenshot({path:path.join(out,'arabic-mobile.png'),fullPage:true});report.cases.push('arabic_mobile_revision_controls_saved_reopen');
 assert.equal(report.page_errors.length,0);assert.equal(report.external_requests.length,0);report.passed=true;
}catch(e){report.passed=false;report.stage=stage;report.error=String(e).slice(0,1800);throw e;}
finally{report.finished_at=new Date().toISOString();await writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));await browser?.close();console.log(JSON.stringify({out,passed:report.passed,cases:report.cases,stage:report.stage,error:report.error}));}
