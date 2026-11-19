"""Read-only checks of the exact official first-batch endpoints. No crawling."""
import hashlib
import json
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
URLS = {
    'python_license': 'https://docs.python.org/3.12/license.html',
    'python_controlflow': 'https://docs.python.org/3.12/tutorial/controlflow.html',
    'react_license': 'https://raw.githubusercontent.com/reactjs/react.dev/main/LICENSE-DOCS.md',
    'react_state': 'https://raw.githubusercontent.com/reactjs/react.dev/main/src/content/reference/react/useState.md',
    'typescript_license': 'https://raw.githubusercontent.com/microsoft/TypeScript-Website/v2/LICENSE',
    'typescript_narrowing': 'https://raw.githubusercontent.com/microsoft/TypeScript-Website/v2/packages/documentation/copy/en/handbook-v2/Narrowing.md',
    'fastapi_license': 'https://raw.githubusercontent.com/fastapi/fastapi/0.162.5/LICENSE',
    'fastapi_body': 'https://raw.githubusercontent.com/fastapi/fastapi/0.162.5/docs/en/docs/tutorial/body.md',
    'wikidata_license': 'https://www.wikidata.org/wiki/Wikidata:Licensing',
    'wikidata_earth': 'https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q2&props=labels%7Cdescriptions&languages=en%7Car%7Che&format=json&maxlag=5',
}
report = {'note': 'Verification downloads, not indexed production documents.', 'items': []}
out = ROOT / 'reports' / 'source-verification'
out.mkdir(parents=True, exist_ok=True)
with httpx.Client(timeout=40, trust_env=False, headers={'User-Agent':'RiwaqKnowledge/0.1 (local research; https://github.com/ollama/ollama)', 'Accept-Encoding':'identity'}, follow_redirects=False) as client:
    for name, url in URLS.items():
        try:
            r = client.get(url)
            data = r.content
            if len(data)>2*1024*1024: raise ValueError('Verification response above 2 MiB')
            row = {'name':name,'url':url,'status':r.status_code,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'etag':r.headers.get('etag'),'last_modified':r.headers.get('last-modified')}
            if r.status_code == 200:
                (out / (name + '.txt')).write_bytes(data)
            report['items'].append(row)
            print(json.dumps(row),flush=True)
        except Exception as exc:
            report['items'].append({'name':name,'url':url,'error':type(exc).__name__+': '+str(exc)})
            print(name, type(exc).__name__, str(exc), flush=True)
report['total_download_bytes'] = sum(r.get('bytes',0) for r in report['items'])
(ROOT/'reports'/'knowledge-source-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
