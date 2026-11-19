// Controlled, original evaluation content. Never production knowledge or training.
import {chromium} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const out=new URL('../../tests/fixtures/documents/',import.meta.url);await mkdir(out,{recursive:true});
const font=await readFile(new URL('../public/fonts/IBMPlexSansArabic-Regular.woff2',import.meta.url));
const style=`@font-face{font-family:Plex;src:url(data:font/woff2;base64,${font.toString('base64')})}*{box-sizing:border-box}body{font-family:Plex;direction:rtl;margin:0;color:#172B2A;font-size:19px;line-height:1.7}.page{page-break-after:always;padding:30px;height:1000px}.page:last-child{page-break-after:auto}.cols{display:grid;grid-template-columns:1fr 1fr;gap:35px}h1{font-size:26px}img{max-width:100%}`;
const browser=await chromium.launch({channel:'chrome',headless:true});
const report=[];
try{
 const page=await browser.newPage();
 async function pdf(name,html,expected){await page.setContent(`<html lang="ar" dir="rtl"><meta charset="utf-8"><style>${style}</style><body>${html}</body></html>`);await page.evaluate(()=>document.fonts.ready);const bytes=await page.pdf({format:'A4',printBackground:true,margin:{top:'10mm',right:'10mm',bottom:'10mm',left:'10mm'}});await writeFile(new URL(name,out),bytes);report.push({name,sha256:createHash('sha256').update(bytes).digest('hex'),expected});}
 const pages=Array.from({length:12},(_,i)=>`<section class="page"><h1>ملف التقييم — قسم ${i+1}</h1><div class="cols"><div><h2>بيانات المشروع</h2><p>هذا مستند مصطنع لاختبار القراءة، ولا يصف بيانات مستخدم حقيقي.</p><p>تاريخ الإصدار 2026-11-14. تُحفظ المراجع كما هي.</p></div><div><h2>ملاحظة القسم</h2><p>${i===11?'رمز التسليم النهائي للمشروع هو RQ-742. موعد التسليم 18 ديسمبر 2026.':'المراجعة هنا تتناول ترتيب الأقسام وحفظ الأصل فقط.'}</p><p>لا توجد في هذا الملف قيمة لميزانية المشروع.</p></div></div></section>`).join('');
 await pdf('arabic-columns-long.pdf',pages,{pages:12,question:'ما رمز التسليم النهائي للمشروع؟',answer:'RQ-742',answer_page:12,absent:'ميزانية المشروع'});
 const png=await readFile(new URL('ocr-ara.png',out));
 await pdf('arabic-scan-mixed.pdf',`<section class="page"><img src="data:image/png;base64,${png.toString('base64')}"/></section><section class="page"><h1>ملف مختلط</h1><p>القيمة النصية الأصلية 42.</p><img src="data:image/png;base64,${png.toString('base64')}"/></section>`,{pages:2,page1_kind:'scan',page2_kind:'mixed',ocr_text:'يحتوي المشروع على 12 ملفا.'});
 const csv='id,amount,currency\r\n001,0.1,ILS\r\n002,0.2,ILS\r\n003,,ILS\r\n004,8,USD\r\n';await writeFile(new URL('verified-table.csv',out),csv);report.push({name:'verified-table.csv',sha256:createHash('sha256').update(csv).digest('hex'),expected:{range:'B2:B3',exact:'3/10',decimal:'0.3',unit:'ILS',blank:'B4',identifier:'A2'}});
 await writeFile(new URL('pdf-table-reference.json',out),JSON.stringify({license:'CC0-1.0: original controlled fixture text authored for Riwaq tests; embedded IBM Plex retains OFL license.',purpose:'Development fixtures. Not a hidden quality benchmark.',fixtures:report},null,2));
 console.log(JSON.stringify({created:report.map(x=>x.name)}));
}finally{await browser.close();}
