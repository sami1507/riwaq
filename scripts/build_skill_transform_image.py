"""Prepare an offline, pinned image context. Never installs or downloads packages."""
from __future__ import annotations
import argparse,hashlib,importlib.metadata,json,re,shutil,stat,zipfile
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'reports/skills-batch-03-20261116/documents'
def sha(b):return hashlib.sha256(b).hexdigest()
def prepare(output,config_path=None):
    output=Path(output).resolve()
    if output.exists():raise ValueError('Use a new context directory')
    cfg=json.loads((Path(config_path) if config_path else ROOT/'reports/document-isolation-candidate/isolated-workers.json').read_bytes())
    base=cfg['images']['document_page']
    if not re.fullmatch('sha256:[a-f0-9]{64}',base):raise ValueError('Pinned base required')
    selected={}
    def add(path,raw):
        p=PurePosixPath(path)
        if p.is_absolute() or any(x in ('.','..') for x in p.parts) or '\\' in path or ':' in path or path in selected:raise ValueError('Unsafe context entry')
        selected[path]=raw
    acquisition=json.loads((REPORT/'pdf-dependency-acquisition.json').read_bytes())
    for record in acquisition['packages']:
        path=(ROOT/record['path']).resolve()
        if not path.is_relative_to((REPORT/'wheels').resolve()) or path.is_symlink():raise ValueError('Unexpected wheel location')
        raw=path.read_bytes()
        if len(raw)!=record['bytes'] or sha(raw)!=record['sha256']:raise ValueError('Wheel integrity mismatch')
        with zipfile.ZipFile(path) as z:
            if len(z.infolist())>5000 or sum(x.file_size for x in z.infolist())>64*1024**2:raise ValueError('Wheel bounds')
            for m in z.infolist():
                if m.is_dir():continue
                if stat.S_ISLNK(m.external_attr>>16) or m.flag_bits&1 or Path(m.filename).suffix.lower() in {'.pth','.exe','.dll','.pyd'}:raise ValueError('Unreviewed wheel entry')
                add('packages/'+m.filename,z.read(m))
    dist=importlib.metadata.distribution('pypdf')
    if dist.version!='6.21.4':raise ValueError('Review updated pypdf before using')
    for f in dist.files:
        path=dist.locate_file(f)
        if str(f).replace('\\','/').startswith('pypdf/') and path.suffix in {'.py','.txt','.typed'} or 'licenses/' in str(f).replace('\\','/'):
            add('packages/'+str(f).replace('\\','/'),path.read_bytes())
    fonts=ROOT/'frontend/public/fonts'
    for name in ('IBMPlexSansArabic-Regular.woff2','IBMPlexSansHebrew-Regular.woff2','IBMPlexSans-Regular.woff2','manifest.json','plex-sans-arabic-LICENSE.txt','plex-sans-hebrew-LICENSE.txt','plex-sans-LICENSE.txt'):
        add('fonts/'+name,(fonts/name).read_bytes())
    for name in ('backend/skill_transform_protocol.py','backend/isolated_skill_documents.py','backend/document_extract.py','backend/skill_data.py','scripts/isolated_skill_transform.py'):
        add('source/'+name,(ROOT/name).read_bytes())
    add('source/backend/__init__.py',b'')
    output.mkdir(parents=True)
    for name,raw in selected.items():
        path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    dockerfile=f'FROM {base}\nCOPY packages /opt/transform-packages\nCOPY fonts /opt/riwaq/fonts\nCOPY source /opt/riwaq\nENV PYTHONDONTWRITEBYTECODE=1\nUSER 65534:65534\n'
    (output/'Dockerfile').write_text(dockerfile,encoding='utf-8')
    result={'base_image':base,'files':[{ 'path':k,'bytes':len(v),'sha256':sha(v)} for k,v in sorted(selected.items())],'dockerfile_sha256':sha(dockerfile.encode()),'bytes':sum(map(len,selected.values())), 'install_hooks':False,'network_required':False,'build_executed':False,'licenses':'Upstream wheel licenses, pypdf BSD notice and IBM Plex OFL notices included; fpdf2 LGPL3 requires license/source obligations when distributing.'}
    (output/'manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8');return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--config');args=p.parse_args();result=prepare(args.output,args.config)
    print(json.dumps({'files':len(result['files']),'bytes':result['bytes'],'build_executed':False}))
