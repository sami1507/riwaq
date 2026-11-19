"""Fail-closed code-test boundary with an optional pinned container adapter.

Legacy paths never execute. run_snapshot accepts bounded explicit bytes and a
live authorization callback; only a server-owned pinned Docker configuration can
select the separate adapter. No host user-code execution or automatic pull.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from typing import Callable,Literal
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'config/coding_capabilities.json'
VERSION='coding-sandbox-v1'
LIMITS={'max_targets':20,'max_seconds':120,'max_memory_mb':2048,'max_output_bytes':65536}
REASON_AR='لا توجد بيئة عزل تنفيذ مُهيأة ومتحقق منها. يمكن مراجعة الملفات والتعديلات؛ لم تُشغّل اختبارات المشروع.'
REASON_EN='No configured and verified code-execution isolation is available. File and patch review remain available; project tests have not run.'


class TestRequest(BaseModel):
    """A bounded future test intent, never an executable host command."""
    __test__=False
    model_config=ConfigDict(extra='forbid',strict=True)
    runner:Literal['python_unittest','node_test']
    targets:list[str]=Field(min_length=1,max_length=20)
    timeout_seconds:int=Field(default=30,ge=1,le=120)
    memory_mb:int=Field(default=512,ge=128,le=2048)
    network:Literal[False]=False
    install_dependencies:Literal[False]=False

    @field_validator('targets')
    @classmethod
    def relative_targets(cls,values):
        for value in values:
            if not value or len(value)>512 or value.startswith(('/', '\\')) or '\\' in value:
                raise ValueError('Test targets must be explicit relative POSIX file paths')
            if any(ord(c)<32 or ord(c)==127 for c in value) or any(c in value for c in ':*?[];|&<>"'):
                raise ValueError('No command syntax, glob, URL or drive in test target')
            parts=value.split('/')
            if any(part in {'','.','..'} or part.endswith((' ','.')) for part in parts):
                raise ValueError('Ambiguous and traversing test paths are forbidden')
            if any(part.casefold() in {'.git','.hg','.svn','.env','.aws','.ssh','.gnupg','.azure','.config','.codex','.agents','.idea','node_modules','.venv','venv'} or part.casefold().startswith('.env.') or re.search(r'(?:^|[._-])(?:credentials?|secrets?)(?:[._-]|$)',part,re.I) for part in parts):
                raise ValueError('Secrets, tool settings and dependency trees are not test targets')
        if len({value.casefold() for value in values})!=len(values):raise ValueError('Duplicate test targets')
        return values

    @model_validator(mode='after')
    def test_kind(self):
        extensions=('.py',) if self.runner=='python_unittest' else ('.js','.mjs','.cjs')
        if any(not value.endswith(extensions) for value in self.targets):raise ValueError('Target extension does not match the selected test runner')
        return self


def _read_evidence(config_path:Path):
    with config_path.open('rb') as stream:raw=stream.read(128*1024+1)
    if len(raw)>128*1024:raise ValueError('Capability evidence exceeds size bound')
    data=json.loads(raw.decode('utf-8-sig'))
    allowed={'version','mode','execution_enabled','observed_at','observed_scope','reason_code','reason_ar','reason_en','runtime_observations','limits','declared_runners','not_isolation','future_enablement_requires','official_sources'}
    if not isinstance(data,dict) or set(data)!=allowed or data.get('version')!=VERSION or data.get('mode')!='preview_only' or data.get('execution_enabled') is not False:
        raise ValueError('Capability evidence cannot authorize execution')
    if data.get('limits')!=LIMITS or data.get('declared_runners')!=['python_unittest','node_test']:
        raise ValueError('Capability evidence limits differ from the reviewed contract')
    if not isinstance(data.get('runtime_observations'),dict):raise ValueError('Missing runtime observation record')
    return data,hashlib.sha256(raw).hexdigest()


def status(config_path:Path|str|None=None):
    """Read recorded evidence only. Never starts a runtime or probes a daemon.

    The JSON is evidence, not an authorization switch. No configuration value,
    discovered binary or environment variable can enable host execution.
    """
    reason='sandbox_unavailable';evidence={};fingerprint=None
    try:evidence,fingerprint=_read_evidence(Path(config_path) if config_path is not None else CONFIG)
    except (OSError,ValueError,UnicodeError):reason='sandbox_configuration_invalid'
    result={'version':VERSION,'mode':'preview_only','available':False,'tested':False,
        'execution':{'available':False,'tested':False,'adapter':None,'reason_code':reason,
                     'reason_ar':REASON_AR,'reason_en':REASON_EN,'host_fallback':False,'automatic_download':False},
        'reason_code':reason,'reason_ar':REASON_AR,'reason_en':REASON_EN,
        'limits':dict(LIMITS),'declared_runners':['python_unittest','node_test'],
        'runners':[{'id':name,'available':False,'tested':False} for name in ('python_unittest','node_test')],
        'isolation_verified':False,'runtime_observations':deepcopy(evidence.get('runtime_observations',{})),
        'observed_at':evidence.get('observed_at'),'observation_scope':evidence.get('observed_scope'),
        'capability_evidence_sha256':fingerprint,
        'notice':'These are recorded observations, not live daemon health. The isolated adapter requires server-owned runtime pins and a fresh validation on every run; there is no host fallback.'}
    # Configuration is server-owned. A status read does not contact Docker or
    # turn recorded configuration into evidence of live isolation.
    if config_path is None and _runtime_config_path().exists():
        try:
            from backend.isolated_worker import load_config, _QUARANTINED
            runtime=load_config(_runtime_config_path())
            result.update(mode='isolated_configured',configured=True,reason_code='runtime_probe_required')
            result['execution'].update(configured=True,adapter='docker-linux',reason_code='runtime_probe_required',can_request=True)
            result['reason_ar']='إعداد الحاوية المحلية مثبت؛ سيُتحقق من المحرك والصورة والقيود عند كل تشغيل.'
            result['reason_en']='Pinned local container configuration is recorded; engine, image and policy are checked for each run.'
            result['execution'].update(reason_ar=result['reason_ar'],reason_en=result['reason_en'])
            result['runners']=[{'id':name,'available':False,'tested':False,'configured':name in runtime.images} for name in ('python_unittest','node_test')]
            result['notice']='Pinned local container configuration is recorded. A fresh bounded daemon/image/policy check is required on every execution; no live probe ran for this status.'
            if _QUARANTINED:
                result.update(reason_code='cleanup_quarantine')
                result['execution'].update(can_request=False,reason_code='cleanup_quarantine',
                    reason_ar='لم يُثبت تنظيف حاوية سابقة؛ التنفيذ متوقف حتى استعادة التنظيف.',
                    reason_en='Previous container cleanup is unverified; execution is blocked pending recovery.')
        except (OSError,ValueError,RuntimeError):
            result.update(configured=False,reason_code='sandbox_configuration_invalid')
            result['execution'].update(reason_code='sandbox_configuration_invalid',can_request=False)
    return result


def run_tests(snapshot_root:Path|str,request:TestRequest|dict,stop:threading.Event|None=None,
              progress:Callable[[dict],None]|None=None):
    """Return an explicit non-execution receipt; do not even open the snapshot.

    The pipeline owns grants, provenance and snapshot construction. It must retain
    this blocked outcome separately from static analysis and model review.
    """
    intent=request if isinstance(request,TestRequest) else TestRequest.model_validate(request)
    capabilities=status();cancelled=stop is not None and stop.is_set()
    result={'version':VERSION,'status':'cancelled' if cancelled else 'blocked',
        'reason_code':'cancelled' if cancelled else capabilities['reason_code'],
        'reason_ar':'أُلغي الطلب قبل أي تنفيذ.' if cancelled else REASON_AR,
        'reason_en':'Cancelled before any execution.' if cancelled else REASON_EN,
        'executed':False,'tests_passed':None,'test_count':None,'exit_code':None,
        'runner':intent.runner,'targets':list(intent.targets),'requested_limits':{
            'seconds':intent.timeout_seconds,'memory_mb':intent.memory_mb,'output_bytes':LIMITS['max_output_bytes']},
        'limits_enforced':False,'isolation_verified':False,'network_policy':'none_requested_not_exercised',
        'snapshot_inspected':False,'stdout':'','stderr':'','host_fallback':False,
        'automatic_download':False,'adapter':None,'seconds':0,
        'capability_evidence_sha256':capabilities['capability_evidence_sha256']}
    if progress:progress({'stage':result['status'],'executed':False,'reason_code':result['reason_code']})
    return result


def _runtime_config_path():
    # Do not create data directories during status reads. Never accept this path
    # in a project/model request. No environment flag enables a host fallback.
    return Path(os.environ.get('RIWAQ_DATA_DIR',str(ROOT/'data')))/'isolated-workers.json'


def run_snapshot(files:dict[str,bytes],request:TestRequest|dict,stop:threading.Event|None=None,
                 authorize:Callable[[],bool]|None=None,progress:Callable[[dict],None]|None=None):
    """Only already-authorized explicit bytes may enter the container adapter.

    Legacy run_tests(Path, ...) above remains a non-execution method. Callers
    must supply authority that is rechecked before and during every new run.
    """
    intent=request if isinstance(request,TestRequest) else TestRequest.model_validate(request)
    from backend.isolated_worker import IsolatedError,execute,load_config
    from backend.isolated_journal import Journal,assert_ready
    from backend.isolated_locks import LeaseError,worker_lease
    try:
        if authorize is None:raise IsolatedError('authorization_callback_required')
        runtime=load_config(_runtime_config_path())
        with worker_lease():
            assert_ready()
            journal=Journal(runtime,intent.runner,progress)
            receipt=execute(files,intent.runner,list(intent.targets),config=runtime,stop=stop,
                           authorize=authorize,progress=journal.event,timeout_seconds=intent.timeout_seconds,memory_mb=intent.memory_mb)
            try:journal.finish(receipt)
            except (OSError,RuntimeError):receipt.update(status='cleanup_failed',reason_code='journal_completion_failed',stdout='',stderr='',runner_exit_zero=None)
            return receipt
    except (OSError,ValueError,IsolatedError,LeaseError) as exc:
        value=run_tests(Path('.'),intent,stop)
        value['reason_code']=getattr(exc,'code','sandbox_configuration_invalid')
        return value


def cleanup_snapshot(container_name:str):
    """Server recovery hook for a durably recorded interrupted container only."""
    from backend.isolated_worker import cleanup_owned,load_config
    try:return cleanup_owned(load_config(_runtime_config_path()),container_name)
    except Exception as exc:return {'cleanup_verified':False,'reason_code':getattr(exc,'code','cleanup_unverified')}
