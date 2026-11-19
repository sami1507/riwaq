import {chromium,expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
const root=new URL('../../',import.meta.url),data=JSON.parse(await readFile(new URL('reports/audit-live.json',root),'utf8')),calc=data.cases.find(x=>x.label==='exact_calculation'),out=new URL('reports/audit-ui/',root);await mkdir(out,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true}),context=await browser.newContext({viewport:{width:1440,height:1000},extraHTTPHeaders:{'X-Riwaq-Client':'local'}}),page=await context.newPage(),errors=[],external=[],report={real_browser:true,actual_saved_model_output:true,errors,external,layouts:[]};
page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/.test(r.url())&&new URL(r.url()).hostname!=='127.0.0.1')external.push(r.url());});
await context.addInitScript(({pid,cid})=>{if(!sessionStorage.getItem('auditInit')){localStorage.setItem('riwaq.v1.project',pid);localStorage.setItem('riwaq.v1.conversation',cid);localStorage.setItem('riwaq.v1.lang','ar');localStorage.setItem('riwaq.v1.theme','light');sessionStorage.setItem('auditInit','1');}},{pid:data.project_id,cid:calc.conversation_id});
try{
 await page.goto('http://127.0.0.1:8765');
 const message=page.locator('[data-message-id="'+calc.message_id+'"]');await expect(message).toBeVisible();
 const result=message.getByRole('region',{name:'نتيجة الحاسبة المستقلة'});await expect(result).toBeVisible();await expect(result.locator('pre')).toHaveText('(19.95 * 3) * 1.17 - 10 = 60.0245');await expect(result).toContainText('قد يخالفها');assert(await result.evaluate(el=>Boolean(el.compareDocumentPosition(el.parentElement.querySelector('.markdown'))&Node.DOCUMENT_POSITION_FOLLOWING)));
 assert((await message.locator('.markdown').innerText()).includes('59.9945'),'Original model failure must remain observable, not silently rewritten');
 await expect(page.getByRole('button',{name:'استوديو الصور',exact:true})).toHaveCount(0);
 for(const width of [360,390,768,1280,1440])for(const theme of ['light','dark']){
  await page.setViewportSize({width,height:900});await page.evaluate(theme=>localStorage.setItem('riwaq.v1.theme',theme),theme);await page.reload();await expect(result).toBeVisible();await result.scrollIntoViewIfNeeded();
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);assert(!overflow);assert(await result.evaluate(el=>[...el.children].every(child=>{const b=child.getBoundingClientRect(),a=el.getBoundingClientRect();return b.left>=a.left&&b.right<=a.right&&child.scrollWidth<=child.clientWidth+1;})),'Calculator children must fit without clipping');
  report.layouts.push({width,theme,overflow:false,calculator_visible:true});if(width===390||width===1440)await page.screenshot({path:new URL(width+'-'+theme+'.png',out).pathname.replace(/^\/([A-Za-z]:)/,'$1'),fullPage:true,animations:'disabled'});
 }
 await page.setViewportSize({width:1440,height:1000});await page.getByRole('button',{name:/محادثة جديدة/}).first().click();
 await page.locator('.composer textarea').fill('أجب بالعدد فقط: كم يساوي 6 × 7؟');
 const waiting=page.waitForResponse(r=>/\/generate$/.test(new URL(r.url()).pathname)&&r.request().method()==='POST');await page.getByRole('button',{name:'إرسال',exact:true}).click();
 const response=await waiting,events=(await response.text()).trim().split('\n').map(JSON.parse),done=events.findLast(x=>x.type==='done'),start=events.find(x=>x.type==='start');assert.equal(response.status(),200);assert(done&&start);assert.equal(done.message.status,'complete');assert.equal(done.message.content.trim(),'42');assert(events.some(x=>x.type==='delta'));
 await page.reload();await expect(page.locator('[data-message-id="'+done.message.id+'"] .markdown')).toHaveText('42');
 report.new_actual_conversation={conversation_id:start.conversation_id,message_id:done.message.id,answer:done.message.content,restored:true};report.independent_calculation_visible=true;report.passed=errors.length===0&&external.length===0;assert(report.passed);
}finally{await writeFile(new URL('report.json',out),JSON.stringify(report,null,2));await browser.close();}
console.log(JSON.stringify({passed:report.passed,layouts:report.layouts.length,actual_answer:report.new_actual_conversation?.answer}));
