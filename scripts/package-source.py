"""Create a source/web delivery without dependencies, user databases, or secrets."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import zipfile

parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--label',default='');args=parser.parse_args()
if args.label and not re.fullmatch(r'[A-Za-z0-9._-]+',args.label):raise SystemExit('Package label must contain safe filename characters only')
suffix='-'+args.label if args.label else ''
root=Path(__file__).resolve().parent.parent
destination=Path(args.output);destination.mkdir(parents=True,exist_ok=True)
dirs=['backend','sync_server','frontend','desktop','tests','scripts','training','docs','reports','knowledge','evaluation','config']
excluded={'node_modules','__pycache__','.pytest_cache','target','binaries','.tools','.qa-workspace','dist','test-results','playwright-report'}
media_extensions={'.wav','.mp3','.mp4','.webm','.ogg','.opus','.flac','.m4a','.aac','.wma','.mov','.avi'}
top=['README.md','PROJECT_STATE.md','requirements.txt','requirements.lock.txt','pytest.ini','.gitignore','.env.example']
files=[root/name for name in top]
for name in dirs:
    for current,folders,names in os.walk(root/name,followlinks=False):
        folders[:]=[d for d in folders if d not in excluded and not d.endswith('-runtime')]
        # Runtime media evidence may contain recordings whose redistribution was
        # never authorized. Ship reports/screenshots; keep audio/video local.
        files.extend(Path(current)/n for n in names if not n.endswith(('.pyc','.tsbuildinfo','.log','.sqlite','.sqlite3','.db')) and n!='.env'
            and not (Path(current).is_relative_to(root/'reports') and Path(n).suffix.lower() in media_extensions))
archive=destination/f'Riwaq-0.1.0{suffix}-source.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for file in sorted(files):
        if file.is_file():z.write(file,'Riwaq/'+file.relative_to(root).as_posix())
web=destination/f'Riwaq-0.1.0{suffix}-web.zip'
with zipfile.ZipFile(web,'w',zipfile.ZIP_DEFLATED) as z:
    for file in (root/'frontend/dist').rglob('*'):
        if file.is_file():z.write(file,file.relative_to(root/'frontend/dist'))
manifest={'source':{'name':archive.name,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'files':len(files)},'web':{'name':web.name,'sha256':hashlib.sha256(web.read_bytes()).hexdigest(),'requires':'Riwaq FastAPI API; static assets alone do not provide AI'},'windows_native_ready':False,'reason':'Application Control OS4551 blocked native build and packaged service execution'}
(destination/f'manifest{suffix}.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(json.dumps(manifest,indent=2))
