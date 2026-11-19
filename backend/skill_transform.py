"""Trusted parent wrapper. User documents are never parsed on the host."""
from __future__ import annotations
import sys
from backend.packaged_workers import bundled
import threading
from backend.db import data_dir
from backend.isolated_worker import execute,load_config,IsolatedError
from backend.isolated_journal import Journal,assert_ready
from backend.isolated_locks import worker_lease,LeaseError
from backend import skill_transform_protocol as protocol

def status():
    try:
        if getattr(sys,'frozen',False) and not bundled('skill_transform',sys): raise protocol.TransformError('packaged_transform_worker_unavailable')
        config=load_config(data_dir()/'isolated-workers.json')
        if 'skill_transform' not in config.images: raise protocol.TransformError('transform_image_unconfigured')
        return {'available':True,'image_id':config.images['skill_transform'],'host_fallback':False,'tested':False}
    except (OSError,ValueError,IsolatedError) as exc:
        return {'available':False,'host_fallback':False,'reason':getattr(exc,'code','transform_unconfigured')}

def execute_transform(domain,request,sources,*,check,stop=None,timeout_seconds=90):
    if not callable(check): raise protocol.TransformError('authority_required')
    stop=stop or threading.Event()
    def authorize():
        if stop.is_set(): return False
        try:return check() is not False
        except Exception:return False
    if not authorize(): raise protocol.TransformError('authority_revoked')
    if getattr(sys,'frozen',False) and not bundled('skill_transform',sys): raise protocol.TransformError('packaged_transform_worker_unavailable')
    payload=protocol.encode_payload(domain,request,sources)
    try:
        with worker_lease():
            config=load_config(data_dir()/'isolated-workers.json')
            if 'skill_transform' not in config.images: raise protocol.TransformError('transform_image_unconfigured')
            assert_ready();journal=Journal(config,'skill_transform')
            receipt=execute({'transform.json':payload},'skill_transform',['transform.json'],config=config,stop=stop,
                            authorize=authorize,timeout_seconds=timeout_seconds,memory_mb=1024,progress=journal.event)
            journal.finish(receipt)
            if stop.is_set() or receipt['status']=='cancelled': raise protocol.TransformError('transform_cancelled')
            if not authorize() or receipt['status']=='revoked': raise protocol.TransformError('authority_revoked')
            if receipt['status']=='timeout': raise protocol.TransformError('transform_timeout')
            if receipt['status']!='completed' or not receipt.get('cleanup_verified') or not receipt.get('policy_inspected') or receipt.get('exit_code')!=0:
                raise protocol.TransformError('transform_isolation_failed')
            resources={k:v for k,v in receipt.items() if k not in {'stdout','stderr'}}
            result=protocol.decode_result(receipt['stdout'].encode('utf-8'),payload,resources)
            if domain=='data':
                # A valid transport/artifact hash does not establish that the
                # pinned container contains the data engine reviewed on host.
                # Missing markers from older images fail before publication.
                from backend.skill_data import VERSION as data_engine_version
                if result.result.get('engine_version')!=data_engine_version:
                    raise protocol.TransformError('data_engine_version_mismatch')
            if not authorize(): raise protocol.TransformError('authority_revoked')
            return result
    except (IsolatedError,LeaseError,OSError) as exc: raise protocol.TransformError(getattr(exc,'code','transform_unavailable')) from None
