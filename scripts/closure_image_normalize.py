"""Opt-in real Docker/authenticated API proof with synthetic uploads only."""
from pathlib import Path
import argparse,hashlib,io,json,os,random,sys,threading,time
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True,type=Path);parser.add_argument('--output',required=True,type=Path);parser.add_argument('--model',action='store_true');args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False);runtime=args.output/'data';runtime.mkdir()
    (runtime/'isolated-workers.json').write_bytes(args.config.read_bytes());os.environ['RIWAQ_DATA_DIR']=str(runtime.resolve());os.environ['RIWAQ_KNOWLEDGE_REGISTRY']=str(runtime/'absent.json')
    from PIL import Image,ImageDraw,ImageFont,PngImagePlugin
    from backend import media,document_isolation as adapter,isolated_document_page as protocol
    def image_bytes(image,format='PNG',**kw):
        output=io.BytesIO();image.save(output,format=format,**kw);return output.getvalue()
    meta=PngImagePlugin.PngInfo();meta.add_text('location','synthetic private value')
    png=image_bytes(Image.new('RGB',(12,10),'red'),pnginfo=meta)
    exif=Image.Exif();exif[274]=6;exif[270]='synthetic private value'
    jpeg=image_bytes(Image.new('RGB',(20,10),'blue'),'JPEG',exif=exif)
    large=image_bytes(Image.frombytes('RGB',(2000,2000),random.Random(7).randbytes(12_000_000)),'JPEG',quality=80)
    too_large=image_bytes(Image.new('RGB',(2500,2000),'white'))
    gif=image_bytes(Image.new('RGB',(10,10),'green'),'GIF')
    picture=Image.new('RGB',(640,320),'white');draw=ImageDraw.Draw(picture);draw.text((70,95),'VALUE 12',font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',64),fill='black');question_image=image_bytes(picture)
    report={'scope':'real pinned Docker normalization with synthetic inputs; optional installed local Ollama; no production data','image_id':adapter.status()['image_id'],'host_fallback':False,'cases':[],'model_requested':args.model}
    def save(): (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    def no_decode(*a,**kw):raise AssertionError('Native image decoder reached on host')
    with patch.object(Image,'open',no_decode):
        for name,raw,size in [('png_metadata',png,{'width':12,'height':10}),('jpeg_exif',jpeg,{'width':10,'height':20}),('four_million_no_resize',large,{'width':2000,'height':2000})]:
            started=time.monotonic();normalized,dimensions,evidence=media.validate_image(raw,authorize=lambda:True,with_receipt=True)
            assert dimensions==size and protocol.png_dimensions(normalized,protocol.MAX_NORMALIZED_BYTES)==(size['width'],size['height'])
            report['cases'].append({'id':name,'passed':True,'input_bytes':len(raw),'output_bytes':len(normalized),'dimensions':dimensions,'evidence':evidence,'seconds':time.monotonic()-started});save()
            if name!='four_million_no_resize':(args.output/(name+'.png')).write_bytes(normalized)
        for name,raw,expected in [('pixel_limit',too_large,413),('invalid',b'not an image',422),('gif',gif,415)]:
            try:media.validate_image(raw,authorize=lambda:True);raise AssertionError('Rejected input accepted')
            except media.HTTPException as exc:assert exc.status_code==expected,(name,exc.status_code,exc.detail)
            report['cases'].append({'id':name,'passed':True,'http_status':expected});save()
    from fastapi.testclient import TestClient
    from backend import app,local_auth
    from backend.db import get_db
    actual_execute=adapter.execute
    receipts=[]
    def captured_execute(*a,**kw):
        receipt=actual_execute(*a,**kw);receipts.append({k:v for k,v in receipt.items() if k not in {'stdout','stderr'}});return receipt
    with TestClient(app.app,headers={'X-Riwaq-Client':'local'}) as client,patch.object(adapter,'execute',captured_execute),patch.object(Image,'open',no_decode):
        assert client.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
        if args.model:
            result=client.post('/api/media/vision',files={'file':('synthetic.png',question_image,'image/png')},data={'prompt':'Read the literal visible text only.'})
            report['cases'].append({'id':'authenticated_upload_normalize_local_model','passed':result.status_code==200,'http_status':result.status_code,'answer':result.json()});save()
            assert result.status_code==200,result.text
            assert '12' in result.json()['text'] and result.json()['normalization']['cleanup_verified']
        entered=threading.Event();release=threading.Event()
        def paused_execute(*a,**kw):
            actual_progress=kw['progress']
            def progress(event):
                actual_progress(event)
                if event['stage']=='container_created':entered.set();assert release.wait(8)
            kw['progress']=progress;return captured_execute(*a,**kw)
        responses=[]
        def upload():responses.append(client.post('/api/media/vision',files={'file':('synthetic.png',png,'image/png')}))
        with patch.object(adapter,'execute',paused_execute):
            thread=threading.Thread(target=upload);thread.start();assert entered.wait(8)
            stopped=client.post('/api/media/stop');assert stopped.status_code==200;time.sleep(.3);release.set();thread.join(10)
            assert not thread.is_alive() and responses[0].status_code==499
        assert receipts[-1]['cleanup_verified'] and receipts[-1]['status']=='cancelled'
        report['cases'].append({'id':'api_cancel_container_cleanup','passed':True,'http_status':responses[0].status_code,'receipt':receipts[-1]});save()
        def revoked_execute(*a,**kw):
            result=captured_execute(*a,**kw)
            with get_db() as c:c.execute('UPDATE local_auth_sessions SET revoked_at=?',(time.time(),))
            return result
        with patch.object(adapter,'execute',revoked_execute):
            result=client.post('/api/media/vision',files={'file':('synthetic.png',png,'image/png')})
        assert result.status_code in {401,403} and 'text' not in result.json()
        report['cases'].append({'id':'api_session_revoked_after_normalization','passed':True,'http_status':result.status_code,'receipt':receipts[-1]});save()
    report['passed']=all(x['passed'] for x in report['cases']);report['limits']='Synthetic fixture evidence only; model interpretation is not a general image/OCR accuracy benchmark. Cancel/revoke are controlled signals around actual Docker execution, not simulated worker results.';save()
    print(json.dumps({'passed':report['passed'],'cases':len(report['cases']),'report':str(args.output/'report.json')}))


if __name__=='__main__':main()
