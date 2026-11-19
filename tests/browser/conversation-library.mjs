// Real built UI -> authenticated HTTP -> isolated SQLite. No model provider.
import {chromium,expect} from '@playwright/test';
import {spawn,execFileSync} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const stamp=new Date().toISOString().replace(/[:.]/g,'-');
const output=resolve(root,'reports/core-stabilization-20261118/conversation-library/ui',stamp);
const scratch=resolve(root,'.qa-workspace/core-conversation-library',stamp);
await mkdir(output,{recursive:true});await mkdir(scratch,{recursive:true});
const base='http://127.0.0.1:8798';
const environment={...process.env,RIWAQ_DATA_DIR:scratch,RIWAQ_DEV_ORIGIN:base};
const python=resolve(root,'.venv/Scripts/python.exe');
const child=spawn(python,['-m','uvicorn','backend.app:app','--host','127.0.0.1','--port','8798'],{cwd:root,env:environment,windowsHide:true,stdio:['ignore','pipe','pipe']});
let serverLog='';child.stdout.on('data',data=>serverLog+=data);child.stderr.on('data',data=>serverLog+=data);
const checks=[],errors=[],external=[];
let browser;
function passed(name){checks.push({name,status:'PASS'});console.log(`PASS ${name}`);}
try{
 for(let attempt=0;attempt<120;attempt++){
  if(child.exitCode!==null)throw Error('Owned scratch server stopped before ready');
  try{const result=await fetch(base+'/api/auth/status');if(result.ok)break;}catch{}
  if(attempt===119)throw Error('Owned scratch server startup timed out');
  await new Promise(done=>setTimeout(done,250));
 }
 const pairing=execFileSync(python,['-c','from backend.local_auth import issue_pairing_code; print(issue_pairing_code())'],{cwd:root,env:environment,encoding:'utf8',windowsHide:true}).trim();
 browser=await chromium.launch({channel:'chrome',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:960},extraHTTPHeaders:{'X-Riwaq-Client':'local'}});
 const login=await context.request.post(base+'/api/auth/login',{data:{pairing_code:pairing}});assert.equal(login.status(),200);
 async function request(method,path,data){const result=await context.request.fetch(base+path,{method,data});if(!result.ok())throw Error(`${method} ${path}: ${result.status()} ${await result.text()}`);return result.json();}
 const projects=await request('GET','/api/projects');const pid=projects[0].id;
 const target=await request('POST','/api/projects',{name:'وجهة اختبار — Target'});
 const eligible=await request('POST','/api/conversations',{project_id:pid});
 const blocked=await request('POST','/api/conversations',{project_id:pid});
 const generated=await context.request.post(base+`/api/conversations/${blocked.id}/generate`,{data:{request_id:'library-calculation-001',content:'0.1+0.2',answer_mode:'calculator'}});
 assert.equal(generated.status(),200);await generated.text();
 const answer=await request('GET',`/api/conversations/${blocked.id}`);assert.equal(answer.messages.at(-1).status,'complete');
 const libraryPath=`/api/projects/${pid}/conversation-library`;
 const blockedDetail=await request('GET',`${libraryPath}/${blocked.id}`);assert.equal(blockedDetail.move.reason,'generation_history');
 const page=await context.newPage();
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(!request.url().startsWith(base)&&!request.url().startsWith('data:')&&!request.url().startsWith('blob:'))external.push(request.url());});
 await page.addInitScript(()=>localStorage.setItem('riwaq.v1.lang','ar'));
 await page.goto(base);await page.locator('.app[aria-busy="false"]').waitFor();
 await page.getByRole('button',{name:'تنظيم المحادثات',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'تنظيم المحادثات'});
 await dialog.locator('.library-list').getByRole('button').filter({hasText:'محادثة جديدة'}).click();
 const input=dialog.getByLabel('اسم المحادثة',{exact:true});await expect(input).toBeVisible();
 await input.fill('مراجعة عربية 2026 — x² + 1');
 await dialog.getByRole('button',{name:'حفظ الاسم',exact:true}).click();
 await expect(dialog.getByRole('status')).toContainText('حُفظ الاسم الجديد');
 assert.equal((await request('GET',`${libraryPath}/${eligible.id}`)).title,'مراجعة عربية 2026 — x² + 1');
 passed('RTL rename saves through actual API and SQLite');
 await dialog.getByRole('button',{name:'أرشفة المحادثة',exact:true}).click();
 await expect(dialog.getByRole('status')).toContainText('أُرشفت المحادثة');
 await dialog.getByLabel('عرض',{exact:true}).selectOption('archived');
 await expect(dialog.locator('.library-list button')).toHaveCount(1);
 await dialog.locator('.library-list button').click();
 await dialog.getByRole('button',{name:'استعادة من الأرشيف',exact:true}).click();
 await expect(dialog.getByRole('status')).toContainText('استُعيدت المحادثة');
 assert.equal((await request('GET',`${libraryPath}/${eligible.id}`)).archived,false);
 passed('archive and restore filters reflect persisted state');
 await dialog.getByLabel('عرض',{exact:true}).selectOption('active');
 await dialog.locator('.library-list button').filter({hasText:'مراجعة عربية'}).click();
 await expect(input).toHaveValue('مراجعة عربية 2026 — x² + 1');
 const current=await request('GET',`${libraryPath}/${eligible.id}`);
 await request('PATCH',`${libraryPath}/${eligible.id}`,{expected_version:current.version,title:'تعديل من نافذة أخرى'});
 await input.fill('تعديل قديم لا يكتب فوق الأحدث');
 await dialog.getByRole('button',{name:'حفظ الاسم',exact:true}).click();
 await expect(dialog.getByRole('alert')).toContainText('تغيرت المحادثة');
 await expect(input).toHaveValue('تعديل قديم لا يكتب فوق الأحدث');
 assert.equal((await request('GET',`${libraryPath}/${eligible.id}`)).title,'تعديل من نافذة أخرى');
 passed('stale dialog shows conflict and preserves unsaved input');
 await dialog.getByRole('button',{name:'تحديث',exact:true}).click();
 await dialog.locator('.library-list button').filter({hasText:'تعديل من نافذة أخرى'}).click();
 await dialog.getByLabel('المشروع الجديد',{exact:true}).selectOption(target.id);
 const moveButton=dialog.getByRole('button',{name:'نقل المحادثة',exact:true});await expect(moveButton).toBeDisabled();
 await dialog.getByLabel('أفهم تغير السياق وأن الملفات والذاكرة لا تُنقل.').check();
 await moveButton.click();
 await expect(dialog.getByRole('status')).toContainText('نُقلت المحادثة');
 assert.equal((await request('GET',`/api/conversations/${eligible.id}`)).project_id,target.id);
 passed('eligible move requires acknowledgement and changes project binding');
 await dialog.locator('.library-list button').filter({hasText:'0.1+0.2'}).click();
 await expect(dialog.locator('.library-blocked')).toContainText('سجل محاولة توليد');
 await expect(dialog.getByRole('button',{name:'نقل المحادثة',exact:true})).toHaveCount(0);
 passed('real calculation receipt blocks unsafe historical relocation');
 await page.screenshot({path:resolve(output,'desktop-arabic.png'),fullPage:true,animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 await expect(dialog).toBeVisible();
 await input.scrollIntoViewIfNeeded();await input.focus();
 await page.screenshot({path:resolve(output,'mobile-rtl.png'),fullPage:true,animations:'disabled'});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 const bounds=await dialog.boundingBox();assert(bounds&&bounds.width<=390);
 await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
 // Keyboard escape restores focus to the original launcher even on a narrow view.
 assert.equal(await page.evaluate(()=>document.activeElement?.getAttribute('aria-label')??document.activeElement?.textContent),'تنظيم المحادثات');
 passed('390px RTL layout and keyboard dismissal preserve focus');
 await page.setViewportSize({width:1440,height:960});await page.reload();await page.locator('.app[aria-busy="false"]').waitFor();
 await page.getByRole('button',{name:'تنظيم المحادثات',exact:true}).click();
 await expect(page.getByRole('dialog').locator('.library-list button').filter({hasText:'0.1+0.2'})).toBeVisible();
 assert.equal((await request('GET',`/api/conversations/${eligible.id}`)).title,'تعديل من نافذة أخرى');
 passed('page reload retains title and destination through stored records');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 await writeFile(resolve(output,'report.json'),JSON.stringify({at:new Date().toISOString(),checks,passed:checks.length,failed:0,real_built_ui:true,real_authenticated_http:true,real_sqlite:true,real_calculator:true,model_provider_used:false,desktop_native_tested:false,page_errors:errors,external_requests:external,scratch},null,2));
 console.log(`Evidence: ${output}`);
}catch(error){
 await writeFile(resolve(output,'report.json'),JSON.stringify({at:new Date().toISOString(),checks,failed:1,error:String(error),page_errors:errors,external_requests:external,scratch},null,2));
 throw error;
}finally{
 if(browser)await browser.close();
 // This script owns only this child, never a user service or another test port.
 child.kill();await writeFile(resolve(output,'server.log'),serverLog);
}
