"""Export/import a portable edit project and its verified originals through local API.

No credentials, provider calls, playback, generation or automatic export. Imported
projects are new projects; existing user data is never overwritten.
"""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
import httpx

def client(url):
    u=urlsplit(url)
    if u.scheme!='http' or u.hostname not in ('127.0.0.1','localhost') or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):
        raise ValueError('Only an explicit local Riwaq HTTP service is accepted')
    return httpx.Client(base_url=url.rstrip('/'),headers={'X-Riwaq-Client':'local'},trust_env=False,timeout=60)

def result(response):response.raise_for_status();return response.json()

def export_project(c,pid,tid,folder):
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=False)
    source=result(c.get(f'/api/projects/{pid}/video/timelines/{tid}/source'))
    if source['timeline']['missing_assets']:raise ValueError('Cannot export missing originals')
    for asset in source['originals']:
        data=c.get(f"/api/projects/{pid}/video/assets/{asset['id']}/content");data.raise_for_status()
        if len(data.content)>8*1024**2 or hashlib.sha256(data.content).hexdigest()!=asset['sha256']:raise ValueError('Original failed integrity verification')
        suffix=Path(asset['filename']).suffix.lower();relative='assets/'+asset['id']+suffix
        destination=folder/relative;destination.parent.mkdir(exist_ok=True);destination.write_bytes(data.content);asset['relative_path']=relative
    source['notice']='Portable original media and edit source. Import creates a new project and verifies hashes. Exports, history and secrets are not included.'
    (folder/'source.json').write_text(json.dumps(source,ensure_ascii=False,indent=2),'utf-8')
    return {'folder':str(folder),'originals':len(source['originals']),'version':source['timeline']['version']}

def import_project(c,folder,title):
    folder=Path(folder).resolve();manifest=(folder/'source.json').resolve()
    if not manifest.is_relative_to(folder) or manifest.stat().st_size>1024**2:raise ValueError('Manifest outside bundle or exceeds1MiB')
    with manifest.open('rb') as source_file:raw=source_file.read(1024**2+1)
    if len(raw)>1024**2:raise ValueError('Source manifest exceeds1MiB')
    source=json.loads(raw)
    if source.get('format')!='riwaq-video-v1' or len(source.get('originals',[]))>16:raise ValueError('Invalid project manifest')
    # Inspect every file before creating any new project; no archive extraction.
    originals=[];seen=set()
    for a in source['originals']:
        p=(folder/a['relative_path']).resolve()
        if not p.is_relative_to(folder) or not p.is_file() or p.stat().st_size>8*1024**2:raise ValueError('Original outside bundle or exceeds8MiB')
        data=p.read_bytes()
        if a['id'] in seen or hashlib.sha256(data).hexdigest()!=a['sha256']:raise ValueError('Duplicate or changed original')
        seen.add(a['id']);originals.append((a,data))
    pid=result(c.post('/api/projects',json={'name':title}))['id'];mapping={}
    for a,data in originals:
        uploaded=result(c.post(f'/api/projects/{pid}/video/assets',files={'file':(a['filename'],data)},data={'license':a.get('origin',{}).get('rights','Imported local edit project; rights not independently verified')}))
        mapping[a['id']]=uploaded['id']
    spec=source['timeline']['spec']
    for item in [*spec['clips'],*spec['audio']]:item['asset_id']=mapping[item['asset_id']]
    value=result(c.post(f'/api/projects/{pid}/video/timelines',json={'title':source['timeline']['title'],'spec':spec}))
    return {'project_id':pid,'timeline_id':value['id'],'version':value['version'],'notice':'New project created. No render/generation was started. Interrupted imports remain visible for explicit cleanup.'}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8765')
    sub=parser.add_subparsers(dest='mode',required=True)
    save=sub.add_parser('export');save.add_argument('--project',required=True);save.add_argument('--timeline',required=True);save.add_argument('--folder',required=True)
    load=sub.add_parser('import');load.add_argument('--folder',required=True);load.add_argument('--title',default='فيديو مستعاد')
    a=parser.parse_args()
    # IDs are not URLs or paths; do not permit route traversal in CLI input.
    import re
    if a.mode=='export' and any(not re.fullmatch('[a-f0-9]{32}',v) for v in (a.project,a.timeline)):raise ValueError('Expected project and timeline IDs')
    with client(a.url) as c:
        output=export_project(c,a.project,a.timeline,a.folder) if a.mode=='export' else import_project(c,a.folder,a.title)
    print(json.dumps(output,ensure_ascii=False))

if __name__=='__main__':main()
