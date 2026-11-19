"""Real pinned Docker acceptance using original synthetic fixtures only."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);parser.add_argument('--output',type=Path,default=ROOT/'reports/document-isolation-live.json');args=parser.parse_args()
    config=args.config.resolve();os.environ['RIWAQ_DATA_DIR']=str(config.parent)
    from backend import document_isolation as adapter
    module_spec=importlib.util.spec_from_file_location('synthetic_document_fixtures',ROOT/'tests/test_document_extract.py')
    fixtures=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(fixtures)
    folder=ROOT/'tests/fixtures/documents'
    cases=[('text_exact','literal.txt',b'001\r\n\r\nLiteral 731',1,'eng',False,'001\r\n\r\nLiteral 731'),
           ('csv_cells','table.csv',b'id;value\n001;73\n002;\n',1,'eng',False,'001'),
           ('native_pdf_inspect','native.pdf',fixtures.pdf_bytes(),0,'eng',False,None),
           ('native_pdf','native.pdf',fixtures.pdf_bytes(),1,'eng',False,'Native source 42'),
           ('xlsx_cells','values.xlsx',fixtures.spreadsheet(),1,'eng',False,'001'),
           ('docx_table','word-table.docx',(folder/'word-table.docx').read_bytes(),1,'ara+eng+heb',False,None),
           ('native_arabic_page12','arabic-columns-long.pdf',(folder/'arabic-columns-long.pdf').read_bytes(),12,'ara+eng',False,None),
           ('mixed_pdf','arabic-scan-mixed.pdf',(folder/'arabic-scan-mixed.pdf').read_bytes(),1,'ara+eng+heb',False,None),
           ('corrupt_pdf','corrupt.pdf',b'%PDF-1.7 damaged',0,'eng',True,None),
           ('wrong_page','native.pdf',fixtures.pdf_bytes(),2,'eng',True,None)]
    for language in ['eng','ara','heb']:
        cases.append(('ocr_'+language,'ocr-'+language+'.png',(folder/('ocr-'+language+'.png')).read_bytes(),1,language,False,None))
    report={'scope':'Real Linux container document-page extraction; original synthetic fixtures only. No cloud/model/training or human quality review.',
            'configuration_sha256':hashlib.sha256(config.read_bytes()).hexdigest(),'image_id':adapter.status().get('image_id'),
            'host_fallback':False,'cases':[],'faults':[]}
    def save():args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for label,name,data,page,language,reject,needle in cases:
        started=time.monotonic();row={'id':label,'input_sha256':hashlib.sha256(data).hexdigest(),'page':page,'language':language,'expected_rejection':reject}
        try:
            value,preview,receipt=adapter.extract(data,name,page,language,threading.Event(),authorize=lambda:True)
            page_data=value.get('page',{});elements=page_data.get('elements',[])
            texts=[element.get('text','') for element in elements if element.get('indexable',True)]
            row.update(accepted=True,result=value,preview_bytes=len(preview or b''),preview_sha256=hashlib.sha256(preview).hexdigest() if preview else None,receipt=receipt,
                expected_literal_found=None if needle is None else any(needle in text for text in texts),passed=not reject and (needle is None or any(needle in text for text in texts)))
        except adapter.IsolationError as exc:
            row.update(accepted=False,error_code=exc.code,http_status=exc.status_code,passed=reject and exc.status_code==422)
        row['seconds']=round(time.monotonic()-started,3);report['cases'].append(row);save()
        print(json.dumps({'id':label,'passed':row['passed'],'seconds':row['seconds']}),flush=True)
    # Real cancellation/revocation after the adapter reaches dispatch, with
    # supervised cleanup evidence captured independently of parser output.
    real_execute=adapter.execute
    for mode in ['cancel','revoke','timeout']:
        stop=threading.Event();allowed=[True];receipts=[];timers=[]
        def observed_execute(*positional,**keywords):
            progress=keywords.get('progress')
            def observe(event):
                if progress:progress(event)
                if event.get('stage')=='running' and mode!='timeout':
                    def signal():
                        if mode=='cancel':stop.set()
                        else:allowed[0]=False
                    timer=threading.Timer(.3,signal);timers.append(timer);timer.start()
            keywords['progress']=observe
            if mode=='timeout':keywords['timeout_seconds']=1
            value=real_execute(*positional,**keywords);receipts.append({k:v for k,v in value.items() if k not in {'stdout','stderr'}});return value
        adapter.execute=observed_execute;entry={'id':mode}
        try:
            adapter.extract((folder/'ocr-ara.png').read_bytes(),'ocr-ara.png',1,'ara+eng+heb',stop,authorize=lambda:allowed[0])
            entry.update(passed=False,unexpected_result=True)
        except adapter.IsolationError as exc:
            expected={'cancel':499,'revoke':403,'timeout':504}[mode]
            entry.update(passed=exc.status_code==expected and bool(receipts) and receipts[-1].get('cleanup_verified') is True,http_status=exc.status_code,error_code=exc.code,receipts=receipts)
        finally:
            adapter.execute=real_execute
            for timer in timers:timer.cancel()
        report['faults'].append(entry);save();print(json.dumps({'id':mode,'passed':entry['passed']}),flush=True)
    report['technical_passed']=all(row['passed'] for row in report['cases']+report['faults'])
    report['quality_limit']='OCR and Arabic PDF text are recorded for review; technical completion is not transcription accuracy, reading-order correctness or a language benchmark.'
    save();print(json.dumps({'technical_passed':report['technical_passed'],'cases':len(report['cases']),'faults':len(report['faults'])}))


if __name__=='__main__':main()
