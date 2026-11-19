"""Audit names and validity without emitting environment values or executing source."""
import ast,os,re
from pathlib import Path
from urllib.parse import urlsplit

PREFIXES=('RIWAQ_','OLLAMA_')
SECRET=re.compile(r'(SECRET|PASSWORD|TOKEN|API_KEY|PRIVATE_KEY)',re.I)
def inventory(root):
    root=Path(root).resolve();found={};dynamic=[];client_secrets=[]
    for folder in ('backend','scripts','sync_server','tests'):
        base=root/folder
        if not base.exists():continue
        for path in sorted(base.rglob('*.py')):
            if path.is_symlink() or '__pycache__' in path.parts:continue
            try:tree=ast.parse(path.read_text('utf-8-sig'))
            except (SyntaxError,UnicodeError):continue
            for node in ast.walk(tree):
                key=None;has_default=False
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                    owner=ast.unparse(node.func.value)
                    if (owner=='os.environ' and node.func.attr in {'get','setdefault'}) or (owner=='os' and node.func.attr=='getenv'):
                        if node.args and isinstance(node.args[0],ast.Constant) and isinstance(node.args[0].value,str):key=node.args[0].value;has_default=len(node.args)>1
                        else:dynamic.append({'path':path.relative_to(root).as_posix(),'line':node.lineno})
                elif isinstance(node,ast.Subscript) and ast.unparse(node.value)=='os.environ' and isinstance(node.slice,ast.Constant):key=node.slice.value
                if isinstance(key,str) and key.startswith(PREFIXES):found.setdefault(key,[]).append({'path':path.relative_to(root).as_posix(),'line':node.lineno,'fallback_present':has_default})
        for path in sorted(base.rglob('*.ps1')):
            if path.is_symlink():continue
            text=path.read_text('utf-8-sig')
            for match in re.finditer(r'\$env:((?:RIWAQ_|OLLAMA_)[A-Z0-9_]+)',text):found.setdefault(match.group(1),[]).append({'path':path.relative_to(root).as_posix(),'line':text.count('\n',0,match.start())+1,'fallback_present':None})
    front=root/'frontend/src'
    if front.exists():
        for path in sorted(front.rglob('*')):
            if not path.is_file() or path.is_symlink() or path.suffix not in {'.ts','.tsx','.js','.jsx'}:continue
            text=path.read_text('utf-8-sig')
            for match in re.finditer(r'(?:import\.meta\.env|process\.env)\.([A-Z][A-Z0-9_]*)',text):
                if SECRET.search(match.group(1)):client_secrets.append({'name':match.group(1),'path':path.relative_to(root).as_posix(),'line':text.count('\n',0,match.start())+1})
    example=root/'.env.example';documented=set(re.findall(r'^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=',example.read_text('utf-8-sig'),re.M)) if example.exists() else set()
    return {'variables':[{'name':key,'sensitive':bool(SECRET.search(key)),'scope':'test' if key.startswith('RIWAQ_TEST_') or '_TEST_RUN' in key else 'server','usages':uses,'documented':key in documented} for key,uses in sorted(found.items())], 'documented_unused':sorted(k for k in documented if k not in found),'dynamic_accesses':dynamic,'client_secret_references':client_secrets,'values_included':False,'scan_scope':'Python literal reads and launch defaults; PowerShell environment references; frontend direct import.meta.env/process.env access; dynamic names remain review-required'}

def validate_settings(environ=None):
    env=os.environ if environ is None else environ;errors=[]
    for key in ('RIWAQ_DATA_DIR','RIWAQ_STATIC_DIR','RIWAQ_WHISPER_MODEL','RIWAQ_IMAGE_MODEL_DIR','RIWAQ_DOCUMENT_RUNTIME','RIWAQ_VISUAL_PYTHON'):
        if key in env and (not isinstance(env[key],str) or not env[key].strip() or any(ord(c)<32 for c in env[key])):errors.append({'name':key,'reason_code':'invalid_nonempty_path'})
    if env.get('RIWAQ_DEV_ORIGIN') not in (None,'','1'):
        try:
            match=re.fullmatch(r'http://(?:127\.0\.0\.1|localhost):([0-9]{4,5})',env['RIWAQ_DEV_ORIGIN']);valid=bool(match and 1024<=int(match[1])<=65535)
        except (ValueError,TypeError):valid=False
        if not valid:errors.append({'name':'RIWAQ_DEV_ORIGIN','reason_code':'loopback_origin_required'})
    for key in ('RIWAQ_EMBED_MODEL','RIWAQ_VISION_MODEL'):
        if key in env and (not env[key].strip() or len(env[key])>200 or any(ord(c)<32 for c in env[key])):errors.append({'name':key,'reason_code':'invalid_model_identifier'})
    return {'valid':not errors,'errors':errors,'values_included':False,'missing_required':[],'required_policy':'All audited launch variables are optional; operation-specific engines and credentials have separate capability gates.'}

def audit_environment(root,environ=None):
    env=os.environ if environ is None else environ;result=inventory(root)
    for row in result['variables']:row['configured']=row['name'] in env
    result['validation']=validate_settings(env);result['state']='blocked' if result['client_secret_references'] or not result['validation']['valid'] else 'review_required' if result['dynamic_accesses'] else 'checked'
    return result

def validate_startup(environ=None):
    result=validate_settings(environ)
    if not result['valid']:raise ValueError('environment_configuration_invalid:'+','.join(e['name'] for e in result['errors']))
    return result
