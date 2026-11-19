"""Offline layer preparation from three hash-pinned wheels; no installer scripts."""
from pathlib import Path,PurePosixPath
import argparse,hashlib,json,stat,zipfile
ROOT=Path(__file__).resolve().parents[1]
EXPECTED={'segno':('1.6.6','28c7d081ed0cf935e0411293a465efd4d500704072cdb039778a2ab8736190c7'),
 'python-barcode':('0.16.1','5776567478c9a0dae473374bb86631ba0b5ea99aaf302763b364e367ac51f367'),
 'zxing-cpp':('3.1.1','9cf67341949946307d086b302cefd453fb47bc6d6ddc7d088839e9481982757b')}
BASE='sha256:3af2ee5223f18fe18139795ec01580ad5b8ffa45efcaad5a81a1f15fa1a8696a'


def prepare(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    acquired=ROOT/'reports/closure-20261116/visual-codes/acquisition';ledger=json.loads((acquired/'ledger.json').read_text())
    if ledger['state']!='complete' or len(ledger['packages'])!=3:raise ValueError('Incomplete acquisition')
    records=[];total=0
    def put(path,raw):
        nonlocal total
        total+=len(raw)
        if total>20*1024**2:raise ValueError('Context bounds')
        target=output/path;target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():raise ValueError('Duplicate output')
        target.write_bytes(raw);records.append({'path':str(path),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    for item in ledger['packages']:
        expected=EXPECTED[item['name']];path=acquired/item['filename']
        if item['version']!=expected[0] or hashlib.sha256(path.read_bytes()).hexdigest()!=expected[1]:raise ValueError('Wheel changed')
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                relative=PurePosixPath(member.filename)
                if relative.is_absolute() or '..' in relative.parts or '\\' in member.filename or ':' in member.filename or stat.S_ISLNK(member.external_attr>>16) or member.file_size>5*1024**2:raise ValueError('Unsafe wheel member')
                if member.is_dir():continue
                put(Path('packages')/Path(*relative.parts),archive.read(member))
    for name in ('backend/isolated_visual_codes.py','backend/visual_codes_worker.py','backend/image_normalize.py','backend/isolated_document_page.py','scripts/isolated_document_worker.py'):
        put(Path('source')/name,(ROOT/name).read_bytes())
    dockerfile='FROM '+BASE+'\nCOPY packages /opt/document-runtime/python-packages\nCOPY source /opt/riwaq\n'
    put(Path('Dockerfile'),dockerfile.encode())
    manifest={'base_image':BASE,'files':records,'total_bytes':total,'wheel_pins':EXPECTED,'no_install_scripts':True,'network':False,'licenses':'Complete wheel dist-info licenses retained in container','required_build_options':['--network=none','--pull=false']}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps({'files':len(records),'bytes':total}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path);args=parser.parse_args();prepare(args.output)
