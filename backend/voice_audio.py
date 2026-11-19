"""Fixed local voice adapters. No media.ACTIVE state, network provider or downloads.

The caller owns one job directory, its cancellation Event and any persisted results.
Only the temporary worker directory contains source recordings and synthesized WAV.
"""
from __future__ import annotations
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
VERSION='voice-audio-v2'
RATE=16000
MAX_BYTES=8*1024**2
MAX_SECONDS=60
MAX_TEXT=2000
VAD_OPTIONS={'threshold':.5,'min_speech_duration_ms':250,'min_silence_duration_ms':500,'speech_pad_ms':100,'max_speech_duration_s':30}

class VoiceAudioError(ValueError):
    def __init__(self,message,code='voice_audio_failed',resources=None):
        super().__init__(message);self.code=code;self.resources=resources or {}


def packed(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def sha(data):return hashlib.sha256(data).hexdigest()
def implementation_fingerprint():
    paths=('backend/voice_audio.py','backend/voice_worker.py','backend/isolated_audio.py','backend/audio_isolation.py')
    if getattr(sys,'frozen',False):
        from backend.packaged_workers import source_fingerprint
        return source_fingerprint(paths)
    return sha(b'\n'.join(name.encode()+b':'+sha((ROOT/name).read_bytes()).encode() for name in paths))


def local_recording(path,folder):
    path=Path(path).resolve();folder=Path(folder).resolve()
    if not path.is_relative_to(folder) or not path.is_file():raise VoiceAudioError('Recording must be materialized inside this job directory','invalid_input')
    if not 0<path.stat().st_size<=MAX_BYTES:raise VoiceAudioError('Recording exceeds 8 MiB or is empty','invalid_input')
    return path


def model_languages(folder):
    """Actual local tokenizer vocabulary, not a claim that every language is accurate."""
    from faster_whisper.tokenizer import _LANGUAGE_CODES
    folder=Path(folder)
    if not folder.is_absolute() or not folder.is_dir():raise VoiceAudioError('An installed absolute local Whisper directory is required','stt_unavailable')
    for name in ('model.bin','config.json','tokenizer.json'):
        if not (folder/name).is_file():raise VoiceAudioError('Local Whisper files are incomplete; downloads are disabled','stt_unavailable')
    path=folder/'tokenizer.json'
    if path.stat().st_size>16*1024**2:raise VoiceAudioError('Tokenizer metadata exceeds limit','stt_unavailable')
    raw=path.read_bytes();value=json.loads(raw)
    vocab=value.get('model',{}).get('vocab',{})
    tokens=set(vocab) if isinstance(vocab,dict) else {entry[0] for entry in vocab if isinstance(entry,list) and entry}
    tokens.update(entry.get('content') for entry in value.get('added_tokens',[]) if isinstance(entry,dict))
    codes=sorted(code for code in _LANGUAGE_CODES if '<|'+code+'|>' in tokens)
    if not codes:raise VoiceAudioError('No supported language tokens in installed model','stt_unavailable')
    return {'codes':codes,'tokenizer_sha256':sha(raw),'model':folder.name,
        'model_identity_scope':'Tokenizer SHA256 plus weight size/mtime fingerprint; not a full rehash of model weights.',
        'model_fingerprint':sha('|'.join(f'{name}:{(folder/name).stat().st_size}:{(folder/name).stat().st_mtime_ns}' for name in ('model.bin','config.json','tokenizer.json')).encode())}


def validate_tts(text,voice_id,language,rate,volume):
    if not isinstance(text,str) or not 1<=len(text)<=MAX_TEXT or not text.strip():raise VoiceAudioError('Speech text must contain 1 to 2000 characters','invalid_input')
    if any((ord(c)<32 and c not in '\t\r\n') or 0xd800<=ord(c)<=0xdfff for c in text):raise VoiceAudioError('Unsupported speech text control or surrogate','invalid_input')
    if not isinstance(voice_id,str) or not re.fullmatch(r'sapi-[a-f0-9]{32}',voice_id):raise VoiceAudioError('Choose an actually discovered local voice','invalid_voice')
    if not isinstance(language,str) or not re.fullmatch(r'[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8}){0,3}',language) or language=='auto':raise VoiceAudioError('An explicit supported speech language is required','unsupported_language')
    if type(rate) is not int or not -5<=rate<=5 or type(volume) is not int or not 0<=volume<=100:raise VoiceAudioError('Speech rate must be -5..5 and volume 0..100','invalid_input')


def _invoke(mode,payload,folder,stop,progress=lambda _:None,*,authorize=None):
    if getattr(sys,'frozen',False):
        from backend.packaged_workers import bundled,fixed_command
        if mode not in ('voices','tts') or not bundled('voice_tts'):
            raise VoiceAudioError('The packaged service has no configured worker for this voice operation. Use the source runtime; no alternate executable or cloud fallback was started.','packaged_worker_unavailable')
        command=fixed_command('voice_tts')
    else:
        command=[sys.executable,'-B',str(ROOT/'scripts/voice_audio_worker.py')]
    from backend.visual_runner import execute
    if authorize is not None and authorize() is not True:raise VoiceAudioError('Voice authority revoked before dispatch','authorization_revoked')
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    request=folder/'voice-input.json';result_path=folder/'voice-result.json'
    if request.exists() or result_path.exists():raise VoiceAudioError('Use a fresh directory per voice operation','invalid_input')
    request.write_text(packed(payload),encoding='utf-8')
    seconds=10 if mode=='voices' else 180 if mode=='stt' else 60
    receipt=execute([*command,'--mode',mode,'--input',str(request),'--output',str(folder)],
        ROOT,folder,stop,progress,seconds=seconds,memory=(4*1024**3 if mode=='stt' else 512*1024**2),
        **({'authorize':authorize} if authorize is not None else {}))
    resources={k:v for k,v in receipt.items() if k!='log_tail'}
    if authorize is not None and authorize() is not True:raise VoiceAudioError('Voice authority revoked','authorization_revoked',resources)
    if receipt.get('abort_reason') or stop.is_set():
        code='cancelled' if stop.is_set() or receipt.get('abort_reason')=='cancelled' else receipt.get('abort_reason')
        raise VoiceAudioError('Voice operation stopped or exceeded its resource limit',code,resources)
    if not result_path.is_file() or result_path.stat().st_size>512*1024:raise VoiceAudioError('No bounded voice worker result','worker_failed',resources)
    result=json.loads(result_path.read_text(encoding='utf-8'))
    if result.get('success') is not True:raise VoiceAudioError(result.get('error','Voice engine unavailable'),result.get('code','worker_failed'),resources)
    if mode=='tts':
        wav=folder/'speech.wav'
        if not wav.is_file() or not 0<wav.stat().st_size<=MAX_BYTES or sha(wav.read_bytes())!=result['audio']['sha256']:
            raise VoiceAudioError('Synthesized audio integrity check failed','worker_failed',resources)
    return result|{'resources':resources,'implementation_sha256':implementation_fingerprint()}


def discover_voices(folder,stop):return _invoke('voices',{},folder,stop)
def transcribe(path,model_path,language,folder,stop,progress=lambda _:None,*,authorize=None):
    from backend import audio_isolation,isolated_audio
    path=local_recording(path,folder);folder=Path(folder).resolve()
    try:
        pcm,metadata,evidence=audio_isolation.decode(path.read_bytes(),stop=stop,authorize=authorize)
    except audio_isolation.AudioIsolationError as exc:
        raise VoiceAudioError('Isolated recording decode failed: '+exc.code,exc.code) from None
    destination=folder/'decoded.f32le'
    if destination.exists():raise VoiceAudioError('Use a fresh PCM destination','invalid_input')
    destination.write_bytes(pcm)
    try:
        if stop.is_set():raise VoiceAudioError('Cancelled before transcription','cancelled')
        if authorize is None or authorize() is not True:raise VoiceAudioError('Recording authority revoked','authorization_revoked')
        result=_invoke('stt',{'pcm_path':str(destination),'pcm_sha256':sha(pcm),'source':metadata['source'],
            'model_path':str(model_path),'language':language},folder,stop,progress,authorize=authorize)
        if authorize() is not True:raise VoiceAudioError('Recording authority revoked','authorization_revoked')
        return result|{'decode_isolation':evidence,'decode_engine':metadata['engine']}
    finally:destination.unlink(missing_ok=True)
def synthesize(text,voice_id,language,rate,volume,folder,stop,progress=lambda _:None):
    validate_tts(text,voice_id,language,rate,volume)
    return _invoke('tts',{'text':text,'voice_id':voice_id,'language':language,'rate':rate,'volume':volume},folder,stop,progress)


def deny_extra_io(*args,**kwargs):raise VoiceAudioError('External media references are disabled','invalid_input')


def decode_recording(raw,*,stop=None,authorize=None):
    """Compatibility wrapper; compressed input always crosses the container boundary."""
    from backend import audio_isolation,isolated_audio
    try:
        pcm,metadata,_=audio_isolation.decode(raw,stop=stop,authorize=authorize)
        return isolated_audio.diagnostics(pcm,metadata['source'])
    except audio_isolation.AudioIsolationError as exc:
        raise VoiceAudioError('Isolated recording decode failed: '+exc.code,exc.code) from None


def generated_wav_diagnostics(raw):
    """Our fixed SAPI output is canonical uncompressed PCM; no AV parser is used."""
    import wave
    import numpy as np
    from backend import isolated_audio
    if not isinstance(raw,bytes) or not 1<=len(raw)<=MAX_BYTES:raise VoiceAudioError('Synthesized WAV exceeds output bound','output_limit')
    try:
        with wave.open(io.BytesIO(raw),'rb') as recording:
            if recording.getcomptype()!='NONE' or recording.getnchannels()!=1 or recording.getsampwidth()!=2 or recording.getframerate()!=RATE:
                raise VoiceAudioError('Unexpected synthesized PCM format','worker_failed')
            frames=recording.getnframes()
            if not RATE//10<=frames<=RATE*MAX_SECONDS:raise VoiceAudioError('Synthesized speech exceeds 60 seconds or is empty','output_limit')
            body=recording.readframes(frames+1)
            if len(body)!=frames*2:raise VoiceAudioError('Incomplete synthesized PCM','worker_failed')
        pcm=(np.frombuffer(body,dtype='<i2').astype(np.float32)/32768).astype('<f4').tobytes()
        return isolated_audio.diagnostics(pcm,{'kind':'wav','sample_rate':RATE,'channels':1})[1]
    except (wave.Error,EOFError,ValueError) as exc:
        if isinstance(exc,VoiceAudioError):raise
        raise VoiceAudioError('Invalid synthesized PCM WAV','worker_failed') from None


def speech_regions(audio):
    from faster_whisper.vad import VadOptions,get_speech_timestamps
    return get_speech_timestamps(audio,VadOptions(**VAD_OPTIONS),sampling_rate=RATE)


def transcribe_local(payload,folder,progress):
    from importlib.metadata import version
    import numpy as np
    if set(payload)!={'pcm_path','pcm_sha256','source','model_path','language'}:raise VoiceAudioError('Invalid transcription fields','invalid_input')
    model_info=model_languages(Path(payload['model_path']));language=payload['language']
    if language!='auto' and language not in model_info['codes']:raise VoiceAudioError('Language is absent from installed model tokenizer','unsupported_language')
    from backend import isolated_audio
    source=local_recording(payload['pcm_path'],folder)
    if source.stat().st_size>isolated_audio.MAX_PCM:raise VoiceAudioError('PCM input exceeds limit','invalid_input')
    raw=source.read_bytes()
    if sha(raw)!=payload['pcm_sha256']:raise VoiceAudioError('PCM identity changed','invalid_input')
    audio,diagnostics=isolated_audio.diagnostics(raw,payload['source'])
    progress({'stage':'audio_decoded','step':1,'total':1})
    warnings=[]
    if diagnostics['clipped_fraction']>.01:warnings.append('Recording contains clipped samples; transcription may be unreliable.')
    empty={'success':True,'status':'no_speech','text':'','segments':[],'language':None,'language_probability':None,
        'language_probability_scope':'Engine language-classification score, not transcription accuracy.',
        'diagnostics':diagnostics,'warnings':warnings,'model':model_info,'processing':'local','experimental':True,
        'vad':{'enabled':True,'options':VAD_OPTIONS},'engine':{'name':'faster-whisper','version':version('faster-whisper'),'device':'cpu','compute_type':'int8'}}
    if diagnostics['peak']<.001 or diagnostics['rms']<.0001:
        empty['reason']='near_silence';empty['vad'].update({'evaluated':False,'speech_seconds':0,'speech_regions':[]})
        return empty
    regions=speech_regions(audio)
    speech_seconds=sum(r['end']-r['start'] for r in regions)/RATE
    empty['vad'].update({'evaluated':True,'speech_seconds':speech_seconds,
        'speech_regions':[{'start':r['start']/RATE,'end':r['end']/RATE} for r in regions]})
    if speech_seconds<.25:
        empty['reason']='no_detected_speech';warnings.append('No sufficient speech detected. Noise rejection is probabilistic, not a guarantee for every recording.')
        return empty
    progress({'stage':'speech_detected','step':round(speech_seconds*1000),'total':round(diagnostics['duration']*1000)})
    from faster_whisper import WhisperModel
    model=WhisperModel(str(Path(payload['model_path']).resolve()),device='cpu',compute_type='int8',cpu_threads=4,num_workers=1,local_files_only=True)
    segments,info=model.transcribe(audio,language=None if language=='auto' else language,beam_size=1,best_of=1,
        temperature=0,condition_on_previous_text=False,vad_filter=True,vad_parameters=VAD_OPTIONS,
        no_speech_threshold=.6,log_prob_threshold=-1,compression_ratio_threshold=2.4)
    rows=[];characters=0
    for segment in segments:
        text=segment.text.strip()
        if not text:continue
        characters+=len(text)
        if len(rows)>=500 or characters>20000:raise VoiceAudioError('Transcription output limit exceeded','output_limit')
        row={'start':round(segment.start,3),'end':round(segment.end,3),'text':text,
            'avg_logprob':float(segment.avg_logprob),'no_speech_probability':float(segment.no_speech_prob),'compression_ratio':float(segment.compression_ratio)}
        if not all(math.isfinite(row[k]) for k in ('start','end','avg_logprob','no_speech_probability','compression_ratio')):raise VoiceAudioError('Invalid transcription metrics','worker_failed')
        rows.append(row);progress({'stage':'transcribing_segments','step':min(round(segment.end*1000),round(diagnostics['duration']*1000)),'total':round(diagnostics['duration']*1000)})
    probability=float(info.language_probability)
    if probability<.7:warnings.append('Language identification is uncertain; confirm the transcript and language before continuing.')
    if any(r['avg_logprob']<-.8 for r in rows):warnings.append('Some transcript segments have low model likelihood; review wording.')
    warnings.append('VAD and model scores cannot guarantee correctness or reject all noise. Review the transcript before sending it to the assistant.')
    return empty|{'status':'transcribed' if rows else 'no_speech','reason':None if rows else 'no_recognized_speech',
        'text':' '.join(r['text'] for r in rows),'segments':rows,'language':info.language,'language_probability':probability,
        'requested_language':language}

# Constant code only: user text is read as JSON and passed to Speak(String), never
# interpolated into PowerShell code, SSML, a command line, or a lexicon URL.
SAPI_SCRIPT=r'''
param([Parameter(Mandatory=$true)][string]$InputPath,[Parameter(Mandatory=$true)][string]$OutputFolder)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
$request=[IO.File]::ReadAllText($InputPath,[Text.Encoding]::UTF8) | ConvertFrom-Json
$synth=New-Object System.Speech.Synthesis.SpeechSynthesizer
function DescribeVoice($voice) {
    $info=$voice.VoiceInfo
    $material=$info.Id+"`n"+$info.Name+"`n"+$info.Culture.Name
    $hash=[Security.Cryptography.SHA256]::Create()
    try { $hex=([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($material)))).Replace('-','').ToLowerInvariant() }
    finally { $hash.Dispose() }
    return [PSCustomObject]@{id=('sapi-'+$hex.Substring(0,32));name=$info.Name;language=$info.Culture.Name;enabled=[bool]$voice.Enabled;local=$true;gender=[string]$info.Gender;age=[string]$info.Age}
}
try {
    $allowedNames=@('Microsoft David Desktop','Microsoft Zira Desktop','Microsoft David','Microsoft Mark','Microsoft Zira')
    $installed=@($synth.GetInstalledVoices() | Where-Object {$_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'en-US' -and $allowedNames -ccontains $_.VoiceInfo.Name})
    $voices=@($installed | ForEach-Object { DescribeVoice $_ })
    if ($request.operation -eq 'voices') {
        $result=@{success=$true;engine='windows-system-speech';voices=$voices;processing='local';scope='Installed enabled voices; enumeration is not an audio quality test.'}
    } elseif ($request.operation -eq 'tts') {
        $selected=@($installed | Where-Object {(DescribeVoice $_).id -ceq [string]$request.voice_id})
        if ($selected.Count -ne 1) {throw 'voice_unavailable'}
        $descriptor=DescribeVoice $selected[0]
        $requested=([string]$request.language).ToLowerInvariant()
        $actual=([string]$descriptor.language).ToLowerInvariant()
        if (($requested -ne $actual) -and (($requested.Contains('-')) -or ($requested -ne $actual.Split('-')[0]))) {throw 'unsupported_language'}
        if ([int]$request.rate -lt -5 -or [int]$request.rate -gt 5 -or [int]$request.volume -lt 0 -or [int]$request.volume -gt 100) {throw 'invalid_input'}
        $synth.SelectVoice($selected[0].VoiceInfo.Name)
        $synth.Rate=[int]$request.rate
        $synth.Volume=[int]$request.volume
        $format=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
        $synth.SetOutputToWaveFile((Join-Path $OutputFolder 'speech.wav'),$format)
        $synth.Speak([string]$request.text)
        $synth.SetOutputToNull()
        $result=@{success=$true;engine='windows-system-speech';voice=$descriptor;rate=[int]$request.rate;volume=[int]$request.volume;processing='local';input_mode='plain_text_Speak_String';generated=$true;voice_cloning=$false}
    } else {throw 'invalid_input'}
    $json=$result | ConvertTo-Json -Depth 8 -Compress
    [IO.File]::WriteAllText((Join-Path $OutputFolder 'sapi-result.json'),$json,(New-Object Text.UTF8Encoding($false)))
} catch {
    $code='tts_unavailable'
    if ($_.Exception.Message -in @('unsupported_language','voice_unavailable','invalid_input')) {$code=$_.Exception.Message}
    $json=@{success=$false;code=$code;error='Installed Windows speech engine could not complete this request.'} | ConvertTo-Json -Compress
    [IO.File]::WriteAllText((Join-Path $OutputFolder 'sapi-result.json'),$json,(New-Object Text.UTF8Encoding($false)))
} finally { $synth.Dispose() }
'''


def sapi_local(mode,payload,folder,progress):
    if os.name!='nt':raise VoiceAudioError('Windows speech synthesis is not available on this platform','tts_unavailable')
    folder=Path(folder).resolve()
    executable=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    if not executable.is_file():raise VoiceAudioError('Windows PowerShell 5 speech host is unavailable','tts_unavailable')
    if mode=='tts':
        if set(payload)!={'text','voice_id','language','rate','volume'}:raise VoiceAudioError('Invalid speech fields','invalid_input')
        validate_tts(**payload)
        if (folder/'speech.wav').exists():raise VoiceAudioError('Refusing to overwrite synthesized audio','invalid_input')
    elif payload:raise VoiceAudioError('Voice enumeration takes no input','invalid_input')
    request=folder/'sapi-input.json';request.write_text(packed(payload|{'operation':mode}),encoding='utf-8')
    script=folder/'sapi-fixed.ps1';script.write_text(SAPI_SCRIPT,encoding='utf-8-sig')
    progress({'stage':'discovering_voices' if mode=='voices' else 'synthesizing','step':0,'total':1})
    stderr=folder/'sapi-stderr.log'
    with stderr.open('wb') as err:
        process=subprocess.run([str(executable),'-NoLogo','-NoProfile','-NonInteractive','-File',str(script),'-InputPath',str(request),'-OutputFolder',str(folder)],
            cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=err,
            timeout=8 if mode=='voices' else 50,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    result_path=folder/'sapi-result.json'
    if process.returncode or not result_path.is_file() or result_path.stat().st_size>128*1024:
        raise VoiceAudioError('Installed Windows speech engine is unavailable or failed','tts_unavailable')
    result=json.loads(result_path.read_text(encoding='utf-8-sig'))
    if result.get('success') is not True:raise VoiceAudioError(result.get('error','Speech engine unavailable'),result.get('code','tts_unavailable'))
    result.update({'experimental':True,'local_voice_policy':'Only the five verified Microsoft David Desktop/Zira Desktop/David/Mark/Zira en-US voice names are enabled for this pilot. Other installed engines are not assumed local.','redistribution':'Uses the installed Windows speech engine. Voice binaries are not bundled or redistributed; installation and generated-output rights are not expanded by this app.',
                   'network_scope':'Fixed local System.Speech engine, no cloud adapter; Python socket guard is not an OS firewall.'})
    if mode=='tts':
        path=folder/'speech.wav'
        if not path.is_file() or not 0<path.stat().st_size<=MAX_BYTES:raise VoiceAudioError('Synthesized WAV exceeds output bound','output_limit')
        raw=path.read_bytes();diagnostics=generated_wav_diagnostics(raw)
        if diagnostics['duration']>60:raise VoiceAudioError('Synthesized speech exceeds 60 seconds; shorten the text','output_limit')
        if payload['volume']>0 and diagnostics['peak']<.001:raise VoiceAudioError('Speech engine produced no audible waveform','tts_unavailable')
        result['audio']={'filename':'speech.wav','mime':'audio/wav','bytes':len(raw),'sha256':sha(raw),**diagnostics}
        result['text_sha256']=sha(payload['text'].encode('utf-8'))
        result['warnings']=['Synthesized speech is not a recording of the user and is not voice cloning. Pronunciation and prosody require listening review.']
        if payload['volume']==0:result['warnings'].append('Output is deliberately muted because volume is zero.')
    progress({'stage':'voices_discovered' if mode=='voices' else 'speech_synthesized','step':1,'total':1})
    return result
