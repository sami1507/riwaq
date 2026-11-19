"""Acquire a pinned official Office renderer; no system installation or retries."""
import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
PREVIOUS=ROOT/'reports/document-isolation-downloads.json'
BOOK=ROOT/'reports/closure-20261116/office-acquisition.json'
DEST=Path('D:/RiwaqRuntime/libreoffice-26.8.3')
NAME='LibreOffice_26.8.3_Win_x86-64.msi'
URL='https://download.documentfoundation.org/libreoffice/stable/26.8.3/win/x86_64/'+NAME
SIZE=376311808
SHA='0e6918c273eabbdbbd800ee0dacbbf130a14c13c2e4cec861b06dd59e015bf94'
CAP=400*1024**2

def save(book):
    temp=BOOK.with_suffix('.tmp');temp.write_text(json.dumps(book,indent=2),encoding='utf-8');temp.replace(BOOK)

class HttpsRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not newurl.startswith('https://'):raise ValueError('Non-HTTPS mirror refused')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def main():
    if BOOK.exists():raise ValueError('Previous acquisition retained; inspect it before another attempt')
    prior=PREVIOUS.read_bytes();previous=json.loads(prior);accounted=previous['aggregate_accounted_bytes']
    if accounted+CAP>previous['aggregate_cap_bytes']:raise ValueError('Cumulative authorized ceiling exceeded')
    book={'state':'reserved','version':'closure-office-acquisition-v1','previous_record':str(PREVIOUS),
          'previous_sha256':hashlib.sha256(prior).hexdigest(),'previous_accounted_bytes':accounted,
          'aggregate_cap_bytes':previous['aggregate_cap_bytes'],'reservation_bytes':CAP,
          'received_bytes':0,'url':URL,'metadata_url':URL+'.mirrorlist','expected_bytes':SIZE,'expected_sha256':SHA,
          'system_install':False,'accounting':'Response body bytes including failed reads; not a full wire meter.'}
    save(book);DEST.mkdir(parents=True,exist_ok=True);target=DEST/(NAME+'.partial');started=time.monotonic()
    try:
        opener=urllib.request.build_opener(HttpsRedirect,urllib.request.ProxyHandler({}))
        request=urllib.request.Request(URL,headers={'Accept-Encoding':'identity','User-Agent':'Riwaq-local-QA/1.0'})
        digest=hashlib.sha256()
        with opener.open(request,timeout=30) as response,target.open('xb') as output:
            if response.status!=200 or int(response.headers.get('Content-Length',SIZE))!=SIZE:raise ValueError('Unexpected response size/status')
            book['mirror_url']=response.url;save(book)
            while book['received_bytes']<SIZE:
                block=response.read(min(1024**2,SIZE-book['received_bytes']))
                if not block:break
                book['received_bytes']+=len(block);book['aggregate_accounted_bytes']=accounted+book['received_bytes'];save(book)
                if time.monotonic()-started>900:raise TimeoutError('Download deadline')
                digest.update(block);output.write(block)
        if book['received_bytes']!=SIZE or digest.hexdigest()!=SHA:raise ValueError('Official size/SHA256 mismatch')
        destination=DEST/NAME
        if destination.exists():raise ValueError('Will not overwrite existing package')
        target.rename(destination)
        book.update(state='complete',sha256=digest.hexdigest(),path=str(destination),seconds=round(time.monotonic()-started,3));save(book)
    except BaseException as exc:
        book.update(state='failed',error_type=type(exc).__name__,seconds=round(time.monotonic()-started,3));save(book);raise
    print(json.dumps({k:book[k] for k in ('state','received_bytes','aggregate_accounted_bytes','path')}))

if __name__=='__main__':main()
