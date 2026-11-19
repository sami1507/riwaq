"""Record reviewable source hashes/diffs; never changes product data or the baseline."""
from datetime import datetime,timezone
import difflib,hashlib,json,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'reports/skills-batch-03-20261116'

def main():
    baseline=json.loads((REPORT/'baseline-manifest.json').read_bytes())
    before={x['path']:x for x in baseline['files']}
    names=subprocess.run(['rg','--files','backend','frontend/src','frontend/scripts','config','skills','tests','docs','scripts'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.splitlines()
    names=set(x.replace('\\','/') for x in names)|set(before)|{'README.md','PROJECT_STATE.md','.env.example'}
    names={x for x in names if '__pycache__' not in x and not x.endswith(('.pyc','.pyo'))}
    new_modules={'skill_batch03','skill_files','skill_files_api','skill_data','skill_documents','isolated_skill_documents','skill_transform','skill_transform_protocol','skill_web','skill_web_parser'}
    new_packages={s['slug'] for s in json.loads((ROOT/'config/skills-batch-01.json').read_bytes())['skills'] if s['number']>=51}
    def created_here(name):
        return (name in {f'backend/{x}.py' for x in new_modules} or name=='frontend/src/SkillTransforms.tsx' or name=='frontend/scripts/skills-batch03-ui.mjs'
                or name in {'scripts/register_batch03.py','scripts/finalize_batch03.py','scripts/isolated_skill_transform.py','scripts/build_skill_transform_image.py'}
                or name.startswith('docs/skills/SKILLS_BATCH_03_') or name.startswith('tests/test_skill_batch03_')
                or name=='tests/fixtures/skill_routing/cases-batch03.json' or any(name.startswith('skills/'+slug+'/') for slug in new_packages))
    rows=[];changes=[];patch=[]
    for name in sorted(names):
        path=ROOT/name
        if not path.is_file():
            if name in before:changes.append({'path':name,'state':'missing'})
            continue
        raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
        rows.append({'path':name,'sha256':digest,'bytes':len(raw)})
        if name not in before or digest!=before[name]['sha256']:
            state='changed' if name in before else 'added' if created_here(name) else 'not_captured_by_baseline'
            changes.append({'path':name,'state':state,'sha256':digest})
            saved=REPORT/'baseline'/name
            if state=='not_captured_by_baseline' or name in before and not saved.is_file():continue
            try:
                old=saved.read_bytes().decode('utf-8-sig').splitlines(keepends=True) if saved.is_file() else []
                current=raw.decode('utf-8-sig').splitlines(keepends=True)
                patch.extend(difflib.unified_diff(old,current,fromfile='baseline/'+name,tofile='current/'+name))
            except UnicodeDecodeError:pass
    aggregate=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    result={'recorded_at':datetime.now(timezone.utc).isoformat(),'environment':'Windows user PC; source checkout; no Git directory',
            'source_sha256':aggregate,'files':rows,'changes':changes,'baseline_missing_files':[x['path'] for x in changes if x['state']=='missing'],
            'baseline_coverage_note':'419 captured files. Existing frontend scripts and .env.example were not in the initial snapshot; they are not called newly created files or given fabricated before diffs.'}
    (REPORT/'final-source-manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (REPORT/'changes.patch').write_text(''.join(patch),encoding='utf-8')
    print(json.dumps({'files':len(rows),'changes':len(changes),'missing':result['baseline_missing_files'],'source_sha256':aggregate}))

if __name__=='__main__':main()
