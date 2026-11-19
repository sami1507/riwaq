import {chromium,expect} from '@playwright/test';
import {writeFile} from 'node:fs/promises';
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const page=await browser.newPage({viewport:{width:1280,height:900}});
 await page.addInitScript(()=>localStorage.setItem('riwaq.v1.lang','en'));
 await page.goto('http://127.0.0.1:8765');
 await page.locator('.app[aria-busy="false"]').waitFor();
 await page.getByRole('button',{name:'Images & audio',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await expect(dialog.getByLabel('Image file')).toBeEnabled();
 await dialog.getByLabel('Image file').setInputFiles('reports/vision-fixture.png');
 await dialog.getByLabel('Question about the image').fill('صف الأشكال والألوان بالعربية.');
 await dialog.getByRole('button',{name:'Analyze image locally',exact:true}).click();
 await expect(dialog.locator('.media-result')).toContainText('الأحمر',{timeout:150000});
 const text=await dialog.locator('.media-result > div').innerText();
 await page.screenshot({path:'reports/media-browser.png',fullPage:true});
 await dialog.getByRole('button',{name:'Add text to draft',exact:true}).click();
 await expect(page.getByPlaceholder('What would you like to work on?')).toHaveValue(text);
 await writeFile('reports/media-browser.json',JSON.stringify({passed:true,real_vision:true,explicit_file_upload:true,arabic_response:text,draft_only:true},null,2));
 console.log('MEDIA BROWSER PASSED');
}finally{await browser.close();}
