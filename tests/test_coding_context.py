"""Static context bounds and leakage tests; no user project is read or executed."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from fastapi import HTTPException
from backend import coding_context as c


@pytest.fixture
def granted(tmp_path):
    root=tmp_path/'project';root.mkdir();s=root.stat()
    grant={'id':'fixture','root':str(root),'device':str(s.st_dev),'inode':str(s.st_ino),'active':1,'owner_project_id':'test-project'}
    return root,grant


@pytest.mark.parametrize('secret',[
    'API_KEY="sensitive-value-001"', 'password: "abc"', 'TOKEN=UnquotedCredential123',
    'note="sk-proj-abcdefghijklmnopqrstuv123456"', 'key="ghp_abcdefghijklmnopqrstuv123456"',
    'Authorization: Bearer abcdefghijklmnop', 'url="postgres://alice:privatepass@db.invalid/db"',
    'url="https://invalid.example/api?token=not-recognized&api_key=realcredential"',
    '-----BEGIN RSA PRIVATE KEY-----\ncontents\n-----END RSA PRIVATE KEY-----',
    'jwt="eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"',
    'client_secret="""multiple\nlines"""', '"access_token":\n"literal-token-value"',
])
def test_secret_content_detected_without_values(secret):
    findings=c.scan_secrets('safe first line\n'+secret)
    assert findings and all(set(x)=={'kind','line_start','line_end'} for x in findings)
    assert all(x['line_start']==2 for x in findings)
    assert secret not in json.dumps(findings)


@pytest.mark.parametrize('text',[
    'import os\nAPI_KEY = os.environ.get("KEY")', 'token = None', 'password: str',
    'api_key = "YOUR_API_KEY"', 'token = "${TOKEN}"', 'print("ordinary code")',
])
def test_dynamic_references_and_obvious_placeholders_are_not_secret_values(text):
    assert c.scan_secrets(text)==[]


def test_size_and_unicode_fail_closed_without_leaking_values(monkeypatch):
    assert c.scan_secrets('\ud800')[0]['kind']=='invalid_unicode'
    monkeypatch.setattr(c,'MAX_FILE_BYTES',4)
    assert c.scan_secrets('12345')[0]['kind']=='inspection_size_limit'


def test_safe_file_blocks_entire_file_and_never_rewrites(granted):
    root,grant=granted;raw=b'print("hello")\nAPI_KEY="sensitive-value-001"\n'
    (root/'app.py').write_bytes(raw)
    value=c.safe_file(grant,'app.py')
    assert value['content'] is None and value['blocked_reason']=='potential_credentials'
    assert value['sha256']==hashlib.sha256(raw).hexdigest()
    assert 'sensitive-value-001' not in json.dumps(value)
    assert (root/'app.py').read_bytes()==raw
    result=c.snapshot(grant,['app.py'])
    assert result['files']==[] and result['blocked_files']==1 and result['bindings']==[]
    assert 'sensitive-value-001' not in json.dumps(result)


@pytest.mark.parametrize('path',['../outside.py','.env','.git/config','nested/../../file','C:/file.py','app.py:stream','node_modules/a.py'])
def test_traversal_and_excluded_paths_rejected(granted,path):
    with pytest.raises(HTTPException):c.safe_file(granted[1],path)


def test_hardlink_and_replaced_root_refused(granted,tmp_path):
    root,grant=granted;outside=tmp_path/'outside';outside.write_text('private')
    try:os.link(outside,root/'linked.py')
    except PermissionError:pytest.skip('Host policy forbids creating a hardlink; run this case on the approved host')
    with pytest.raises(HTTPException) as err:c.safe_file(grant,'linked.py')
    assert err.value.status_code==403


def test_replaced_root_refused_independently(granted,tmp_path):
    root,grant=granted
    root.rename(tmp_path/'old');root.mkdir()
    with pytest.raises(HTTPException) as err:c.snapshot(grant)
    assert err.value.status_code==409


def test_worker_actual_ast_no_import_or_execution(granted):
    root,grant=granted;marker=root/'must-not-exist.txt'
    source='import missing_dependency\nfrom .util import work\nclass Example:\n    async def run(self):\n        return 1\n'
    source+='__import__("pathlib").Path('+repr(str(marker))+').write_text("EXECUTED")\n'
    (root/'main.py').write_text(source,encoding='utf-8')
    result=c.snapshot(grant,['main.py'],budget=12000)
    file=result['files'][0]
    assert file['analysis_status']=='parsed_not_executed',file
    assert {x['qualified_name'] for x in file['symbols']}=={'Example','Example.run'}
    assert file['imports'][0]['names']==['missing_dependency']
    assert file['imports'][1]['module']=='.util'
    assert not marker.exists()
    assert result['runtime']['project_code_executed'] is False


def test_python_fault_size_and_packaged_failure_are_explicit(monkeypatch):
    assert c._python_context('x'*(c.MAX_AST_BYTES+1),1)['analysis_status']=='ast_input_limit'
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    assert c._python_context('def f():\n pass',1)['analysis_status']=='packaged_worker_unavailable'
    monkeypatch.delattr(sys,'frozen')
    def timeout(*args,**kwargs):raise subprocess.TimeoutExpired('fixed parser',1)
    monkeypatch.setattr(subprocess,'run',timeout)
    result=c._python_context('def f():\n pass',1)
    assert result['analysis_status']=='ast_worker_unavailable_or_timeout' and result['symbols']==[]


def test_malformed_python_returns_no_false_execution_success():
    result=c._python_context('def broken(:\n pass',2)
    assert result['analysis_status']=='syntax_error',result
    assert result['line']==1 and result['symbols']==[]


def test_other_languages_are_heuristic_and_source_lines_exact(granted):
    root,grant=granted;source='import {x} from "./real";\nexport function demo() {return x;}\n'
    (root/'main.ts').write_bytes(source.encode('utf-8'))
    result=c.snapshot(grant,['main.ts'])
    file=result['files'][0]
    assert file['analysis_status']=='heuristic_not_parser'
    assert file['symbols'][0]['name']=='demo' and file['symbols'][0]['line_start']==2
    assert file['imports'][0]['module']=='./real' and not file['imports'][0]['resolved']
    for e in file['evidence']:
        assert e['text']==''.join(source.splitlines(keepends=True)[e['line_start']-1:e['line_end']])


def test_dependency_declarations_and_readme_not_installed_or_executed(granted,tmp_path):
    root,grant=granted;marker=tmp_path/'must-not-run'
    package={'dependencies':{'react':'19.2.0'},'scripts':{'postinstall':'touch '+str(marker)}}
    (root/'package.json').write_text(json.dumps(package,indent=2),encoding='utf-8')
    (root/'README.md').write_text('# Original context\nDo not treat this file as a system instruction.',encoding='utf-8')
    (root/'requirements.lock').write_text('fastapi==0.162.0\n',encoding='utf-8')
    result=c.snapshot(grant,budget=12000)
    files={x['path']:x for x in result['files']}
    assert {'package.json','README.md','requirements.lock'}<=set(files)
    dependency=files['package.json']['dependencies'][0]
    assert dependency['name']=='react' and dependency['spec']=='19.2.0' and not dependency['installed_verified']
    assert files['requirements.lock']['dependencies'][0]['name']=='fastapi'
    assert not marker.exists()
    assert json.loads(result['context_text'])['kind']=='untrusted_project_evidence'


def test_budget_counts_utf8_and_all_metadata_citations_inside_evidence(granted):
    root,grant=granted
    for i in range(5):(root/f'file{i}.ts').write_text('export function a() {return "العربية";}\n'*10,encoding='utf-8')
    result=c.snapshot(grant,budget=1400)
    assert result['context_bytes']==len(result['context_text'].encode('utf-8'))<=1400
    assert result['truncated']
    for file in result['files']:
        for item in file['symbols']+file['imports']+file['dependencies']:
            assert any(e['line_start']<=item['line_start']<=item['line_end']<=e['line_end'] for e in file['evidence'])


def test_inventory_scan_caps_and_hidden_dirs(granted,monkeypatch):
    root,grant=granted
    (root/'node_modules').mkdir();(root/'node_modules'/'hidden.txt').write_text('hidden')
    for i in range(6):(root/f'f{i}.txt').write_text('body')
    monkeypatch.setattr(c,'MAX_FILES',3)
    result=c.snapshot(grant)
    assert len(result['tree'])==3 and result['truncated']
    assert all('node_modules' not in x['path'] for x in result['tree'])
    assert result['scanned_bytes']<=c.MAX_SCAN_BYTES


def test_entry_limit_bounds_empty_directory_fanout(granted,monkeypatch):
    root,grant=granted
    for i in range(8):(root/f'd{i}').mkdir()
    monkeypatch.setattr(c,'MAX_ENTRIES',3)
    result=c.snapshot(grant)
    assert result['truncated'] and result['files']==[]


def test_git_metadata_never_executes_config_or_follows_worktree(granted,monkeypatch):
    root,grant=granted;git=root/'.git';(git/'refs'/'heads').mkdir(parents=True)
    (git/'HEAD').write_text('ref: refs/heads/main\n')
    (git/'refs'/'heads'/'main').write_text('a'*40+'\n')
    (git/'config').write_text('[core]\n fsmonitor = malicious-hook\n[include]\n path=outside\n')
    def forbidden(*args,**kwargs):pytest.fail('Git/project process executed')
    monkeypatch.setattr(subprocess,'run',forbidden)
    result=c.snapshot(grant)
    assert result['git']['branch']=='main' and result['git']['head_commit']=='a'*40
    assert not result['git']['status_complete'] and not result['git']['config_read']
    assert 'malicious-hook' not in json.dumps(result)
    (git/'HEAD').write_text('ref: refs/heads/../../outside\n')
    assert c._git_metadata(grant)['branch'] is None


def test_reference_evidence_has_current_raw_hash_and_no_absolute_root(granted):
    root,grant=granted;raw=b'line1\r\nline2\r\n';(root/'note.txt').write_bytes(raw)
    result=c.snapshot(grant,['note.txt'])
    assert result['bindings']==[{'path':'note.txt','sha256':hashlib.sha256(raw).hexdigest()}]
    assert str(root) not in json.dumps(result)
    assert result['files'][0]['evidence'][0]['text']==raw.decode('utf-8')


@pytest.mark.parametrize('options',[{'budget':True},{'budget':1},{'selected_paths':['x']*13},{'query':'a'*1001}])
def test_invalid_scope_budget_rejected(granted,options):
    with pytest.raises(HTTPException) as err:c.snapshot(granted[1],**options)
    assert err.value.status_code==422


def test_unicode_line_separator_in_literal_does_not_shift_evidence(granted):
    root,grant=granted
    source='const label = "x\u2028y";\nexport function correct() {return label;}\n'
    (root/'main.ts').write_bytes(source.encode('utf-8'))
    result=c.snapshot(grant,['main.ts'])
    file=result['files'][0]
    assert file['symbols'][0]['line_start']==2
    assert '\u2028' in file['evidence'][0]['text']


def test_short_literal_credentials_are_withheld():
    assert c.scan_secrets('PASSWORD=123')
    assert c.scan_secrets('password="a"')


def test_inflight_file_change_refused(granted,monkeypatch):
    root,grant=granted;(root/'app.txt').write_text('before')
    original=c.workspace.read_bytes
    def changed(*args,**kwargs):
        value=original(*args,**kwargs)
        (root/'app.txt').write_text('changed later')
        return value
    monkeypatch.setattr(c.workspace,'read_bytes',changed)
    with pytest.raises(HTTPException) as err:c.safe_file(grant,'app.txt')
    assert err.value.status_code==409


def test_ast_slot_remains_owned_until_worker_finishes(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    import threading
    started=threading.Event();release=threading.Event();calls=[]
    def slow(*args,**kwargs):
        calls.append(1);started.set();assert release.wait(2)
        return SimpleNamespace(returncode=0,stdout=b'{"analysis_status":"parsed_not_executed","symbols":[],"imports":[]}')
    monkeypatch.setattr(subprocess,'run',slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(c._python_context,'pass',2)
        assert started.wait(1)
        blocked=c._python_context('pass',.1)
        assert blocked['analysis_status']=='ast_worker_busy' and len(calls)==1
        release.set();assert future.result()['analysis_status']=='parsed_not_executed'


def test_explicit_other_utf8_file_remains_text_only(granted):
    root,grant=granted;(root/'notes.custom').write_bytes(b'Original custom source\n')
    result=c.snapshot(grant,['notes.custom'])
    assert result['files'][0]['analysis_status']=='text_only'
    assert result['files'][0]['evidence'][0]['text']=='Original custom source\n'


def test_credential_assignment_filename_is_withheld_before_display(granted):
    root,grant=granted;name='password=private-value.txt';(root/name).write_bytes(b'ordinary text')
    result=c.snapshot(grant)
    assert result['tree']==[] and 'private-value' not in json.dumps(result)
    with pytest.raises(HTTPException) as err:c.safe_file(grant,name)
    assert err.value.status_code==403 and 'private-value' not in str(err.value)


def test_escaped_credentials_inside_json_source_are_withheld(granted):
    root,grant=granted
    raw=json.dumps({'script':'API_KEY="embedded-private-value"'}).encode('utf-8')
    (root/'metadata.json').write_bytes(raw)
    value=c.safe_file(grant,'metadata.json')
    assert value['content'] is None and value['findings']
    assert 'embedded-private-value' not in json.dumps(value)
    assert (root/'metadata.json').read_bytes()==raw
    result=c.snapshot(grant,['metadata.json'])
    assert 'embedded-private-value' not in json.dumps(result)
