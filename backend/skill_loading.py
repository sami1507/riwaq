"""Bounded progressive activation of reviewed local packages; never executes them."""
from __future__ import annotations
import hashlib
import re
import time
from pathlib import Path


def load_packages(selected, manifest, root, budget=14000, references=None, *, constraints=None, pin=None):
    from backend.skill_registry import resolve_dependencies,resolve_conflicts,RegistryError
    if pin is not None:
        from backend.skill_pinning import pinned_packages
        return pinned_packages(selected,manifest,pin,budget=budget,references=references)
    started=time.monotonic(); known={}
    for entry in manifest['skills']:
        name=entry['slug']
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',name) or name in known:raise ValueError('Invalid or conflicting skill name')
        known[name]=entry
    if type(budget) is not int or not 1<=budget<=14000:raise RegistryError('invalid_instruction_budget')
    resolved=resolve_dependencies(selected,{'version':'0.0.0',**manifest},allow_unready=True)
    if resolved['blockers']:
        code=resolved['blockers'][0]['code']
        raise RegistryError('Circular skill dependency' if code=='dependency_cycle' else code)
    active=resolved['order']
    composition=resolve_conflicts(selected,{'version':'0.0.0',**manifest},constraints=constraints) if constraints is not None else None
    if composition and not composition['ready']:raise RegistryError('skill_constraint_conflict_requires_review')
    root=Path(root).resolve();texts=[];receipt=[];used=0
    def read(name,relative,expected):
        nonlocal used
        parts=Path(relative).parts
        if not parts or Path(relative).is_absolute() or any(p in {'..','.'} or ':' in p for p in parts):raise ValueError('Unsafe package resource')
        base=root/'skills'/name; path=base/relative
        for part in [base,*path.parents]:
            if part==root:break
            if part.is_symlink() or getattr(part,'is_junction',lambda:False)():raise ValueError('Linked package is not trusted')
        if path.is_symlink() or not path.resolve().is_relative_to(base.resolve()):raise ValueError('Linked package resource')
        if path.stat().st_size>budget*4:raise ValueError('Resource context limit')
        raw=path.read_bytes()
        digest=hashlib.sha256(raw).hexdigest()
        if digest!=expected:raise ValueError('Package changed; review required')
        value=raw.decode('utf-8');used+=len(value)
        if used>budget:raise ValueError('Skill context budget exceeded')
        return value,digest
    for name in active:
        entry=known[name];value,digest=read(name,'SKILL.md',entry['instructions_sha256'])
        if not value.startswith('---\nname: '+name+'\n') or '\ndescription: ' not in value.split('---',2)[1]:raise ValueError('Invalid package frontmatter')
        texts.append(value.split('---',2)[2].strip())
        receipt.append({'slug':name,'version':entry['version'],'sha256':digest,'resource':'SKILL.md'})
        for relative in dict.fromkeys([*entry.get('required_resources',[]),*(references or {}).get(name,[])]):
            expected=entry.get('resources',{}).get(relative)
            if not expected:raise ValueError('Unregistered reference')
            value,digest=read(name,relative,expected);texts.append(value)
            receipt.append({'slug':name,'version':entry['version'],'sha256':digest,'resource':relative})
    return {'text':'\n\n'.join(texts),'loaded':receipt,'characters':used,'seconds':round(time.monotonic()-started,6),
            'composition':composition,'optional_unavailable':resolved['optional_unavailable'],
            'executed_scripts':False,'cache':'none; current bytes and permission scope checked each activation'}
