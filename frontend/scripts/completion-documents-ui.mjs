// Actual modern DocumentWorkbench acceptance. Run only after the QA service
// and reviewed document adapter are frozen; no setup, server start or downloads.
import {chromium,expect} from '../../tests/browser/node_modules/@playwright/test/index.mjs';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const base=process.env.RIWAQ_URL;
assert.equal(base,'http://127.0.0.1:8767','Set RIWAQ_URL to the isolated QA service http://127.0.0.1:8767 exactly.');
assert(process.env.RIWAQ_DATA_DIR&&path.resolve(process.env.RIWAQ_DATA_DIR)===path.resolve(root,'reports/completion-live-data'),
  'Only reports/completion-live-data is permitted; production/native data is forbidden.');
const out=path.join(root,'reports/completion-documents-ui',new Date().toISOString().replaceAll(':','-'));
await mkdir(out,{recursive:true});
const runFile=promisify(execFile),python=path.join(root,'.venv/Scripts/python.exe');
const hash=value=>createHash('sha256').update(value).digest('hex');
const headers={'X-Riwaq-Client':'local'};
const safe=error=>String(error?.stack??error).replace(/[A-Za-z0-9_-]{40,}/g,'[redacted]').slice(0,6000);
const report={started_at:new Date().toISOString(),base,data_directory:'reports/completion-live-data',
  real_backend:true,model_responses_mocked:false,browser_transport_mocked:false,
  synthetic_project_only:true,pairing_codes_saved:false,cookies_saved:false,
  upload_route:'modern /projects/{id}/documents',model_calls_requested:1,
  image_understanding_requested:false,fixtures:[],cases:[],layouts:[],page_errors:[],external_requests:[],
  quality_scope:'Operational UI checks and exact fixture values. OCR CER/WER are one clean fixture only; no general accuracy or independent human review claim.',
  human_review_completed:false};
let browser,context,page,project,stage='startup',lang='en',currentDocument;
const uploaded=[];
const active=status=>['queued','running','processing','cancelling','extracting','ocr','rendering'].includes(status);
const dialog=()=>page.getByRole('dialog',{name:lang==='ar'?'مساحة المستندات':'Document workbench',exact:true});
const docPath=id=>`/projects/${project.id}/documents/${id}`;
const delay=milliseconds=>new Promise(resolve=>setTimeout(resolve,milliseconds));
const record=(name,details={})=>report.cases.push({name,operational_assertions_passed:true,...details});

async function api(route,options={}){
  const response=await context.request.fetch(base+'/api'+route,{...options,headers:{...headers,...options.headers},timeout:Math.min(options.timeout??120000,120000)});
  assert(response.ok(),route+': HTTP '+response.status());
  return response.json();
}

async function openWorkbench(){
  await page.getByRole('button',{name:lang==='ar'?'إضافة ملف خاص بالمشروع':'Add private project file',exact:true}).click();
  await page.getByRole('button',{name:lang==='ar'?'مساحة المستندات':'Document workbench',exact:true}).click();
  await expect(dialog()).toBeVisible();
}

async function selectDocument(filename){
  await dialog().locator('.document-file-list button').filter({hasText:filename}).click();
  await expect(dialog().locator('.document-header h3')).toHaveText(filename);
  await expect(dialog().locator('.document-element-list button').first()).toBeVisible();
}

async function waitDocument(id){
  const deadline=performance.now()+180000;
  const expired=()=>Error('Document processing exceeded 180 seconds; no terminal result was assumed.');
  while(true){
    const remaining=Math.floor(deadline-performance.now());
    if(remaining<=0)throw expired();
    let value;
    try{value=await api(docPath(id),{timeout:Math.min(remaining,120000)});}
    catch(error){if(performance.now()>=deadline)throw expired();throw error;}
    if(performance.now()>=deadline)throw expired();
    if(!active(value.job?.status??value.status))return value;
    await delay(Math.min(1000,Math.max(0,deadline-performance.now())));
  }
}

async function upload(name,mimeType,buffer){
  const started=performance.now();
  const [response]=await Promise.all([
    page.waitForResponse(r=>new URL(r.url()).pathname===`/api/projects/${project.id}/documents`&&r.request().method()==='POST',{timeout:120000}),
    dialog().locator('input[type=file]').setInputFiles({name,mimeType,buffer})
  ]);
  assert.equal(response.status(),202,'Modern document upload HTTP '+response.status());
  const added=await response.json();uploaded.push(added.id);
  const value=await waitDocument(added.id);currentDocument=value.id;
  assert.equal(value.status,'complete',JSON.stringify({filename:name,status:value.status,job:value.job}));
  assert.equal(value.sha256,hash(buffer));
  assert(Array.isArray(value.job?.resources)&&value.job.resources.length===value.page_count,'Expected a real supervisor receipt for every new page.');
  for(const receipt of value.job.resources){
    assert.equal(receipt.adapter,'docker-linux');assert.equal(receipt.runner,'document_page');
    assert.equal(receipt.status,'completed');assert.equal(receipt.policy_inspected,true);
    assert.equal(receipt.cleanup_verified,true);assert.equal(receipt.host_fallback,false);
    assert.equal(receipt.network_policy,'none');
  }
  await dialog().getByRole('button',{name:'Refresh documents',exact:true}).click();
  await selectDocument(name);
  report.fixtures.push({name,mime_type:mimeType,bytes:buffer.length,sha256:hash(buffer),file_id:value.id,
    document_version:value.version,page_count:value.page_count,seconds:Math.round(performance.now()-started)/1000,
    processing_job:value.job});
  return value;
}

async function previewReady(){
  const image=dialog().locator('.document-page-plane img');
  await expect(image).toBeVisible();
  await expect.poll(()=>image.evaluate(el=>el.complete&&el.naturalWidth>0&&el.naturalHeight>0)).toBe(true);
  return image;
}

async function calculate({column='B',from=2,to=4,unit='ILS',operation='sum',expectedStatus=200}={}){
  await dialog().getByRole('combobox',{name:'Operation',exact:true}).selectOption(operation);
  await dialog().getByRole('combobox',{name:'Column',exact:true}).selectOption(column);
  await dialog().getByRole('spinbutton',{name:'From row',exact:true}).fill(String(from));
  await dialog().getByRole('spinbutton',{name:'To row',exact:true}).fill(String(to));
  await dialog().getByRole('textbox',{name:'Declared unit',exact:true}).fill(unit);
  const [response]=await Promise.all([
    page.waitForResponse(r=>new URL(r.url()).pathname===`/api${docPath(currentDocument)}/table-calculate`&&r.request().method()==='POST'),
    dialog().getByRole('button',{name:'Calculate from cells',exact:true}).click()
  ]);
  const value=await response.json();assert.equal(response.status(),expectedStatus,JSON.stringify(value));
  return value;
}

function editDistance(left,right){
  let previous=Array.from({length:right.length+1},(_,i)=>i);
  for(let i=0;i<left.length;i++){
    const next=[i+1];
    for(let j=0;j<right.length;j++)next.push(Math.min(next[j]+1,previous[j+1]+1,previous[j]+(left[i]===right[j]?0:1)));
    previous=next;
  }
  return previous[right.length];
}

function ocrMetrics(actual,expected){
  // Only whitespace is normalized. Arabic letters, digits, punctuation and
  // direction-sensitive source ordering are not silently corrected.
  const normalize=value=>value.trim().replace(/\s+/gu,' ');
  const a=normalize(actual),e=normalize(expected),chars=[...e],words=e.split(' ');
  const ce=editDistance([...a],chars),we=editDistance(a?a.split(' '):[],words);
  return {normalization:'trim and collapse Unicode whitespace only',exact_after_whitespace:a===e,
    character_edits:ce,reference_characters:chars.length,cer:ce/Math.max(chars.length,1),
    word_edits:we,reference_words:words.length,wer:we/Math.max(words.length,1)};
}

// Original deterministic OOXML fixture, using stdlib ZIP only. No Office,
// formula engine, dependency installation, test imports or network calls.
const xlsxGenerator=String.raw`
import base64,io,zipfile
parts={
'[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
'_rels/.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
'xl/workbook.xml':'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Values" sheetId="1" r:id="rId1"/></sheets></workbook>',
'xl/_rels/workbook.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
'xl/worksheets/sheet1.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>ID</t></is></c><c r="B1" t="inlineStr"><is><t>Amount</t></is></c><c r="C1" t="inlineStr"><is><t>Currency</t></is></c><c r="D1" t="inlineStr"><is><t>Note</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>001</t></is></c><c r="B2"><v>0</v></c><c r="C2" t="inlineStr"><is><t>ILS</t></is></c><c r="D2"/></row><row r="3"><c r="A3" t="inlineStr"><is><t>002</t></is></c><c r="B3"><v>12</v></c><c r="C3" t="inlineStr"><is><t>ILS</t></is></c></row><row r="4"><c r="A4" t="inlineStr"><is><t>003</t></is></c><c r="B4"><v>30</v></c><c r="C4" t="inlineStr"><is><t>ILS</t></is></c></row><row r="5"><c r="A5" t="inlineStr"><is><t>Total</t></is></c><c r="B5"><f>SUM(B2:B4)</f><v>42</v></c><c r="C5" t="inlineStr"><is><t>ILS</t></is></c></row></sheetData></worksheet>'}
stream=io.BytesIO()
with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_STORED) as archive:
 for name,content in sorted(parts.items()):
  info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));archive.writestr(info,content.encode('utf-8'))
print(base64.b64encode(stream.getvalue()).decode())
`;

try{
  stage='synthetic-fixtures';
  const generated=await runFile(python,['-X','utf8','-B','-c',xlsxGenerator],{cwd:root,windowsHide:true,timeout:10000,maxBuffer:128*1024});
  const xlsx=Buffer.from(generated.stdout.trim(),'base64');assert.equal(xlsx.subarray(0,2).toString(),'PK');
  await writeFile(path.join(out,'original-values.xlsx'),xlsx);
  const csv=Buffer.from('ID,Amount,Currency,Note\r\n001,0,ILS,\r\n002,12,ILS,\r\n003,30,ILS,0004\r\n','utf8');
  await writeFile(path.join(out,'original-values.csv'),csv);
  const ocrGold=JSON.parse(await readFile(path.join(root,'tests/fixtures/documents/ocr-exact-text.json'),'utf8')).fixtures.find(x=>x.id==='ara');
  assert(ocrGold);const image=await readFile(path.join(root,ocrGold.path));assert.equal(hash(image),ocrGold.image_sha256);
  report.fixture_rights={new_pdf_csv_xlsx:'Original synthetic fixtures authored for this local acceptance; no external/private content.',
    ocr:'Existing original synthetic fixture documented by tests/fixtures/documents/ocr-exact-text.json; IBM Plex font notices retained in repository.'};

  browser=await chromium.launch({channel:'chrome',headless:true});
  context=await browser.newContext({viewport:{width:1440,height:1000}});page=await context.newPage();
  page.setDefaultTimeout(20000);
  page.on('pageerror',error=>report.page_errors.push(safe(error)));
  await context.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(/^https?:$/.test(url.protocol)&&url.origin!==base){report.external_requests.push(url.origin+url.pathname);await route.abort('blockedbyclient');}
    else await route.continue();
  });
  await context.addInitScript(()=>{if(!sessionStorage.getItem('completion.documents.qa')){
    localStorage.setItem('riwaq.v1.lang','en');localStorage.setItem('riwaq.v1.theme','light');
    sessionStorage.setItem('completion.documents.qa','1');
  }});

  stage='synthetic-browser-pairing';
  await page.goto(base);await expect(page.locator('#riwaq-pairing-code')).toBeVisible();
  let pair;
  try{pair=await runFile(python,['-X','utf8','-B','-c','from backend.local_auth import issue_pairing_code; print(issue_pairing_code())'],
    {cwd:root,env:{...process.env},windowsHide:true,timeout:15000,maxBuffer:4096});}
  catch{throw Error('Synthetic pairing setup failed; secret output omitted.');}
  let code=pair.stdout.trim();pair.stdout='';pair.stderr='';assert(/^[A-Za-z0-9_-]{43}$/.test(code));
  await page.locator('#riwaq-pairing-code').fill(code);code='';
  await page.getByRole('button',{name:'Pair browser',exact:true}).click();
  await expect(page.locator('.composer textarea')).toBeEnabled();
  report.capabilities=await api('/document-capabilities');
  assert.equal(report.capabilities.available,true,'Reviewed modern document runtime must be available; no host fallback is allowed.');
  assert(/docker/i.test(report.capabilities.boundary??report.capabilities.worker?.boundary??''),'Expected the reviewed Docker document boundary.');
  project=await api('/projects',{method:'POST',data:{name:'QA modern documents '+Date.now(),instructions:'Original synthetic document acceptance. Answer the supplied fixture question using only the explicitly selected source and cite it.'}});
  report.project_id=project.id;
  report.settings=await api('/settings',{method:'PATCH',data:{mode:'local',provider:'ollama',model:'qwen3:4b-instruct',max_tokens:256,temperature:0}});
  await page.evaluate(id=>{localStorage.setItem('riwaq.v1.project',id);localStorage.removeItem('riwaq.v1.conversation');},project.id);
  await page.reload();await expect(page.locator('.composer textarea')).toBeEnabled();

  stage='native-pdf-preview';
  const fixture=await context.newPage();
  await fixture.setContent('<!doctype html><meta charset="utf-8"><style>@page{size:A4;margin:24mm}body{font:20px Arial;line-height:1.7}.next{break-before:page}</style><h1>Riwaq original document fixture</h1><p>Gauge Zafira has a pressure limit of 42 kPa.</p><p>This is synthetic evidence for testing, not a real device specification.</p><div class="next"><h1>Excluded draft</h1><p>Gauge Zafira alternate draft pressure limit is 99 kPa.</p></div>');
  const pdf=await fixture.pdf({format:'A4',printBackground:true});await fixture.close();
  await writeFile(path.join(out,'native-evidence.pdf'),pdf);
  await openWorkbench();const native=await upload('native-evidence.pdf','application/pdf',pdf);
  assert.equal(native.page_count,2);
  const nativePage=await api(docPath(native.id)+'/pages/1');
  const pressure=nativePage.elements.find(element=>element.text.includes('42 kPa'));assert(pressure);
  assert(Array.isArray(pressure.bbox)&&pressure.bbox.length===4);
  await dialog().locator('.document-element-list button').filter({hasText:'42 kPa'}).click();
  await previewReady();await expect(dialog().locator('.document-box.selected')).toHaveCount(1);
  const previewResponse=await context.request.get(base+'/api'+docPath(native.id)+'/pages/1/preview',{headers});
  assert(previewResponse.ok());assert.match(previewResponse.headers()['content-type'],/^image\/png/);
  const previewBytes=await previewResponse.body();assert(previewBytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])));
  await dialog().getByRole('combobox',{name:'Preview zoom',exact:true}).selectOption('200');
  await dialog().getByRole('button',{name:/^Rotate/}).click();
  const geometry=await dialog().locator('.document-page-plane').evaluate(el=>({transform:getComputedStyle(el).transform,
    image:el.querySelector('img').getBoundingClientRect().toJSON(),box:el.querySelector('.document-box.selected')?.getBoundingClientRect().toJSON()}));
  assert(geometry.box&&geometry.box.width>0&&geometry.box.height>0);assert.notEqual(geometry.transform,'none');
  assert(geometry.box.x>=geometry.image.x-2&&geometry.box.right<=geometry.image.right+2);
  assert(geometry.box.y>=geometry.image.y-2&&geometry.box.bottom<=geometry.image.bottom+2);
  await page.screenshot({path:path.join(out,'pdf-rotated-200.png'),fullPage:true});
  record('pdf_real_page_image_evidence_region_zoom_rotation',{file_id:native.id,physical_pages:2,element_id:pressure.id,
    locator:pressure.locator,bbox:pressure.bbox,preview_sha256:hash(previewBytes),geometry});

  stage='correction-raw-history';
  await dialog().getByText('Original extracted text',{exact:true}).click();
  assert.equal(await dialog().locator('.document-correction details').filter({has:page.getByText('Original extracted text',{exact:true})}).locator('pre').textContent(),pressure.text_raw);
  const correctionInput=dialog().getByRole('textbox',{name:'User correction',exact:true});
  await correctionInput.fill(pressure.text+' User-reviewed fixture note.');
  // HTML textarea values use LF line endings. Compare the submitted value,
  // while independently requiring the immutable extracted raw text below.
  const revised=await correctionInput.inputValue();
  const [patch]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/elements/'+pressure.id)&&r.request().method()==='PATCH'),dialog().getByRole('button',{name:'Save correction',exact:true}).click()]);
  assert.equal(patch.status(),200);const corrected=await patch.json();assert.equal(corrected.revision,2);assert.equal(corrected.text_raw,pressure.text_raw);assert.equal(corrected.text,revised);
  await expect(dialog().getByRole('status').filter({hasText:'User correction saved; original text retained.'})).toBeVisible();
  const [historyResponse]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/elements/'+pressure.id+'/versions')),dialog().getByText('Correction history',{exact:true}).click()]);
  assert.equal(historyResponse.status(),200);const history=await historyResponse.json();
  assert.equal(history.length,2);assert.deepEqual(history.map(row=>row.revision),[1,2]);
  assert(history.every(row=>row.text_raw===pressure.text_raw));assert.equal(history[0].text,pressure.text);assert.equal(history[1].text,revised);
  await expect(dialog().locator('.document-history-row')).toHaveCount(2);
  const downloaded=page.waitForEvent('download');await dialog().getByRole('button',{name:'JSON',exact:true}).click();
  await (await downloaded).saveAs(path.join(out,'native-export.json'));
  const exported=JSON.parse(await readFile(path.join(out,'native-export.json'),'utf8'));
  assert.equal(exported.pages[0].elements.find(element=>element.id===pressure.id).text_raw,pressure.text_raw);
  assert.equal(exported.pages[0].elements.find(element=>element.id===pressure.id).revision,2);
  record('correction_preserves_raw_and_two_real_revisions',{file_id:native.id,element_id:pressure.id,raw:pressure.text_raw,corrected:revised,history});

  stage='csv-cells-calculation';
  const csvDocument=await upload('original-values.csv','text/csv',csv);
  const csvPage=await api(docPath(csvDocument.id)+'/pages/1'),csvTable=csvPage.elements.find(element=>element.cells?.length);
  assert(csvTable);const csvCells=Object.fromEntries(csvTable.cells.map(cell=>[cell.address,cell]));
  assert.equal(csvCells.A2.value,'001');assert.equal(csvCells.B2.value,'0');assert.equal(csvCells.D2.value,'');assert.equal(csvCells.D4.value,'0004');
  await expect(dialog().getByRole('textbox',{name:'User correction',exact:true})).toHaveCount(0);
  await expect(dialog().getByRole('button',{name:'A2: 001',exact:true})).toBeVisible();
  const wrongUnit=await calculate({unit:'USD',expectedStatus:422});assert.match(wrongUnit.detail,/Mixed or conflicting units/);
  await expect(dialog().getByRole('alert')).toContainText('Mixed or conflicting units');await dialog().getByRole('button',{name:'Dismiss',exact:true}).click();
  const csvSum=await calculate();assert.equal(csvSum.result.exact,'42');assert.equal(csvSum.cellrefs.length,3);
  await expect(dialog().getByRole('region',{name:'Table calculation result',exact:true})).toContainText('42 ILS');
  await dialog().locator('.document-cell-refs').getByRole('button',{name:'B2: 0',exact:true}).click();
  await expect(dialog().locator('.document-table-scroll td.selected')).toHaveText('0');
  const count=await calculate({operation:'count',unit:''});assert.equal(count.result.exact,'3');
  record('csv_exact_sum_zero_blank_identifiers_and_cell_location',{file_id:csvDocument.id,cells:csvCells,result:csvSum,count:count.result,conflicting_unit_rejected:true});

  stage='xlsx-formula-cache';
  const xlsxDocument=await upload('original-values.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',xlsx);
  const xlsxPage=await api(docPath(xlsxDocument.id)+'/pages/1'),xlsxTable=xlsxPage.elements.find(element=>element.cells?.length);
  assert(xlsxTable);const cells=Object.fromEntries(xlsxTable.cells.map(cell=>[cell.address,cell]));
  assert.equal(cells.A2.value,'001');assert.equal(cells.B2.value,'0');assert.equal(cells.D2.value,'');
  assert.equal(cells.B5.formula,'SUM(B2:B4)');assert.equal(cells.B5.cached_value,'42');
  await expect(dialog().getByRole('button',{name:'A2: 001',exact:true})).toBeVisible();
  await expect(dialog().getByRole('button',{name:/^D2:\s*$/})).toContainText('Empty');
  await expect(dialog().locator('.document-table-scroll table')).toContainText('SUM(B2:B4)');
  await expect(dialog().locator('.document-table-scroll table')).toContainText('Cached value: 42');
  const xlsxSum=await calculate();assert.equal(xlsxSum.result.exact,'42');assert.equal(xlsxSum.cellrefs.length,3);
  await dialog().locator('.document-cell-refs').getByRole('button',{name:'B3: 12',exact:true}).click();await expect(dialog().locator('.document-table-scroll td.selected')).toHaveText('12');
  const formulaRejected=await calculate({from:5,to:5,expectedStatus:422});assert.match(formulaRejected.detail,/Formula caches are not verified or recalculated/);
  await expect(dialog().getByRole('alert')).toContainText('Formula caches are not verified or recalculated');await dialog().getByRole('button',{name:'Dismiss',exact:true}).click();
  const identifierRejected=await calculate({column:'A',from:2,to:2,unit:'',expectedStatus:422});assert.match(identifierRejected.detail,/identifier or non-decimal/);
  await dialog().getByRole('button',{name:'Dismiss',exact:true}).click();
  record('xlsx_sum_zero_blank_and_untrusted_formula_cache',{file_id:xlsxDocument.id,cells,result:xlsxSum,formula_cache_rejected:true,leading_zero_identifier_rejected:true,formula_execution:false});

  stage='arabic-image-ocr';
  const imageDocument=await upload('ocr-arabic-fixture.png','image/png',image);await previewReady();
  const scan=await api(docPath(imageDocument.id)+'/pages/1');assert(scan.elements.some(element=>element.text.trim()),'Actual OCR produced no text');
  const rawOCR=scan.elements.map(element=>element.text_raw).join('\n'),searchableOCR=scan.elements.map(element=>element.text).join('\n');
  const quality={expected:ocrGold.text,raw:rawOCR,searchable:searchableOCR,raw_metrics:ocrMetrics(rawOCR,ocrGold.text),searchable_metrics:ocrMetrics(searchableOCR,ocrGold.text)};
  record('arabic_ocr_preview_and_measured_fixture_error',{file_id:imageDocument.id,method:scan.method,quality,
    engine_indicators:scan.elements.map(element=>element.quality),warnings:scan.warnings,
    metric_notice:'This case passes for actual extraction/preview; exactness and CER/WER are separate observed measurements, not forced to pass.'});

  stage='two-layouts';
  for(const layout of [{width:390,height:844,lang:'ar',theme:'dark',file:'ocr-arabic-fixture.png'},{width:1440,height:1000,lang:'en',theme:'light',file:'native-evidence.pdf'}]){
    await dialog().getByRole('button',{name:'Close / إغلاق',exact:true}).click();
    await page.evaluate(({lang,theme})=>{localStorage.setItem('riwaq.v1.lang',lang);localStorage.setItem('riwaq.v1.theme',theme);},layout);
    lang=layout.lang;await page.setViewportSize({width:layout.width,height:layout.height});await page.reload();
    await expect(page.locator('.composer textarea')).toBeEnabled();await openWorkbench();await selectDocument(layout.file);await previewReady();
    const geometry=await dialog().evaluate(el=>({scroll:el.scrollWidth,width:el.clientWidth,body:document.documentElement.scrollWidth,viewport:innerWidth,dir:el.closest('[dir]')?.getAttribute('dir')}));
    assert(geometry.scroll<=geometry.width+2,JSON.stringify(geometry));assert(geometry.body<=geometry.viewport+2,JSON.stringify(geometry));
    const screenshot=`layout-${layout.width}-${layout.lang}-${layout.theme}.png`;await page.screenshot({path:path.join(out,screenshot),fullPage:true});
    report.layouts.push({...layout,...geometry,screenshot});
  }

  stage='explicit-scope-real-qwen';
  await dialog().getByRole('combobox',{name:'Document unit',exact:true}).selectOption('1');
  await dialog().getByRole('checkbox',{name:'Include this unit in the question',exact:true}).check();
  await dialog().getByRole('button',{name:'Use selection for question',exact:true}).click();
  await expect(page.locator('.document-scope-note')).toContainText('1 file');
  const scope=[{file_id:native.id,pages:[1]}];
  await page.locator('.composer textarea').fill('In the selected synthetic source, what pressure limit is explicitly stated for Gauge Zafira? Answer in one short English sentence using only the selected page, with its [S#] citation.');
  const [response]=await Promise.all([page.waitForResponse(r=>/\/conversations\/[^/]+\/generate$/.test(new URL(r.url()).pathname)&&r.request().method()==='POST',{timeout:120000}),page.getByRole('button',{name:'Send',exact:true}).click()]);
  assert.equal(response.status(),200,'Real model request HTTP '+response.status());
  const request=response.request().postDataJSON();assert.equal(request.use_files,true);assert.equal(request.use_knowledge,false);assert(!request.research);assert.deepEqual(request.file_scope,scope);
  const events=(await response.text()).trim().split('\n').filter(Boolean).map(JSON.parse);
  assert(!events.some(event=>event.type==='error'),JSON.stringify(events));assert(events.some(event=>event.type==='delta'));
  const answer=events.findLast(event=>event.type==='done')?.message;assert(answer);assert.equal(answer.status,'complete');assert.match(answer.model,/qwen/i);
  assert.deepEqual(answer.retrieval_options.file_scope,scope);assert(answer.citations.length>0);
  assert(answer.citations.every(citation=>citation.document?.file_id===native.id&&citation.document?.page===1));
  const sourceIndex=answer.citations.findIndex(citation=>(citation.content??citation.text??'').includes('42 kPa')&&citation.document?.element_id===pressure.id);
  assert(sourceIndex>=0,'No supporting selected element citation');assert.equal(answer.citations[sourceIndex].document.revision,2);
  assert(/\b42\b/.test(answer.content)&&!(/\b99\b/.test(answer.content)),answer.content);assert(answer.content.includes(`[S${sourceIndex+1}]`),answer.content);
  const cid=new URL(response.url()).pathname.split('/')[3],saved=await api('/conversations/'+cid);
  assert.equal(saved.messages.find(message=>message.id===answer.id).content,answer.content);
  const card=page.locator(`#evidence-${answer.id}-${sourceIndex+1}`);
  await expect(card).toBeVisible();if((await card.getAttribute('open'))===null)await card.locator('summary').click();
  const [evidenceResponse]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname.endsWith(`/research/evidence/${answer.id}/${sourceIndex}`)),card.getByRole('button',{name:'Review evidence and source metadata',exact:true}).click()]);
  assert.equal(evidenceResponse.status(),200);const evidence=await evidenceResponse.json();assert.equal(evidence.current,true);assert.equal(evidence.citation.document.file_id,native.id);assert.equal(evidence.citation.document.page,1);assert(evidence.excerpt.includes('42 kPa'));
  await expect(page.getByRole('dialog').locator('.research-excerpt')).toHaveText(evidence.excerpt);
  await page.getByRole('dialog').getByRole('button',{name:'Open document and location',exact:true}).click();
  await expect(dialog().locator('.document-header h3')).toHaveText('native-evidence.pdf');await previewReady();
  await expect(dialog().getByRole('combobox',{name:'Document unit',exact:true})).toHaveValue('1');await expect(dialog().locator('.document-box.selected')).toHaveCount(1);
  await expect(dialog().getByRole('textbox',{name:'User correction',exact:true})).toHaveValue(revised);
  await page.screenshot({path:path.join(out,'model-citation-to-corrected-page-region.png'),fullPage:true});
  record('real_qwen_selected_page_citation_opens_exact_document_region',{conversation_id:cid,message_id:answer.id,model:answer.model,content:answer.content,request_scope:request.file_scope,stored_scope:answer.retrieval_options.file_scope,citations:answer.citations,event_types:events.map(event=>event.type),evidence});

  stage='saved-reload';
  await dialog().getByRole('button',{name:'Close / إغلاق',exact:true}).click();await page.reload();await expect(page.locator('.composer textarea')).toBeEnabled();
  await expect(page.locator(`[data-message-id="${answer.id}"]`)).toContainText('42');
  const restored=await api('/conversations/'+cid),restoredAnswer=restored.messages.find(message=>message.id===answer.id);
  assert.equal(restoredAnswer.content,answer.content);assert.deepEqual(restoredAnswer.citations,answer.citations);assert.deepEqual(restoredAnswer.retrieval_options.file_scope,scope);
  record('reload_preserves_answer_citations_scope_without_regeneration',{message_id:answer.id,content_sha256:hash(answer.content)});
  assert.equal(report.page_errors.length,0,JSON.stringify(report.page_errors));assert.equal(report.external_requests.length,0,JSON.stringify(report.external_requests));
  report.status='passed';
}catch(error){
  report.status='failed';report.stage=stage;report.error=safe(error);process.exitCode=1;
  if(page)await page.screenshot({path:path.join(out,'failure.png'),fullPage:true,mask:[page.locator('#riwaq-pairing-code')]}).catch(()=>{});
}finally{
  // Stop only this run's unfinished document jobs, without deleting fixtures or
  // reinterpreting a browser closure as a successful extraction/cancellation.
  report.unfinished_job_cleanup=[];
  if(context&&project)for(const id of uploaded){
    try{const value=await api(docPath(id));if(active(value.job?.status??value.status)){
      const r=await context.request.post(base+'/api'+docPath(id)+'/cancel',{headers,timeout:10000});
      report.unfinished_job_cleanup.push({file_id:id,cancel_requested:true,http_status:r.status()});
    }}catch{report.unfinished_job_cleanup.push({file_id:id,verified:false});}
  }
  report.finished_at=new Date().toISOString();await writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));
  if(browser)await browser.close();
  console.log(JSON.stringify({status:report.status,cases:report.cases.length,layouts:report.layouts.length,stage,report:path.join(out,'report.json')}));
}
