"""Temporary-DB voice session contracts using explicitly simulated STT/TTS/LLM.

These tests prove API/state/privacy behavior, NOT speech recognition, synthesis,
listening quality, model answer quality, or real worker resource containment.
"""
import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import time
import wave
import pytest
from fastapi.testclient import TestClient
from backend import app as service,voice,voice_audio,media,providers,visual_jobs,voice_capabilities
from backend.db import get_db

VOICE='sapi-'+'a'*32


def recording():
    out=io.BytesIO()
    with wave.open(out,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*16000)
    return out.getvalue()


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(voice,'SESSIONS',{});monkeypatch.setattr(voice,'ACTIVE',{});monkeypatch.setattr(voice,'ENDED',{})
    monkeypatch.setattr(service,'ACTIVE',{});monkeypatch.setattr(media,'ACTIVE_MEDIA',None)
    monkeypatch.setattr(visual_jobs,'busy',lambda:False)
    monkeypatch.setattr(voice,'VOICE_CACHE',{'voices':[{'id':VOICE,'name':'SIMULATED English voice','language':'en-US','enabled':True,'local':True}],'error':None,'at':time.monotonic()})
    evidence=SimpleNamespace(calls=[],stt_text='An uncorrected recorded sentence.',reply='A simulated response.',root=tmp_path)
    async def validate(provider,model,mode):
        evidence.calls.append(('validate',provider,model,mode));return {'model':model}
    async def stream(provider,model,messages,options):
        evidence.calls.append(('llm',provider,model,messages,options))
        yield {'content':evidence.reply};yield {'finish_reason':'stop'}
    def stt(path,model,language,folder,stop,progress,*,authorize):
        assert authorize() is True
        evidence.calls.append(('stt',language));assert path.is_file()
        return {'success':True,'text':evidence.stt_text,'language':'en','language_probability':.9,'segments':[{'start':0,'end':1,'text':evidence.stt_text}], 'fixture':'SIMULATED TRANSCRIPTION'}
    def tts(text,voice_id,language,rate,volume,folder,stop,progress):
        evidence.calls.append(('tts',text,voice_id,language,rate,volume));folder.mkdir(parents=True,exist_ok=True)
        raw=recording();(folder/'speech.wav').write_bytes(raw)
        return {'success':True,'audio':{'duration':1.0,'sha256':voice.sha(raw)},'fixture':'SILENT PCM STORAGE FIXTURE, NOT SYNTHESIZED SPEECH'}
    def resolve(tag,folder):
        mapping={'auto':None,'en':'en','en-US':'en','ar':'ar','ar-PS':'ar','he':'he','es':'es'}
        if tag not in mapping:raise ValueError('Unsupported fixture language')
        return mapping[tag]
    monkeypatch.setattr(providers,'validate_provider',validate);monkeypatch.setattr(providers,'stream',stream)
    monkeypatch.setattr(voice_audio,'transcribe',stt);monkeypatch.setattr(voice_audio,'synthesize',tts)
    monkeypatch.setattr(media,'whisper_config',lambda:{'configured':True,'model_path':str(tmp_path/'simulated-model'),'model':'mock-only'})
    monkeypatch.setattr(voice_capabilities,'resolve_language',resolve)
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as client:
        from backend.local_auth import issue_pairing_code
        assert client.post('/api/auth/login', json={'pairing_code': issue_pairing_code()}).status_code == 200
        evidence.client=client;evidence.pid=client.get('/api/projects').json()[0]['id'];yield evidence
    assert not voice.ACTIVE and not voice.SESSIONS


def start(e,**opts):
    response=e.client.post(f'/api/projects/{e.pid}/voice/sessions',json={'reply_language':'en','voice_id':VOICE,**opts})
    assert response.status_code==201,response.text
    session=response.json();session['_path']=f'/api/projects/{e.pid}/voice/sessions/'+session['id']
    session['_headers']={'X-Riwaq-Voice-Token':session['token']};return session


def call(e,s,method,suffix='',**kwargs):
    return e.client.request(method,s['_path']+suffix,headers=s['_headers'],**kwargs)


def wait(e,s,seconds=3):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        response=call(e,s,'GET');assert response.status_code==200,response.text
        value=response.json()
        if not value['active']:return value
        time.sleep(.01)
    raise AssertionError('Simulated session failed to settle: '+str(value))


def submit(e,s,text='Reviewed text',key='submit-key-001',**extra):
    epoch=call(e,s,'GET').json()['epoch']
    response=call(e,s,'POST','/turns',json={'text':text,'turn_key':key,'expected_epoch':epoch,**extra})
    assert response.status_code==202,response.text
    return response.json()


def transcribe(e,s,key='record-key-001',**data):
    epoch=call(e,s,'GET').json()['epoch']
    response=call(e,s,'POST','/transcribe',files={'file':('test.wav',recording(),'audio/wav')},
        data={'turn_key':key,'expected_epoch':str(epoch),'origin':'upload',**data})
    assert response.status_code==202,response.text
    return response.json()


def test_capability_token_project_scope_and_two_session_limit(setup):
    e=setup;s=start(e);q=e.client.post('/api/projects',json={'name':'Other scope'}).json()['id']
    assert e.client.get(s['_path']).status_code==404
    assert e.client.get(s['_path'],headers={'X-Riwaq-Voice-Token':'wrong'}).status_code==404
    foreign=s['_path'].replace(e.pid,q)
    assert e.client.get(foreign,headers=s['_headers']).status_code==404
    assert e.client.post(foreign+'/end',headers=s['_headers']).status_code==404
    second=start(e)
    assert e.client.post(f'/api/projects/{e.pid}/voice/sessions',json={}).status_code==429
    assert call(e,s,'POST','/end').json()=={'ended':True}
    assert call(e,s,'POST','/end').json()=={'ended':True}
    assert call(e,s,'GET').status_code==404
    assert start(e)['id'] not in {s['id'],second['id']}


def test_no_implicit_sqlite_or_raw_audio_persistence(setup):
    e=setup;s=start(e);t=transcribe(e,s);ready=wait(e,s)['turns'][0]
    assert ready['state']=='ready_to_submit' and not any(c[0]=='llm' for c in e.calls)
    submit(e,s,'I corrected the transcript.',turn_id=t['id']);turn=wait(e,s)['turns'][0]
    assert turn['audio_ready'] and turn['transcript_original']==e.stt_text
    assert turn['transcript']=='I corrected the transcript.' and turn['corrected']
    assert '_audio' not in turn and 'token' not in call(e,s,'GET').json()
    with get_db() as db:
        assert db.execute('SELECT COUNT(*) FROM conversations').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM voice_saved_turns').fetchone()[0]==0
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='voice_audio'").fetchone()
    assert not any((e.root/'voice_work').iterdir())
    assert call(e,s,'POST','/end').status_code==200
    assert not voice.SESSIONS and not voice.ACTIVE


def test_turn_idempotency_and_conflicting_reuse(setup):
    e=setup;s=start(e);body={'text':'Same final text','turn_key':'idempotent-turn-1','expected_epoch':0}
    first=call(e,s,'POST','/turns',json=body);assert first.status_code==202
    wait(e,s)
    second=call(e,s,'POST','/turns',json=body);assert second.json()['id']==first.json()['id']
    assert len([c for c in e.calls if c[0]=='llm'])==1
    assert call(e,s,'POST','/turns',json=body|{'text':'Changed'}).status_code==409
    assert call(e,s,'POST','/turns',json=body|{'turn_key':'new-key-old-epoch'}).status_code==409


def test_transcribe_idempotency_and_language_override(setup):
    e=setup;s=start(e,input_language='en');t=transcribe(e,s);wait(e,s)
    again=transcribe(e,s);assert again['id']==t['id']
    assert len([x for x in e.calls if x[0]=='stt'])==1
    response=call(e,s,'POST','/transcribe',files={'file':('test.wav',recording(),'audio/wav')},
        data={'turn_key':'different-language','expected_epoch':'1','origin':'upload','language':'ar'})
    assert response.status_code==422


@pytest.mark.parametrize('mode',['conversation','translation','transcription'])
def test_modes_have_distinct_provider_dispatch(setup,mode):
    e=setup;s=start(e,mode=mode);submit(e,s,'Do not translate the number 27.',key='mode-contract-1')
    turn=wait(e,s)['turns'][0];calls=[x for x in e.calls if x[0]=='llm']
    assert turn['state']=='complete'
    if mode=='transcription':
        assert not calls and not any(x[0] in {'validate','tts'} for x in e.calls)
        assert turn['reply']=='' and not turn['audio_ready']
    else:
        assert len(calls)==1 and calls[0][1]=='ollama'
        messages=calls[0][3]
        if mode=='translation':
            assert 'Translate the literal text' in messages[0]['content']
            assert json.loads(messages[1]['content'])['text']=='Do not translate the number 27.'
            assert turn['citations']==[]
        else:assert any('Reply in language tag en' in m['content'] for m in messages if m['role']=='system')


def test_cancel_provider_stalled_before_first_packet_is_prompt(setup,monkeypatch):
    e=setup;s=start(e);entered=threading.Event();closed=threading.Event()
    async def stalled(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
            yield {'content':'Must never publish'}
        finally:closed.set()
    monkeypatch.setattr(providers,'stream',stalled)
    turn=submit(e,s,key='cancel-stalled-first');assert entered.wait(2)
    active=call(e,s,'GET').json();started=time.monotonic()
    response=call(e,s,'POST','/interrupt',json={'expected_epoch':active['epoch']})
    assert response.status_code==200
    settled=wait(e,s,seconds=1.5)
    assert time.monotonic()-started<1.5 and closed.is_set()
    assert settled['turns'][0]['state']=='cancelled' and settled['turns'][0]['reply']==''
    assert call(e,s,'GET',f'/turns/{turn["id"]}/audio').status_code==404
    assert not any(x[0]=='tts' for x in e.calls)


def test_cancel_stt_disallows_late_result_and_retains_occupied_slot(setup,monkeypatch):
    e=setup;s=start(e);entered=threading.Event();release=threading.Event()
    def stalled(path,model,language,folder,stop,progress,*,authorize):
        assert authorize() is True
        entered.set();release.wait(3)
        return {'text':'LATE DATA MUST NOT PUBLISH','language':'en','segments':[]}
    monkeypatch.setattr(voice_audio,'transcribe',stalled)
    try:
        transcribe(e,s);assert entered.wait(2)
        epoch=call(e,s,'GET').json()['epoch']
        assert call(e,s,'POST','/interrupt',json={'expected_epoch':epoch}).status_code==200
        current=call(e,s,'GET').json()
        assert current['active']
        assert call(e,s,'POST','/turns',json={'text':'New request','turn_key':'while-cleaning-up','expected_epoch':current['epoch']}).status_code==409
        release.set();finished=wait(e,s)
        assert finished['turns'][0]['state']=='cancelled' and finished['turns'][0]['transcript']==''
    finally:release.set()


def test_playback_duration_epoch_and_unheard_history(setup):
    e=setup;s=start(e);t=submit(e,s);first=wait(e,s);turn=first['turns'][0]
    path=f'/turns/{t["id"]}/playback';epoch=first['epoch']
    assert call(e,s,'POST',path,json={'expected_epoch':epoch,'played_seconds':2.0}).status_code==422
    assert call(e,s,'POST',path,json={'expected_epoch':epoch,'played_seconds':0.0,'completed':True}).status_code==422
    assert call(e,s,'POST',path,json={'expected_epoch':epoch,'played_seconds':.2,'completed':False}).status_code==200
    playback=call(e,s,'POST',path,json={'expected_epoch':epoch,'played_seconds':.1,'completed':False}).json()['playback']
    assert playback['played_seconds']==.2 and not playback['completed']
    submit(e,s,'Next message',key='next-message-epoch');wait(e,s)
    assert call(e,s,'GET',f'/turns/{t["id"]}/audio').status_code==404
    assert call(e,s,'POST',path,json={'expected_epoch':epoch,'played_seconds':1.0,'completed':True}).status_code==409
    latest=[x for x in e.calls if x[0]=='llm'][-1][3]
    assert any('unheard text is omitted' in m['content'] for m in latest)
    assert not any(m['role']=='assistant' and m['content']==e.reply for m in latest)


def test_save_confirmation_idempotency_original_edit_and_cascade(setup):
    e=setup;s=start(e);t=transcribe(e,s);wait(e,s);submit(e,s,'Reviewed replacement.',turn_id=t['id']);finished=wait(e,s)
    assert call(e,s,'POST','/save',json={'confirm':False}).status_code==422
    saved=call(e,s,'POST','/save',json={'confirm':True,'title':'Explicitly saved'});assert saved.status_code==200,saved.text
    cid=saved.json()['conversation_id'];assert saved.json()['raw_audio_saved'] is False
    assert call(e,s,'POST','/save',json={'confirm':True,'title':'Same content'}).json()['conversation_id']==cid
    with get_db() as db:
        row=db.execute('SELECT * FROM voice_saved_turns WHERE conversation_id=?',(cid,)).fetchone()
        assert row['transcript_original']==e.stt_text and row['transcript_reviewed']=='Reviewed replacement.'
        receipt=json.loads(row['receipt']);assert receipt['corrected'] and receipt['origin']=='upload'
        assistant=db.execute("SELECT * FROM messages WHERE conversation_id=? AND role='assistant'",(cid,)).fetchone()
        assert assistant['status']=='interrupted'  # no playback confirmation, even though generation finished
    assert e.client.delete('/api/conversations/'+cid).status_code==200
    with get_db() as db:
        assert db.execute('SELECT COUNT(*) FROM voice_saved_turns').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==0


def test_unsupported_reply_keeps_text_and_never_falls_back_to_english(setup):
    e=setup;s=start(e,reply_language='ar');submit(e,s,'مرحبا');turn=wait(e,s)['turns'][0]
    assert turn['state']=='complete' and turn['reply']==e.reply and not turn['audio_ready']
    assert not any(x[0]=='tts' for x in e.calls)
    assert 'No selected local voice' in turn['error']


def test_hybrid_cloud_selection_is_not_dispatched_by_voice(setup):
    e=setup
    with get_db() as db:
        db.execute('INSERT INTO settings VALUES(?,?)',('provider',json.dumps('openai')))
        db.execute('INSERT INTO settings VALUES(?,?)',('mode',json.dumps('hybrid')))
    s=start(e);submit(e,s);turn=wait(e,s)['turns'][0]
    assert turn['state']=='failed' and not turn['audio_ready']
    assert not any(x[0] in {'validate','llm','tts'} for x in e.calls)


def test_unknown_stt_language_rejected_before_audio_worker(setup):
    e=setup;s=start(e,input_language='zz')
    response=call(e,s,'POST','/transcribe',files={'file':('test.wav',recording(),'audio/wav')},
        data={'turn_key':'unknown-language-1','expected_epoch':'0','origin':'upload'})
    assert response.status_code==422 and not any(x[0]=='stt' for x in e.calls)


def test_session_expiry_clears_in_memory_turns(setup):
    e=setup;s=start(e,mode='transcription');submit(e,s);wait(e,s)
    voice.SESSIONS[s['id']]['deadline']=time.monotonic()-1
    assert call(e,s,'GET').status_code==410
    assert s['id'] not in voice.SESSIONS
    with get_db() as db:assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==0


def test_unreviewed_transcription_is_not_saved_or_model_history(setup):
    e=setup;s=start(e);transcribe(e,s);wait(e,s)
    response=call(e,s,'POST','/save',json={'confirm':True})
    assert response.status_code==422,response.text
    with get_db() as db:assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==0
    submit(e,s,'A separately typed and reviewed request.',key='reviewed-new-turn')
    wait(e,s)
    messages=[entry for entry in e.calls if entry[0]=='llm'][-1][3]
    assert not any(e.stt_text in m['content'] for m in messages)
    saved=call(e,s,'POST','/save',json={'confirm':True})
    assert saved.status_code==200 and saved.json()['saved_turns']==1


def test_save_again_after_saved_conversation_delete_cannot_claim_missing_copy(setup):
    e=setup;s=start(e,mode='transcription');submit(e,s,'Persist only by explicit consent.');wait(e,s)
    first=call(e,s,'POST','/save',json={'confirm':True});assert first.status_code==200
    old=first.json()['conversation_id'];assert e.client.delete('/api/conversations/'+old).status_code==200
    again=call(e,s,'POST','/save',json={'confirm':True})
    # Recreate explicitly, or explain deletion. A 200 pointing to vanished data is invalid.
    assert again.status_code in {200,404,409},again.text
    if again.status_code==200:
        new=again.json()['conversation_id']
        with get_db() as db:assert db.execute('SELECT 1 FROM conversations WHERE id=?',(new,)).fetchone(),again.text
        assert new!=old


def test_user_end_clears_tokens_and_cancels_stalled_provider(setup,monkeypatch):
    e=setup;s=start(e);entered=threading.Event();closed=threading.Event()
    async def stalled(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
            yield {'content':'Never retained'}
        finally:closed.set()
    monkeypatch.setattr(providers,'stream',stalled)
    submit(e,s,key='end-while-awaiting');assert entered.wait(2)
    assert call(e,s,'POST','/end').status_code==200
    assert call(e,s,'GET').status_code==404
    deadline=time.monotonic()+1.5
    while voice.ACTIVE and time.monotonic()<deadline:time.sleep(.01)
    assert not voice.ACTIVE and closed.is_set()
    assert not voice.SESSIONS
    assert e.client.post(s['_path']+'/end',headers={'X-Riwaq-Voice-Token':'wrong'}).status_code==404


def test_completed_playback_is_required_for_complete_saved_assistant(setup):
    e=setup;s=start(e);t=submit(e,s);finished=wait(e,s)
    response=call(e,s,'POST',f'/turns/{t["id"]}/playback',json={'expected_epoch':finished['epoch'],'played_seconds':1.0,'completed':True})
    assert response.status_code==200
    saved=call(e,s,'POST','/save',json={'confirm':True});assert saved.status_code==200
    with get_db() as db:
        row=db.execute("SELECT status FROM messages WHERE conversation_id=? AND role='assistant'",(saved.json()['conversation_id'],)).fetchone()
        assert row['status']=='complete'


def test_session_metadata_is_not_http_cacheable(setup):
    e=setup;s=start(e)
    response=call(e,s,'GET')
    assert response.status_code==200
    assert response.headers['cache-control']=='no-store'
    assert 'token' not in response.json()


def test_old_revoked_source_does_not_cancel_an_unrelated_new_turn(setup,monkeypatch):
    e=setup;s=start(e,use_files=True)
    uploaded=e.client.post(f'/api/projects/{e.pid}/files',files={'file':('calibration.txt',b'The calibration code is ALFA-42.','text/plain')})
    assert uploaded.status_code==201
    submit(e,s,'What is the calibration code?');old=wait(e,s)['turns'][0]
    assert old['citations']
    assert e.client.delete(f'/api/projects/{e.pid}/files/'+uploaded.json()['id']).status_code==200
    entered=threading.Event();release=threading.Event()
    def pending(path,model,language,folder,stop,progress,*,authorize):
        assert authorize() is True
        entered.set();release.wait(2)
        assert not stop.is_set(),'Revoked history cancelled an unrelated current turn'
        return {'text':'Independent new recording','language':'en','segments':[]}
    monkeypatch.setattr(voice_audio,'transcribe',pending)
    try:
        new=transcribe(e,s,key='after-deletion-record');assert entered.wait(2)
        snapshot=call(e,s,'GET').json()
        assert snapshot['turns'][0]['state']=='revoked'
        assert not voice.ACTIVE[s['id']]['stop'].is_set()
        release.set();snapshot=wait(e,s)
        assert next(t for t in snapshot['turns'] if t['id']==new['id'])['state']=='ready_to_submit'
    finally:release.set()
