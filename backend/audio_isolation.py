"""Compressed audio admission through the existing pinned container supervisor."""
from __future__ import annotations
import hashlib,sys,threading,time
from backend.db import data_dir
from backend import isolated_audio as protocol
from backend.isolated_worker import execute,load_config,IsolatedError
from backend.isolated_journal import Journal,assert_ready
from backend.isolated_locks import worker_lease,LeaseError
from backend.packaged_workers import bundled

class AudioIsolationError(ValueError):
    def __init__(self,code,status=503):self.code=code;self.status_code=status;super().__init__(code)

def status():
    try:
        if getattr(sys,'frozen',False) and not bundled('media_decode',sys):raise AudioIsolationError('packaged_worker_unavailable')
        config=load_config(data_dir()/'isolated-workers.json')
        if 'media_decode' not in config.images:raise AudioIsolationError('audio_decoder_unconfigured')
        return {'available':True,'configured':True,'tested':False,'reason_code':None,'image_id':config.images['media_decode'],
                'protocol':protocol.VERSION,'boundary':'docker_linux_network_none_no_host_mounts','host_fallback':False}
    except (OSError,ValueError,IsolatedError) as exc:
        return {'available':False,'configured':False,'tested':False,'reason_code':getattr(exc,'code','audio_decoder_unconfigured'),'host_fallback':False}

def decode(data,*,stop=None,authorize=None,deadline=None):
    try:protocol.kind(data)
    except protocol.AudioProtocolError as exc:raise AudioIsolationError(str(exc),413 if str(exc)=='audio_input_limit' else 415) from None
    if authorize is None:raise AudioIsolationError('audio_authority_required',401)
    stop=stop or threading.Event()
    try:
        if stop.is_set():raise AudioIsolationError('audio_cancelled',499)
        if authorize() is not True:raise AudioIsolationError('audio_authority_revoked',403)
        if getattr(sys,'frozen',False) and not bundled('media_decode',sys):raise AudioIsolationError('packaged_worker_unavailable')
        deadline=min(deadline if deadline is not None else time.monotonic()+30,time.monotonic()+30)
        with worker_lease():
            config=load_config(data_dir()/'isolated-workers.json')
            if 'media_decode' not in config.images:raise AudioIsolationError('audio_decoder_unconfigured')
            assert_ready();journal=Journal(config,'media_decode')
            remaining=int(deadline-time.monotonic())
            if remaining<1:raise AudioIsolationError('audio_timeout',504)
            receipt=execute({'input.bin':data},'media_decode',['input.bin'],config=config,stop=stop,authorize=authorize,
                            timeout_seconds=remaining,memory_mb=512,progress=journal.event)
            try:journal.finish(receipt)
            except (OSError,RuntimeError):raise AudioIsolationError('audio_cleanup_journal_failed') from None
            if stop.is_set() or receipt['status']=='cancelled':raise AudioIsolationError('audio_cancelled',499)
            if receipt['status']=='revoked' or authorize() is not True:raise AudioIsolationError('audio_authority_revoked',403)
            if receipt['status']=='timeout':raise AudioIsolationError('audio_timeout',504)
            if receipt['status']!='completed' or not receipt.get('cleanup_verified') or not receipt.get('policy_inspected'):
                raise AudioIsolationError('audio_isolation_failed')
            pcm,metadata=protocol.decode_response(receipt['stdout'].encode('utf-8'),data)
            if pcm is None:raise AudioIsolationError(metadata['error_code'],422)
            if stop.is_set():raise AudioIsolationError('audio_cancelled',499)
            if authorize() is not True:raise AudioIsolationError('audio_authority_revoked',403)
            resources={k:v for k,v in receipt.items() if k not in {'stdout','stderr'}}
            resources.update(protocol=protocol.VERSION,image_id=config.images['media_decode'],input_sha256=hashlib.sha256(data).hexdigest(),
                output_sha256=hashlib.sha256(pcm).hexdigest(),boundary='linux_container_shared_kernel',host_fallback=False)
            return pcm,metadata,resources
    except AudioIsolationError:raise
    except (LeaseError,IsolatedError,OSError) as exc:raise AudioIsolationError(getattr(exc,'code','audio_isolation_unavailable')) from None
    except (ValueError,TypeError,KeyError,AttributeError,UnicodeError):raise AudioIsolationError('audio_result_rejected',422) from None
