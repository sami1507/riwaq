"""Document-page parent wrapper. Missing isolation never invokes host parsing."""
from __future__ import annotations
from dataclasses import asdict
import hashlib
import json
import sys
from backend.packaged_workers import bundled
import threading
import time
from backend.db import ROOT,data_dir
from backend.document_extract import load_limits,languages
from backend import isolated_document_page as protocol
from backend.isolated_worker import execute,load_config,IsolatedError
from backend.isolated_journal import Journal,assert_ready
from backend.isolated_locks import worker_lease,LeaseError


class IsolationError(ValueError):
    def __init__(self,code,status=503):self.code=code;self.status_code=status;super().__init__(code)


def _require_packaged():
    if getattr(sys,'frozen',False) and not bundled('document_page',sys):raise IsolationError('packaged_worker_unavailable')


def status():
    try:
        _require_packaged()
        config=load_config(data_dir()/'isolated-workers.json')
        if 'document_page' not in config.images:raise IsolationError('document_image_unconfigured')
        return {'available':True,'configured':True,'tested':False,'reason_code':None,'image_id':config.images['document_page'],'boundary':'docker_linux_network_none_no_host_mounts','host_fallback':False}
    except (OSError,ValueError,IsolatedError) as exc:
        return {'available':False,'configured':False,'tested':False,'reason_code':getattr(exc,'code','document_isolation_unconfigured'),'host_fallback':False}


def fingerprint():
    value={'version':protocol.VERSION,'image_id':status().get('image_id'),'files':{}}
    for relative in ['backend/document_extract.py','backend/document_region.py','backend/image_normalize.py','backend/isolated_document_page.py','scripts/isolated_document_worker.py','scripts/document_ocr.mjs']:
        path=ROOT/relative;value['files'][relative]=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return hashlib.sha256(protocol.dump(value)).hexdigest()


def normalize_image(data,stop=None,authorize=None,deadline=None,*,variant=None):
    """Decode only inside the reviewed container; visual variant preserves alpha."""
    if variant not in (None,'visual'):raise IsolationError('image_variant_rejected',422)
    limit=protocol.MAX_VISUAL_INPUT if variant=='visual' else protocol.MAX_IMAGE_INPUT
    if not isinstance(data,bytes) or not 1<=len(data)<=limit:raise IsolationError('image_input_limit',413)
    if authorize is None:raise IsolationError('document_authority_required',401)
    stop=stop or threading.Event()
    try:
        if stop.is_set():raise IsolationError('document_cancelled',499)
        if authorize() is not True:raise IsolationError('document_authority_revoked',403)
        _require_packaged()
        deadline=min(deadline or time.monotonic()+30,time.monotonic()+30)
        with worker_lease():
            config=load_config(data_dir()/'isolated-workers.json')
            if 'document_page' not in config.images:raise IsolationError('document_image_unconfigured')
            assert_ready();journal=Journal(config,'document_page')
            remaining=int(deadline-time.monotonic())
            if remaining<1:raise IsolationError('document_timeout',504)
            metadata={'operation':'normalize'}|({'variant':'visual'} if variant=='visual' else {})
            receipt=execute({'input.bin':data,'metadata.json':protocol.dump(metadata)},'document_page',['input.bin'],
                config=config,stop=stop,authorize=authorize,timeout_seconds=remaining,memory_mb=512,progress=journal.event)
            try:journal.finish(receipt)
            except (OSError,RuntimeError):raise IsolationError('document_cleanup_journal_failed') from None
            if stop.is_set() or receipt['status']=='cancelled':raise IsolationError('document_cancelled',499)
            if receipt['status']=='revoked' or authorize() is not True:raise IsolationError('document_authority_revoked',403)
            if receipt['status']=='timeout':raise IsolationError('document_timeout',504)
            if receipt['status']!='completed' or not receipt.get('cleanup_verified') or not receipt.get('policy_inspected'):raise IsolationError('document_isolation_failed')
            picture,dimensions=protocol.normalize_response(receipt['stdout'].encode('utf-8'),data,variant=variant)
            if picture is None:
                code=dimensions['error_code'];raise IsolationError(code,{'image_input_limit':413,'image_pixel_limit':413,'image_format':415}.get(code,422))
            if stop.is_set():raise IsolationError('document_cancelled',499)
            if authorize() is not True:raise IsolationError('document_authority_revoked',403)
            evidence={k:receipt[k] for k in ('cleanup_verified','policy_inspected','status')}
            evidence.update(protocol=protocol.VISUAL_NORMALIZE_VERSION if variant=='visual' else protocol.NORMALIZE_VERSION,image_id=config.images['document_page'],host_fallback=False,
                input_sha256=hashlib.sha256(data).hexdigest(),output_sha256=hashlib.sha256(picture).hexdigest(),boundary='linux_container_shared_kernel')
            return picture,dimensions,evidence
    except IsolationError:raise
    except (LeaseError,IsolatedError,OSError) as exc:raise IsolationError(getattr(exc,'code','document_isolation_unavailable')) from None
    except (ValueError,TypeError,KeyError,AttributeError,UnicodeError):raise IsolationError('image_result_rejected',422) from None


def prepare_region(preview,bbox,stop=None,authorize=None,deadline=None):
    """Existing document container; no native image decoder or host fallback."""
    stop=stop or threading.Event()
    if authorize is None:raise IsolationError('document_authority_required',401)
    try:
        metadata=protocol.validate_region_metadata({'operation':'region','bbox':bbox})
        protocol.png_dimensions(preview)
        if stop.is_set():raise IsolationError('document_cancelled',499)
        if authorize() is not True:raise IsolationError('document_authority_revoked',403)
        _require_packaged()
        deadline=min(deadline or time.monotonic()+30,time.monotonic()+30)
        with worker_lease():
            config=load_config(data_dir()/'isolated-workers.json')
            if 'document_page' not in config.images:raise IsolationError('document_image_unconfigured')
            assert_ready();journal=Journal(config,'document_page')
            remaining=int(deadline-time.monotonic())
            if remaining<1:raise IsolationError('document_timeout',504)
            receipt=execute({'input.bin':preview,'metadata.json':protocol.dump(metadata)},'document_page',['input.bin'],config=config,
                stop=stop,authorize=authorize,timeout_seconds=remaining,memory_mb=512,progress=journal.event)
            try:journal.finish(receipt)
            except (OSError,RuntimeError):raise IsolationError('document_cleanup_journal_failed') from None
            if stop.is_set() or receipt['status']=='cancelled':raise IsolationError('document_cancelled',499)
            if receipt['status']=='revoked' or authorize() is not True:raise IsolationError('document_authority_revoked',403)
            if receipt['status']=='timeout':raise IsolationError('document_timeout',504)
            if receipt['status']!='completed' or not receipt.get('cleanup_verified') or not receipt.get('policy_inspected'):raise IsolationError('document_isolation_failed')
            data,region=protocol.region_response(receipt['stdout'].encode('utf-8'),metadata,preview)
            if stop.is_set():raise IsolationError('document_cancelled',499)
            if authorize() is not True:raise IsolationError('document_authority_revoked',403)
            region['isolation']={k:receipt[k] for k in ('cleanup_verified','policy_inspected','status')}
            region['isolation'].update(protocol=protocol.REGION_VERSION,boundary='linux_container_shared_kernel',host_fallback=False,image_id=config.images['document_page'])
            return data,region
    except IsolationError:raise
    except (LeaseError,IsolatedError,OSError) as exc:raise IsolationError(getattr(exc,'code','document_isolation_unavailable')) from None
    except (ValueError,TypeError,KeyError,AttributeError,UnicodeError):raise IsolationError('document_region_rejected',422) from None


def extract(data,filename,page,language,stop=None,authorize=None,deadline=None):
    stop=stop or threading.Event()
    if authorize is None:raise IsolationError('document_authority_required',401)
    try:
        metadata=protocol.validate_metadata({'name':filename,'page':page,'languages':languages(language),'limits':asdict(load_limits())})
        if not isinstance(data,bytes) or not 1<=len(data)<=metadata['limits']['max_input_bytes']:raise IsolationError('document_input_limit',413)
        if stop.is_set():raise IsolationError('document_cancelled',499)
        if authorize() is not True:raise IsolationError('document_authority_revoked',403)
        _require_packaged()
        maximum=90 if page else 30
        deadline=min(deadline if deadline is not None else time.monotonic()+maximum,time.monotonic()+maximum)
        with worker_lease():
            config=load_config(data_dir()/'isolated-workers.json')
            if 'document_page' not in config.images:raise IsolationError('document_image_unconfigured')
            assert_ready();journal=Journal(config,'document_page')
            remaining=int(deadline-time.monotonic())
            if remaining<1:raise IsolationError('document_timeout',504)
            receipt=execute({'input.bin':data,'metadata.json':protocol.dump(metadata)},'document_page',['input.bin'],config=config,
                stop=stop,authorize=authorize,timeout_seconds=remaining,memory_mb=2048,progress=journal.event)
            try:journal.finish(receipt)
            except (OSError,RuntimeError):raise IsolationError('document_cleanup_journal_failed') from None
            if stop.is_set() or receipt['status']=='cancelled':raise IsolationError('document_cancelled',499)
            if receipt['status']=='revoked' or authorize() is not True:raise IsolationError('document_authority_revoked',403)
            if receipt['status']=='timeout':raise IsolationError('document_timeout',504)
            if receipt['status']!='completed' or not receipt.get('cleanup_verified') or not receipt.get('policy_inspected'):raise IsolationError('document_isolation_failed')
            result,preview=protocol.decode_response(receipt['stdout'].encode('utf-8'),metadata,hashlib.sha256(data).hexdigest(),len(data))
            if stop.is_set() or authorize() is not True:raise IsolationError('document_authority_revoked',403)
            resources={k:v for k,v in receipt.items() if k not in {'stdout','stderr'}}
            resources.update(protocol=protocol.VERSION,boundary='linux_container_shared_kernel',host_fallback=False)
            return result,preview,resources
    except IsolationError:raise
    except (LeaseError,IsolatedError,OSError) as exc:raise IsolationError(getattr(exc,'code','document_isolation_unavailable')) from None
    except (ValueError,TypeError,KeyError,AttributeError,UnicodeError):raise IsolationError('document_result_rejected',422) from None
