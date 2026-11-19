"""Real local API/STT/LLM/TTS smoke. Synthetic controlled speech, never a live mic claim.

Run against a running Riwaq loopback service. Keeps its labelled QA project and one
explicitly saved text conversation. No user recording, paid provider or download.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import threading
import time
import wave
import httpx
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.voice_audio import synthesize

def main(folder,base):
    folder=Path(folder).resolve()
    if folder.exists():raise ValueError('Use a new output folder')
    folder.mkdir(parents=True)
    report={'version':'voice-live-v1','started':time.time(),'passed':False,'cases':[],
      'scope':'Real local HTTP API, Whisper, Ollama and new System.Speech output. Controlled synthetic input, not a physical microphone or human speech quality test.',
      'paid_api_cost':0,'download_bytes':0,'human_listening':False,'physical_microphone':False}
    session=None;prefix=None;headers={'X-Riwaq-Client':'local'}
    client=httpx.Client(base_url=base,headers=headers,timeout=30,trust_env=False)
    def api(method,path,**kw):
        response=client.request(method,path,**kw);response.raise_for_status();return response.json()
    def snapshot():return api('GET',prefix)
    def wait_turn(tid,timeout=180):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            value=snapshot();t=next(x for x in value['turns'] if x['id']==tid)
            if not value['active']:return value,t
            time.sleep(.1)
        raise TimeoutError('Voice job did not finish')
    try:
        project=api('POST','/api/projects',json={'name':'QA voice real local '+time.strftime('%Y-%m-%d %H:%M:%S'),'instructions':'Controlled voice acceptance. Reply briefly and preserve numbers.'})
        report['project_id']=project['id'];root=f"/api/projects/{project['id']}/voice"
        caps=api('GET',root+'/status');report['stt_model']=caps['stt'];report['voices']=caps['voices']
        assert caps['stt']['available'] and caps['voices']
        selected=next(v for v in caps['voices'] if v['name']=='Microsoft David Desktop')
        opts={'mode':'conversation','input_language':'en','reply_language':'en-US','voice_id':selected['id'],'rate':0,'volume':80,'use_files':False}
        session=api('POST',root+'/sessions',json=opts|{'persist':False});prefix=root+'/sessions/'+session['id']
        client.headers['X-Riwaq-Voice-Token']=session['token']
        reference='What is seven times eight? Answer in one short sentence.'
        fixture=synthesize(reference,selected['id'],'en-US',0,100,folder/'input',threading.Event())
        report['input']={'text':reference,'audio_sha256':fixture['audio']['sha256'],'synthetic':True,'newly_generated':True}
        raw=(folder/'input/speech.wav').read_bytes()
        start=time.monotonic()
        turn=api('POST',prefix+'/transcribe',data={'turn_key':'live-transcribe-001','expected_epoch':str(session['epoch']),'origin':'upload'},files={'file':('controlled.wav',raw,'audio/wav')})
        session,turn=wait_turn(turn['id']);assert turn['state']=='ready_to_submit',turn
        report['cases'].append({'case':'synthetic_input_transcription','state':turn['state'],'transcript_original':turn['transcript_original'],'seconds':time.monotonic()-start,'timings':turn['timings']})
        text=turn['transcript'].lower()
        assert ('seven' in text or '7' in text) and ('eight' in text or '8' in text)
        turn=api('POST',prefix+'/turns',json={'turn_id':turn['id'],'text':turn['transcript'],'turn_key':'live-reviewed-001','expected_epoch':session['epoch']})
        session,turn=wait_turn(turn['id']);assert turn['state']=='complete' and turn['audio_ready'],turn
        assert '56' in turn['reply'] or 'fifty-six' in turn['reply'].lower(),turn['reply']
        audio=client.get(prefix+'/turns/'+turn['id']+'/audio');audio.raise_for_status()
        audio_path=folder/'response.wav';audio_path.write_bytes(audio.content)
        with wave.open(io.BytesIO(audio.content)) as w:duration=w.getnframes()/w.getframerate();assert w.getnframes()>0
        assert hashlib.sha256(audio.content).hexdigest()==turn['audio_sha256']
        assert abs(duration-turn['audio_duration'])<.02
        report['cases'].append({'case':'real_llm_and_new_speech','reply':turn['reply'],'spoken_text':turn['spoken_text'],'model':turn['model'],'timings':turn['timings'],'audio_sha256':turn['audio_sha256'],'duration':duration,'heard':False})
        # No synthetic playback acknowledgement: downloading is not hearing.
        saved=api('POST',prefix+'/save',json={'confirm':True,'title':'QA controlled voice text'})
        saved_again=api('POST',prefix+'/save',json={'confirm':True});assert saved==saved_again
        report['cases'].append({'case':'explicit_text_save_idempotent','result':saved})
        interrupted=api('POST',prefix+'/interrupt',json={'expected_epoch':session['epoch'],'turn_id':turn['id'],'played_seconds':0.0,'completed':False})
        assert client.get(prefix+'/turns/'+turn['id']+'/audio').status_code==404
        report['cases'].append({'case':'interrupt_invalidates_audio','epoch':interrupted['epoch']})
        session=api('PATCH',prefix,json=opts|{'mode':'translation','reply_language':'ar','voice_id':None})
        turn=api('POST',prefix+'/turns',json={'text':'Where is the train station?','turn_key':'live-translation-001','expected_epoch':session['epoch']})
        session,turn=wait_turn(turn['id']);assert turn['state']=='complete' and not turn['audio_ready'],turn
        report['cases'].append({'case':'real_text_translation_arabic','source':'Where is the train station?','reply':turn['reply'],'timings':turn['timings'],'quality':'Requires bilingual review; not counted as broad translation accuracy'})
        fixture_file=client.post(f"/api/projects/{project['id']}/files",files={'file':('voice-reference.txt',b'The calibration code for the cobalt instrument is ALFA-42. This controlled fixture is written for Riwaq evaluation.','text/plain')})
        fixture_file.raise_for_status();fid=fixture_file.json()['id']
        session=api('PATCH',prefix,json=opts|{'voice_id':None,'use_files':True})
        turn=api('POST',prefix+'/turns',json={'text':'What is the calibration code for the cobalt instrument? Cite the file.','turn_key':'live-source-001','expected_epoch':session['epoch']})
        session,turn=wait_turn(turn['id']);assert turn['state']=='complete' and 'ALFA-42' in turn['reply'] and '[S1]' in turn['reply'] and turn['citations'][0]['file_id']==fid,turn
        report['cases'].append({'case':'real_project_retrieval','reply':turn['reply'],'citation_file':turn['citations'][0]['filename'],'lines':[turn['citations'][0]['line_start'],turn['citations'][0]['line_end']],'semantic_review':'Exact calibration value and source checked against controlled fixture; not a broad citation benchmark'})
        api('DELETE',f"/api/projects/{project['id']}/files/{fid}")
        revoked=next(x for x in snapshot()['turns'] if x['id']==turn['id']);assert revoked['state']=='revoked' and not revoked['reply'] and not revoked['citations']
        report['cases'].append({'case':'source_deletion_revokes_reuse','state':revoked['state']})
        session=api('PATCH',prefix,json=opts|{'reply_language':'ar','voice_id':None})
        turn=api('POST',prefix+'/turns',json={'text':'أعطني عشرين فقرة عن قواعد البيانات.','turn_key':'live-cancellation-001','expected_epoch':session['epoch']})
        session=snapshot();started=time.monotonic()
        api('POST',prefix+'/interrupt',json={'expected_epoch':session['epoch'],'played_seconds':0.0,'completed':False})
        session,turn=wait_turn(turn['id'],10)
        report['cases'].append({'case':'real_provider_cancel','state':turn['state'],'server_stop_seconds':time.monotonic()-started,'scope':'Server resource release, not physical playback latency'})
        assert turn['state']=='cancelled'
        api('POST',prefix+'/end');assert client.get(prefix).status_code==404
        report['cases'].append({'case':'end_revokes_session','retrieval_status':404})
        session=None;report['passed']=True
    except BaseException as exc:
        report['error']=type(exc).__name__+': '+str(exc)[:1200];raise
    finally:
        if session and prefix:
            try:api('POST',prefix+'/end')
            except Exception:pass
        client.close();report['finished']=time.time()
        target=ROOT/'reports/voice-live.json'
        if target.exists():target.with_name('voice-live-prior-'+str(time.time_ns())+'.json').write_bytes(target.read_bytes())
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':report['passed'],'report':str(target)},ensure_ascii=False),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--url',default='http://127.0.0.1:8765');a=parser.parse_args();main(a.output,a.url)
