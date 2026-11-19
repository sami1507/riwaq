"""Actual isolated visual upload/revocation proof; synthetic images, no downloads."""
from pathlib import Path
import argparse,hashlib,io,json,os,random,sys,threading,time
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True,type=Path);parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False);runtime=args.output/'data';runtime.mkdir()
    (runtime/'isolated-workers.json').write_bytes(args.config.read_bytes());os.environ['RIWAQ_DATA_DIR']=str(runtime.resolve());os.environ['RIWAQ_KNOWLEDGE_REGISTRY']=str(runtime/'absent')
    from PIL import Image
    from backend import app,local_auth,document_isolation as adapter,isolated_document_page as p,visual_codes
    from backend.db import get_db
    from fastapi.testclient import TestClient
    def encode(picture,format='PNG',**kw):
        out=io.BytesIO();picture.save(out,format=format,**kw);return out.getvalue()
    alpha=Image.new('RGBA',(20,10),(30,50,70,96));exif=Image.Exif();exif[274]=6;exif[270]='synthetic private metadata'
    png=encode(alpha,exif=exif);webp=encode(alpha,'WEBP',lossless=True);jpeg=encode(Image.new('RGB',(20,10),'blue'),'JPEG',exif=exif)
    large=encode(Image.frombytes('RGB',(2000,2000),random.Random(19).randbytes(12_000_000)),'JPEG',quality=95)
    oversized=encode(Image.new('RGB',(2500,2000),'white'));gif=encode(Image.new('RGB',(10,10),'white'),'GIF')
    report={'image_id':adapter.status()['image_id'],'actual_docker':True,'synthetic_inputs':True,'host_decoder_forbidden':True,'cases':[]}
    def save():(args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    def no_host(*a,**k):raise AssertionError('Host native decoder reached')
    actual_execute=adapter.execute;receipts=[]
    def execute(*a,**k):
        value=actual_execute(*a,**k);receipts.append({key:item for key,item in value.items() if key not in {'stdout','stderr'}});return value
    stored=[]
    with TestClient(app.app,headers={'X-Riwaq-Client':'local'}) as client,patch.object(adapter,'execute',execute),patch.object(Image,'open',no_host):
        assert client.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
        pid=client.post('/api/projects',json={'name':'Synthetic isolated visual uploads'}).json()['id'];base=f'/api/projects/{pid}/visual'
        for name,raw,dimensions in [('png_alpha_exif',png,(10,20)),('webp_alpha',webp,(20,10)),('jpeg_exif',jpeg,(10,20)),('four_mp_jpeg',large,(2000,2000))]:
            response=client.post(base+'/assets',files={'file':(name+'.webp',raw,'application/octet-stream')});assert response.status_code==201,response.text
            item=response.json();download=client.get(base+'/assets/'+item['id']+'/content');assert download.status_code==200
            assert item['filename'].endswith('.png') and item['mime']=='image/png'
            assert p.png_dimensions(download.content,p.MAX_VISUAL_NORMALIZED_BYTES,allow_alpha=True)==dimensions
            assert hashlib.sha256(download.content).hexdigest()==item['sha256'] and item['origin']['isolation']['cleanup_verified']
            path=args.output/(name+'.png');path.write_bytes(download.content);stored.append((name,path))
            report['cases'].append({'id':name,'passed':True,'input_bytes':len(raw),'output_bytes':len(download.content),'asset':item});save()
        for name,raw,expected in [('invalid',b'not an image',422),('gif',gif,415),('pixels',oversized,413),('bytes',b'x'*(6*1024**2+1),413)]:
            result=client.post(base+'/assets',files={'file':(name+'.png',raw,'image/png')});assert result.status_code==expected,result.text
            report['cases'].append({'id':name,'passed':True,'status':expected});save()
        entered=threading.Event();release=threading.Event();responses=[]
        def paused(*a,**kw):
            progress=kw['progress']
            def held(event):
                progress(event)
                if event['stage']=='container_created':entered.set();assert release.wait(10)
            kw['progress']=held;return execute(*a,**kw)
        def upload():responses.append(client.post(base+'/assets',files={'file':('revoked.png',png,'image/png')}))
        with patch.object(adapter,'execute',paused):
            thread=threading.Thread(target=upload);thread.start();assert entered.wait(10)
            with get_db() as c:c.execute('UPDATE guard_projects SET owner_id=? WHERE project_id=?',('other',pid))
            time.sleep(.3);release.set();thread.join(12);assert not thread.is_alive() and responses[0].status_code in {403,404}
        assert receipts[-1]['cleanup_verified']
        with get_db() as c:assert c.execute('SELECT COUNT(*) FROM visual_assets WHERE project_id=?',(pid,)).fetchone()[0]==4
        report['cases'].append({'id':'revoked_during_actual_container','passed':True,'status':responses[0].status_code,'receipt':receipts[-1]});save()
    stop=threading.Event()
    def cancelling(*a,**kw):
        progress=kw['progress']
        def cancelled(event):
            progress(event)
            if event['stage']=='container_created':stop.set()
        kw['progress']=cancelled;return execute(*a,**kw)
    with patch.object(adapter,'execute',cancelling),patch.object(Image,'open',no_host):
        try:adapter.normalize_image(png,stop=stop,authorize=lambda:True,variant='visual');raise AssertionError('Cancelled image accepted')
        except adapter.IsolationError as exc:assert exc.status_code==499
    assert receipts[-1]['cleanup_verified'] and receipts[-1]['status']=='cancelled'
    report['cases'].append({'id':'cancel_actual_visual_container','passed':True,'status':499,'receipt':receipts[-1]});save()
    # Output pixel checks occur only in the test process after the host adapter/API guard is removed.
    for name,path in stored:
        with Image.open(path) as pic:
            assert not pic.info and not pic.getexif()
            if name in ('png_alpha_exif','webp_alpha'):assert pic.mode=='RGBA' and pic.getpixel((0,0))[3]==96
    code_results=[]
    for kind,text in [('qr','Riwaq 001'),('qr','مرحبا 001'),('qr','001'),('code128','001')]:
        picture,proof=visual_codes.generate(text.encode(),{'operation':'code','kind':kind,'scale':4},threading.Event(),lambda:True)
        assert proof['decode_matches'] and proof['input_sha256']==proof['decoded_sha256']==hashlib.sha256(text.encode()).hexdigest()
        code_results.append({'kind':kind,'text':text,'proof':proof})
    report['codes']=code_results;report['passed']=True;save();print(json.dumps({'passed':True,'cases':len(report['cases']),'code_cases':len(code_results),'report':str(args.output/'report.json')}))


if __name__=='__main__':main()
