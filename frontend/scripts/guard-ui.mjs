import {chromium,expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdir,writeFile,copyFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';

// Run only against the explicitly configured isolated Guard QA service. No traces,
// HARs, cookie dumps, auth request bodies, or pairing codes enter this report.
const base=process.env.RIWAQ_URL||'http://127.0.0.1:8765';
assert(['127.0.0.1','localhost'].includes(new URL(base).hostname),'Guard UI QA requires a loopback service.');
assert(process.env.RIWAQ_DATA_DIR,'Root must provide the same isolated RIWAQ_DATA_DIR used by the QA service.');
const root=fileURLToPath(new URL('../../',import.meta.url));
const runId=new Date().toISOString().replace(/[:.]/g,'-');
const out=new URL('../../reports/guard-ui/',import.meta.url),runOut=new URL(runId+'/',out);
await mkdir(runOut,{recursive:true});
await copyFile(new URL('report.json',out),new URL('report-prior-'+runId+'.json',out)).catch(error=>{if(error.code!=='ENOENT')throw error;});
const report={run_id:runId,real_backend:true,model_responses_mocked:false,browser_transport_mocked:false,synthetic_project_only:true,pairing_codes_saved:false,cookies_saved:false,source_text_saved_in_report:false,global_settings_changed:false,semantic_accuracy_certified:false,native_client_tested:false,screen_reader_tested:false,cases:[],layouts:[],page_errors:[],browser_external_requests:[]};
const headers={'X-Riwaq-Client':'local'};
const execFileAsync=promisify(execFile),requests=[];
let browser,context,page,project,resultId,sourceId;
let stage='startup';
const safeError=error=>String(error?.stack??error).replace(/[A-Za-z0-9_-]{40,}/g,'[redacted]').slice(0,6000);
const pathOf=value=>new URL(value.url()).pathname;
const safeDecision=value=>({id:value.id,decision:value.decision,reason:value.reason,operation:value.operation,policy_version:value.policy_version,created_at:value.created_at});
async function api(path,options={}){const response=await context.request.fetch(base+'/api'+path,{...options,headers:{...headers,...options.headers}});assert(response.ok(),`QA API ${path} returned HTTP ${response.status()}`);return response.status()===204?null:response.json();}
async function issueCode(){
 let issued;
 try{issued=await execFileAsync(fileURLToPath(new URL('../../.venv/Scripts/python.exe',import.meta.url)),['-m','backend.local_auth','pair','--show'],{cwd:root,env:{...process.env},windowsHide:true,timeout:15000,maxBuffer:4096});}
 catch(error){throw Error('Local pairing CLI failed; secret output was not recorded. Exit code: '+String(error.code??'unknown'));}
 const code=issued.stdout.trim();issued.stdout='';issued.stderr='';
 assert(/^[A-Za-z0-9_-]{40,128}$/.test(code),'Local pairing CLI returned an unexpected code format.');return code;
}
async function login(){
 await expect(page.locator('#riwaq-pairing-code')).toBeVisible();
 let code=await issueCode();
 try{
  await page.locator('#riwaq-pairing-code').fill(code);
  const [response]=await Promise.all([page.waitForResponse(value=>pathOf(value)==='/api/auth/login'&&value.request().method()==='POST'),page.getByRole('button',{name:'Pair browser',exact:true}).click()]);
  assert.equal(response.status(),200,'Actual local pairing must succeed.');
 }finally{code='';}
 await expect(page.locator('.pairing-screen')).toHaveCount(0);
 await expect(page.locator('.composer textarea')).toBeEnabled();
 assert.equal((await api('/auth/status')).authenticated,true);
 const storageKeys=await page.evaluate(()=>[...Object.keys(localStorage),...Object.keys(sessionStorage)]);
 assert(!storageKeys.some(key=>/pairing|token|password|session.secret/i.test(key)),'No pairing credential may be stored in web storage.');
 assert(!new URL(page.url()).search,'No pairing credential may appear in the URL.');
}
async function openSettings(ar=false){
 const menu=page.locator('.mobile-menu');
 if(await menu.isVisible())await menu.click();
 await page.locator('.sidebar').getByRole('button',{name:ar?'الإعدادات':'Settings',exact:true}).click();
 await expect(page.getByRole('dialog',{name:ar?'الإعدادات':'Settings',exact:true})).toBeVisible();
}
async function openGuard(ar=false){
 await openSettings(ar);
 await page.getByRole('dialog').getByRole('button',{name:ar?'درع رواق · حماية التنفيذ':'Riwaq Guard · execution protection',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:ar?'درع رواق · حماية التنفيذ':'Riwaq Guard · execution protection',exact:true});
 await expect(dialog.locator('.guard-toolbar select')).toBeEnabled();return dialog;
}
async function shot(filename){
 await page.evaluate(()=>document.fonts.ready);
 await page.screenshot({path:fileURLToPath(new URL(filename,runOut)),fullPage:true,animations:'disabled',mask:[page.locator('#riwaq-pairing-code')]});
}
try{
 browser=await chromium.launch({channel:'chrome',headless:true});
 context=await browser.newContext({viewport:{width:1440,height:1000}});page=await context.newPage();
 await context.addInitScript(()=>{if(!sessionStorage.getItem('guardQaAppearance')){localStorage.setItem('riwaq.v1.lang','en');localStorage.setItem('riwaq.v1.theme','light');sessionStorage.setItem('guardQaAppearance','1');}});
 page.on('pageerror',error=>report.page_errors.push(safeError(error)));
 page.on('request',request=>{const url=new URL(request.url());if(/^https?:$/.test(url.protocol)&&url.origin!==new URL(base).origin)report.browser_external_requests.push({origin:url.origin,path:url.pathname});if(url.origin===new URL(base).origin&&url.pathname.startsWith('/api/'))requests.push({method:request.method(),path:url.pathname});});
 await page.goto(base);await expect(page.locator('#riwaq-pairing-code')).toBeVisible();
 await expect(page.locator('.composer')).toHaveCount(0);
 assert(requests.every(request=>request.path==='/api/auth/status'),'App must not request protected API data before pairing.');
 const denied=await context.request.get(base+'/api/projects',{headers});assert.equal(denied.status(),401);
 report.cases.push({name:'unpaired_gate_no_protected_app_mount',passed:true,protected_http_status:401});

 stage='pair_browser';await login();
 report.cases.push({name:'real_single_use_pairing_code_cookie_login',passed:true,code_input_removed:true,credential_storage:false});
 const settings=await api('/settings');assert.equal(settings.provider,'ollama','Root must configure the isolated service for the actual local model.');
 project=await api('/projects',{method:'POST',data:{name:'QA Guard '+runId,instructions:'Synthetic local authorization QA. No external actions.'}});report.project_id=project.id;
 const source='Synthetic Guard evaluation fixture. The cedar workshop has 12 notebooks. The review meeting is on Monday. This is invented test data, not a real person or organization.';
 const file=await api(`/projects/${project.id}/files`,{method:'POST',multipart:{file:{name:'guard-synthetic.txt',mimeType:'text/plain',buffer:Buffer.from(source,'utf8')}}});sourceId=file.id;
 report.source={id:file.id,sha256:file.sha256,synthetic:true};
 await page.evaluate(pid=>{localStorage.setItem('riwaq.v1.project',pid);localStorage.removeItem('riwaq.v1.conversation');},project.id);
 await page.reload();await expect(page.locator('.composer textarea')).toBeEnabled();

 stage='local_guard_summary';let panel=await openGuard();
 await panel.getByLabel('Private file to summarize',{exact:true}).selectOption(sourceId);
 const [started]=await Promise.all([page.waitForResponse(value=>pathOf(value)===`/api/projects/${project.id}/guard/summaries`&&value.request().method()==='POST'),panel.getByRole('button',{name:'Summarize locally with Guard',exact:true}).click()]);
 assert.equal(started.status(),202);const start=await started.json();resultId=start.id;report.result_id=resultId;
 await expect(panel.locator('.guard-result > header [role="status"]')).toHaveText('Summary completed',{timeout:120000});
 const result=await api(`/projects/${project.id}/guard/results/${resultId}`);
 assert.equal(result.status,'completed');assert.equal(typeof result.content,'string');assert(result.content.trim().length>0);
 await expect(panel.locator('.guard-content')).toHaveText(result.content);
 report.summary={id:result.id,status:result.status,model:result.model,content_sha256:createHash('sha256').update(result.content).digest('hex'),content_characters:result.content.length,decision_id:result.decision_id};
 report.cases.push({name:'actual_qwen_guarded_private_file_summary',passed:true,semantic_accuracy_certified:false});

 stage='actual_egress_denial';
 const [egress]=await Promise.all([page.waitForResponse(value=>pathOf(value)===`/api/projects/${project.id}/guard/results/${resultId}/egress-check`),panel.getByRole('button',{name:'Check transfer denial',exact:true}).click()]);
 assert.equal(egress.status(),403);const refusal=(await egress.json()).detail;
 assert.equal(typeof refusal.reason,'string');assert.equal(typeof refusal.decision_id,'string');
 await expect(panel.getByRole('region',{name:'Guard decision evidence',exact:true})).toContainText(refusal.decision_id);
 const decision=await api('/guard/decisions/'+refusal.decision_id);
 assert.equal(decision.decision,'deny');assert.equal(decision.operation,'send_derived_result');
 assert.deepEqual(Object.keys(decision).sort(),['id','decision','reason','operation','policy_version','created_at'].sort());
 report.denial={http_status:403,...safeDecision(decision)};
 await panel.getByRole('button',{name:'Open decision evidence',exact:true}).click();
 await expect(panel.locator('.guard-decision')).toContainText(decision.reason);
 await shot('guard-denied-en-1440.png');
 report.cases.push({name:'real_default_deny_gate_and_safe_receipt',passed:true,external_request_attempted:false});

 stage='reload_and_layouts';await page.reload();await expect(page.locator('.composer textarea')).toBeEnabled();panel=await openGuard();
 await expect(panel.locator(`[data-result-id="${resultId}"] .guard-content`)).toHaveText(result.content);
 report.cases.push({name:'saved_guard_result_after_reload',passed:true});
 for(const width of [360,390,768,1280,1440])for(const theme of ['light','dark']){
  const ar=width!==1280;await page.setViewportSize({width,height:width<600?844:1000});
  await page.evaluate(({lang,theme})=>{localStorage.setItem('riwaq.v1.lang',lang);localStorage.setItem('riwaq.v1.theme',theme);},{lang:ar?'ar':'en',theme});
  await page.reload();await expect(page.locator('.composer textarea')).toBeEnabled();panel=await openGuard(ar);
  await expect(panel.locator('.guard-content')).toHaveText(result.content);
  await panel.getByRole('button',{name:ar?'راجع قرار التلخيص':'Review summary decision',exact:true}).click();
  await expect(panel.locator('.guard-decision')).toBeVisible();
  await panel.locator('.guard-intro').scrollIntoViewIfNeeded();
  const geometry=await page.evaluate(()=>({pageWidth:document.documentElement.clientWidth,scrollWidth:document.documentElement.scrollWidth,dialogWidth:document.querySelector('.guard-modal').getBoundingClientRect().width,viewportWidth:innerWidth}));
  assert(geometry.scrollWidth<=geometry.pageWidth+1,'No horizontal document overflow is permitted.');assert(geometry.dialogWidth<=geometry.viewportWidth+1);
  const name=`guard-${width}-${ar?'ar':'en'}-${theme}.png`;await shot(name);report.layouts.push({width,language:ar?'ar':'en',theme,screenshot:runId+'/'+name,...geometry});
 }

 stage='explicit_logout_and_repair';await page.setViewportSize({width:1440,height:1000});
 await page.evaluate(()=>{localStorage.setItem('riwaq.v1.lang','en');localStorage.setItem('riwaq.v1.theme','light');});await page.reload();await expect(page.locator('.composer textarea')).toBeEnabled();
 await openSettings();await page.getByRole('dialog').getByRole('button',{name:'End browser session',exact:true}).click();
 await expect(page.locator('#riwaq-pairing-code')).toBeVisible();await expect(page.locator('.composer')).toHaveCount(0);
 assert.equal((await api('/auth/status')).authenticated,false);await login();
 report.cases.push({name:'explicit_logout_then_fresh_pairing',passed:true});

 stage='runtime_401_no_mutation_replay';
 await page.locator('.sidebar').getByRole('button',{name:'New project',exact:true}).click();
 const rejectedName='QA Guard rejected mutation '+runId;
 await page.getByRole('dialog',{name:'New project',exact:true}).getByRole('textbox',{name:'Name',exact:true}).fill(rejectedName);
 await api('/auth/logout',{method:'POST'}); // Real server expiry while the existing UI remains mounted.
 const before=requests.filter(request=>request.path==='/api/projects'&&request.method==='POST').length;
 const [rejected]=await Promise.all([page.waitForResponse(value=>pathOf(value)==='/api/projects'&&value.request().method()==='POST'),page.getByRole('dialog').getByRole('button',{name:'Create',exact:true}).click()]);
 assert.equal(rejected.status(),401);await expect(page.locator('#riwaq-pairing-code')).toBeVisible();await expect(page.locator('.composer')).toHaveCount(0);
 await login();
 assert.equal(requests.filter(request=>request.path==='/api/projects'&&request.method==='POST').length-before,1,'Rejected mutation must never be automatically retried.');
 assert(!(await api('/projects')).some(item=>item.name===rejectedName),'Rejected project creation must not occur after re-pairing.');
 report.cases.push({name:'runtime_401_repair_without_replaying_mutation',passed:true,rejected_request_count:1});
 assert.equal(report.page_errors.length,0);assert.equal(report.browser_external_requests.length,0);
 report.status='passed';
}catch(error){report.status='failed';report.failure_stage=stage;report.error=safeError(error);if(page)await shot('failure.png').catch(()=>{});process.exitCode=1;}
finally{
 if(context)await context.request.post(base+'/api/auth/logout',{headers}).catch(()=>{});
 report.completed_at=new Date().toISOString();
 await writeFile(new URL('report.json',runOut),JSON.stringify(report,null,2));await writeFile(new URL('report.json',out),JSON.stringify(report,null,2));
 if(browser)await browser.close();
 console.log(JSON.stringify({status:report.status,cases:report.cases.length,layouts:report.layouts.length,report:'reports/guard-ui/'+runId+'/report.json'}));
}
