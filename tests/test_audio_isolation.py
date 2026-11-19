"""Audio boundary contracts; container execution is recorded by a separate live run."""
import base64,hashlib,io,json,struct,threading,wave
from copy import deepcopy
from contextlib import nullcontext
from pathlib import Path
import pytest
from backend import isolated_audio as wire,audio_isolation as adapter,audio_decode,voice_audio,isolated_worker

def wav(seconds=.2):
    stream=io.BytesIO()
    with wave.open(stream,'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(b'\0\0'*int(seconds*16000))
    return stream.getvalue()

def response(raw=None):
    raw=raw or wav();pcm,source,engine=audio_decode.decode(raw)
    return {'version':wire.VERSION,'input_sha256':hashlib.sha256(raw).hexdigest(),'success':True,'source':source,'engine':engine,
            'pcm':{'format':'f32le','sample_rate':16000,'channels':1,'samples':len(pcm)//4,'bytes':len(pcm),
                   'sha256':hashlib.sha256(pcm).hexdigest(),'base64':base64.b64encode(pcm).decode()} }

def test_bounded_pcm_protocol_roundtrip():
    raw=wav();pcm,meta=wire.decode_response(wire.dump(response(raw)),raw)
    values,diagnostics=wire.diagnostics(pcm,meta['source'])
    assert len(values)==3200 and diagnostics['duration']==.2 and diagnostics['peak']==0

@pytest.mark.parametrize('change',[
    lambda v:v.update(input_sha256='a'*64),lambda v:v.update(version='wrong'),lambda v:v.update(extra='untrusted'),
    lambda v:v['pcm'].update(samples=True),lambda v:v['pcm'].update(bytes=4),lambda v:v['pcm'].update(sample_rate=48000),
    lambda v:v['pcm'].update(format='mp3'),lambda v:v['pcm'].update(channels=2),lambda v:v['pcm'].update(sha256='0'*64),
    lambda v:v['pcm'].update(base64='!!!!'),lambda v:v['source'].update(channels=8),lambda v:v['source'].update(kind='webm'),
    lambda v:v['engine'].update(extra='claim'),lambda v:v.update(success='true'),
])
def test_untrusted_response_fields_cannot_certify_pcm(change):
    value=response();change(value)
    with pytest.raises(ValueError):wire.decode_response(wire.dump(value),wav())

@pytest.mark.parametrize('value',[float('nan'),float('inf'),float('-inf')])
def test_pcm_nonfinite_rejected_even_with_matching_hash(value):
    v=response();pcm=struct.pack('<f',value)+b'\0'*12796
    v['pcm'].update(base64=base64.b64encode(pcm).decode(),sha256=hashlib.sha256(pcm).hexdigest())
    with pytest.raises(ValueError,match='nonfinite'):wire.decode_response(wire.dump(v),wav())

def test_payload_has_no_paths_or_user_commands():
    payload,hashes=isolated_worker.prepare_payload({'input.bin':wav()},'media_decode',['input.bin'])
    assert set(wire.loads(payload))=={'version','body'} and list(hashes)==['input.bin']
    with pytest.raises(ValueError):wire.loads(b'{"version":1,"version":2}')
    with pytest.raises(isolated_worker.IsolatedError):isolated_worker.prepare_payload({'input.bin':wav(),'command':b'run'},'media_decode',['input.bin'])

def test_container_command_keeps_existing_os_policy():
    config=isolated_worker.IsolatedConfig(docker_executable='C:/docker.exe',docker_sha256='a'*64,
        endpoint='npipe:////./pipe/dockerDesktopLinuxEngine',daemon_id='test-only',images={'media_decode':'sha256:'+'b'*64})
    args=isolated_worker.create_arguments(config,'media_decode','riwaq-test',512,30)
    assert args[args.index('--network')+1]=='none' and '--read-only' in args
    assert '--mount' not in args and '--volume' not in args and args[args.index('--pull')+1]=='never'
    assert args[-1]=='/opt/riwaq/scripts/isolated_audio_worker.py'

def test_missing_authority_or_configuration_never_calls_host_decoder(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    def forbidden(*args,**kwargs):raise AssertionError('Native decoding must never fall back to host')
    monkeypatch.setattr(audio_decode,'decode',forbidden)
    with pytest.raises(adapter.AudioIsolationError,match='audio_authority_required'):adapter.decode(wav())
    with pytest.raises(adapter.AudioIsolationError):adapter.decode(wav(),authorize=lambda:True)
    assert not adapter.status()['available']

@pytest.fixture
def supervised(monkeypatch):
    class Config:images={'media_decode':'sha256:'+'a'*64}
    class Journal:
        def __init__(self,*args):pass
        def event(self,*args):pass
        def finish(self,value):pass
    monkeypatch.setattr(adapter,'load_config',lambda _:Config())
    monkeypatch.setattr(adapter,'worker_lease',nullcontext)
    monkeypatch.setattr(adapter,'Journal',Journal)
    monkeypatch.setattr(adapter,'assert_ready',lambda:None)
    return {'status':'completed','cleanup_verified':True,'policy_inspected':True,'stdout':wire.dump(response()).decode()}

@pytest.mark.parametrize('field,value,reason',[
    ('status','timeout','audio_timeout'),('status','revoked','audio_authority_revoked'),
    ('status','cancelled','audio_cancelled'),('cleanup_verified',False,'audio_isolation_failed'),
    ('policy_inspected',False,'audio_isolation_failed'),('status','blocked','audio_isolation_failed')])
def test_supervisor_failure_cannot_be_hidden_by_pcm_result(supervised,monkeypatch,field,value,reason):
    supervised[field]=value;monkeypatch.setattr(adapter,'execute',lambda *a,**k:supervised)
    with pytest.raises(adapter.AudioIsolationError,match=reason):adapter.decode(wav(),authorize=lambda:True)

def test_late_revocation_and_cancellation_discard_pcm(supervised,monkeypatch):
    allowed=True;stop=threading.Event()
    def run(*args,**kwargs):
        nonlocal allowed
        allowed=False;return supervised
    monkeypatch.setattr(adapter,'execute',run)
    with pytest.raises(adapter.AudioIsolationError,match='audio_authority_revoked'):adapter.decode(wav(),authorize=lambda:allowed)
    allowed=True
    def cancelled(*args,**kwargs):stop.set();return supervised
    monkeypatch.setattr(adapter,'execute',cancelled)
    with pytest.raises(adapter.AudioIsolationError,match='audio_cancelled'):adapter.decode(wav(),stop=stop,authorize=lambda:True)

def test_voice_model_worker_rejects_original_compressed_manifest(tmp_path):
    path=tmp_path/'raw.wav';path.write_bytes(wav())
    with pytest.raises(voice_audio.VoiceAudioError,match='Invalid transcription fields'):
        voice_audio.transcribe_local({'path':str(path),'model_path':'unused','language':'en'},tmp_path,lambda _:None)

def test_fixed_generated_sapi_pcm_does_not_need_av_or_docker(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('No compressed decoder for fixed PCM output')
    monkeypatch.setattr(audio_decode,'decode',forbidden);monkeypatch.setattr(adapter,'decode',forbidden)
    report=voice_audio.generated_wav_diagnostics(wav())
    assert report['duration']==.2 and report['sample_rate']==16000 and report['channels']==1

def test_transcription_authority_is_checked_before_dispatch_and_during_supervision(tmp_path,monkeypatch):
    from backend import visual_runner
    folder=tmp_path/'not-created'
    with pytest.raises(voice_audio.VoiceAudioError,match='revoked before dispatch'):
        voice_audio._invoke('stt',{},folder,threading.Event(),authorize=lambda:False)
    assert not folder.exists()
    allowed=[True];seen=[]
    def revoked(cmd,cwd,folder,stop,progress,**limits):
        seen.append(limits['authorize']())
        allowed[0]=False
        (folder/'voice-result.json').write_text(json.dumps({'success':True,'text':'Do not publish'}))
        return {'abort_reason':'authorization_revoked','remaining_processes':0}
    monkeypatch.setattr(visual_runner,'execute',revoked)
    with pytest.raises(voice_audio.VoiceAudioError) as caught:
        voice_audio._invoke('stt',{},folder,threading.Event(),authorize=lambda:allowed[0])
    assert seen==[True] and caught.value.code=='authorization_revoked'

def test_pcm_identity_is_rechecked_before_model_loading(tmp_path,monkeypatch):
    path=tmp_path/'decoded.f32le';path.write_bytes(b'\0'*12800)
    monkeypatch.setattr(voice_audio,'model_languages',lambda _: {'codes':['en']})
    with pytest.raises(voice_audio.VoiceAudioError,match='PCM identity changed'):
        voice_audio.transcribe_local({'pcm_path':str(path),'pcm_sha256':'a'*64,
            'source':{'kind':'wav','sample_rate':16000,'channels':1},'model_path':'unused','language':'en'},tmp_path,lambda _:None)

@pytest.mark.asyncio
async def test_media_revocation_cancels_and_reaps_actual_inference_child(tmp_path,monkeypatch):
    import asyncio,sys,time,psutil
    from fastapi import HTTPException
    from backend import media
    pid_file=tmp_path/'child.pid'
    # A trusted sleeping child exercises the actual process supervisor, without
    # spending model time or pretending this is a transcription-quality test.
    child=tmp_path/'child.py'
    child.write_text('import os,sys,time\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(str(os.getpid()))\ntime.sleep(60)\n')
    monkeypatch.setattr(media,'worker_command',lambda *args:[sys.executable,str(child),str(pid_file)])
    class Connected:
        async def is_disconnected(self):return False
    job=media.begin_media('stt');started=time.monotonic()
    try:
        task=asyncio.create_task(media.transcribe_process('unused','unused','en'))
        with pytest.raises(HTTPException) as caught:
            await media.await_work(task,job,Connected(),10,authorize=lambda:not pid_file.exists())
        assert caught.value.status_code==403 and task.cancelled()
        assert pid_file.exists() and not psutil.pid_exists(int(pid_file.read_text()))
        assert time.monotonic()-started<5
    finally:media.finish_media(job)

@pytest.mark.asyncio
async def test_media_authorization_check_errors_fail_closed():
    import asyncio
    from fastapi import HTTPException
    from backend import media
    class Connected:
        async def is_disconnected(self):return False
    def broken():raise RuntimeError('authorization store unavailable')
    job=media.begin_media('stt')
    try:
        task=asyncio.create_task(asyncio.sleep(60))
        with pytest.raises(HTTPException) as caught:
            await media.await_work(task,job,Connected(),1,authorize=broken)
        assert caught.value.status_code==403 and task.cancelled()
    finally:media.finish_media(job)
