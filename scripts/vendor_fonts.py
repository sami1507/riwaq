"""Vendor a bounded set of official IBM fonts. No npm scripts or telemetry."""
from pathlib import Path
import hashlib,json,urllib.request
ROOT=Path(__file__).resolve().parents[1]
COMMIT='2f9ba1b25957d958db71a849e85d72e3ecfb845a'
BASE='https://raw.githubusercontent.com/IBM/plex/'+COMMIT+'/'
out=ROOT/'frontend/public/fonts';out.mkdir(parents=True,exist_ok=True)
records=[];css=[];total=0
for family,prefix,label in [('plex-sans-arabic','IBMPlexSansArabic','IBM Plex Sans Arabic'),('plex-sans','IBMPlexSans','IBM Plex Sans'),('plex-mono','IBMPlexMono','IBM Plex Mono')]:
 for weight,suffix in [(400,'Regular'),(500,'Medium'),(600,'SemiBold')]:
  filename=prefix+'-'+suffix+'.woff2';url=BASE+'packages/'+family+'/fonts/complete/woff2/'+filename
  with urllib.request.urlopen(url,timeout=30) as r:
   data=r.read(2*1024*1024+1)
  if not data.startswith(b'wOF2') or len(data)>2*1024*1024:raise RuntimeError('Invalid bounded font: '+filename)
  total+=len(data)
  if total>12*1024*1024:raise RuntimeError('Font budget exceeded')
  (out/filename).write_bytes(data)
  records.append(dict(file=filename,url=url,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
  css.append('@font-face{font-family:"'+label+'";src:url("/fonts/'+filename+'") format("woff2");font-style:normal;font-weight:'+str(weight)+';font-display:swap;}')
 url=BASE+'packages/'+family+'/LICENSE.txt'
 with urllib.request.urlopen(url,timeout=30) as r:data=r.read(100000)
 if b'SIL OPEN FONT LICENSE' not in data:raise RuntimeError('Missing OFL license')
 (out/(family+'-LICENSE.txt')).write_bytes(data)
 records.append(dict(file=family+'-LICENSE.txt',url=url,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()));total+=len(data)
(ROOT/'frontend/src/fonts.css').write_text('\n'.join(css)+'\n',encoding='utf-8')
manifest=dict(upstream='https://github.com/IBM/plex',commit=COMMIT,license='OFL-1.1',retrieved_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),bytes=total,files=records)
(out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(json.dumps(dict(fonts=9,licenses=3,bytes=total,commit=COMMIT)))
