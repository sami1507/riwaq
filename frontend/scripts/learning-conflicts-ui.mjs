import {chromium, expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

let input='';for await(const chunk of process.stdin)input+=chunk;
const settings=JSON.parse(input);input='';
assert.equal(settings.base,'http://127.0.0.1:18765');
const {base,output}=settings;await mkdir(output,{recursive:true});
const report={started_at:new Date().toISOString(),synthetic:true,backend_mocked:false,model_calls:0,training:false,
  cases:[],page_errors:[],external_requests:[]};
let browser,page,stage='pairing';
try{
 browser=await chromium.launch({channel:'chrome',headless:true});report.browser=browser.version();
 const context=await browser.newContext({viewport:{width:1440,height:1050}});page=await context.newPage();page.setDefaultTimeout(20000);
 page.on('pageerror',e=>report.page_errors.push(e.message.slice(0,200)));
 await context.route('**/*',async route=>{const u=new URL(route.request().url());if(/^https?:$/.test(u.protocol)&&u.origin!==base){report.external_requests.push(u.origin);await route.abort();}else await route.continue();});
 await context.addInitScript(()=>{localStorage.setItem('riwaq.v1.lang','en');localStorage.setItem('riwaq.v1.theme','light');});
 await page.goto(base);await page.locator('#riwaq-pairing-code').fill(settings.code);settings.code='';
 await page.getByRole('button',{name:'Pair browser',exact:true}).click();await expect(page.locator('.composer textarea')).toBeEnabled();
 const api=async(route,data)=>{const r=await context.request.fetch(base+'/api'+route,{method:data?'POST':'GET',headers:{'X-Riwaq-Client':'local'},...(data?{data}:{})});assert(r.ok(),route+':'+r.status());return r.json();};
 const project=await api('/projects',{name:'Synthetic conflict review'});
 await page.evaluate(pid=>{localStorage.setItem('riwaq.v1.project',pid);localStorage.removeItem('riwaq.v1.conversation');},project.id);await page.reload();
 stage='math-worker';await page.locator('input[name="riwaq-answer-mode"][value="problem"]').check();await page.locator('.composer textarea').fill('2+2');
 const [r]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/generate')&&r.request().method()==='POST'),page.getByRole('button',{name:'Calculate',exact:true}).click()]);
 assert.equal(r.status(),200);const events=(await r.text()).trim().split('\n').map(JSON.parse),message=events.at(-1).message;
 assert.equal(message.run.model_calls,0);assert.equal(message.run.tool_calls,1);
 const cid=await page.evaluate(()=>localStorage.getItem('riwaq.v1.conversation')),learning=`/projects/${project.id}/learning`;
 const snapshot=await api(learning+`/targets/message/${message.id}`),target={kind:snapshot.kind,id:snapshot.id,version:snapshot.version};
 stage='conflicting-checks';const rows=[];
 for(const revised_input of ['2+3','2+4']){
  const row=await api(learning+'/feedback',{idempotency_key:crypto.randomUUID(),target,signal:'correction',
   correction:{kind:'input_correction',revised_input},scope:'conversation',purposes:['current_task'],conversation_id:cid});
  rows.push(await api(learning+`/feedback/${row.id}/verify`,{expected_version:row.version}));
 }
 assert((await api(learning+'/feedback')).every(r=>r.status==='conflicted'));
 report.cases.push({id:'actual_server_conflict',status:'passed',worker_calls:rows.reduce((n,r)=>n+r.verification.worker_calls,0)});
 stage='review-ui';await page.locator(`[data-message-id="${message.id}"]`).getByRole('button',{name:'Not helpful or correct',exact:true}).click();
 const panel=page.getByRole('dialog').locator('.learning-panel');
 await panel.getByRole('button',{name:'Conflicting corrections; use suspended · Original conversation only',exact:true}).first().click();
 await expect(panel.getByRole('alert')).toContainText('Neither recency nor vote count');
 await expect(panel.getByRole('button',{name:'Save candidate',exact:true})).toBeDisabled();
 await expect(panel.locator('.learning-answer')).toHaveCount(0);
 await panel.locator('[data-feedback-id]').scrollIntoViewIfNeeded();
 await expect(panel.getByRole('alert')).toBeVisible();
 await page.screenshot({path:path.join(output,'conflict-quarantine.png'),fullPage:true});
 report.cases.push({id:'quarantine_visible_adoption_disabled',status:'passed'});
 stage='withdraw-and-recheck';
 const [deleted]=await Promise.all([page.waitForResponse(r=>r.request().method()==='DELETE'&&r.url().includes('/feedback/')),
  panel.locator('.learning-row').first().getByRole('button',{name:'Forget feedback',exact:true}).click()]);
 assert.equal(deleted.status(),200);
 await expect(panel.getByRole('button',{name:'Conflicting corrections; use suspended · Original conversation only',exact:true})).toHaveCount(1);
 await panel.getByRole('button',{name:'Conflicting corrections; use suspended · Original conversation only',exact:true}).click();
 await expect(panel.getByRole('button',{name:'Save candidate',exact:true})).toBeDisabled();
 const [checked]=await Promise.all([page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/verify')),
  panel.getByRole('button',{name:'Verify correction',exact:true}).click()]);
 assert.equal(checked.status(),200);const checkedRow=await checked.json();assert.equal(checkedRow.status,'verified');
 await expect(panel.locator('.learning-answer')).toContainText('Verified result');
 await expect(panel.getByRole('button',{name:'Save candidate',exact:true})).toBeEnabled();
 report.cases.push({id:'withdrawal_requires_fresh_check',status:'passed'});
 assert.deepEqual(report.page_errors,[]);assert.deepEqual(report.external_requests,[]);
 report.status='passed';
}catch(e){report.status='failed';report.failure={stage,message:String(e.message).slice(0,1000)};if(page)await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{});process.exitCode=1;}
finally{settings.code='';report.completed_at=new Date().toISOString();await writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2));await browser?.close();console.log(JSON.stringify({status:report.status,cases:report.cases.length,output}));}
