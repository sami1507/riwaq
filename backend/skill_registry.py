"""Validated discovery over the one trusted manifest; never executes a package.

Discovery is an observation, not a grant. Operation-specific runtime checks are
still mandatory. Optional dependencies never become permission to use a cloud.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib,json,re
from pathlib import Path

SLUG=re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')
HASH=re.compile(r'[a-f0-9]{64}\Z')
VERSION=re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z')
POLICY='riwaq-skill-composition-1'

class RegistryError(ValueError):pass

def canonical_json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def digest(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()

def _text(value,limit=3000):return isinstance(value,str) and bool(value.strip()) and len(value)<=limit and not any(ord(c)<32 and c not in '\n\t' for c in value)

def version_matches(version,requirement):
    if not isinstance(version,str) or not VERSION.fullmatch(version):return False
    if requirement in (None,'*'):return True
    if not isinstance(requirement,str) or len(requirement)>100:return False
    actual=tuple(map(int,version.split('.')))
    for part in requirement.split(','):
        m=re.fullmatch(r'\s*(==|>=|<=|>|<)?\s*(\d+\.\d+\.\d+)\s*',part)
        if not m or not VERSION.fullmatch(m[2]):return False
        expected=tuple(map(int,m[2].split('.')));op=m[1] or '=='
        if not {'==':actual==expected,'>=':actual>=expected,'<=':actual<=expected,'>':actual>expected,'<':actual<expected}[op]:return False
    return True

def dependency(value):
    if isinstance(value,str):
        if not SLUG.fullmatch(value):raise RegistryError('invalid_skill_dependency')
        return {'slug':value,'version':'*','optional':False}
    if not isinstance(value,dict) or set(value)-{'slug','version','optional'}:raise RegistryError('invalid_skill_dependency')
    result={'version':'*','optional':False,**value}
    if not isinstance(result.get('slug'),str) or not SLUG.fullmatch(result['slug']) or type(result['optional']) is not bool:raise RegistryError('invalid_skill_dependency')
    requirement=result['version']
    if requirement!='*' and (not isinstance(requirement,str) or not all(re.fullmatch(r'\s*(?:==|>=|<=|>|<)?\s*\d+\.\d+\.\d+\s*',p) for p in requirement.split(','))):raise RegistryError('invalid_dependency_version')
    return result

def validate_catalog(manifest,root=None):
    """Validate descriptors without reading or trusting package instructions."""
    if not isinstance(manifest,dict) or not VERSION.fullmatch(str(manifest.get('version',''))):raise RegistryError('invalid_catalog_version')
    entries=manifest.get('skills')
    if not isinstance(entries,list) or not 1<=len(entries)<=1000:raise RegistryError('invalid_catalog_size')
    names=set();numbers=set()
    for entry in entries:
        if not isinstance(entry,dict):raise RegistryError('invalid_skill_descriptor')
        name=entry.get('slug');number=entry.get('number')
        if not isinstance(name,str) or not SLUG.fullmatch(name) or name in names:raise RegistryError('duplicate_or_invalid_skill_name')
        if type(number) is not int or number<1 or number in numbers:raise RegistryError('duplicate_or_invalid_skill_number')
        names.add(name);numbers.add(number)
        if not _text(entry.get('display_name'),200) or not _text(entry.get('description')):raise RegistryError('missing_skill_description')
        if not VERSION.fullmatch(str(entry.get('version',''))) or not HASH.fullmatch(str(entry.get('instructions_sha256',''))):raise RegistryError('invalid_package_version_or_hash')
        if entry.get('status') not in {'blocked','partial','unimplemented','unverified','verified','ready'}:raise RegistryError('invalid_declared_status')
        if not _text(entry.get('execution_kind'),50):raise RegistryError('missing_execution_kind')
        for field in ['tools','dependencies','skill_dependencies','required_resources']:
            values=entry.get(field,[])
            if not isinstance(values,list) or len(values)>64:raise RegistryError('invalid_dependency_list')
            for item in values:
                if field=='skill_dependencies':dependency(item)
                elif not _text(item,250):raise RegistryError('invalid_dependency_name')
        for flag in ['enabled','revoked','requires_cloud']:
            if flag in entry and type(entry[flag]) is not bool:raise RegistryError('invalid_skill_flag')
        revoked_versions=entry.get('revoked_versions',[])
        if not isinstance(revoked_versions,list) or len(revoked_versions)>100 or any(not isinstance(v,str) or not VERSION.fullmatch(v) for v in revoked_versions):raise RegistryError('invalid_revoked_versions')
        resources=entry.get('resources',{})
        if not isinstance(resources,dict) or len(resources)>32:raise RegistryError('invalid_resources')
        for path,sha in resources.items():
            if not _text(path,250) or Path(path).is_absolute() or any(p in {'.','..'} or ':' in p for p in Path(path).parts) or not HASH.fullmatch(str(sha)):raise RegistryError('invalid_resource_binding')
        if any(name not in resources for name in entry.get('required_resources',[])):raise RegistryError('unregistered_required_resource')
        if not isinstance(entry.get('tools',[]),list) or len(set(entry.get('tools',[])))!=len(entry.get('tools',[])):raise RegistryError('duplicate_tool')
        if 'preferences' in entry and (not isinstance(entry['preferences'],dict) or set(entry['preferences'])-{'language','format'} or any(not _text(v,80) for v in entry['preferences'].values())):raise RegistryError('invalid_skill_preferences')
        if 'requirements' in entry:
            if not isinstance(entry['requirements'],list) or len(entry['requirements'])>64:raise RegistryError('invalid_runtime_requirements')
            for req in entry['requirements']:
                if not isinstance(req,dict) or set(req)-{'id','kind','optional','version','operations'} or req.get('kind') not in {'tool','model','library','connection'} or not _text(req.get('id'),250) or type(req.get('optional',False)) is not bool:raise RegistryError('invalid_runtime_requirement')
                if not isinstance(req.get('operations',[]),list) or any(not _text(x,100) for x in req.get('operations',[])):raise RegistryError('invalid_required_operation')
    aliases=manifest.get('aliases',{})
    if not isinstance(aliases,dict) or len(aliases)>1000:raise RegistryError('invalid_aliases')
    for old,new in aliases.items():
        if not isinstance(old,str) or not SLUG.fullmatch(old) or old in names or not isinstance(new,str) or new not in names:raise RegistryError('ambiguous_or_missing_alias')
    if type(manifest.get('max_active_skills',8)) is not int or not 1<=manifest.get('max_active_skills',8)<=8:raise RegistryError('invalid_activation_limit')
    return deepcopy(manifest)

def resolve_dependencies(selected,manifest,*,strict=False,allow_unready=False):
    manifest=validate_catalog(manifest);known={x['slug']:x for x in manifest['skills']}
    if not isinstance(selected,(list,tuple)) or len(selected)>8 or any(not isinstance(x,str) for x in selected):raise RegistryError('invalid_selection')
    selected=list(dict.fromkeys(manifest.get('aliases',{}).get(x,x) for x in selected))
    order=[];visited=set();visiting=[];issues=[];optional=[]
    def issue(code,slug,parent=None,optional_dep=False):
        (optional if optional_dep else issues).append({'code':code,'skill':slug,'required_by':parent})
    def visit(name,parent=None,req=None):
        opt=bool(req and req['optional'])
        if name not in known:issue('dependency_missing',name,parent,opt);return
        entry=known[name]
        if req and not version_matches(entry['version'],req['version']):issue('dependency_version_mismatch',name,parent,opt);return
        if not allow_unready and (entry.get('revoked') or entry['version'] in entry.get('revoked_versions',[]) or entry.get('enabled',True) is False or entry['status'] in {'blocked','unimplemented'}):issue('dependency_disabled',name,parent,opt);return
        if name in visiting:issue('dependency_cycle',name,parent,opt);return
        if name in visited:return
        visiting.append(name)
        for raw in entry.get('skill_dependencies',[]):
            item=dependency(raw);item['slug']=manifest.get('aliases',{}).get(item['slug'],item['slug'])
            prior_errors=len(issues);prior_order=list(order);prior_visited=set(visited)
            visit(item['slug'],name,item)
            if item['optional'] and len(issues)>prior_errors:
                optional.extend(issues[prior_errors:]);del issues[prior_errors:]
                order[:]=prior_order;visited.clear();visited.update(prior_visited)
        visiting.pop();visited.add(name);order.append(name)
    for name in selected:visit(name)
    if len(order)>manifest.get('max_active_skills',8):issues.append({'code':'dependency_context_limit','skill':None,'required_by':None})
    result={'selected':selected,'order':order,'blockers':issues,'optional_unavailable':optional,'ready':not issues}
    if strict and issues:raise RegistryError(issues[0]['code'])
    return result

def package_bytes(root,name,relative,expected,max_bytes=56000):
    """Read only a manifest-bound regular resource inside a nonlinked package."""
    root=Path(root).resolve();base=root/'skills'/name
    if not SLUG.fullmatch(name) or Path(relative).is_absolute() or any(p in {'.','..'} or ':' in p for p in Path(relative).parts):raise RegistryError('unsafe_package_resource')
    path=base/relative
    for ancestor in [path,*path.parents]:
        if ancestor==root:break
        if ancestor.is_symlink() or getattr(ancestor,'is_junction',lambda:False)():raise RegistryError('linked_package_resource')
    if not path.resolve().is_relative_to(base.resolve()) or not path.is_file():raise RegistryError('package_resource_missing')
    if path.stat().st_size>max_bytes:raise RegistryError('package_resource_limit')
    raw=path.read_bytes()
    if len(raw)>max_bytes or hashlib.sha256(raw).hexdigest()!=expected:raise RegistryError('package_resource_changed')
    return raw

def package_status(entry,root):
    try:
        raw=package_bytes(root,entry['slug'],'SKILL.md',entry['instructions_sha256'])
        text=raw.decode('utf-8');head=text.split('---',2)
        if len(head)!=3 or not text.startswith('---\nname: '+entry['slug']+'\n') or not re.search(r'^description:\s*\S',head[1],re.M):raise RegistryError('invalid_package_frontmatter')
        for relative in entry.get('required_resources',[]):package_bytes(root,entry['slug'],relative,entry['resources'][relative])
        return {'installed':True,'reason':None}
    except (OSError,ValueError,UnicodeError):return {'installed':False,'reason':'package_missing_changed_or_invalid'}

def discovery(manifest,root,*,tool_status=None,dependency_status=None,release_status=None):
    """tool_status is supplied by trusted server probes; unknown is not ready."""
    manifest=validate_catalog(manifest);tool_status=tool_status or {};dependency_status=dependency_status or {};release_status=release_status or {}
    package_states={s['slug']:package_status(s,root) for s in manifest['skills']};out=[]
    for entry in manifest['skills']:
        name=entry['slug'];state=package_states[name];resolved=resolve_dependencies([name],manifest)
        blockers=list(resolved['blockers']);warnings=list(resolved['optional_unavailable']);dependency_observations={}
        if not state['installed']:blockers.append({'code':state['reason'],'skill':name})
        for target in resolved['order']:
            descriptor=next(s for s in manifest['skills'] if s['slug']==target)
            dependency_gate=release_status.get(target,{})
            dependency_observations[target]={'descriptor':descriptor,'package':package_states[target],'release':dependency_gate}
            if descriptor.get('release_profile') and dependency_gate.get('approved') is not True:
                blockers.append({'code':'release_not_approved','skill':target})
            if not package_states[target]['installed']:blockers.append({'code':'dependency_package_unavailable','skill':target})
            requirements=[{'kind':'tool','id':x} for x in descriptor.get('tools',[])]+descriptor.get('requirements',[])
            for req in requirements:
                observed=(tool_status if req['kind']=='tool' else dependency_status).get(req['id'])
                if observed is True:observed={'available':True,'operations':[]}
                if not isinstance(observed,dict) or observed.get('available') is not True or any(x not in observed.get('operations',[]) for x in req.get('operations',[])) or req.get('version') and not version_matches(observed.get('version',''),req['version']):
                    (warnings if req.get('optional') else blockers).append({'code':'required_capability_unavailable','dependency':req['id'],'skill':target})
            for dep in descriptor.get('dependencies',[]):
                if dependency_status.get(dep) is not True and not (isinstance(dependency_status.get(dep),dict) and dependency_status[dep].get('available') is True):
                    (warnings if dep.startswith('optional-') else blockers).append({'code':'dependency_unverified','dependency':dep,'skill':target})
        gate=release_status.get(name,{})
        enabled=entry.get('enabled',True) and not entry.get('revoked',False) and entry['version'] not in entry.get('revoked_versions',[]) and entry['status'] not in {'blocked','unimplemented'}
        out.append({**entry,'installed':state['installed'],'enabled':enabled,'ready':bool(enabled and not blockers),'tested':gate.get('approved') is True,'tested_scope':gate.get('tested_scope','unverified'),'blockers':blockers,'warnings':warnings,'source':{'kind':'bundled','package':name,'namespace':'local'},'discovery_fingerprint':digest({'entry':entry,'resolution':resolved,'dependency_observations':dependency_observations,'packages':package_states,'tools':tool_status,'dependencies':dependency_status,'gate':gate})})
    return {**manifest,'skills':out,'discovery_only':True,'permission_grant':False}

def resolve_conflicts(selected,manifest,*,constraints=None):
    """Explicit request/policy wins over advisory package preferences."""
    manifest=validate_catalog(manifest);constraints=constraints or {}
    if not isinstance(constraints,dict) or set(constraints)-{'language','format','processing','outputs','read_only'}:raise RegistryError('invalid_composition_constraints')
    if constraints.get('processing','local-only') not in {'local-only','explicit-approved-destination'}:raise RegistryError('invalid_processing_scope')
    if 'read_only' in constraints and type(constraints['read_only']) is not bool:raise RegistryError('invalid_read_only_constraint')
    if 'outputs' in constraints and (not isinstance(constraints['outputs'],list) or len(constraints['outputs'])>8 or any(not _text(v,80) for v in constraints['outputs'])):raise RegistryError('invalid_output_constraints')
    for key in ['language','format']:
        if key in constraints and not _text(constraints[key],80):raise RegistryError('invalid_composition_constraint')
    resolved=resolve_dependencies(selected,manifest,strict=True);known={s['slug']:s for s in manifest['skills']}
    decisions=[];clarifications=[];effective=deepcopy(constraints)
    for key in ['language','format']:
        choices=sorted({known[n].get('preferences',{}).get(key) for n in resolved['order'] if known[n].get('preferences',{}).get(key)})
        if constraints.get(key):
            if any(x!=constraints[key] for x in choices):decisions.append({'field':key,'resolution':'explicit_request_overrides_package_preference'})
        elif len(choices)>1:clarifications.append({'field':key,'reason':'equal_package_preferences_conflict','choices':choices})
        elif choices:effective[key]=choices[0]
    blockers=[]
    if constraints.get('processing','local-only')=='local-only':
        for name in resolved['order']:
            if known[name].get('requires_cloud') is True:blockers.append({'skill':name,'reason':'local_processing_constraint'})
    return {'policy_version':POLICY,'effective':effective,'decisions':decisions,'clarifications':clarifications,'blockers':blockers,'ready':not blockers and not clarifications,'source_content_may_override':False,'permission_grant':False}
