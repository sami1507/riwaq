"""Bounded voice contract tests and opt-in actual Windows speech/Whisper proof.

pytest is unit validation; --smoke-dir performs actual supervised local inference.
"""
from __future__ import annotations
import io
import json
from pathlib import Path
import re
import sys
import threading
import wave
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import pytest
from backend import voice_audio as audio
from backend import audio_decode,isolated_audio

def decoded_fixture(raw):
    """Trusted synthetic unit bytes only; production dispatch is tested separately."""
    pcm,source,_=audio_decode.decode(raw)
    return isolated_audio.diagnostics(pcm,source)

def pcm_fixture(path,raw):
    pcm,source,_=audio_decode.decode(raw);path.write_bytes(pcm)
    return {'pcm_path':str(path),'pcm_sha256':audio.sha(pcm),'source':source,'model_path':'unused','language':'en'}


def wav_bytes(values,rate=16000,channels=1):
    import numpy as np
    output=io.BytesIO()
    with wave.open(output,'wb') as out:
        out.setnchannels(channels);out.setsampwidth(2);out.setframerate(rate)
        out.writeframes(np.asarray(values,dtype='<i2').tobytes())
    return output.getvalue()


def test_plain_text_is_not_interpolated_into_script():
    text='<speak>Hello</speak> $(Get-Content secrets) `quoted`'
    audio.validate_tts(text,'sapi-'+'a'*32,'en',0,100)
    assert text not in audio.SAPI_SCRIPT
    assert '$synth.Speak([string]$request.text)' in audio.SAPI_SCRIPT
    assert 'SpeakSsml' not in audio.SAPI_SCRIPT
    assert 'SetOutputToDefaultAudioDevice' not in audio.SAPI_SCRIPT
    assert 'GetInstalledVoices()' in audio.SAPI_SCRIPT
    assert "'unsupported_language'" in audio.SAPI_SCRIPT


@pytest.mark.parametrize('change',[
    {'text':''},{'text':'a'*2001},{'text':'nul\x00'},{'voice_id':'arbitrary-registry-path'},
    {'language':'auto'},{'language':'en; command'},{'rate':True},{'rate':6},{'volume':101},{'volume':-1}])
def test_invalid_synthesis_rejected_before_spawn(change):
    values={'text':'Hello','voice_id':'sapi-'+'a'*32,'language':'en-US','rate':0,'volume':100}|change
    with pytest.raises(audio.VoiceAudioError):audio.validate_tts(**values)


def test_recording_paths_and_external_io_are_fenced(tmp_path):
    folder=tmp_path/'job';folder.mkdir();foreign=tmp_path/'private.wav';foreign.write_bytes(b'private')
    with pytest.raises(audio.VoiceAudioError):audio.local_recording(foreign,folder)
    with pytest.raises(audio.VoiceAudioError):audio.deny_extra_io('https://example.invalid/audio',0,{})
    with pytest.raises(audio.VoiceAudioError):audio.decode_recording(b'#EXTM3U\nhttps://example.invalid')


def test_actual_stereo_resampling_and_audio_diagnostics():
    import numpy as np
    x=(np.sin(2*np.pi*440*np.arange(48000)/48000)*8000).astype('<i2')
    stereo=np.column_stack([x,x]).reshape(-1)
    values,stats=decoded_fixture(wav_bytes(stereo,48000,2))
    assert len(values)==16000 and stats['sample_rate']==16000 and stats['channels']==1
    assert stats['source_sample_rate']==48000 and stats['source_channels']==2 and stats['resampled']
    assert .15<stats['rms']<.3 and stats['decode_complete']
    assert stats['clipped_fraction']==0


def test_decoded_duration_limit_is_enforced():
    import numpy as np
    with pytest.raises(audio_decode.AudioRejected,match='audio_duration'):
        decoded_fixture(wav_bytes(np.zeros(61*8000,dtype='<i2'),8000))


def test_silence_returns_no_text_without_loading_whisper_or_running_vad(tmp_path,monkeypatch):
    import numpy as np
    path=tmp_path/'silence.f32le';payload=pcm_fixture(path,wav_bytes(np.zeros(16000,dtype='<i2')))
    monkeypatch.setattr(audio,'model_languages',lambda _: {'codes':['en'],'model':'test-only-metadata'})
    def must_not_run(_):raise AssertionError('Near-silence must exit before VAD/model inference')
    monkeypatch.setattr(audio,'speech_regions',must_not_run)
    result=audio.transcribe_local(payload,tmp_path,lambda _:None)
    assert result['status']=='no_speech' and result['text']=='' and result['segments']==[]
    assert result['reason']=='near_silence' and not result['vad']['evaluated']


def test_no_vad_speech_does_not_run_whisper(tmp_path,monkeypatch):
    import numpy as np
    path=tmp_path/'tone.f32le';payload=pcm_fixture(path,wav_bytes((np.sin(2*np.pi*440*np.arange(16000)/16000)*8000).astype('<i2')))
    monkeypatch.setattr(audio,'model_languages',lambda _: {'codes':['en'],'model':'test-only-metadata'})
    monkeypatch.setattr(audio,'speech_regions',lambda _:[])
    result=audio.transcribe_local(payload,tmp_path,lambda _:None)
    assert result['status']=='no_speech' and result['reason']=='no_detected_speech'
    assert result['text']=='' and result['vad']['speech_seconds']==0



def test_cancel_at_completion_discards_successful_worker_result(tmp_path,monkeypatch):
    from backend import visual_runner
    stop=threading.Event()
    def completed_but_cancelled(cmd,cwd,folder,event,progress,**limits):
        (folder/'voice-result.json').write_text(json.dumps({'success':True,'voices':[]}),encoding='utf-8')
        event.set()
        return {'abort_reason':None,'remaining_processes':0,'log_tail':'not exposed'}
    monkeypatch.setattr(visual_runner,'execute',completed_but_cancelled)
    with pytest.raises(audio.VoiceAudioError) as caught:audio.discover_voices(tmp_path,stop)
    assert caught.value.code=='cancelled'
    assert caught.value.resources['remaining_processes']==0
    assert 'log_tail' not in caught.value.resources


def test_timeout_never_accepts_a_worker_success_file(tmp_path,monkeypatch):
    from backend import visual_runner
    def timed_out(cmd,cwd,folder,event,progress,**limits):
        assert limits=={'seconds':10,'memory':512*1024**2}
        (folder/'voice-result.json').write_text(json.dumps({'success':True,'voices':[]}),encoding='utf-8')
        return {'abort_reason':'time_limit','remaining_processes':0}
    monkeypatch.setattr(visual_runner,'execute',timed_out)
    with pytest.raises(audio.VoiceAudioError) as caught:audio.discover_voices(tmp_path,threading.Event())
    assert caught.value.code=='time_limit'


def test_invalid_tts_never_starts_worker(tmp_path,monkeypatch):
    from backend import visual_runner
    def forbidden(*args,**kwargs):raise AssertionError('Invalid request must not spawn')
    monkeypatch.setattr(visual_runner,'execute',forbidden)
    with pytest.raises(audio.VoiceAudioError):audio.synthesize('Hello','not-discovered','en',0,100,tmp_path,threading.Event())


def test_fixed_worker_command_does_not_contain_user_text(tmp_path,monkeypatch):
    from backend import visual_runner
    text='Literal $(Write-Output arbitrary) <speak>text</speak>'
    def inspect_request(cmd,cwd,folder,event,progress,**limits):
        assert text not in ' '.join(cmd) and '--mode' in cmd and 'tts' in cmd
        saved=json.loads((folder/'voice-input.json').read_text(encoding='utf-8'))
        assert saved['text']==text
        assert limits=={'seconds':60,'memory':512*1024**2}
        (folder/'voice-result.json').write_text(json.dumps({'success':False,'code':'unsupported_language','error':'Unsupported'}),encoding='utf-8')
        return {'abort_reason':None,'remaining_processes':0}
    monkeypatch.setattr(visual_runner,'execute',inspect_request)
    with pytest.raises(audio.VoiceAudioError) as caught:
        audio.synthesize(text,'sapi-'+'a'*32,'ar',0,100,tmp_path,threading.Event())
    assert caught.value.code=='unsupported_language'



@pytest.mark.parametrize('mode',['voices','stt','tts'])
def test_packaged_runtime_refuses_before_spawn_or_job_write(tmp_path,monkeypatch,mode):
    from backend import visual_runner
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    def forbidden(*args,**kwargs):raise AssertionError('An unconfigured packaged worker must never spawn')
    monkeypatch.setattr(visual_runner,'execute',forbidden)
    folder=tmp_path/'never-created'
    with pytest.raises(audio.VoiceAudioError) as caught:
        audio._invoke(mode,{},folder,threading.Event())
    assert caught.value.code=='packaged_worker_unavailable'
    assert 'packaged service' in str(caught.value)
    assert not folder.exists()


def normalized_words(text):return re.findall(r"[a-z]+(?:'[a-z]+)?",text.lower())
def word_error_rate(reference,hypothesis):
    ref=normalized_words(reference);hyp=normalized_words(hypothesis);previous=list(range(len(hyp)+1))
    for i,left in enumerate(ref,1):
        row=[i]
        for j,right in enumerate(hyp,1):row.append(min(row[-1]+1,previous[j]+1,previous[j-1]+(left!=right)))
        previous=row
    return {'distance':previous[-1],'reference_words':len(ref),'hypothesis_words':len(hyp),
        'wer':previous[-1]/len(ref),'normalization':'Lowercase English words, preserving internal apostrophes; punctuation ignored.'}


def smoke(directory,model):
    import numpy as np
    import time
    folder=Path(directory).resolve()
    if folder.exists():raise RuntimeError('Use a new smoke folder; evidence is not overwritten')
    folder.mkdir(parents=True)
    report={'version':'voice-audio-smoke-v1','passed':False,'implementation_sha256':audio.implementation_fingerprint(),
        'scope':'Actual locally synthesized English Windows voice and local Whisper-small transcription, silence, deterministic white noise; synthetic fixtures only. No human-recorded speech, accent benchmark, Arabic TTS claim, independent listening review or voice cloning.',
        'acceptance':{'english_synthetic_wer_max':.25,'silence_text_must_be_empty':True,'white_noise_text_must_be_empty':True},
        'started_unix':time.time()}
    started=time.monotonic();progress=[];stop=threading.Event()
    output=ROOT/'reports/voice-audio-smoke.json'
    if output.exists():output.with_name('voice-audio-smoke-prior-'+str(time.time_ns())+'.json').write_bytes(output.read_bytes())
    try:
        voices=audio.discover_voices(folder/'voices',stop);report['discovery']=voices
        english=[v for v in voices['voices'] if v['enabled'] and v['language']=='en-US'];assert english
        selected=next((v for v in english if v['name']=='Microsoft David Desktop'),english[0])
        text='This is a local voice test. The blue square is above the green circle.'
        speech=audio.synthesize(text,selected['id'],'en-US',0,100,folder/'tts',stop,progress.append)
        report['tts']=speech;report['reference_text']=text
        stt_folder=folder/'stt';stt_folder.mkdir();path=stt_folder/'recording.wav'
        path.write_bytes((folder/'tts/speech.wav').read_bytes())
        transcription=audio.transcribe(path,model,'en',stt_folder,stop,progress.append,authorize=lambda:True)
        report['stt']=transcription;report['english_synthetic_wer']=word_error_rate(text,transcription['text'])
        assert transcription['status']=='transcribed' and report['english_synthetic_wer']['wer']<=.25
        for name,values in [('silence',np.zeros(3*16000,dtype='<i2')),
                            ('white_noise',np.random.default_rng(1917).integers(-5000,5001,3*16000,dtype=np.int16))]:
            job=folder/name;job.mkdir();path=job/'recording.wav';path.write_bytes(wav_bytes(values))
            result=audio.transcribe(path,model,'auto',job,stop,progress.append,authorize=lambda:True);report[name]=result
            assert result['status']=='no_speech' and result['text']=='' and not result['segments']
        bad=folder/'unsupported-arabic'
        try:audio.synthesize('مرحبا بك',selected['id'],'ar',0,100,bad,stop,progress.append)
        except audio.VoiceAudioError as exc:
            assert exc.code=='unsupported_language',exc.code
            report['unsupported_arabic']={'rejected':True,'code':exc.code,'english_fallback':False}
        else:raise AssertionError('Arabic was silently spoken with an English voice')
        report['passed']=True
    except BaseException as exc:
        report['error']=type(exc).__name__+': '+str(exc)[:1500];raise
    finally:
        report['seconds']=time.monotonic()-started;report['progress']=progress
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':report['passed'],'report':str(output),'seconds':report['seconds']}),flush=True)
    return report


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--smoke-dir',required=True)
    parser.add_argument('--model',default='D:/RiwaqRuntime/whisper-small');args=parser.parse_args()
    smoke(args.smoke_dir,args.model)
