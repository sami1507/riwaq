"""Verify the portable build inventory, without starting any executable."""
import argparse
import hashlib
import json
from pathlib import Path, PureWindowsPath


def verify(folder):
    root=Path(folder).resolve(strict=True)
    manifest=root/'manifest.json'
    if manifest.stat().st_size>1024**2:
        raise ValueError('Manifest exceeds limit')
    data=json.loads(manifest.read_text(encoding='utf-8-sig'))
    rows=data.get('files')
    if not isinstance(rows,list) or not 3<=len(rows)<=512:
        raise ValueError('Invalid manifest inventory')
    checked=[]; names=set()
    for row in rows:
        name=row['name']; relative=PureWindowsPath(name)
        if relative.is_absolute() or relative.drive or any(p in ('..','.') for p in relative.parts):
            raise ValueError('Manifest path must stay inside package')
        path=root.joinpath(*relative.parts)
        resolved=path.resolve(strict=True)
        if not resolved.is_relative_to(root) or path.is_symlink() or not path.is_file():
            raise ValueError('Invalid package member')
        canonical='/'.join(relative.parts).casefold()
        if canonical in names:
            raise ValueError('Duplicate package member')
        names.add(canonical)
        with path.open('rb') as stream:
            digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if path.stat().st_size!=row['bytes'] or digest!=row['sha256'].lower():
            raise ValueError('Package member differs from manifest: '+name)
        checked.append({'path':name,'bytes':path.stat().st_size,'sha256':digest})
    if not {'riwaq.exe','riwaq-service.exe','scripts/pair-browser.ps1'}<=names:
        raise ValueError('Native executable, service or explicit pairing helper missing')
    return {'passed':True,'scope':'File inventory and exact hashes only; no native process, browser, model or pairing execution.',
            'package_root':str(root),'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
            'runtime_tested_by_this_check':False,'files':checked}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    report=verify(args.root)
    output=Path(args.output)
    if output.exists():
        raise SystemExit('Refuse to overwrite an existing verification report')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'checked_files':len(report['files']),'runtime_tested':False}))
