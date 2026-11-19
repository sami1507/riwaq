import {chromium,expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const origin='http://127.0.0.1:8765';
const browser=await chromium.launch({channel:'chrome',headless:true});
const context=await browser.newContext();const page=await context.newPage();const errors=[],external=[],checks=[],controlChecks=[];
page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(origin)&&!r.url().startsWith('data:')&&!r.url().startsWith('blob:'))external.push(r.url());});
await mkdir('reports/identity',{recursive:true});
function lum(rgb){return rgb.map(x=>{x/=255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4}).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);}
function rgb(value){return value.match(/[\d.]+/g).slice(0,3).map(Number);}
function contrast(a,b){let x=lum(rgb(a)),y=lum(rgb(b));return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}
try{
 for(const theme of ['light','dark'])for(const width of [360,390,768,1280,1440]){
  await page.setViewportSize({width,height:width<500?844:1000});
  await page.goto(origin);await page.evaluate(theme=>{localStorage.setItem('riwaq.v1.theme',theme);localStorage.setItem('riwaq.v1.lang','ar');localStorage.removeItem('riwaq.v1.conversation');},theme);await page.reload();await page.locator('.app[aria-busy="false"]').waitFor();await page.evaluate(()=>document.fonts.ready);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,theme+' '+width+' overflow');
  await expect(page.getByRole('heading',{name:'بماذا نبدأ اليوم؟'})).toBeVisible();
  const composer=await page.locator('.composer').boundingBox();assert(composer&&composer.y>=0&&composer.y+composer.height<=((width<500?844:1000)+1));
  const ratios=await page.evaluate(()=>{const c=getComputedStyle(document.documentElement);const nodes=['.welcome h1','.welcome-copy','.new-chat','.composer textarea'];return nodes.map(selector=>{const e=document.querySelector(selector),s=getComputedStyle(e);return {selector,color:s.color,bg:selector==='.new-chat'?s.backgroundColor:selector==='.composer textarea'?c.getPropertyValue('--color-surface'):c.getPropertyValue('--color-background')};});});
  for(const r of ratios){const color=r.bg.trim().startsWith('#')?'rgb('+[1,3,5].map(i=>parseInt(r.bg.trim().slice(i,i+2),16)).join(',')+')':r.bg;r.ratio=contrast(r.color,color);assert(r.ratio>=4.5,JSON.stringify(r));}
  const controls=await page.evaluate(()=>{const root=getComputedStyle(document.documentElement),composer=getComputedStyle(document.querySelector('.composer'));return {border:composer.borderColor,focus:root.getPropertyValue('--color-focus').trim(),background:root.getPropertyValue('--color-background').trim(),surface:root.getPropertyValue('--color-surface').trim()};});
  const asRgb=value=>value.startsWith('#')?'rgb('+[1,3,5].map(i=>parseInt(value.slice(i,i+2),16)).join(',')+')':value;
  const borderRatio=Math.min(contrast(asRgb(controls.border),asRgb(controls.surface)),contrast(asRgb(controls.border),asRgb(controls.background)));const focusRatio=Math.min(contrast(asRgb(controls.focus),asRgb(controls.surface)),contrast(asRgb(controls.focus),asRgb(controls.background)));assert(borderRatio>=3);assert(focusRatio>=3);controlChecks.push({theme,width,borderRatio,focusRatio});
  await page.screenshot({path:`reports/identity/welcome-${theme}-${width}.png`,fullPage:true});checks.push({theme,width,noOverflow:true,composerVisible:true,ratios});
 }
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'مشاريعي',exact:true}).click();await expect(page.locator('.sidebar')).toHaveClass(/open/);await expect(page.locator('.workspace')).toHaveAttribute('inert','');await page.locator('.sidebar-scrim').click({position:{x:5,y:10}});
 assert.equal(await page.locator('.sidebar').getAttribute('inert'),'');
 await page.setViewportSize({width:1440,height:1000});await page.getByRole('button',{name:'الإعدادات',exact:true}).click();
 const dialog=page.getByRole('dialog');await expect(dialog).toBeVisible();await page.getByLabel('المظهر',{exact:true}).selectOption('system');await page.emulateMedia({colorScheme:'dark'});await expect(page.locator('html')).toHaveAttribute('data-theme','dark');await page.emulateMedia({colorScheme:'light'});await expect(page.locator('html')).toHaveAttribute('data-theme','light');
 await page.getByLabel('عرض الأرقام').selectOption('arab');await page.getByLabel('عرض التاريخ').selectOption('iso');
 await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);await page.reload();assert.equal(await page.evaluate(()=>localStorage.getItem('riwaq.v1.digits')),'arab');assert.equal(await page.evaluate(()=>localStorage.getItem('riwaq.v1.dates')),'iso');
 await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await page.locator('.new-chat').evaluate(e=>getComputedStyle(e).transitionDuration),'0s');
 await page.evaluate(()=>{document.documentElement.style.fontSize='32px'});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.evaluate(()=>{document.documentElement.style.fontSize=''});
 await page.getByRole('button',{name:'الإعدادات',exact:true}).click();await page.locator('.language-select').last().selectOption('en');await page.keyboard.press('Escape');await expect(page.locator('html')).toHaveAttribute('dir','ltr');await expect(page.getByRole('heading',{name:'Where shall we begin today?'})).toBeVisible();await page.screenshot({path:'reports/identity/welcome-en.png',fullPage:true});
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);await writeFile('reports/identity-layout.json',JSON.stringify({passed:true,checks,controlChecks,systemTheme:true,persistedDisplay:true,reducedMotion:true,rootTextSize200Percent:true,englishLTR:true,external,pageErrors:errors,screenReader:'not tested with a screen reader application'},null,2));console.log(JSON.stringify({passed:true,layouts:checks.length,external:external.length}));
}finally{await browser.close();}
