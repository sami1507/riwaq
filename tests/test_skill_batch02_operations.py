"""Independent operation-boundary tests; voice workers are explicit test doubles."""
import asyncio
import hashlib
import io
import json
import wave
from types import SimpleNamespace

import pytest

from backend import guard,skill_operations as operations


def test_credential_presence_never_returns_secret_value(monkeypatch):
    from backend import providers
    marker='SYNTHETIC_SECRET_MUST_NOT_LEAVE_KEYRING'
    monkeypatch.setattr(providers,'secret',lambda name:marker if name=='openai' else None)
    result=operations.credential_status()
    assert marker not in json.dumps(result)
    assert result['credentials'][0]['configured'] is True
    assert all(row['generation_enabled'] is False for row in result['credentials'])
    assert result['connection_verified'] is False


@pytest.mark.asyncio
async def test_initial_permission_denial_never_starts_thread_function():
    invoked=[]
    def work(stop):invoked.append(True);return 'should not execute'
    def denied():raise guard.Denied('revoked')
    with pytest.raises(guard.Denied):await operations.stopped_thread(work,denied)
    assert invoked==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('mismatch',['text','voice','language'])
async def test_speech_rejects_result_bound_to_different_request(monkeypatch,tmp_path,mismatch):
    from backend import voice,voice_audio
    voice_id='sapi-'+'a'*32
    options=SimpleNamespace(voiceId=voice_id,language='en-US',rate=0)
    async def voices():return [{'id':voice_id,'language':'en-US','enabled':True}]
    monkeypatch.setattr(voice,'voices',voices)
    monkeypatch.setattr(voice,'work_dir',lambda:tmp_path)
    def synthesize(text,selected,language,rate,volume,folder,stop):
        raw=io.BytesIO()
        with wave.open(raw,'wb') as sound:
            sound.setnchannels(1);sound.setsampwidth(2);sound.setframerate(16000)
            sound.writeframes(b'\xe8\x03'*1600)
        data=raw.getvalue();(folder/'speech.wav').write_bytes(data)
        return {'audio':{'sha256':hashlib.sha256(data).hexdigest()},
                'text_sha256':hashlib.sha256(('wrong' if mismatch=='text' else text).encode()).hexdigest(),
                'voice':{'id':('sapi-'+'b'*32 if mismatch=='voice' else selected),'language':('ar-SA' if mismatch=='language' else language)},
                'generated':True,'processing':'local'}
    monkeypatch.setattr(voice_audio,'synthesize',synthesize)
    with pytest.raises(ValueError):await operations.speech('Synthetic narration.',options,lambda:None)
