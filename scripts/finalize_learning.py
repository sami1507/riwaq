"""Record source changes against the preserved learning baseline; never edit source."""
from pathlib import Path
import difflib
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
OUT=ROOT/'reports/learning-20261116'

def main():
    from backend.skill_release import current_source_fingerprint
    prior=json.loads((OUT/'baseline-manifest.json').read_bytes())
    old={r['path']:r for r in prior['files']}
    names=set(old)
    for folder in ['backend','frontend/src','frontend/scripts','config','skills','tests','docs','scripts','evaluation','sync_server','desktop','training']:
        found=subprocess.run(['rg','--files',folder],cwd=ROOT,capture_output=True,text=True,check=True)
        names.update(n.replace('\\','/') for n in found.stdout.splitlines())
    names={n for n in names if '__pycache__' not in n and not n.endswith(('.pyc','.pyo')) and '/target/' not in n}
    records=[];changes=[];patch=[];unbaselined=[]
    for name in sorted(names):
        path=ROOT/name
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):raise ValueError('Linked source is outside the delivery manifest')
        if not path.is_file():
            if name in old:changes.append({'path':name,'change':'deleted'})
            continue
        raw=path.read_bytes()
        record={'path':name,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
        records.append(record)
        if name in old and record['sha256']==old[name]['sha256']:continue
        # Earlier delivery inventories excluded training/. Most files there
        # predate this task. Missing from a manifest does not mean newly made.
        if name.startswith('training/') and name not in old and name!='training/learning_data.py':
            unbaselined.append(record)
            if name=='training/validate_data.py':
                changes.append({'path':name,'change':'modified','baseline_contents_available':False,
                                'evidence':'evaluation/data-preflight-before-after.json; evaluation/data-preflight-legacy-closure.json'})
                patch.append('Existing file modified, original bytes not in baseline: '+name+'\n')
            continue
        changes.append({'path':name,'change':'modified' if name in old else 'added'})
        before=(OUT/'baseline'/name).read_bytes() if name in old else b''
        try:
            left=before.decode('utf-8-sig');right=raw.decode('utf-8-sig')
            if '\0' in left or '\0' in right:raise UnicodeError()
        except UnicodeError:
            patch.append(f'Binary content changed: {name}\n');continue
        patch.extend(difflib.unified_diff(left.splitlines(keepends=True),right.splitlines(keepends=True),fromfile='baseline/'+name,tofile='current/'+name))
    source_hash=hashlib.sha256(json.dumps(records,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    result={'recorded_at':datetime.now(timezone.utc).isoformat(),'git_present':False,
            'baseline':'baseline-manifest.json','source_sha256':source_hash,
            'release_gate_fingerprint':current_source_fingerprint(ROOT),'files':records,'changes':changes,
            'preexisting_training_inventory_without_baseline':unbaselined,
            'scope':'Source snapshot; test results and UI evidence retain their own environment and timestamps.'}
    (OUT/'final-source-manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'changes.patch').write_text(''.join(patch),encoding='utf-8')
    print(json.dumps({'files':len(records),'changes':len(changes),'deleted':[x['path'] for x in changes if x['change']=='deleted'],'source_sha256':source_hash,'release_gate_fingerprint':result['release_gate_fingerprint']}))

if __name__=='__main__':main()
