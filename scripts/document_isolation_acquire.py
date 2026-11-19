"""Acquire two reviewed Linux wheels only; no installs, image pulls or builds."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEST = Path('D:/RiwaqRuntime/documents-linux-v1')
BOOK = ROOT/'reports/document-isolation-downloads.json'
PREVIOUS = ROOT/'reports/completion-image-acquisition.json'
CAP = 16*1024**2
PINS = [
    ('pypdfium2','5.14.0','pypdfium2-5.14.0-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl',3799581,'11f281613fa22313d9c7ab89947665e84eccf8ebe40e1198a84a88352305648d'),
    ('pillow','13.0.0','pillow-13.0.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl',7144542,'42d63be3af4f9b951b199b0a9d23365d37ab14687e6d77b74940432c8515c4ca'),
]


def save(value):
    temporary=BOOK.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    temporary.replace(BOOK)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('Unexpected redirect refused')


def fetch(url,limit,book):
    if not url.startswith(('https://pypi.org/pypi/','https://files.pythonhosted.org/packages/')):
        raise ValueError('Unapproved official endpoint')
    request=urllib.request.Request(url,headers={'User-Agent':'Riwaq/0.1 reviewed-document-runtime','Accept-Encoding':'identity'})
    row={'url':url,'received_bytes':0,'status':'started'};book['requests'].append(row);save(book)
    raw=bytearray();deadline=time.monotonic()+60
    opener=urllib.request.build_opener(NoRedirect,urllib.request.ProxyHandler({}))
    with opener.open(request,timeout=15) as response:
        if response.status!=200:raise ValueError('Unexpected HTTP status')
        while True:
            part=response.read(65536)
            if not part:break
            row['received_bytes']+=len(part);book['received_bytes']+=len(part)
            save(book)
            if book['received_bytes']>CAP or len(raw)+len(part)>limit:raise ValueError('Download budget exceeded')
            if time.monotonic()>deadline:raise TimeoutError('Download wall deadline')
            raw.extend(part)
    row['status']='received';save(book)
    return bytes(raw)


def main():
    if BOOK.exists():
        value=json.loads(BOOK.read_bytes())
        if value.get('state')=='complete':
            for row in value['artifacts']:
                raw=Path(row['path']).read_bytes()
                if len(raw)!=row['bytes'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('Existing artifact changed')
            print(json.dumps({'state':'already_complete','received_bytes':value['received_bytes']}));return
        raise ValueError('Prior attempt is retained; review it explicitly before another acquisition')
    previous_raw=PREVIOUS.read_bytes();previous=json.loads(previous_raw)
    accounted=previous['prior_aggregate_consumed_bytes']+previous['budget_reservation_bytes']
    if accounted+CAP>previous['aggregate_cap_bytes']:raise ValueError('Global acquisition ceiling would be exceeded')
    book={'version':'document-isolation-acquisition-v1','state':'reserved','previous_record':str(PREVIOUS),
          'previous_sha256':hashlib.sha256(previous_raw).hexdigest(),'previous_accounted_bytes':accounted,
          'aggregate_cap_bytes':previous['aggregate_cap_bytes'],'batch_cap_bytes':CAP,'reservation_bytes':CAP,
          'received_bytes':164760,'initial_official_metadata_bytes':164760,'requests':[],'artifacts':[],
          'accounting':'Response-body bytes, including first metadata probe. Failed reads remain charged. Not a complete wire meter.',
          'installs':False,'image_pulls':False,'cuda_changes':False}
    # Durable reservation precedes every request below, including metadata.
    save(book)
    try:
        DEST.mkdir(parents=True,exist_ok=True)
        for project,version,filename,size,expected in PINS:
            metadata=fetch(f'https://pypi.org/pypi/{project}/{version}/json',2*1024**2,book)
            data=json.loads(metadata);selected=[r for r in data['urls'] if r['filename']==filename]
            if len(selected)!=1:raise ValueError('Reviewed wheel missing')
            row=selected[0]
            if row['size']!=size or row['digests']['sha256']!=expected or row['yanked']:raise ValueError('Official metadata differs from review')
            metadata_path=DEST/(project+'-'+version+'-metadata.json')
            with metadata_path.open('xb') as stream:stream.write(metadata)
            raw=fetch(row['url'],size,book)
            if len(raw)!=size or hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Wheel size or SHA256 mismatch')
            target=DEST/filename
            with target.open('xb') as stream:stream.write(raw)
            book['artifacts'].append({'name':project,'version':version,'filename':filename,'path':str(target),
               'bytes':size,'sha256':expected,'url':row['url'],'metadata_path':str(metadata_path),
               'metadata_sha256':hashlib.sha256(metadata).hexdigest()})
            save(book)
        book['state']='complete';book['aggregate_accounted_bytes']=accounted+book['received_bytes'];save(book)
        print(json.dumps({'state':book['state'],'received_bytes':book['received_bytes'],'artifacts':len(book['artifacts'])}))
    except BaseException as exc:
        book['state']='failed';book['error_type']=type(exc).__name__;save(book);raise


if __name__=='__main__':main()
