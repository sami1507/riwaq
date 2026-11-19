"""Preserve the prior checkout and record a reviewable Batch04 source manifest."""
from datetime import datetime,timezone
import difflib,hashlib,json,re,subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'reports/skills-batch-04-20261116'

def main():
    baseline=json.loads((REPORT/'baseline-manifest.json').read_bytes())
    before={r['path']:r for r in baseline['files']}
    names=subprocess.run(['rg','--files','backend','frontend/src','frontend/scripts','config','skills','tests','docs','scripts'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.splitlines()
    names={n.replace('\\','/') for n in names}|set(before)|{'README.md','PROJECT_STATE.md','.env.example','requirements.txt','requirements.lock.txt'}
    names={n for n in names if '__pycache__' not in n and not n.endswith(('.pyc','.pyo'))}
    records=[];changes=[];patch=[]
    for name in sorted(names):
        path=ROOT/name
        if not path.is_file():
            if name in before:changes.append({'path':name,'state':'missing'})
            continue
        raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest()
        records.append({'path':name,'sha256':sha,'bytes':len(raw)})
        if name not in before or before[name]['sha256']!=sha:
            changes.append({'path':name,'state':'added' if name not in before else 'changed','sha256':sha})
            old_path=REPORT/'baseline'/name
            try:
                old=old_path.read_bytes().decode('utf-8-sig').splitlines(keepends=True) if old_path.is_file() else []
                patch.extend(difflib.unified_diff(old,raw.decode('utf-8-sig').splitlines(keepends=True),fromfile='baseline/'+name,tofile='current/'+name))
            except UnicodeDecodeError:pass
    aggregate=hashlib.sha256(json.dumps(records,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    result={'recorded_at':datetime.now(timezone.utc).isoformat(),'environment':'Windows source checkout; no Git','source_sha256':aggregate,'files':records,'changes':changes,'baseline_missing_files':[r['path'] for r in changes if r['state']=='missing']}
    (REPORT/'final-source-manifest.json').write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode())
    (REPORT/'changes.patch').write_bytes(''.join(patch).encode())
    catalog=json.loads((ROOT/'config/skills-batch-01.json').read_bytes())
    old_catalog=json.loads((REPORT/'baseline/config/skills-batch-01.json').read_bytes())
    packages={s['slug']:hashlib.sha256((ROOT/'skills'/s['slug']/'SKILL.md').read_bytes()).hexdigest()==s['instructions_sha256'] for s in catalog['skills'][75:]}
    ui_path='ui/2026-11-16T10-24-28.069Z/report.json'
    ui=json.loads((REPORT/ui_path).read_bytes())
    ui_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha for name,sha in ui['code_sha256'].items()}
    suites={}
    for name in ['baseline-tests.xml','planning/tests-final.xml','schedule/final-kernel-tests.xml','regression-full.xml','productivity-final.xml','entry-final.xml','credentials.xml','prior-data.xml']:
        nodes=ET.parse(REPORT/name).getroot().findall('.//testsuite')
        totals={key:sum(int(n.get(key,'0')) for n in nodes) for key in ['tests','failures','errors','skipped']}
        totals['passed']=totals['tests']-totals['failures']-totals['errors']-totals['skipped']
        totals['junit_seconds']=sum(float(n.get('time','0')) for n in nodes)
        suites[name]=totals
    documents={};broken=[]
    for name in ['SPEC','ACCEPTANCE','REPORT']:
        doc=ROOT/f'docs/skills/SKILLS_BATCH_04_{name}.md';body=doc.read_text('utf-8')
        if name=='ACCEPTANCE':
            rows=re.findall(r'^\| (\d+) [a-z-]+ \|',body,re.M)
            documents['acceptance_rows_in_order']=[int(n) for n in rows]==list(range(76,101))
        for target in re.findall(r'\]\(([^)]+)\)',body):
            if '://' in target or target.startswith('#'):continue
            if not (doc.parent/target.split('#')[0]).exists():broken.append(f'{name}:{target}')
    checks={'no_original_files_missing':not result['baseline_missing_files'],'registry_numbers_1_to_100':[s['number'] for s in catalog['skills']]==list(range(1,101)),'first_75_records_unchanged':catalog['skills'][:75]==old_catalog['skills'],'new_package_hashes_match':all(packages.values()),'final_ui_source_hashes_match':all(ui_hashes.values()),'all_recorded_final_suites_have_no_failures':all(not (s['failures'] or s['errors']) for s in suites.values()),'all_real_browser_cases_passed':all(c['passed'] for c in ui['real_cases']),'mocked_browser_contract_passed':all(c['passed'] for c in ui['contract_cases']),'document_links_exist':not broken,**documents}
    review={'recorded_at':datetime.now(timezone.utc).isoformat(),'source_sha256':aggregate,'checks':checks,'broken_links':broken,'test_suites':suites,'test_count_note':'Suites overlap; full regression predates final targeted changes. Use productivity-final and entry-final for the affected final backend paths; UI report for final frontend. Do not add counts.','browser':{'report':ui_path,'real_cases':len(ui['real_cases']),'mocked_contract_cases':len(ui['contract_cases']),'page_errors':len(ui['page_errors']),'external_requests':len(ui['external_browser_requests']),'source_hashes':ui_hashes},'native_post_pairing':'deferred_by_user','live_external_smtp':'unverified; owned SMTP fixture only','external_mailbox_and_calendar_adapters':'not_implemented','device_notifications':'not_implemented; persistent in-app inbox only','status':'passed' if all(checks.values()) else 'failed'}
    (REPORT/'delivery-review.json').write_bytes((json.dumps(review,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps({'source_sha256':aggregate,'files':len(records),'changes':len(changes),'missing':result['baseline_missing_files']}))
    print(json.dumps({'delivery_audit':review['status'],'checks':checks}))
    if review['status']!='passed':raise SystemExit(1)

if __name__=='__main__':main()
