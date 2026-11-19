"""Fixed container-only bootstraps. These strings must NEVER run on the host.

The supervisor does not treat their output or test counts as trusted evidence.
No shell, dependency install, source-selected executable or host mount is used.
"""

PYTHON = r'''
import base64, json, os, pathlib, re, sys, unittest
raw = sys.stdin.buffer.read(15000001)
if len(raw) > 15000000: raise ValueError('payload limit')
data = json.loads(raw)
if set(data) != {'files', 'targets'}: raise ValueError('payload keys')
files, targets = data['files'], data['targets']
if not isinstance(files, dict) or not 1 <= len(files) <= 256: raise ValueError('file limit')
if not isinstance(targets, list) or not 1 <= len(targets) <= 20: raise ValueError('target limit')
root = pathlib.Path('/tmp/riwaq-work'); root.mkdir(mode=0o700)
total = 0
for name, encoded in files.items():
    if not isinstance(name, str) or len(name) > 512 or any(c in name for c in '\\:\x00'):
        raise ValueError('path')
    if name.startswith('/') or any(p in {'', '.', '..'} for p in name.split('/')): raise ValueError('path')
    content = base64.b64decode(encoded, validate=True); total += len(content)
    if len(content) > 1048576 or total > 10485760: raise ValueError('content limit')
    path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
if any(not isinstance(t, str) or t not in files or t.startswith('-') or not t.endswith('.py') for t in targets):
    raise ValueError('target')
os.chdir(root); sys.path.insert(0, str(root)); sys.argv = ['unittest', *targets]
unittest.main(module=None)
'''.strip()

NODE = r'''
'use strict';
const fs = require('node:fs'), path = require('node:path'), cp = require('node:child_process');
const parts = []; let length = 0;
for (;;) {
  const b = Buffer.alloc(65536); const n = fs.readSync(0, b, 0, b.length, null);
  if (!n) break; length += n; if (length > 15000000) throw Error('payload limit'); parts.push(b.subarray(0,n));
}
const data = JSON.parse(Buffer.concat(parts).toString('utf8'));
if (Object.keys(data).sort().join(',') !== 'files,targets') throw Error('payload keys');
const files = data.files, targets = data.targets, entries = Object.entries(files);
if (Array.isArray(files) || !entries.length || entries.length > 256) throw Error('file limit');
if (!Array.isArray(targets) || !targets.length || targets.length > 20) throw Error('target limit');
const root='/tmp/riwaq-work'; fs.mkdirSync(root, {mode:0o700}); let total=0;
for (const [name, encoded] of entries) {
  if (!name || name.length>512 || /[\\:\x00]/.test(name) || name.startsWith('/') || name.split('/').some(p=>!p||p==='.'||p==='..')) throw Error('path');
  if (typeof encoded!=='string' || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(encoded)) throw Error('base64');
  const content=Buffer.from(encoded,'base64'); total+=content.length;
  if(content.length>1048576 || total>10485760) throw Error('content limit');
  const dest=path.join(root,name); fs.mkdirSync(path.dirname(dest),{recursive:true}); fs.writeFileSync(dest,content,{flag:'wx',mode:0o600});
}
if(targets.some(t=>typeof t!=='string'||!Object.hasOwn(files,t)||t.startsWith('-')||!(/\.(?:js|mjs|cjs)$/).test(t))) throw Error('target');
const result=cp.spawnSync('/usr/local/bin/node',['--test',...targets],{cwd:root,stdio:'inherit',env:{PATH:'/usr/local/bin:/usr/bin:/bin',HOME:'/tmp',LANG:'C.UTF-8'}});
process.exit(Number.isInteger(result.status)?result.status:1);
'''.strip()

RUNNERS = {
    'media_decode': ('/usr/local/bin/python', ['-I', '-B', '/opt/riwaq/scripts/isolated_audio_worker.py']),
    'python_unittest': ('/usr/local/bin/python', ['-I', '-B', '-c', PYTHON]),
    'node_test': ('/usr/local/bin/node', ['--eval', NODE]),
    'document_extract': ('/usr/local/bin/python', ['-I', '-B', '-c',
        "import sys; sys.path.insert(0, '/opt/riwaq'); from isolated_parsers import main; raise SystemExit(main())"]),
    'document_page': ('/usr/local/bin/python', ['-I', '-B', '/opt/riwaq/scripts/isolated_document_worker.py']),
    'skill_transform': ('/usr/local/bin/python', ['-I', '-B', '/opt/riwaq/scripts/isolated_skill_transform.py']),
}
