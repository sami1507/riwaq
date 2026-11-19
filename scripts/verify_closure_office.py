"""Independent native rendering/recalculation of explicit synthetic QA files.

Does not enable a production document converter or read user documents. Each
conversion owns a separate profile, bounded process tree and new output folder.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1]
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main','w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def formula_fixture(path,value):
    parts={
      '[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
      '_rels/.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
      'xl/workbook.xml':'<workbook xmlns="'+NS['s']+'" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="QA" sheetId="1" r:id="rId1"/></sheets><calcPr calcMode="auto" fullCalcOnLoad="1" forceFullCalc="1"/></workbook>',
      'xl/_rels/workbook.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
      'xl/worksheets/sheet1.xml':f'<worksheet xmlns="{NS["s"]}"><sheetData><row r="1"><c r="A1"><v>{value}</v></c><c r="B1"><f>A1*2</f><v>997</v></c><c r="C1" t="inlineStr"><is><t>001</t></is></c><c r="D1"><v>0</v></c></row></sheetData></worksheet>'}
    with zipfile.ZipFile(path,'x',zipfile.ZIP_DEFLATED) as archive:
        for name,content in parts.items():archive.writestr(name,content)

def invoke(command,timeout=90):
    import psutil
    started=time.monotonic();process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        stdout,stderr=process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        owner=psutil.Process(process.pid);children=owner.children(recursive=True)
        for child in reversed(children):
            try:child.kill()
            except psutil.NoSuchProcess:pass
        process.kill();process.communicate();psutil.wait_procs(children,timeout=10);raise
    return {'exit_code':process.returncode,'stdout':stdout.decode('utf-8',errors='replace')[:2000],
            'stderr':stderr.decode('utf-8',errors='replace')[:2000],'seconds':round(time.monotonic()-started,3)}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--office',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--revision-dir',type=Path,default=ROOT/'reports/closure-20261116/data-revision/live-coverage-final')
    args=parser.parse_args()
    office=args.office.resolve(strict=True);out=args.output.resolve();out.relative_to(ROOT/'reports');out.mkdir(parents=True,exist_ok=False)
    report={'scope':'Synthetic independent LibreOffice renderer, not Microsoft Office or production converter acceptance','cases':[],
            'engine':invoke([str(office),'--version']),'exe_sha256':digest(office),'visual_review':'pending'}
    def save():(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    def convert(source,kind,case):
        source=source.resolve(strict=True);source.relative_to(ROOT/'reports')
        if source.is_symlink():raise ValueError('Linked QA source refused')
        target=out/case;target.mkdir();before=digest(source)
        command=[str(office),'-env:UserInstallation='+(target/'profile').as_uri(),'--headless','--nologo','--norestore','--convert-to',kind,'--outdir',str(target),str(source)]
        execution=invoke(command);artifact=target/(source.stem+'.'+kind.split(':')[0])
        row={'id':case,'source':str(source.relative_to(ROOT)),'source_sha256':before,'source_unchanged':before==digest(source),
             'command':command,'execution':execution,'artifact_exists':artifact.is_file(),'passed':False}
        report['cases'].append(row);save()
        if execution['exit_code'] or not artifact.is_file():return row,None
        row['artifact_sha256']=digest(artifact);row['artifact']=str(artifact.relative_to(ROOT));return row,artifact
    try:
        fixtures=out/'fixtures';fixtures.mkdir()
        sources=[ROOT/'reports/ai-core-continuation-20261116/visual/arabic-table.docx',
                 args.revision_dir/'v2-data-report.docx', args.revision_dir/'v2-data-chart.xlsx']
        source=fixtures/'formula-10.xlsx';formula_fixture(source,10)
        # CLI conversion can preserve a stale cached value. Explicitly recalculate
        # and edit the same workbook through the native model, then reopen it.
        command=[str(office.parent/'python.exe'),str(ROOT/'scripts/closure_office_uno.py'),
                 '--office',str(office),'--fixture',str(source),'--docx',str(sources[0]),
                 '--output',str(out/'native-model')]
        execution=invoke(command)
        native_path=out/'native-model/report.json'
        native=json.loads(native_path.read_text(encoding='utf-8')) if native_path.is_file() else {}
        report['cases'].append({'id':'native-recalculation-and-editing','execution':execution,
            'report':str(native_path.relative_to(ROOT)), 'passed':execution['exit_code']==0 and native.get('passed') is True})
        save()
        for index,source in enumerate(sources):
            row,pdf=convert(source,'pdf',f'render-{index+1}')
            if pdf:
                from pypdf import PdfReader
                reader=PdfReader(pdf);texts=[page.extract_text() for page in reader.pages]
                row.update(pages=len(reader.pages),extracted_text=texts)
                row['passed']=row['source_unchanged'] and 0<len(reader.pages)<=8
                # Existing approved local PDFium runtime; synthetic PDFs only.
                sys.path.insert(0,'D:/RiwaqRuntime/documents-v1/python-packages')
                import pypdfium2
                with pypdfium2.PdfDocument(str(pdf)) as document:
                    row['page_images']=[]
                    for page_no in range(len(document)):
                        path=pdf.parent/f'page-{page_no+1}.png'
                        page=document[page_no]
                        try:
                            bitmap=page.render(scale=1.3)
                            try:bitmap.to_pil().save(path)
                            finally:bitmap.close()
                        finally:page.close()
                        row['page_images'].append(str(path.relative_to(ROOT)))
            save()
        # The native model above edits a real table cell, saves and reopens DOCX.
        # A redundant same-format CLI copy failed in v1/v2 and is retained there;
        # no success is inferred from a CLI zero exit code without an artifact.
    except Exception as exc:
        report['error']={'type':type(exc).__name__,'detail':str(exc)[:1500]}
    report['automated_passed']=bool(report['cases']) and all(x['passed'] for x in report['cases']) and 'error' not in report
    save();print(json.dumps({'automated_passed':report['automated_passed'],'cases':len(report['cases']),'visual_review':report['visual_review']}))

if __name__=='__main__':main()
