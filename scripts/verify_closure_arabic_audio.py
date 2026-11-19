"""Recheck the existing synthetic Arabic fixture, with no microphone or playback.

Fixture conversion is test preparation, not the application's decoder. The
actual API receives WAV and must use the pinned isolated decoder before Whisper.
This smoke checks execution and language presence, not transcription accuracy.
"""
import argparse,hashlib,io,json,os,re,sys,time,wave
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False);data=out/'data';data.mkdir()
    (data/'isolated-workers.json').write_bytes((ROOT/'data/isolated-workers.json').read_bytes())
    os.environ.update(RIWAQ_DATA_DIR=str(data),RIWAQ_WHISPER_MODEL='D:/RiwaqRuntime/whisper-small',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    source=Path('D:/RiwaqRuntime/arabic-validation.mp3');raw=source.read_bytes()
    import av
    wav=io.BytesIO()
    with av.open(io.BytesIO(raw)) as audio, wave.open(wav,'wb') as target:
        target.setnchannels(1);target.setsampwidth(2);target.setframerate(16000)
        resampler=av.AudioResampler(format='s16',layout='mono',rate=16000)
        for frame in audio.decode(audio=0):
            for part in resampler.resample(frame):target.writeframes(part.to_ndarray().tobytes())
        for part in resampler.resample(None):target.writeframes(part.to_ndarray().tobytes())
    recording=wav.getvalue()
    from backend import app,local_auth
    from fastapi.testclient import TestClient
    started=time.monotonic()
    with TestClient(app.app,headers={'X-Riwaq-Client':'local'}) as client:
        assert client.post('/api/auth/login',json={'pairing_code':local_auth.issue_pairing_code()}).status_code==200
        response=client.post('/api/media/transcribe',files={'file':('synthetic-arabic.wav',recording,'audio/wav')},data={'language':'ar'})
        result=response.json()
    passed=(response.status_code==200 and result.get('language')=='ar' and len(re.findall('[\u0600-\u06ff]',result.get('text','')))>20 and result.get('decode_isolation',{}).get('cleanup_verified') is True)
    report={'passed':passed,'status':response.status_code,'seconds':time.monotonic()-started,'source_sha256':hashlib.sha256(raw).hexdigest(),'wav_sha256':hashlib.sha256(recording).hexdigest(),'source':'https://huggingface.co/rhasspy/piper-voices/tree/main/ar/ar_JO/kareem/medium/samples','result':result,'scope':'Previously acquired public synthetic Arabic speech, actual authenticated API + pinned Docker decode + local CPU Whisper. No gold transcript, WER, pronunciation, accent or microphone-quality claim; no playback.','source_files':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ('backend/media.py','backend/audio_isolation.py','backend/isolated_audio.py','backend/audio_decode.py')}}
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if passed:
        candidate={'passed':True,'model_path':result['model_path'],'fingerprint':result['fingerprint'],'evidence':str((out/'report.json').relative_to(ROOT)),'scope':report['scope'],'quality_measured':False,'languages_tested':['ar'],'decode_image_id':result['decode_isolation'].get('image_id')}
        (out/'stt-record.json').write_text(json.dumps(candidate,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':passed,'status':response.status_code}));return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
