import { chromium, expect } from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const root='http://127.0.0.1:8765';
const out=fileURLToPath(new URL('../../reports/',import.meta.url));
await mkdir(out,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const dialog=page.getByRole('dialog');
const errors=[],outgoing=[],mutations=[];
const arabic='الإجابة من المعرفة تجريبية: قد يفسّر النموذج المقاطع خطأ. راجع الشواهد الأصلية.';
const english='Knowledge-grounded answers are experimental: the model can misread excerpts. Review the original evidence.';
page.on('pageerror',error=>errors.push(error.message));
page.on('request',request=>{
 if(!request.url().startsWith(root)&&!request.url().startsWith('blob:')&&!request.url().startsWith('data:'))outgoing.push(request.url());
 if(!['GET','HEAD','OPTIONS'].includes(request.method()))mutations.push({method:request.method(),url:request.url()});
});
async function screenshot(name){await page.screenshot({path:out+'/'+name,fullPage:true,animations:'disabled'});}
async function noOverflow(){assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'No horizontal overflow');}
try{
 await page.goto(root);
 await page.locator('.app[aria-busy="false"]').waitFor();
 assert.equal(await page.locator('html').getAttribute('dir'),'rtl');
 await expect(page.locator('.composer-knowledge-notice')).toHaveCount(0);
 await page.locator('.source-toggle.public').click();
 await expect(page.locator('.composer-knowledge-notice')).toBeVisible();
 await expect(page.locator('.composer-knowledge-notice')).toHaveText(arabic);
 await expect(page.getByPlaceholder('ما الذي تريد العمل عليه؟')).toHaveAttribute('aria-describedby','knowledge-answer-note');
 await screenshot('knowledge-notice-ar-chat.png');
 await page.getByRole('button',{name:'مكتبة المعرفة',exact:true}).click();
 await expect(dialog.locator('.knowledge-source-card')).not.toHaveCount(0);
 await expect(dialog.locator('.knowledge-answer-notice')).toHaveText(arabic);
 await screenshot('knowledge-notice-ar-library.png');
 await dialog.getByRole('button',{name:'البحث المحلي',exact:true}).click();
 await expect(dialog.locator('.knowledge-answer-notice')).toBeVisible();
 await expect(dialog.locator('.knowledge-answer-notice')).toHaveText(arabic);
 await screenshot('knowledge-notice-ar-search.png');
 await page.setViewportSize({width:390,height:844});
 await expect(dialog.locator('.knowledge-answer-notice')).toBeVisible();
 await noOverflow();
 await screenshot('knowledge-notice-ar-mobile.png');
 await dialog.getByRole('button',{name:'Close / إغلاق',exact:true}).click();
 await page.setViewportSize({width:1440,height:1000});
 await page.getByRole('button',{name:'الإعدادات',exact:true}).click();
 await dialog.getByRole('combobox').last().selectOption('en');
 await dialog.getByRole('button',{name:'Close / إغلاق',exact:true}).click();
 await expect(page.locator('.composer-knowledge-notice')).toHaveText(english);
 await page.getByRole('button',{name:'Dark',exact:true}).click();
 await screenshot('knowledge-notice-en-dark.png');
 await page.getByRole('button',{name:'Knowledge library',exact:true}).click();
 await expect(dialog.locator('.knowledge-answer-notice')).toHaveText(english);
 await noOverflow();
 await screenshot('knowledge-notice-en-library.png');
 assert.deepEqual(errors,[]);assert.deepEqual(outgoing,[]);assert.deepEqual(mutations,[]);
 await writeFile(out+'/knowledge-notice-browser.json',JSON.stringify({passed:true,read_only:true,arabic_rtl:true,english_ltr:true,public_mode_notice:true,library_notice:true,search_notice:true,mobile_overflow:false,dark_mode:true,page_errors:errors,external_requests:outgoing,mutation_requests:mutations},null,2));
 console.log('KNOWLEDGE NOTICE READ-ONLY UI TEST PASSED');
}finally{await browser.close();}
