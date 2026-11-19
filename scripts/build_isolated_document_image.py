"""Prepare offline reviewed context only. No installation/build/pull/network."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path,PurePosixPath
import re
import shutil
import stat
import zipfile

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=Path('D:/RiwaqRuntime/documents-v1')
EXPECTED={'pypdfium2':('5.14.0',3799581,'11f281613fa22313d9c7ab89947665e84eccf8ebe40e1198a84a88352305648d'),
          'pillow':('13.0.0',7144542,'42d63be3af4f9b951b199b0a9d23365d37ab14687e6d77b74940432c8515c4ca')}


def digest(raw):return hashlib.sha256(raw).hexdigest()


def safe_relative(name):
    path=PurePosixPath(name)
    if not name or path.is_absolute() or '\\' in name or ':' in name or '\x00' in name or any(p in {'.','..'} for p in path.parts):raise ValueError('Unsafe artifact path')
    return path


def prepare(output):
    output=Path(output).resolve()
    if output.exists():raise ValueError('Use a fresh context; existing evidence is preserved')
    book=json.loads((ROOT/'reports/document-isolation-downloads.json').read_bytes())
    if book.get('state')!='complete' or len(book['artifacts'])!=2:raise ValueError('Reviewed acquisition is incomplete')
    images=json.loads((ROOT/'reports/completion-image-acquisition.json').read_bytes())['images']
    pins={row['repository'].split('/')[-1]:row['manifest_digest'] for row in images}
    if set(pins)!={'python','node'} or any(not re.fullmatch(r'sha256:[0-9a-f]{64}',v) for v in pins.values()):raise ValueError('Immutable base images required')
    selected={}
    def add(relative,raw):
        path=safe_relative(relative)
        if str(path) in selected or len(raw)>64*1024**2:raise ValueError('Duplicate or oversized source')
        selected[str(path)]=raw
    seen=set()
    for row in book['artifacts']:
        name=row['name']
        if name in seen or name not in EXPECTED:raise ValueError('Duplicate/unreviewed package')
        seen.add(name);version,size,sha=EXPECTED[name];path=Path(row['path'])
        if path.is_symlink() or not path.resolve().is_relative_to(Path('D:/RiwaqRuntime/documents-linux-v1').resolve()):raise ValueError('Unexpected wheel path')
        raw=path.read_bytes()
        if (row['version'],len(raw),digest(raw))!=(version,size,sha):raise ValueError('Wheel integrity mismatch')
        with zipfile.ZipFile(path) as archive:
            rows=archive.infolist()
            if len(rows)>4000 or sum(r.file_size for r in rows)>64*1024**2:raise ValueError('Wheel expanded limit')
            for member in rows:
                relative=safe_relative(member.filename)
                if member.is_dir():continue
                if stat.S_ISLNK(member.external_attr>>16) or member.flag_bits&1 or relative.suffix.lower() in {'.pth','.dll','.pyd','.exe'}:raise ValueError('Unreviewed wheel entry')
                add('runtime/python-packages/'+str(relative),archive.read(member))
    manifest=json.loads((RUNTIME/'runtime-manifest.json').read_bytes())
    for name,record in manifest['files'].items():
        if not name.startswith(('node_modules/','licenses/')):continue
        relative=safe_relative(name);source=RUNTIME/str(relative)
        if source.is_symlink() or not source.resolve().is_relative_to(RUNTIME.resolve()):raise ValueError('Runtime path escape')
        raw=source.read_bytes()
        if len(raw)!=record['bytes'] or digest(raw)!=record['sha256']:raise ValueError('OCR dependency changed')
        if source.suffix.lower() in {'.node','.dll','.pyd','.exe'}:raise ValueError('Native host OCR dependency cannot be copied')
        add('runtime/'+str(relative),raw)
    for language,record in manifest['languages'].items():
        if language not in {'ara','eng','heb'}:raise ValueError('Unexpected language')
        raw=(RUNTIME/'tessdata'/(language+'.traineddata')).read_bytes()
        if len(raw)!=record['bytes'] or digest(raw)!=record['sha256']:raise ValueError('OCR language changed')
        add('runtime/tessdata/'+language+'.traineddata',raw)
    clean={'version':manifest['version'],'tessdata_revision':manifest['tessdata_revision'],'node_packages':{key:{k:v for k,v in value.items() if k in {'version','license'}} for key,value in manifest['node_packages'].items()},
           'languages':manifest['languages'],'files':{key:value for key,value in manifest['files'].items() if key.startswith('node_modules/')},'python_packages':{key:{'version':value[0]} for key,value in EXPECTED.items()}}
    add('runtime/runtime-manifest.json',json.dumps(clean,sort_keys=True,indent=2).encode())
    for relative in ['backend/document_extract.py','backend/document_region.py','backend/image_normalize.py','backend/isolated_document_page.py','scripts/isolated_document_worker.py','scripts/document_ocr.mjs']:
        add('source/'+relative,(ROOT/relative).read_bytes())
    add('source/backend/__init__.py',b'')
    if len(selected)>12000 or sum(map(len,selected.values()))>160*1024**2:raise ValueError('Build context limit')
    output.mkdir(parents=True)
    for relative,raw in selected.items():
        destination=output/relative;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(raw)
    dockerfile=f'''FROM docker.io/library/node@{pins['node']} AS node
FROM docker.io/library/python@{pins['python']}
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/LICENSE /opt/riwaq/licenses/Node-LICENSE
COPY runtime /opt/document-runtime
COPY source /opt/riwaq
ENV PYTHONDONTWRITEBYTECODE=1
USER 65534:65534
'''
    (output/'Dockerfile').write_text(dockerfile,encoding='utf-8')
    result={'version':'isolated-document-image-v1','base_images':pins,'wheel_packages':[{k:r[k] for k in ('name','version','bytes','sha256')} for r in book['artifacts']],
            'files':[{'path':p,'bytes':len(raw),'sha256':digest(raw)} for p,raw in sorted(selected.items())],
            'dockerfile_sha256':digest(dockerfile.encode()),'total_copy_bytes':sum(map(len,selected.values())),
            'licenses':'Wheel dist-info licenses and PDFium notices, OCR node-package notices, tessdata license provenance retained; Node LICENSE copied directly from the pinned official image.',
            'network_required':False,'installer_scripts_executed':False,'required_build_options':['--network=none','--pull=false'],'build_executed':False}
    (output/'manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();value=prepare(args.output)
    print(json.dumps({'files':len(value['files']),'copy_bytes':value['total_copy_bytes'],'build_executed':False}))
