"""Real HTTP document/model evaluation using only controlled original fixtures.
Creates a labelled QA project. No user files, secrets, cloud provider or training.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8765');args=parser.parse_args()
    report={'version':'document-live-v1','cases':[],'models':{},'cost':{'provider_usd':0,'electricity':'not measured'},'scope':'Controlled development fixtures; not a heldout quality benchmark.'}
    report_path=ROOT/'reports/documents-live.json'
    started=time.monotonic()
    with httpx.Client(base_url=args.url,headers={'X-Riwaq-Client':'local'},timeout=150,trust_env=False) as client:
        def call(method,path,**kwargs):
            r=client.request(method,'/api'+path,**kwargs);r.raise_for_status();return r
        p=call('POST','/projects',json={'name':'QA Stage 3 document evidence','instructions':'Controlled development fixtures. Answer what the evidence says; this is not real customer data.'}).json()['id']
        report['project_id']=p
        def upload(name,data,language='ara+eng+heb'):
            begin=time.monotonic()
            row=call('POST',f'/projects/{p}/documents',files={'file':(name,data)},data={'languages':language}).json();fid=row['id']
            deadline=time.monotonic()+180
            while time.monotonic()<deadline:
                row=call('GET',f'/projects/{p}/documents/{fid}').json()
                if row['status'] not in {'running','queued','cancelling'}:break
                time.sleep(.15)
            else:raise TimeoutError(name)
            original=call('GET',f'/projects/{p}/documents/{fid}/original').content
            assert original==data
            pages=[call('GET',f'/projects/{p}/documents/{fid}/pages/{n}').json() for n in range(1,row['page_count']+1)]
            report['cases'].append({'kind':'actual_upload','filename':name,'file_id':fid,'status':row['status'],'page_count':row['page_count'],'pages_done':row['pages_done'],'original_sha256':hashlib.sha256(data).hexdigest(),'original_roundtrip':True,'methods':[x.get('method') for x in pages],'seconds':time.monotonic()-begin,'resources':row['job']['resources']})
            return row,pages
        try:
            fixtures=ROOT/'tests/fixtures/documents'
            long,pages=upload('arabic-columns-long.pdf',(fixtures/'arabic-columns-long.pdf').read_bytes())
            assert long['page_count']==12 and long['pages_done']==12
            last=pages[-1];excluded=[e for e in last['elements'] if e.get('indexable') is False]
            report['cases'].append({'kind':'arabic_native_order_limitation','page':12,'raw_native_excluded_from_index':len(excluded),'status':last['status'],'known_identifier':'RQ-742','ocr_identifier_exact':any('RQ-742' in e['text'] for e in last['elements'] if e.get('indexable') is not False),'warnings':last['warnings']})
            cid=call('POST','/conversations',json={'project_id':p}).json()['id']
            for question,expected in [('ما موعد التسليم المذكور في المستند؟ أجب بالعربية مع مرجع.','18'),('ما اسم شركة التأمين المتعاقدة ورقم بوليصتها؟',None)]:
                begin=time.monotonic();r=call('POST',f'/conversations/{cid}/generate',json={'content':question,'use_files':True,'file_scope':[{'file_id':long['id']}],'depth':'quick'})
                packets=[json.loads(line) for line in r.text.splitlines() if line];message=packets[-1]['message']
                accepted=message['status']=='complete' and (expected in message['content'] and any(s.get('document',{}).get('page')==12 for s in message['citations']) if expected else 'لم أجد' in message['content'] or 'لا تتوفر' in message['content'] or 'لم يذكر' in message['content'])
                report['cases'].append({'kind':'actual_local_chat','question':question,'accepted':accepted,'answer':message['content'],'status':message['status'],'citations':message['citations'],'seconds':time.monotonic()-begin,'model':message.get('model'),'run':message.get('run')})
                report['models']['chat']=message.get('model')
            mixed,mp=upload('scan-and-mixed.pdf',(fixtures/'arabic-scan-mixed.pdf').read_bytes())
            assert [page['kind'] for page in mp]==['scan','mixed']
            upload('markdown.md','## النتيجة\nقيمة القياس 31 مترًا.\n'.encode())
            from PIL import Image,ImageDraw,ImageFont
            png=(fixtures/'ocr-ara.png').read_bytes()
            for fmt,ext in [('JPEG','jpeg'),('WEBP','webp')]:
                stream=io.BytesIO();Image.open(io.BytesIO(png)).convert('RGB').save(stream,format=fmt)
                upload('arabic.'+ext,stream.getvalue())
            weak=Image.new('RGB',(600,200),'white');ImageDraw.Draw(weak).text((100,80),'UNREADABLE 987',fill=(254,254,254),font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',6))
            stream=io.BytesIO();weak.rotate(7,fillcolor='white').save(stream,format='PNG')
            weak_doc,wp=upload('unclear-tilted.png',stream.getvalue(),'eng')
            report['cases'].append({'kind':'unclear_image','status':weak_doc['status'],'text':[e['text'] for e in wp[0]['elements']],'warnings':wp[0]['warnings'],'accepted':weak_doc['status'] in {'failed','partial'}})
            sys.path.insert(0,'D:/RiwaqRuntime/documents-v1/python-packages')
            import openpyxl
            book=openpyxl.Workbook();sheet=book.active;sheet.title='بيانات'
            for row in [('ID','Amount','Currency'),('001',12,'ILS'),('002',30,'ILS'),('003',None,'ILS'),('Total','=SUM(B2:B3)','ILS')]:sheet.append(row)
            stream=io.BytesIO();book.save(stream);x,xp=upload('formula-cells.xlsx',stream.getvalue())
            table=xp[0]['elements'][0];lookup={c['address']:c for c in table['cells']}
            assert lookup['A2']['value']=='001' and lookup['B5']['formula']=='SUM(B2:B3)'
            result=call('POST',f'/projects/{p}/documents/{x["id"]}/table-calculate',json={'element_id':table['id'],'operation':'sum','column':'B','row_start':2,'row_end':3,'unit':'ILS'}).json()
            assert result['result']['exact']=='42'
            failure=client.post(f'/api/projects/{p}/documents/{x["id"]}/table-calculate',json={'element_id':table['id'],'operation':'sum','column':'B','row_start':5,'row_end':5})
            assert failure.status_code==422
            report['cases'].append({'kind':'actual_xlsx_calculation','result':result,'formula_rejected':failure.status_code,'cached_value':lookup['B5']['cached_value']})
            from docx import Document
            doc=Document();doc.add_heading('وثيقة الاختبار',0);doc.add_paragraph('رقم الحقل 73.');t=doc.add_table(rows=2,cols=2);t.cell(0,0).text='Item';t.cell(0,1).text='Amount';t.cell(1,0).text='One';t.cell(1,1).text='73'
            stream=io.BytesIO();doc.save(stream);upload('paragraph-table.docx',stream.getvalue())
            from pptx import Presentation
            from pptx.util import Inches
            pres=Presentation();slide=pres.slides.add_slide(pres.slide_layouts[6]);slide.shapes.add_textbox(Inches(1),Inches(1),Inches(7),Inches(2)).text='Riwaq sample slide: 64 units'
            stream=io.BytesIO();pres.save(stream);upload('slide.pptx',stream.getvalue())
            # A controlled numeric plot tests the model's actual visual answer,
            # separately from deterministic table arithmetic and OCR quality.
            chart=Image.new('RGB',(800,500),'white');draw=ImageDraw.Draw(chart)
            font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',24)
            draw.line((90,80,90,400,730,400),fill='black',width=3)
            for value in (0,10,20):
                y=400-value*12;draw.line((85,y,730,y),fill='#cccccc',width=1);draw.text((35,y-12),str(value),fill='black',font=font)
            draw.rectangle((220,280,340,399),fill='#126456');draw.rectangle((480,160,600,399),fill='#497fbe')
            draw.text((245,420),'A',fill='black',font=font);draw.text((505,420),'B',fill='black',font=font)
            draw.text((95,25),'Mass (kg)',fill='black',font=font)
            stream=io.BytesIO();chart.save(stream,format='PNG');plot,pp=upload('mass-chart.png',stream.getvalue(),'eng')
            begin=time.monotonic()
            visual=call('POST',f'/projects/{p}/documents/{plot["id"]}/pages/1/vision',json={'question':'What is the mass of B? Read the vertical axis and unit. Separate direct observation from inference.'}).json()
            report['cases'].append({'kind':'actual_chart_vision','expected':{'series':'B','mass':20,'unit':'kg'},'response':visual,'seconds':time.monotonic()-begin,'quality_verdict':'Requires direct comparison with the controlled plot; schema success alone is not correctness.'})
            report['models']['vision']={'model':visual['model'],'digest':visual['digest']}
            report['completed']=True
        except Exception as exc:
            report['completed']=False;report['error']=type(exc).__name__+': '+str(exc);raise
        finally:
            report['seconds']=time.monotonic()-started
            report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps({'completed':report.get('completed'),'cases':len(report['cases']),'report':str(report_path)}))


if __name__=='__main__':main()
