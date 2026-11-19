import { chromium, expect } from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import { writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const browser=await chromium.launch({channel:'chrome',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000}});
const outgoing=[];page.on('request',request=>{if(!request.url().startsWith('http://127.0.0.1:8765'))outgoing.push(request.url());});
const passphrase='Backup-test-phrase-2026-only';let encryptedRequest='';
page.on('request',request=>{if(request.method()==='PUT'&&request.url().includes('/sync/snapshots/'))encryptedRequest=request.postData()??'';});
try{
 await page.goto('http://127.0.0.1:8765');await page.locator('.app[aria-busy="false"]').waitFor();
 await page.getByRole('button',{name:'الإعدادات',exact:true}).click();await page.getByRole('dialog').getByRole('combobox').last().selectOption('en');
 await page.getByRole('dialog').getByRole('button',{name:'Encrypted project backup'}).click();
 await expect(page.getByRole('dialog')).toContainText('Server backup is blocked in Local only mode');
 await page.getByRole('button',{name:'Open settings',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Hybrid',exact:true}).click();
 await expect(page.getByRole('dialog').locator('.mode-selector .selected')).toHaveText('Hybrid');await page.getByRole('button',{name:'Close / إغلاق'}).click();
 await page.getByRole('button',{name:'New project',exact:true}).click();await page.getByRole('dialog').getByLabel('Name',{exact:true}).fill(`Encrypted browser test ${Date.now()}`);
 await page.getByRole('dialog').getByLabel('Project instructions',{exact:true}).fill('Private Arabic evidence: اللون المفضل أزرق.');await page.getByRole('dialog').getByRole('button',{name:'Create',exact:true}).click();
 await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Encrypted project backup'}).click();
 const dialog=page.getByRole('dialog');await expect(dialog.locator('.sync-endpoint')).toContainText('127.0.0.1:8766');
 await dialog.getByRole('button',{name:'New account',exact:true}).click();await dialog.getByRole('textbox',{name:'Email',exact:true}).fill(`ui-${Date.now()}@example.test`);
 await dialog.getByLabel('Account password',{exact:true}).fill('Test-only-Account-password-2026');await dialog.getByRole('button',{name:'Create account',exact:true}).click();
 await expect(dialog).toContainText('Account session connected');await dialog.getByLabel('Backup passphrase',{exact:true}).fill(passphrase);await dialog.getByLabel('Confirm backup passphrase',{exact:true}).fill(passphrase);
 await dialog.getByRole('button',{name:'Encrypt & upload backup',exact:true}).click();await expect(dialog.locator('.sync-success')).toContainText('Encrypted backup saved');
 assert.ok(encryptedRequest.includes('ciphertext'));assert.ok(!encryptedRequest.includes(passphrase));assert.ok(!encryptedRequest.includes('اللون المفضل'));assert.ok(!encryptedRequest.includes('Private Arabic evidence'));
 await dialog.locator('.sync-list').getByRole('button',{name:'Restore',exact:true}).click();await dialog.getByLabel('Backup passphrase',{exact:true}).fill('This-is-the-wrong-passphrase');
 await dialog.getByRole('button',{name:'Confirm restore as new project'}).click();await expect(dialog.locator('.inline-error')).toContainText('incorrect or the encrypted backup is damaged');
 await dialog.getByLabel('Backup passphrase',{exact:true}).fill(passphrase);const before=await page.locator('.project-item').count();
 await dialog.getByRole('button',{name:'Confirm restore as new project'}).click();await expect(dialog.locator('.sync-success')).toContainText('Restored as a new project');await expect(page.locator('.project-item')).toHaveCount(before+1);
 await dialog.getByRole('button',{name:'Sign out',exact:true}).click();await expect(dialog).toContainText('Sign in to the server');
 assert.deepEqual(outgoing,[]);
 await writeFile(fileURLToPath(new URL('../../reports/sync-browser.json',import.meta.url)),JSON.stringify({passed:true,local_mode_blocks:true,real_local_sync_server:true,registered:true,encrypted_upload:true,no_plaintext_in_upload:true,wrong_passphrase_rejected:true,restored_new_project:true,signed_out:true,external_browser_requests:outgoing},null,2));
 console.log('SYNC BROWSER PASSED');
}finally{
 await page.request.patch('http://127.0.0.1:8765/api/settings',{headers:{'X-Riwaq-Client':'local'},data:{mode:'local',provider:'ollama'}}).catch(()=>{});
 await browser.close();
}
