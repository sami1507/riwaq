"""Confirmed-part API/state checks. Synthesizer here is an explicit PCM fixture."""
import hashlib
import json
import threading
import time
import pytest
from backend import voice,voice_audio,voice_segments
from backend.db import get_db
from test_voice_sessions import setup,start,call,submit,wait,recording


@pytest.mark.parametrize('text',['One sentence.','أولاً. ثانياً؟ ثم بقي النص',('A'*370)+'. Another sentence.',('Words. '*240)[:1800]])
def test_segments_preserve_completed_text_offsets(text):
    parts=voice_segments.parts(text)
    assert ''.join(p['text'] for p in parts)==text
    assert 1<=len(parts)<=6
    for p in parts:assert text[p['start']:p['end']]==p['text']


def test_unbounded_and_bad_audio_are_not_confirmed():
    for text in ('','x'*1801):
        with pytest.raises(ValueError):voice_segments.parts(text)
    raw=recording();receipt={'audio':{'sha256':hashlib.sha256(raw).hexdigest(),'duration':1.0}}
    assert voice_segments.confirm_audio(raw,receipt)==1
    with pytest.raises(ValueError):voice_segments.confirm_audio(raw,{'audio':{'sha256':'0'*64,'duration':1}})
    with pytest.raises(ValueError):voice_segments.confirm_audio(raw,{'audio':{'sha256':hashlib.sha256(raw).hexdigest(),'duration':20}})
    with pytest.raises(ValueError):voice_segments.confirm_audio(raw[:-10],{'audio':{'sha256':hashlib.sha256(raw[:-10]).hexdigest(),'duration':1}})


def wait_part(e,s,seconds=3):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        value=call(e,s,'GET').json()
        if value['turns'] and value['turns'][-1].get('audio_parts'):return value
        time.sleep(.01)
    raise AssertionError('No confirmed part appeared')


def blocked_second(e,monkeypatch):
    e.reply=('First complete sentence with synthetic content. '*7)+'Second complete sentence.'
    original=voice_audio.synthesize;release=threading.Event();count=[]
    def synth(*args):
        count.append(args[0])
        if len(count)==2:
            stop=args[6]
            while not release.wait(.01):
                if stop.is_set():raise ValueError('cancelled fixture')
        return original(*args)
    monkeypatch.setattr(voice_audio,'synthesize',synth)
    return release,count


def test_first_part_available_before_second_ready_and_stable_after_completion(setup,monkeypatch):
    e=setup;release,count=blocked_second(e,monkeypatch);s=start(e,segmented_speech=True);submit(e,s)
    try:
        value=wait_part(e,s);turn=value['turns'][-1]
        assert value['active'] and not turn['audio_parts_complete'] and len(turn['audio_parts'])==1
        route='/turns/'+turn['id']+'/audio-parts/'
        first=call(e,s,'GET',route+'0');assert first.status_code==200
        assert first.headers['X-Riwaq-Audio-SHA256']==turn['audio_parts'][0]['sha256']
        assert call(e,s,'GET',route+'1').status_code==404
        assert call(e,s,'GET','/turns/'+turn['id']+'/audio').status_code==404
    finally:release.set()
    completed=wait(e,s)['turns'][-1]
    assert completed['audio_parts_complete'] and len(count)>=2
    assert ''.join(p['text'] for p in completed['audio_parts'])==completed['spoken_text']
    assert completed['audio_parts'][0]==turn['audio_parts'][0]
    assert call(e,s,'GET',route+'0').content==first.content
    assert completed['playback']['completed'] is False
    saved=call(e,s,'POST','/save',json={'confirm':True})
    assert saved.status_code==200
    with get_db() as c:
        receipt=json.loads(c.execute('SELECT receipt FROM voice_saved_turns WHERE conversation_id=?',(saved.json()['conversation_id'],)).fetchone()[0])
    assert len(receipt['tts_parts'])==len(completed['audio_parts'])
    assert receipt['tts_parts_complete'] and not receipt['raw_audio_saved']
    assert not receipt['tts_part_playback_recorded']


def test_interrupt_discards_confirmed_parts_and_rejects_late_worker_result(setup,monkeypatch):
    e=setup;release,_=blocked_second(e,monkeypatch);s=start(e,segmented_speech=True);submit(e,s)
    value=wait_part(e,s);turn=value['turns'][-1]
    try:
        assert call(e,s,'POST','/interrupt',json={'expected_epoch':value['epoch']}).status_code==200
        assert call(e,s,'GET','/turns/'+turn['id']+'/audio-parts/0').status_code==404
    finally:release.set()
    settled=wait(e,s)['turns'][-1]
    assert settled['audio_parts']==[] and settled['state']=='cancelled'


def test_project_token_and_source_withdrawal_revoke_all_part_bytes(setup,monkeypatch):
    e=setup;release,_=blocked_second(e,monkeypatch)
    uploaded=e.client.post(f'/api/projects/{e.pid}/files',files={'file':('source.txt',b'ORBIT-751 synthetic spacecraft facts.','text/plain')})
    assert uploaded.status_code==201
    fid=uploaded.json()['id'];s=start(e,segmented_speech=True,use_files=True);submit(e,s,text='What is ORBIT-751?')
    value=wait_part(e,s);turn=value['turns'][-1];route='/turns/'+turn['id']+'/audio-parts/0'
    try:
        assert turn['citations']
        assert e.client.get(s['_path']+route,headers={'X-Riwaq-Voice-Token':'wrong'}).status_code==404
        other=e.client.post('/api/projects',json={'name':'Other'}).json()['id']
        assert e.client.get((s['_path']+route).replace(e.pid,other),headers=s['_headers']).status_code==404
        assert e.client.delete(f'/api/projects/{e.pid}/files/{fid}').status_code==200
        assert call(e,s,'GET',route).status_code==404
    finally:release.set()
    settled=wait(e,s)['turns'][-1]
    assert settled['state']=='revoked' and settled['audio_parts']==[] and settled['reply']==''
