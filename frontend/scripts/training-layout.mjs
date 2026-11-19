import {chromium,expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const out=fileURLToPath(new URL('../../reports/',import.meta.url)),base='http://127.0.0.1:8765';
const live=JSON.parse(await readFile(out+'/live-workflows-ui.json','utf8')),source=live.cases.find(c=>c.label==='contract_retry_after_reload');
assert(live.passed&&source);await mkdir(out+'/training-layout',{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true}),report={passed:false,model_calls:0,states:[],scope:'Real saved response; UI-only settings; no inference, native app or screen reader test'};
const sizes=[[360,800],[390,844],[768,1024],[1280,800],[1440,1000]],errors=[],external=[];
async function hit(locator){await locator.scrollIntoViewIfNeeded();return locator.evaluate(e=>{const r=e.getBoundingClientRect(),p=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return p===e||e.contains(p);});}
async function measure(page,label){
 const m=await page.evaluate(()=>{const box=s=>{const r=document.querySelector(s).getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height,left:r.left,right:r.right};};return {width:innerWidth,height:innerHeight,overflow:document.documentElement.scrollWidth>innerWidth,transcript:box('.conversation-scroll'),settings:box('.composer-settings'),composer:box('.composer'),textarea:box('.composer textarea'),send:box('.send-button'),root_font:parseFloat(getComputedStyle(document.documentElement).fontSize)};});
 assert(!m.overflow,label+': horizontal overflow');
 assert(m.transcript.height>=Math.min(180,m.height*.18),label+': collapsed transcript '+m.transcript.height);
 assert(m.textarea.top>=0&&m.textarea.bottom<=m.height&&m.send.top>=0&&m.send.bottom<=m.height,label+': hidden composer');
 assert(m.settings.height<=Math.min(m.height*.32,22*m.root_font)+2,label+': oversized settings');
 assert(m.transcript.bottom<=m.settings.top+2&&m.settings.bottom<=m.composer.top+2,label+': overlapping panels');
 assert(await hit(page.locator('.send-button')),label+': send blocked');
 const retry=page.locator('[data-message-id="'+source.message.id+'"] .message-actions button[aria-label="'+(await page.locator('html').getAttribute('lang')==='ar'?'إعادة المحاولة':'Retry')+'"]');
 // Use title/aria role in the saved message; never dispatch it.
 const button=await retry.count()?retry:page.locator('[data-message-id="'+source.message.id+'"]').getByRole('button',{name:/^(Retry|إعادة المحاولة|أعد المحاولة)$/});
 assert(await hit(button),label+': retry blocked');
 return {label,...m,retry_hit:true,send_hit:true};
}
try{
 for(const [width,height] of sizes)for(const lang of ['ar','en'])for(const theme of ['light','dark']){
  const context=await browser.newContext({viewport:{width,height},extraHTTPHeaders:{'X-Riwaq-Client':'local'},reducedMotion:'reduce'}),page=await context.newPage();page.setDefaultTimeout(15000);
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/.test(r.url())&&new URL(r.url()).origin!==base)external.push(r.url());});
  await page.route('**/api/conversations/*/generate',route=>{report.model_calls++;return route.abort();});
  await context.addInitScript(v=>{localStorage.setItem('riwaq.v1.lang',v.lang);localStorage.setItem('riwaq.v1.theme',v.theme);localStorage.setItem('riwaq.v1.project',v.project);localStorage.setItem('riwaq.v1.conversation',v.conversation);},{lang,theme,project:live.project_id,conversation:source.conversation_id});
  await page.goto(base);await expect(page.locator('[data-message-id="'+source.message.id+'"]')).toBeVisible();await expect(page.locator('html')).toHaveAttribute('dir',lang==='ar'?'rtl':'ltr');await expect(page.locator('html')).toHaveAttribute('data-theme',theme);
  const label=width+'-'+lang+'-'+theme;report.states.push(await measure(page,label+'-compact'));
  if(width<=760)await page.locator('.mobile-menu').click();
  await page.getByRole('button',{name:lang==='ar'?'سجل الحقائق':'Fact ledger',exact:true}).click();const d=page.getByRole('dialog');await expect(d.locator('.fact-row').first()).toBeVisible();await d.locator('.fact-row').first().getByRole('checkbox').check();await d.getByRole('button',{name:lang==='ar'?'استخدم في الرسالة التالية':'Use in next message',exact:true}).click();
  await page.locator('.reasoning-enable input').check();await page.locator('.reasoning-options>summary').click();await page.locator('.reasoning-options input[type=checkbox]').first().check();
  await page.locator('.reasoning-options input[dir=ltr]').fill('7 * 6');await page.locator('.output-contract>label input').check();await page.locator('.output-contract textarea').fill('{"type":"object","properties":{"answer":{"type":"string"}},"required":["answer"],"additionalProperties":false}');
  await page.locator('.source-toggle.private').click();await page.locator('.source-toggle.public').click();await page.locator('.composer textarea').fill('نَصّ عَرَبِيّ مع TypeScript وAPI وv2.1 — long_english_filename_example.ts');
  report.states.push(await measure(page,label+'-expanded'));assert(await hit(page.locator('.composer-knowledge-summary')),label+': knowledge notice hidden');
  const settings=page.locator('.composer-settings');await settings.evaluate(e=>e.scrollTop=0);await settings.focus();await page.keyboard.press('PageDown');await page.waitForTimeout(50);assert(await settings.evaluate(e=>e.scrollTop>0),label+': keyboard region did not scroll');
  await expect(page.locator('.reasoning-options input[dir=ltr]')).toHaveValue('7 * 6');await expect(page.locator('.composer textarea')).toHaveValue('نَصّ عَرَبِيّ مع TypeScript وAPI وv2.1 — long_english_filename_example.ts');
  await page.screenshot({path:out+'/training-layout/'+label+'.png',fullPage:true,animations:'disabled'});await context.close();
 }
 assert.equal(report.model_calls,0);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);report.passed=true;console.log(JSON.stringify({passed:true,states:report.states.length,model_calls:0}));
}catch(e){report.error=String(e);throw e;}finally{report.page_errors=errors;report.external_requests=external;await writeFile(out+'/training-layout.json',JSON.stringify(report,null,2));await browser.close();}
