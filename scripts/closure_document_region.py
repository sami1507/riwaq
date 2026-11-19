"""Real document-region Docker/API check with synthetic input, no host crop fallback."""
from pathlib import Path
import argparse,hashlib,io,json,os,sys,time,threading
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--model',action='store_true');args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False);runtime=args.output/'data';runtime.mkdir()
    (runtime/'isolated-workers.json').write_bytes(args.config.read_bytes());os.environ['RIWAQ_DATA_DIR']=str(runtime.resolve())
    from PIL import Image,ImageDraw,ImageFont
    from backend import document_isolation as adapter
    image=Image.new('RGB',(640,320),'white');draw=ImageDraw.Draw(image);font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',64)
    draw.text((70,95),'VALUE 12',font=font,fill='black');buffer=io.BytesIO();image.save(buffer,format='PNG');raw=buffer.getvalue()
    report={'scope':'synthetic local Docker crop; optional actual authenticated API/Ollama path, no browser claim','image':adapter.status()['image_id'],'cases':[],'model_requested':args.model}
    def save(): (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for label,box,size in [('full',None,[640,320]),('fractional',[.101,.12,.505,.75],[260,202])]:
        started=time.monotonic();data,region=adapter.prepare_region(raw,box,authorize=lambda:True)
        row={'id':label,'region':region,'seconds':time.monotonic()-started,'passed':[region['model_image_width'],region['model_image_height']]==size}
        (args.output/(label+'.png')).write_bytes(data);report['cases'].append(row);save();assert row['passed'],row
    if args.model:
        from fastapi.testclient import TestClient
        from backend import app,local_auth
        with TestClient(app.app,headers={'X-Riwaq-Client':'local'}) as client:
            assert client.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
            pid=client.get('/api/projects').json()[0]['id'];base=f'/api/projects/{pid}/documents'
            uploaded=client.post(base,files={'file':('synthetic-value.png',raw,'image/png')},data={'languages':'eng'})
            assert uploaded.status_code==202,uploaded.text
            fid=uploaded.json()['id'];deadline=time.monotonic()+50
            while time.monotonic()<deadline:
                source=client.get(base+'/'+fid).json()
                if source['status'] not in {'queued','running','cancelling'}:break
                time.sleep(.1)
            assert source['status']=='complete',source
            answer=client.post(base+f'/{fid}/pages/1/vision',json={'question':'Read the literal visible text. Keep inferences empty if unnecessary.','bbox':[0,0,1,1]})
            entry={'id':'authenticated_document_to_crop_to_model','status':answer.status_code,'passed':answer.status_code==200,'answer':answer.json()};report['cases'].append(entry);save()
            assert answer.status_code==200,answer.text
            assert entry['answer']['source']['isolation']['cleanup_verified'] is True
            entry['literal_12_found']='12' in entry['answer']['result']['transcribed_text'];save()
            assert entry['literal_12_found'],entry
    report['passed']=all(c['passed'] for c in report['cases']);report['quality_limit']='One known synthetic fixture, not general OCR/model accuracy; OS isolation receipts are separate from transcription evidence.';save()
    print(json.dumps({'passed':report['passed'],'cases':len(report['cases']),'report':str(args.output/'report.json')}))

if __name__=='__main__':main()
