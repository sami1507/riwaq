// Visual identity evidence against the existing local QA app. No app code edits,
// dependency installation, production data, external requests or model calls.
import {chromium,expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';

const root=fileURLToPath(new URL('../../',import.meta.url)),base=process.env.RIWAQ_URL;
assert.equal(base,'http://127.0.0.1:8767','Only the synthetic QA service on8767 is permitted.');
assert.equal(path.resolve(process.env.RIWAQ_DATA_DIR??''),path.resolve(root,'reports/completion-live-data'),'Only reports/completion-live-data is permitted.');
const phase=process.argv[process.argv.indexOf('--phase')+1];assert(['before','after'].includes(phase),'Pass --phase before or --phase after.');
const dateRoot=path.join(root,'reports/identity-20261116'),out=path.join(dateRoot,phase,new Date().toISOString().replaceAll(':','-'));
await mkdir(out,{recursive:true});
const runFile=promisify(execFile),python=path.join(root,'.venv/Scripts/python.exe'),headers={'X-Riwaq-Client':'local'};
const report={started_at:new Date().toISOString(),phase,base,data_directory:'reports/completion-live-data',browser:'',backend_mocked:false,transport_mocked:false,model_calls:0,synthetic_chat_fixture:true,fixture_notice:'Assistant messages are controlled visual test content persisted to a synthetic QA conversation, not generated model answers.',screenshots:[],checks:[],issues:[],page_errors:[],external_requests:[],native_windows_app_tested:false,screen_reader_application_tested:false};
let browser,context,page,fixture,stage='startup',language='ar',theme='light',width=1440;
const t=(ar,en)=>language==='ar'?ar:en;
const safe=error=>String(error?.stack??error).replace(/[A-Za-z0-9_-]{40,}/g,'[redacted]').slice(0,6000);
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const check=(name,passed,details={})=>{report.checks.push({name,passed,...details});if(!passed)report.issues.push({name,...details});};
async function api(route,options={}){const response=await context.request.fetch(base+'/api'+route,{...options,headers:{...headers,...options.headers},timeout:20000});assert(response.ok(),route+': HTTP '+response.status());return response.status()===204?null:response.json();}
async function ready(){await page.locator('.app[aria-busy="false"]').waitFor();await page.evaluate(()=>document.fonts.ready);}
async function view({lang='ar',color='light',size=1440,chat=false}={}){
 language=lang;theme=color;width=size;await page.setViewportSize({width:size,height:size<600?844:1000});
 await page.evaluate(({projectId,conversationId,lang,color,chat})=>{localStorage.setItem('riwaq.v1.project',projectId);localStorage.setItem('riwaq.v1.lang',lang);localStorage.setItem('riwaq.v1.theme',color);if(chat)localStorage.setItem('riwaq.v1.conversation',conversationId);else localStorage.removeItem('riwaq.v1.conversation');},{...fixture,lang,color,chat});
 await page.reload();await ready();await expect(page.locator('html')).toHaveAttribute('dir',lang==='ar'?'rtl':'ltr');await expect(page.locator('html')).toHaveAttribute('data-theme',color);
}
async function screenshot(name){
 const filename=`${name}-${language}-${theme}-${width}.png`;await page.screenshot({path:path.join(out,filename),fullPage:true,animations:'disabled'});
 const metrics=await page.evaluate(()=>{const root=document.documentElement,c=document.querySelector('.composer')?.getBoundingClientRect(),dock=document.querySelector('.composer-area')?.getBoundingClientRect(),dialog=document.querySelector('[role=dialog]'),panel=document.querySelector('.project-panel');return {viewport:{width:innerWidth,height:innerHeight},horizontalOverflow:root.scrollWidth>innerWidth+1,composer:c?{x:c.x,y:c.y,width:c.width,height:c.height,bottom:c.bottom}:null,composerDock:dock?{y:dock.y,height:dock.height,viewportShare:dock.height/innerHeight}:null,dialogOverflow:dialog?dialog.scrollWidth>dialog.clientWidth+1:false,panelOverflow:panel?panel.scrollWidth>panel.clientWidth+1:false,direction:root.dir,font:getComputedStyle(root).fontFamily};});
 report.screenshots.push({name,filename,language,theme,width,metrics});check(`${name}-${language}-${theme}-${width}:horizontal-overflow`,!metrics.horizontalOverflow&&!metrics.dialogOverflow&&!metrics.panelOverflow,{metrics});return metrics;
}
async function menu(){if(width<761){await page.getByRole('button',{name:t('مشاريعي','PROJECTS'),exact:true}).click();await expect(page.locator('.sidebar')).toHaveClass(/open/);}}
async function closePanel(){const panel=page.locator('.project-panel');if(await panel.isVisible())await panel.locator('.panel-title button').click();}
async function settings(){await menu();await page.getByRole('button',{name:t('الإعدادات','Settings'),exact:true}).click();await expect(page.getByRole('dialog',{name:t('الإعدادات','Settings'),exact:true})).toBeVisible();}
async function checkSidebarFooter(name){
 const control=page.locator('.sidebar .profile-row button');await control.scrollIntoViewIfNeeded();await control.focus();await page.keyboard.press('Shift+Tab');await page.keyboard.press('Tab');await expect(control).toBeFocused();const metrics=await control.evaluate(e=>{const r=e.getBoundingClientRect(),sidebar=e.closest('.sidebar'),s=sidebar.getBoundingClientRect();return {control:{top:r.top,bottom:r.bottom,left:r.left,right:r.right},sidebar:{top:s.top,bottom:s.bottom,scrollTop:sidebar.scrollTop,clientHeight:sidebar.clientHeight,scrollHeight:sidebar.scrollHeight,overflowY:getComputedStyle(sidebar).overflowY},viewport:{width:innerWidth,height:innerHeight},focused:document.activeElement===e};});check(name,metrics.focused&&metrics.control.top>=Math.max(0,metrics.sidebar.top)&&metrics.control.bottom<=Math.min(metrics.viewport.height,metrics.sidebar.bottom)+1&&metrics.control.left>=0&&metrics.control.right<=metrics.viewport.width,{metrics});await screenshot(name);
}
async function checkProjectViewport(){
 const panel=page.locator('.sidebar-scroll'),first=panel.locator('.project-item').first();await expect(first).toBeInViewport({ratio:1});const measure=element=>element.evaluate(e=>{const r=e.getBoundingClientRect(),p=e.closest('.sidebar-scroll').getBoundingClientRect();return {item:{top:r.top,bottom:r.bottom,left:r.left,right:r.right},panel:{top:p.top,bottom:p.bottom,left:p.left,right:p.right},viewport:{height:innerHeight,width:innerWidth}};});const inside=m=>m.item.top>=Math.max(0,m.panel.top)-1&&m.item.bottom<=Math.min(m.viewport.height,m.panel.bottom)+1&&m.item.left>=Math.max(0,m.panel.left)-1&&m.item.right<=Math.min(m.viewport.width,m.panel.right)+1;let metrics=await measure(first);check(`first-project-row-fully-visible-${theme}-${width}`,inside(metrics),{metrics});const heading=panel.locator('.chat-heading');if(await heading.count()){await heading.scrollIntoViewIfNeeded();await expect(heading).toBeInViewport({ratio:1});metrics=await measure(heading);check(`conversation-heading-scroll-accessible-${theme}-${width}`,inside(metrics),{metrics});}await panel.evaluate(e=>{e.scrollTop=0;e.closest('.sidebar').scrollTop=0;});
}
try{
 browser=await chromium.launch({channel:'chrome',headless:true});report.browser=browser.version();context=await browser.newContext({viewport:{width:1440,height:1000}});page=await context.newPage();page.setDefaultTimeout(15000);
 page.on('pageerror',error=>report.page_errors.push(safe(error)));
 await context.route('**/*',async route=>{const url=new URL(route.request().url());if(/^https?:$/.test(url.protocol)&&url.origin!==base){report.external_requests.push(url.origin+url.pathname);await route.abort('blockedbyclient');}else await route.continue();});
 await context.addInitScript(()=>{if(!sessionStorage.getItem('identity.v2.qa')){localStorage.setItem('riwaq.v1.lang','ar');localStorage.setItem('riwaq.v1.theme','light');sessionStorage.setItem('identity.v2.qa','1');}});
 stage='pairing';await page.goto(base);await expect(page.locator('#riwaq-pairing-code')).toBeVisible();await page.screenshot({path:path.join(out,'pairing-ar-light-1440.png'),fullPage:true,animations:'disabled'});
 if(phase==='after'){
  for(const color of ['light','dark'])for(const size of [1440,390]){theme=color;width=size;await page.setViewportSize({width:size,height:size<600?844:1000});await page.evaluate(color=>localStorage.setItem('riwaq.v1.theme',color),color);await page.reload();await expect(page.locator('#riwaq-pairing-code')).toBeVisible();await page.evaluate(()=>document.fonts.ready);await screenshot('pairing');}
  theme='light';width=1440;await page.setViewportSize({width:1440,height:1000});await page.evaluate(()=>localStorage.setItem('riwaq.v1.theme','light'));await page.reload();await expect(page.locator('#riwaq-pairing-code')).toBeVisible();
 }
 let pair;try{pair=await runFile(python,['-X','utf8','-B','-c','from backend.local_auth import issue_pairing_code; print(issue_pairing_code())'],{cwd:root,env:{...process.env},windowsHide:true,timeout:15000,maxBuffer:4096});}catch{throw Error('Synthetic pairing failed; secret output omitted.');}
 let code=pair.stdout.trim();pair.stdout='';pair.stderr='';assert(/^[A-Za-z0-9_-]{43}$/.test(code));await page.locator('#riwaq-pairing-code').fill(code);code='';await page.getByRole('button',{name:'اقرن المتصفح',exact:true}).click();await ready();
 stage='synthetic-fixture';const fixturePath=path.join(dateRoot,'fixture.json');
 try{fixture=JSON.parse(await readFile(fixturePath,'utf8'));const project=await api('/projects/'+fixture.projectId);assert(project.name.startsWith('Identity QA 20261116'),'Fixture project prefix did not match.');await api('/conversations/'+fixture.conversationId);}
 catch(error){if(error.code!=='ENOENT')throw error;
  const project=await api('/projects',{method:'POST',data:{name:'Identity QA 20261116 — مشروع واجهة',instructions:'Original synthetic visual acceptance only. No model or external action is requested.'}});
  const chat=await api('/conversations',{method:'POST',data:{project_id:project.id,title:'عينة محادثة طويلة — العربية وTypeScript'}});
  const sourceText='وثيقة اصطناعية لفحص واجهة رواق.\nحد الضغط التجريبي 42 kPa.\nالمعرّف 001 والتاريخ 2026-11-16 ثابتان في هذه العينة.\nلا تصف هذه البيانات جهازًا أو شخصًا حقيقيًا.';
  const upload=await context.request.post(base+`/api/projects/${project.id}/files`,{headers,multipart:{file:{name:'دليل واجهة رواق — عينة اختبار.txt',mimeType:'text/plain',buffer:Buffer.from(sourceText)}},timeout:20000});assert(upload.ok(),'Synthetic text upload HTTP '+upload.status());const file=await upload.json();
  fixture={projectId:project.id,conversationId:chat.id,fileId:file.id};await writeFile(fixturePath,JSON.stringify(fixture,null,2));
  const seeded=await runFile(python,['-X','utf8','-B','-c',String.raw`
import json,os,sys
from pathlib import Path
from backend.db import get_db,uid,now
root=Path.cwd(); expected=root/'reports/completion-live-data'
assert Path(os.environ['RIWAQ_DATA_DIR']).resolve()==expected.resolve()
fixture=json.loads(Path(sys.argv[1]).read_text('utf-8'))
with get_db() as c:
 project=c.execute('SELECT * FROM projects WHERE id=?',(fixture['projectId'],)).fetchone(); assert project and project['name'].startswith('Identity QA 20261116')
 chat=c.execute('SELECT * FROM conversations WHERE id=? AND project_id=?',(fixture['conversationId'],fixture['projectId'])).fetchone(); assert chat
 assert c.execute('SELECT count(*) FROM messages WHERE conversation_id=?',(chat['id'],)).fetchone()[0]==0
 rows=c.execute('SELECT * FROM knowledge_chunks WHERE file_id=? AND project_id=? ORDER BY rowid',(fixture['fileId'],fixture['projectId'])).fetchall(); assert rows
 citations=[{'id':r['id'],'file_id':r['file_id'],'filename':'دليل واجهة رواق — عينة اختبار.txt','scope':'private','text':r['text'],'line_start':r['line_start'],'line_end':r['line_end']} for r in rows[:1]]
 body='''## عينة عرض اصطناعية — وليست إجابة مولّدة

نراجع وضوح الفكرة، وتسلسل الخطوات، وإمكانية العودة إلى الدليل. **الضغط التجريبي 42 kPa**، والمعرّف §001§، والتاريخ §2026-11-16§ محفوظة دون تبديل الأرقام. [S1]

### الفكرة والخطوة التالية

تعرض هذه الفقرة العربية كيف تتجاور المصطلحات مع TypeScript وAPI v2.1، ومع العبارة العبرية שלום — בדיקת ממשק. المهم أن تبقى القراءة مريحة، وأن يميز المستخدم بين النص العادي والمرجع والشفرة.

1. اقرأ المصدر قبل اعتماد الرقم.
2. راجع القيود والنتيجة المطلوبة.
3. احفظ التعديل مع وصف يمكن مراجعته لاحقًا.

| البند | القيمة | الملاحظة |
| --- | ---: | --- |
| الطلبات | 1234 | قيمة اختبار |
| النسبة | 25% | ليست إحصاءً إنتاجيًا |
| السعر | 199 ₪ | صفر رسوم فعلية في العينة |

§§§typescript
// شفرة للعرض فقط — لا تُنفّذ في هذه المحادثة
type Project = { id: string; title: string };
const project: Project = { id: "001", title: "رواق Riwaq" };
function titleFor(project: Project): string {
  return §project.id + ": " + project.title§;
}
§§§

> لا تثبت هذه العينة جودة نموذج أو جاهزية إنتاجية. إنها بيانات مصرح بها لقياس واجهة القراءة والاستشهادات فقط.

### خلاصة قابلة للمراجعة

تسهل العناوين والمسافات فهم المحتوى الطويل. يبقى النص الأصلي بجوار مرجعه، بينما يمكن عرض معلومات التنفيذ عند الحاجة دون إغراق مساحة القراءة.
'''.replace('§',chr(96))
 ids=[]
 for index in range(3):
  for role,content in [('user',f'هذه عينة اختبار واجهة {index+1}. اعرض نصًا عربيًا منظمًا مع شفرة وجدول ومصدر.'),('assistant',body)]:
   mid=uid(); c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',(mid,chat['id'],role,content,'complete','visual-fixture' if role=='assistant' else None,now(),json.dumps(citations if role=='assistant' else [],ensure_ascii=False))); ids.append(mid)
 print(json.dumps({'message_ids':ids,'synthetic':True}))
`,fixturePath],{cwd:root,env:{...process.env},windowsHide:true,timeout:15000,maxBuffer:5000});fixture.messageIds=JSON.parse(seeded.stdout).message_ids;await writeFile(fixturePath,JSON.stringify(fixture,null,2));
 }
 report.fixture=fixture;const index=await context.request.get(base);const html=await index.text();report.served_index_sha256=hash(html);report.served_assets=[...html.matchAll(/(?:src|href)="([^"]+\.(?:js|css))"/g)].map(match=>match[1]);
 stage='baseline-views';
 for(const color of ['light','dark'])for(const size of [1440,390]){
  await view({color,size});const empty=await screenshot('empty-chat');check(`composer-visible-${color}-${size}`,Boolean(empty.composer&&empty.composer.y>=0&&empty.composer.bottom<=empty.viewport.height+1));
  if(phase==='after'&&size===1440){const chrome=await page.evaluate(()=>({icon:document.querySelector('link[data-riwaq-icon]')?.getAttribute('href'),theme:document.querySelector('meta[name="theme-color"]')?.getAttribute('content'),background:getComputedStyle(document.documentElement).getPropertyValue('--color-background').trim()}));const expected=`/brand/riwaq-symbol-${color}.svg`;check(`favicon-and-theme-${color}`,chrome.icon===expected&&chrome.theme?.toLowerCase()===chrome.background.toLowerCase(),{chrome});const icon=await context.request.get(base+expected),actual=await icon.body(),source=await readFile(path.join(root,'frontend/public',expected));check(`served-favicon-matches-source-${color}`,icon.ok()&&hash(actual)===hash(source),{path:expected,sha256:hash(actual)});}
  if(size<761){await menu();if(phase==='after')await checkProjectViewport();await screenshot('projects');check(`mobile-sidebar-inert-${color}`,await page.locator('.workspace').getAttribute('inert')!==null);await page.locator('.sidebar-scrim').click({position:{x:5,y:10}});}else if(phase==='after')await checkProjectViewport();
  await page.getByRole('button',{name:'إضافة ملف خاص بالمشروع',exact:true}).click();await expect(page.locator('.project-panel')).toBeVisible();await expect(page.locator('.project-panel .file-row')).toContainText('دليل واجهة رواق');await screenshot('files');await closePanel();
  await settings();await screenshot('settings');let contained=true;for(let n=0;n<12;n++){await page.keyboard.press('Tab');contained=contained&&await page.evaluate(()=>Boolean(document.activeElement?.closest('[role=dialog]')));}check(`settings-focus-contained-${color}-${size}`,contained);await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toHaveCount(0);
  await view({color,size,chat:true});await expect(page.locator('.message.assistant')).toHaveCount(3);const answer=page.locator('.message.assistant').last();await answer.locator('pre').scrollIntoViewIfNeeded();check(`code-ltr-${color}-${size}`,await answer.locator('pre').evaluate(node=>getComputedStyle(node).direction)==='ltr');await screenshot('long-chat-code');
  await answer.locator('.evidence-marker').first().click();await expect(answer.locator('.citation-card[open]')).toBeVisible();await answer.locator('.citation-card[open]').scrollIntoViewIfNeeded();await screenshot('long-chat-citation');
 }
 stage='danger-hover';report.danger_contrast=[];
 for(const color of ['light','dark']){
  await view({color,size:1440,chat:true});await page.getByRole('button',{name:'حذف: عينة محادثة طويلة — العربية وTypeScript',exact:true}).click();const danger=page.locator('[role=dialog] .button.danger');await expect(danger).toBeVisible();
  for(const state of ['default','hover']){if(state==='hover'){await danger.hover();await page.waitForTimeout(250);}else await page.mouse.move(8,8);const styles=await danger.evaluate(e=>({color:getComputedStyle(e).color,background:getComputedStyle(e).backgroundColor}));
   const lum=value=>value.match(/[\d.]+/g).slice(0,3).map(Number).map(n=>{const c=n/255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4;}).reduce((sum,n,i)=>sum+n*[.2126,.7152,.0722][i],0);const a=lum(styles.color),b=lum(styles.background),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);report.danger_contrast.push({theme:color,state,...styles,ratio});check(`danger-text-contrast-${color}-${state}`,ratio>=4.5,{ratio,...styles});}
  await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toHaveCount(0);await api('/conversations/'+fixture.conversationId);
 }
 stage='ltr-focus-zoom';await view({lang:'en',color:'light',size:1440,chat:true});await page.locator('.message.assistant').last().locator('pre').scrollIntoViewIfNeeded();await screenshot('long-chat-ltr');
 if(phase==='after'){
  const mixed=page.locator('.message.assistant').last().locator('.markdown p').filter({hasText:'שלום — בדיקת ממשק'});await mixed.evaluate(e=>e.setAttribute('data-identity-font-probe','mixed'));await mixed.scrollIntoViewIfNeeded();await screenshot('mixed-scripts');const cdp=await context.newCDPSession(page);await cdp.send('DOM.enable');await cdp.send('CSS.enable');const doc=await cdp.send('DOM.getDocument'),node=await cdp.send('DOM.querySelector',{nodeId:doc.root.nodeId,selector:'[data-identity-font-probe="mixed"]'}),fonts=await cdp.send('CSS.getPlatformFontsForNode',{nodeId:node.nodeId});report.mixed_script_fonts=fonts.fonts;check('hebrew-uses-bundled-font',fonts.fonts.some(font=>font.familyName==='IBM Plex Sans Hebrew'&&font.isCustomFont&&font.glyphCount>0),{fonts:fonts.fonts});await cdp.detach();
 }
 await page.locator('.composer textarea').focus();await page.keyboard.press('Tab');const focus=await page.evaluate(()=>{const e=document.activeElement,s=getComputedStyle(e);return {tag:e.tagName,label:e.getAttribute('aria-label')||e.textContent?.trim().slice(0,80),outline:s.outlineStyle,width:s.outlineWidth,color:s.outlineColor,rect:{width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height}};});check('keyboard-visible-focus',focus.outline!=='none'&&parseFloat(focus.width)>0,{focus});
 await page.emulateMedia({reducedMotion:'reduce'});const motion=await page.locator('.new-chat').evaluate(e=>({transition:getComputedStyle(e).transitionDuration,animation:getComputedStyle(e).animationDuration}));check('reduced-motion',motion.transition.split(',').every(v=>parseFloat(v)===0)&&motion.animation.split(',').every(v=>parseFloat(v)===0),{motion});
 if(phase==='after'){for(const height of [900,1000]){await view({lang:'en',color:'light',size:1440});await page.setViewportSize({width:1440,height});await checkSidebarFooter(`sidebar-footer-en-${height}`);}}
 await view({lang:'ar',color:'light',size:1280});await page.evaluate(()=>{document.documentElement.style.fontSize='32px';});const zoom=await screenshot('text-size-200pct');check('text-size-200pct-overflow',!zoom.horizontalOverflow);if(phase==='after')await checkSidebarFooter('sidebar-footer-text-size-200pct');await page.evaluate(()=>{document.documentElement.style.fontSize='';});
 if(phase==='after'){
  stage='composer-options';await view({lang:'ar',color:'light',size:390});const options=page.locator('.composer-options'),summary=options.locator(':scope > summary');await expect(options).not.toHaveAttribute('open','');await expect(summary).toContainText('خيارات الرسالة');await page.locator('.composer textarea').fill('عينة رفض مخطط غير صالح؛ لا ترسلها إلى النموذج.');
  await expect(options.locator('.composer-settings')).toBeHidden();await summary.focus();await page.keyboard.press('Enter');await expect(options.locator('.composer-settings')).toBeVisible();check('message-options-native-keyboard-toggle',true);await page.getByRole('checkbox',{name:'بحث بالمصادر داخل المحادثة',exact:true}).check();await summary.click();await expect(summary).toContainText('البحث مفعّل');await summary.click();await expect(page.getByRole('checkbox',{name:'بحث بالمصادر داخل المحادثة',exact:true})).toBeChecked();await page.getByRole('checkbox',{name:'بحث بالمصادر داخل المحادثة',exact:true}).uncheck();
  await page.getByRole('checkbox',{name:'تفعيل أدوات الاستدلال التجريبية',exact:true}).check();await summary.click();await expect(summary).toContainText('الاستدلال مفعّل');await summary.click();await expect(page.getByRole('checkbox',{name:'تفعيل أدوات الاستدلال التجريبية',exact:true})).toBeChecked();await page.getByRole('checkbox',{name:'تنسيق JSON محدد · متقدم',exact:true}).check();await page.getByRole('textbox',{name:'مخطط JSON',exact:true}).fill('{');await summary.click();await expect(summary).toContainText('JSON');
  let generateRequests=0;const observe=request=>{if(new URL(request.url()).pathname.endsWith('/generate')&&request.method()==='POST')generateRequests++;};page.on('request',observe);await page.getByRole('button',{name:'إرسال',exact:true}).click();await expect(summary).toContainText('راجع خطأ التنسيق');check('invalid-contract-blocked-before-network',generateRequests===0,{generateRequests});await screenshot('invalid-format-summary');await summary.click();await expect(page.getByRole('alert').filter({hasText:'صياغة JSON غير صالحة'})).toBeVisible();await expect(page.getByRole('textbox',{name:'مخطط JSON',exact:true})).toHaveValue('{');await expect(page.locator('.composer textarea')).toHaveValue('عينة رفض مخطط غير صالح؛ لا ترسلها إلى النموذج.');check('closed-options-preserve-research-reasoning-json-and-error',true);page.off('request',observe);
  stage='calculator';await page.locator('input[name="riwaq-answer-mode"][value="calculator"]').check();await expect(options).toHaveCount(0);await page.locator('.composer textarea').fill('2 + 2');const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/generate')&&r.request().method()==='POST'),page.getByRole('button',{name:'احسب',exact:true}).click()]);assert.equal(response.status(),200);const body=response.request().postDataJSON();assert.equal(body.answer_mode,'calculator');assert(!('output_contract' in body));const events=(await response.text()).trim().split('\n').map(line=>JSON.parse(line));const started=events.find(event=>event.type==='start');assert(started?.conversation_id);await expect(page.locator('.composer')).not.toHaveClass(/generating/);const saved=await api('/conversations/'+started.conversation_id),answer=saved.messages.findLast(message=>message.role==='assistant');assert(answer);assert.equal(answer.run.model_calls,0);assert(answer.content.includes('4'));check('calculator-still-executes-with-invalid-chat-schema',true,{conversation_id:started.conversation_id,message_id:answer.id,model_calls:answer.run.model_calls});await screenshot('calculator');
  await page.locator('input[name="riwaq-answer-mode"][value="assistant"]').check();await expect(page.locator('.composer textarea')).toHaveValue('عينة رفض مخطط غير صالح؛ لا ترسلها إلى النموذج.');await page.locator('.composer-options > summary').click();await expect(page.getByRole('textbox',{name:'مخطط JSON',exact:true})).toHaveValue('{');check('calculator-switch-retains-chat-draft-and-options',true);
 }
 check('no-page-errors',report.page_errors.length===0,{errors:report.page_errors});check('no-external-requests',report.external_requests.length===0,{requests:report.external_requests});report.status=report.issues.length?'completed_with_findings':'passed';
}catch(error){report.status='failed';report.stage=stage;report.error=safe(error);process.exitCode=1;if(page)await page.screenshot({path:path.join(out,'failure.png'),fullPage:true,mask:[page.locator('#riwaq-pairing-code')]}).catch(()=>{});}
finally{report.finished_at=new Date().toISOString();await writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));if(browser)await browser.close();console.log(JSON.stringify({status:report.status,phase,screenshots:report.screenshots.length,checks:report.checks.length,issues:report.issues.length,stage,report:path.join(out,'report.json')}));}
