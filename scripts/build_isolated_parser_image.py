"""Prepare a reviewed, offline Docker build context; NEVER builds/pulls itself.

Only the installed pure-Python pypdf source and its license are vendored. No
whole environment, credentials, user documents or optional native extras.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]


def prepare(output: Path, base_image: str):
    if not re.fullmatch(r'(?:docker\.io/library/)?python@sha256:[0-9a-f]{64}', base_image):
        raise ValueError('An immutable official Python repository digest is required')
    output = output.resolve()
    if output.exists(): raise ValueError('Use a new empty build-context path; existing evidence is not overwritten')
    dist = importlib.metadata.distribution('pypdf')
    if dist.version != '6.21.4': raise ValueError('Review the changed installed pypdf version before building')
    source = Path(dist.locate_file('pypdf')).resolve()
    selected = []
    for entry in dist.files or []:
        path = Path(str(entry))
        if path.parts and path.parts[0] == 'pypdf' and (path.suffix in {'.py', '.txt'} or path.name == 'py.typed'):
            original = Path(dist.locate_file(entry))
            if original.is_symlink() or not original.resolve().is_relative_to(source): raise ValueError('Invalid package source path')
            selected.append((original, path))
    licenses = [Path(dist.locate_file(p)) for p in dist.files or [] if str(p).endswith('.dist-info/licenses/LICENSE')]
    if len(licenses) != 1 or not selected: raise ValueError('Package source/license inventory unavailable')
    selected += [(licenses[0], Path('licenses/pypdf-LICENSE')),
                 (ROOT/'backend/isolated_parsers.py', Path('isolated_parsers.py'))]
    if any(p.is_symlink() or not p.is_file() or p.stat().st_size > 4*1024**2 for p, _ in selected): raise ValueError('Unexpected vendored source')
    if sum(p.stat().st_size for p, _ in selected) > 16*1024**2: raise ValueError('Build-context size limit')
    output.mkdir(parents=True)
    files = []
    for original, relative in selected:
        target = output/relative; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(original, target)
        files.append({'path': relative.as_posix(), 'bytes': target.stat().st_size, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    dockerfile = f'FROM {base_image}\nCOPY pypdf /opt/riwaq/pypdf\nCOPY isolated_parsers.py /opt/riwaq/isolated_parsers.py\nCOPY licenses /opt/riwaq/licenses\nUSER 65534:65534\n'
    (output/'Dockerfile').write_text(dockerfile, encoding='utf-8')
    manifest = {'version': 'isolated-parser-image-v1', 'base_image': base_image, 'pypdf': dist.version,
                'files': files, 'dockerfile_sha256': hashlib.sha256(dockerfile.encode()).hexdigest(),
                'network_required': False, 'optional_extras': [], 'build_executed': False,
                'build_options_required': ['--network=none', '--pull=false']}
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True); parser.add_argument('--base-image', required=True)
    args = parser.parse_args(); result = prepare(args.output, args.base_image)
    print(json.dumps({'files': len(result['files']), 'pypdf': result['pypdf'], 'build_executed': False}))
