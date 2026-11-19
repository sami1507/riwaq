"""Bounded read-only project evidence. Granted source is never imported or executed."""
from __future__ import annotations
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import threading

VERSION = 'coding-context-v1'
MAX_FILES = 2000
MAX_ENTRIES = 5000
MAX_DIRS = 512
MAX_SCAN_BYTES = 10 * 1024**2
MAX_SELECTED = 12
MAX_CONTEXT = 64000
MAX_SECONDS = 10
MAX_AST_BYTES = 65536
MAX_WORKER_INPUT = MAX_AST_BYTES * 6 + 512
MAX_AST_NODES = 10000
MAX_METADATA = 128
MAX_FILE_BYTES = 1024**2
ROOT = Path(__file__).resolve().parents[1]
_AST_SLOT = threading.BoundedSemaphore(1)
SOURCE_EXTENSIONS = {'.py', '.pyi', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.json', '.toml', '.yaml', '.yml', '.md', '.txt', '.rst', '.go', '.rs', '.java', '.c', '.cpp', '.h', '.hpp', '.cs', '.css', '.html', '.vue', '.svelte', '.sql', '.sh', '.ps1', '.rb', '.php', '.lock'}
MANIFESTS = {'package.json', 'package-lock.json', 'npm-shrinkwrap.json', 'yarn.lock', 'pnpm-lock.yaml', 'pyproject.toml', 'requirements.txt', 'requirements.lock', 'poetry.lock', 'uv.lock', 'cargo.toml', 'cargo.lock', 'go.mod', 'go.sum', 'gemfile', 'gemfile.lock', 'composer.json', 'composer.lock', 'pom.xml', 'build.gradle', 'dockerfile', 'makefile'}


def _parse_python(text):
    """Called only inside the limited trusted worker; source is ast.parse data."""
    if len(text.encode('utf-8')) > MAX_AST_BYTES:
        return {'analysis_status': 'ast_input_limit', 'symbols': [], 'imports': []}
    tree = ast.parse(text, filename='<granted-source>', mode='exec')
    stack, symbols, imports, count = [(tree, '', 0)], [], [], 0
    while stack:
        node, scope, depth = stack.pop()
        count += 1
        if count > MAX_AST_NODES or depth > 80:
            return {'analysis_status': 'ast_structure_limit', 'symbols': [], 'imports': []}
        child_scope = scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name[:200]
            qualified = (scope + '.' + name if scope else name)[:500]
            if len(symbols) < MAX_METADATA:
                symbols.append({'name': name, 'qualified_name': qualified, 'kind': 'class' if isinstance(node, ast.ClassDef) else 'async_function' if isinstance(node, ast.AsyncFunctionDef) else 'function', 'line_start': node.lineno, 'line_end': node.end_lineno, 'method': 'python_ast'})
            child_scope = qualified
        if isinstance(node, (ast.Import, ast.ImportFrom)) and len(imports) < MAX_METADATA:
            imports.append({'module': ('.' * node.level + (node.module or '')) if isinstance(node, ast.ImportFrom) else None,
                            'names': [x.name[:200] for x in node.names[:32]], 'line_start': node.lineno, 'line_end': node.end_lineno, 'method': 'python_ast', 'resolved': False})
        stack.extend((child, child_scope, depth + 1) for child in reversed(list(ast.iter_child_nodes(node))))
    return {'analysis_status': 'parsed_not_executed', 'symbols': symbols, 'imports': imports, 'node_count': count,
            'metadata_truncated': len(symbols) >= MAX_METADATA or len(imports) >= MAX_METADATA}


def _worker_main():
    if getattr(sys,'frozen',False):
        from backend import check_worker as module
    else:
        spec = importlib.util.spec_from_file_location('riwaq_fixed_limits', Path(__file__).with_name('check_worker.py'))
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    job_handle = module.limits()
    import socket
    def denied(*args, **kwargs): raise OSError('Network unavailable in static parser')
    socket.socket.connect = denied; socket.create_connection = denied
    sys.setrecursionlimit(600)
    raw = sys.stdin.buffer.read(MAX_WORKER_INPUT + 1)
    if len(raw) > MAX_WORKER_INPUT: raise ValueError('Bounded parser input exceeded')
    value = json.loads(raw)
    try: result = _parse_python(value['source'])
    except SyntaxError as exc: result = {'analysis_status': 'syntax_error', 'line': exc.lineno, 'symbols': [], 'imports': []}
    except (ValueError, RecursionError, MemoryError): result = {'analysis_status': 'ast_rejected', 'symbols': [], 'imports': []}
    print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))


if __name__ == '__main__':
    if sys.argv[1:] != ['--ast-worker']: raise SystemExit('Fixed internal parser; not a project command runner')
    _worker_main()
    raise SystemExit(0)

from fastapi import HTTPException
from backend import workspace

_SECRET_PATTERNS = [
    ('private_key', re.compile(r'-----BEGIN (?:[A-Z0-9 ]{0,30})PRIVATE KEY-----')),
    ('provider_token', re.compile(r'\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{12,}|AKIA[A-Z0-9]{16})\b')),
    ('jwt', re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b')),
    ('authorization', re.compile(r'(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9+/_.=-]{8,}')),
    ('credential_url', re.compile(r'(?i)\b[a-z][a-z0-9+.-]{1,15}://[^\s/@:]{1,100}:[^\s/@]{1,200}@')),
    ('url_secret', re.compile(r'''(?i)[?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|signature|sig)=[^\s&#"']{4,}''')),
]
_ASSIGNMENT = re.compile(r'''(?ix)(?<![\w])['"]?(?:[\w.-]*[_-])?(?:api[_-]?key|access[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|client[_-]?secret|private[_-]?key|password|passwd|secret|token|connection[_-]?string|database[_-]?url)['"]?\s*(?:=|:)\s*([^\r\n]{1,2048})''')
_PLACEHOLDERS = {'', 'none', 'null', 'true', 'false', 'str', 'string', 'changeme', 'change_me', 'your_key_here', 'your_api_key', 'your_token_here', 'placeholder', 'example', 'dummy', 'redacted', '<redacted>', '<token>', '<password>', 'password_here', 'int', 'float', 'bool', 'bytes', 'any'}


def scan_secrets(text):
    """Findings contain kind and 1-based positions only, never values or snippets."""
    if not isinstance(text, str): raise HTTPException(422, 'Text is required for secret inspection')
    try: size = len(text.encode('utf-8'))
    except UnicodeError: return [{'kind': 'invalid_unicode', 'line_start': 1, 'line_end': 1}]
    if size > MAX_FILE_BYTES: return [{'kind': 'inspection_size_limit', 'line_start': 1, 'line_end': 1}]
    # Inspection-only quote unescaping catches credentials inside serialized strings.
    # No newline/Unicode normalization and no modification of returned source/evidence.
    for _ in range(4):
        inspected=text.replace('\\"','"').replace("\\'", "'")
        if inspected==text:break
        text=inspected
    hits = []
    def add(kind, start, end):
        if len(hits) < 32: hits.append({'kind': kind, 'line_start': text.count('\n', 0, start) + 1, 'line_end': text.count('\n', 0, end) + 1})
    for kind, pattern in _SECRET_PATTERNS:
        for found in pattern.finditer(text):
            add(kind, found.start(), found.end())
            if len(hits) >= 32: return hits
    for found in _ASSIGNMENT.finditer(text):
        value = found.group(1).strip()
        quoted = re.match(r'''^[rubfRUBF]{0,2}(['"])(.*?)\1''', value)
        if value.startswith(('"""', "'''")): add('credential_assignment', found.start(), found.end())
        elif quoted:
            literal = quoted.group(2)
            if literal.casefold() not in _PLACEHOLDERS and not (literal.startswith('${') and literal.endswith('}')): add('credential_assignment', found.start(), found.end())
        else:
            value = re.split(r'\s+#|\s+//|[,;]', value, maxsplit=1)[0].strip()
            if re.fullmatch(r'[A-Za-z0-9_+/=-]{1,}', value) and value.casefold() not in _PLACEHOLDERS: add('credential_assignment', found.start(), found.end())
        if len(hits) >= 32: break
    unique = {(x['kind'], x['line_start'], x['line_end']): x for x in hits}
    return sorted(unique.values(), key=lambda x: (x['line_start'], x['kind']))


def _grant_identity(grant):
    grant = dict(grant)
    if grant.get('active') != 1: raise HTTPException(403, 'Folder grant is not active')
    root = Path(grant['root']); workspace.assert_no_links(root); info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or (str(info.st_dev), str(info.st_ino)) != (grant['device'], grant['inode']): raise HTTPException(409, 'Granted directory identity changed')
    return grant


def safe_file(grant, path):
    """Caller must have checked current owner authorization for this grant."""
    grant = _grant_identity(grant); workspace.relative_parts(path)
    if scan_secrets(path) or any(scan_secrets(Path(part).stem) for part in workspace.relative_parts(path)): raise HTTPException(403, 'Credential-like file path is excluded')
    file = workspace.checked_path(grant, path); before = file.stat()
    data = workspace.read_bytes(grant, path)
    workspace.assert_no_links(file); after = file.stat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)
    if identity(before) != identity(after) or after.st_nlink != 1: raise HTTPException(409, 'File changed during context inspection')
    text = workspace.decode_text(data); findings = scan_secrets(text)
    return {'path': '/'.join(workspace.relative_parts(path)), 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data),
            'content': None if findings else text, 'blocked_reason': 'potential_credentials' if findings else None, 'findings': findings, 'encoding': 'utf-8-sig'}


def _python_context(text, timeout):
    empty = {'symbols': [], 'imports': []}
    if len(text.encode('utf-8')) > MAX_AST_BYTES: return empty | {'analysis_status': 'ast_input_limit'}
    from backend.packaged_workers import bundled,fixed_command
    if getattr(sys, 'frozen', False) and not bundled('ast',sys): return empty | {'analysis_status': 'packaged_worker_unavailable'}
    command=fixed_command('ast',sys) if getattr(sys,'frozen',False) else [sys.executable,'-I','-S','-X','utf8',str(Path(__file__).resolve()),'--ast-worker']
    deadline=time.monotonic()+max(.1,min(4.5,timeout))
    if not _AST_SLOT.acquire(timeout=max(.1,deadline-time.monotonic())):
        return empty | {'analysis_status':'ast_worker_busy'}
    try:
        remaining=deadline-time.monotonic()
        if remaining<=0:return empty | {'analysis_status':'ast_worker_timeout'}
        env = {k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','TEMP','TMP','PATH'}}
        if getattr(sys,'frozen',False):
            from backend.packaged_workers import worker_environment
            env=worker_environment(env)
        try:
            value = subprocess.run(command,
                                   input=json.dumps({'source': text}, ensure_ascii=False).encode('utf-8'), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   cwd=ROOT, env=env, timeout=remaining, shell=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if value.returncode or len(value.stdout) > 160000: return empty | {'analysis_status': 'ast_worker_unavailable_or_rejected'}
            result = json.loads(value.stdout)
            if not isinstance(result, dict) or 'analysis_status' not in result: raise ValueError()
            return result
        except (OSError, ValueError, subprocess.TimeoutExpired): return empty | {'analysis_status': 'ast_worker_unavailable_or_timeout'}
    finally:
        _AST_SLOT.release()


def _physical_lines(text):
    """Keep each original line ending; Unicode separators inside literals stay literal."""
    if not text: return []
    pieces=re.split(r'(\r\n|\r|\n)',text)
    return [''.join(pieces[i:i+2]) for i in range(0,len(pieces),2) if pieces[i:i+2]!=['']]


def _source_lines(text):
    return [re.sub(r'(\r\n|\r|\n)$','',line) for line in _physical_lines(text)]


def _heuristic(text, suffix):
    supported={'.js','.mjs','.cjs','.ts','.tsx','.jsx','.vue','.svelte','.go','.rs','.java','.cs','.cpp','.c','.h','.hpp','.php','.rb'}
    if suffix not in supported:return {'analysis_status':'text_only','symbols':[],'imports':[]}
    symbols, imports = [], []
    for number, line in enumerate(_source_lines(text), 1):
        if len(line) > 2000: continue
        m = re.match(r'\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?(function|class|interface|type|struct|func|fn)\s+([A-Za-z_$][\w$]{0,199})', line)
        if m and len(symbols) < MAX_METADATA: symbols.append({'name': m.group(2), 'kind': m.group(1), 'line_start': number, 'line_end': number, 'method': 'heuristic_not_parser'})
        m = re.search(r'''\b(?:from|require\s*\(|import\s*\(?)[\s]*['"]([^'"\r\n]{1,200})['"]''', line)
        if m and len(imports) < MAX_METADATA: imports.append({'module': m.group(1), 'names': [], 'line_start': number, 'line_end': number, 'method': 'heuristic_not_parser', 'resolved': False})
    return {'analysis_status': 'heuristic_not_parser', 'symbols': symbols, 'imports': imports}


def _dependencies(path, text):
    name = Path(path).name.casefold(); result = []; lines = _source_lines(text)
    if name not in MANIFESTS or len(text.encode('utf-8')) > 128 * 1024: return result
    def add(key, value, group, method):
        if len(result) >= MAX_METADATA or not isinstance(key,str) or not isinstance(value,str) or len(key)>200 or len(value)>300: return
        for number,line in enumerate(lines,1):
            if key in line and value in line:
                result.append({'name':key,'spec':value,'group':group,'method':method,'installed_verified':False,'line_start':number,'line_end':number});return
    try:
        if name == 'package.json':
            data=json.loads(text)
            for group in ('dependencies','devDependencies','peerDependencies','optionalDependencies'):
                values=data.get(group,{})
                if isinstance(values,dict):
                    for key,value in values.items():add(key,value,group,'json_manifest')
        elif name in ('package-lock.json','npm-shrinkwrap.json'):
            data=json.loads(text)
            for key,value in data.get('packages',{}).items():
                if key and isinstance(value,dict):add(key,value.get('version'),'lockfile','json_lockfile')
        elif name == 'pyproject.toml':
            import tomllib
            data=tomllib.loads(text)
            groups={'project.dependencies':data.get('project',{}).get('dependencies',[]),'build-system.requires':data.get('build-system',{}).get('requires',[])}
            for group,values in groups.items():
                if isinstance(values,list):
                    for value in values:
                        if isinstance(value,str):add(re.split(r'[\s<>=!~;\[]',value,1)[0],value,group,'toml_manifest')
        elif name.startswith('requirements'):
            for line in lines:
                m=re.match(r'^\s*([A-Za-z0-9][A-Za-z0-9_.-]{0,199})(?:\[[^\]]+\])?\s*([<>=!~].{1,250})?\s*$',line)
                if m:add(m.group(1),m.group(2) or m.group(1),'requirements','heuristic_declaration')
    except (ValueError,TypeError,AttributeError,RecursionError):return []
    return result


def _discover(grant, deadline):
    root=Path(grant['root']);pending=[root];files=[];entries=directories=0;truncated=False
    while pending:
        if time.monotonic()>=deadline or directories>=MAX_DIRS:truncated=True;break
        directory=pending.pop();directories+=1
        try:
            workspace.assert_no_links(directory);batch=[]
            with os.scandir(directory) as stream:
                for entry in stream:
                    entries+=1
                    if entries>MAX_ENTRIES:truncated=True;break
                    batch.append(entry)
            for entry in sorted(batch,key=lambda x:x.name.casefold()):
                if workspace.forbidden_component(entry.name) or scan_secrets(entry.name) or scan_secrets(Path(entry.name).stem):continue
                try:info=Path(entry.path).lstat()
                except OSError:truncated=True;continue
                if workspace.is_link(info):continue
                if stat.S_ISDIR(info.st_mode):pending.append(Path(entry.path));continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>MAX_FILE_BYTES:continue
                relative=Path(entry.path).relative_to(root).as_posix();workspace.relative_parts(relative)
                files.append({'path':relative,'size':info.st_size})
                if len(files)>=MAX_FILES:truncated=True;break
        except (OSError,HTTPException):truncated=True
        if truncated:break
    return files, {'truncated':truncated,'visited_entries':entries,'visited_directories':directories}


def _git_metadata(grant):
    result={'present':False,'method':'metadata_only','status_complete':False,'executed':False,'config_read':False,'branch':None,'head_commit':None,
            'notice':'No Git command, index/config/hooks, dirty/staged/untracked state, or ahead/behind comparison was used.'}
    git=Path(grant['root'])/'.git'
    try:
        info=git.lstat();result['present']=True
        if workspace.is_link(info) or not stat.S_ISDIR(info.st_mode):return result|{'notice':'Git indirection, worktree files, or links are not followed; status is unavailable.'}
        def read(relative,maximum):
            p=git.joinpath(*relative.split('/'));workspace.assert_no_links(p);before=p.stat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>maximum:raise ValueError()
            fd=os.open(p,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0))
            with os.fdopen(fd,'rb') as f:
                opened=os.fstat(f.fileno())
                if (before.st_dev,before.st_ino)!=(opened.st_dev,opened.st_ino):raise ValueError()
                value=f.read(maximum+1)
            if len(value)>maximum:raise ValueError()
            return value.decode('ascii').strip()
        head=read('HEAD',4096)
        if re.fullmatch('[a-fA-F0-9]{40}|[a-fA-F0-9]{64}',head):result['head_commit']=head
        elif head.startswith('ref: refs/heads/'):
            ref=head[5:]
            if len(ref)>200 or not re.fullmatch(r'refs/heads/[A-Za-z0-9_./-]+',ref) or any(x in {'','..','.'} for x in ref.split('/')) or scan_secrets(ref):return result
            result['branch']=ref[len('refs/heads/'):]
            try:
                commit=read(ref,128)
                if re.fullmatch('[a-fA-F0-9]{40}|[a-fA-F0-9]{64}',commit):result['head_commit']=commit
            except (OSError,ValueError,HTTPException):pass
    except (OSError,ValueError,UnicodeError,HTTPException):pass
    return result


def _windows(lines,query):
    tokens=set(re.findall(r'[\w-]{2,40}',query.casefold()))
    ranges=[(1,min(16,len(lines)))] if lines else []
    for number,line in enumerate(lines,1):
        if tokens and any(token in line.casefold() for token in tokens):
            ranges.insert(0,(max(1,number-2),min(len(lines),number+3)))
            if len(ranges)>=5:break
    merged=[]
    for start,end in ranges:
        if not any(start>=a and end<=b for a,b in merged):merged.append((start,end))
    return merged


def snapshot(grant,selected_paths=None,query='',budget=5000):
    """Caller supplies an owner-authorized grant; reauthorize before model dispatch."""
    grant=_grant_identity(grant)
    if type(budget) is not int or not 512<=budget<=MAX_CONTEXT:raise HTTPException(422,'Context budget must be 512 to 64000 UTF-8 bytes')
    if not isinstance(query,str) or len(query)>1000:raise HTTPException(422,'Query exceeds 1000 characters')
    if selected_paths is not None and (not isinstance(selected_paths,list) or len(selected_paths)>MAX_SELECTED or any(not isinstance(x,str) for x in selected_paths)):raise HTTPException(422,'Select at most 12 relative paths')
    selected=[]
    for path in selected_paths or []:
        normalized='/'.join(workspace.relative_parts(path))
        if normalized not in selected:selected.append(normalized)
    deadline=time.monotonic()+MAX_SECONDS;tree,discovery=_discover(grant,deadline)
    tokens=set(re.findall(r'[\w-]{2,40}',query.casefold()))
    ranked=sorted(tree,key=lambda x:(0 if x['path'] in selected else 1 if Path(x['path']).name.casefold() in MANIFESTS or Path(x['path']).name.casefold().startswith('readme') else 2 if any(t in x['path'].casefold() for t in tokens) else 3,x['path']))
    present={x['path'] for x in tree}
    if any(x not in present for x in selected):raise HTTPException(403,'A selected file is unavailable within the bounded grant inventory')
    candidates=[];scanned=count=blocked=0;truncated=discovery['truncated'];warnings=[]
    for item in ranked:
        # Reserve the maximum bounded read, so concurrent growth cannot exceed the total cap.
        if time.monotonic()>=deadline or scanned+MAX_FILE_BYTES>MAX_SCAN_BYTES:truncated=True;break
        path=item['path'];name=Path(path).name.casefold();suffix=Path(path).suffix.casefold()
        if path not in selected and suffix not in SOURCE_EXTENSIONS and name not in MANIFESTS and not name.startswith('readme'):continue
        try:value=safe_file(grant,path)
        except (HTTPException,OSError):continue
        scanned+=value['size'];count+=1
        if value['blocked_reason']:blocked+=1;continue
        text=value['content'];folded=text.casefold();score=sum(min(3,folded.count(t)) for t in tokens)
        priority=10000 if path in selected else 1000 if name in MANIFESTS or name.startswith('readme') else score
        if selected and path not in selected and name not in MANIFESTS and not name.startswith('readme') and not score:continue
        candidates.append((priority,path,value));candidates.sort(key=lambda x:(-x[0],x[1]));candidates=candidates[:MAX_SELECTED]
    payload={'kind':'untrusted_project_evidence','version':VERSION,'files':[]}
    encoded=lambda x:json.dumps(x,ensure_ascii=False,separators=(',',':'))
    for _,path,value in candidates:
        if time.monotonic()>=deadline:truncated=True;break
        text=value['content'];suffix=Path(path).suffix.casefold()
        analysis=_python_context(text,deadline-time.monotonic()) if suffix in {'.py','.pyi'} else _heuristic(text,suffix)
        dependencies=_dependencies(path,text)
        full={'path':path,'sha256':value['sha256'],'size':value['size'],'language':'python' if suffix in {'.py','.pyi'} else suffix.lstrip('.') or 'text',**analysis,'dependencies':dependencies,'evidence':[]}
        lines=_source_lines(text)
        for start,end in _windows(lines,query):
            excerpt=''.join(_physical_lines(text)[start-1:end]);trial=dict(full);trial['evidence']=full['evidence']+[{'line_start':start,'line_end':end,'text':excerpt}]
            windows=trial['evidence']
            visible=lambda x:any(e['line_start']<=x['line_start'] and x['line_end']<=e['line_end'] for e in windows)
            trial['symbols']=[x for x in analysis['symbols'] if visible(x)]
            trial['imports']=[x for x in analysis['imports'] if visible(x)]
            trial['dependencies']=[x for x in dependencies if visible(x)]
            candidate=payload|{'files':payload['files']+[trial]}
            if len(encoded(candidate).encode('utf-8'))<=budget:full=trial
            else:truncated=True
        if full['evidence']:payload['files'].append(full)
    _grant_identity(grant);context=encoded(payload)
    if blocked:warnings.append({'code':'potential_credentials_files_withheld','count':blocked})
    if not payload['files']:warnings.append({'code':'no_safe_evidence_within_budget'})
    if truncated:warnings.append({'code':'bounded_or_partial_context'})
    return {'version':VERSION,'files':payload['files'],'tree':tree,'context_text':context,'context_bytes':len(context.encode('utf-8')),
            'scanned_files':count,'scanned_bytes':scanned,'discovered_files':len(tree),'blocked_files':blocked,'truncated':truncated,
            'warnings':warnings,'git':_git_metadata(grant),'bindings':[{'path':x['path'],'sha256':x['sha256']} for x in payload['files']],
            'limits':{'files':MAX_FILES,'scan_bytes':MAX_SCAN_BYTES,'entries':MAX_ENTRIES,'directories':MAX_DIRS,'selected_files':MAX_SELECTED,'seconds':MAX_SECONDS,'context_bytes':budget},
            'runtime':{'python':sys.version.split()[0],'ast_isolated':True,'project_code_executed':False,'git_executed':False},
            'scope':'Static partial evidence only. Recheck owner authorization, current file hashes and secret status before dispatch. Filtering is heuristic, not proof that all secrets are detected.'}
