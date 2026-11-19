"""Repeat one controlled local speech fixture; reports sample p50/p95, not human UX."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time
import httpx

def percentile(values,p):
    values=sorted(values);position=(len(values)-1)*p;low=math.floor(position);high=math.ceil(position)
    return values[low]+(values[high]-values[low])*(position-low)

def main(fixture,n,base):
    raw=Path(fixture).read_bytes();report={'version':'voice-latency-v1','n':n,'fixture_sha256':hashlib.sha256(raw).hexdigest(),
      'scope':'Repeated identical synthetic English fixture, real local STT/LLM/TTS, service warm. No capture, playback or human-speech latency claim. Small-sample percentiles are descriptive only.',
      'targets_seconds':{'stt':10,'response_audio_ready':10,'stop_server':.5},'rows':[],'passed':False,'cost_paid':0}
    with httpx.Client(base_url=base,headers={'X-Riwaq-Client':'local'},trust_env=False,timeout=30) as c:
        def api(method,path,**kw):
            r=c.request(method,path,**kw);r.raise_for_status();return r.json()
        pid=api('POST','/api/projects',json={'name':'QA repeated voice latency','instructions':'Answer in one short sentence.'})['id']
        report['project_id']=pid;root=f'/api/projects/{pid}/voice';caps=api('GET',root+'/status');voice=caps['voices'][0]['id']
        def wait(path,tid):
            until=time.monotonic()+180
            while time.monotonic()<until:
                s=api('GET',path)
                if not s['active']:return s,next(t for t in s['turns'] if t['id']==tid)
                time.sleep(.05)
            raise TimeoutError('Voice operation')
        try:
            for i in range(n):
                s=api('POST',root+'/sessions',json={'input_language':'en','reply_language':'en-US','voice_id':voice,'mode':'conversation'})
                path=root+'/sessions/'+s['id'];c.headers['X-Riwaq-Voice-Token']=s['token']
                try:
                    t=api('POST',path+'/transcribe',data={'turn_key':f'latency-record-{i}','expected_epoch':'0','origin':'upload'},files={'file':('fixture.wav',raw,'audio/wav')})
                    s,t=wait(path,t['id']);assert t['state']=='ready_to_submit'
                    stt=t['timings']['transcription_seconds'];recognized=t['transcript']
                    t=api('POST',path+'/turns',json={'turn_id':t['id'],'text':t['transcript'],'turn_key':f'latency-review-{i}','expected_epoch':s['epoch']})
                    s,t=wait(path,t['id']);assert t['state']=='complete' and t['audio_ready'],t.get('error')
                    row={'sample':i+1,'stt':stt,'response_first_text':t['timings']['first_text_seconds'],'response_audio_ready':t['timings']['audio_ready_seconds'],'transcript':recognized,'reply':t['reply'],'audio_duration':t['audio_duration']}
                    t=api('POST',path+'/turns',json={'text':'List one hundred kinds of trees with descriptions.','turn_key':f'latency-stop-{i}','expected_epoch':s['epoch']})
                    s=api('GET',path);started=time.monotonic();api('POST',path+'/interrupt',json={'expected_epoch':s['epoch'],'played_seconds':0.0})
                    s,t=wait(path,t['id']);assert t['state']=='cancelled';row['stop_server']=time.monotonic()-started
                    report['rows'].append(row)
                finally:api('POST',path+'/end')
            report['percentiles']={key:{'p50':percentile([r[key] for r in report['rows']],.5),'p95':percentile([r[key] for r in report['rows']],.95)} for key in ('stt','response_first_text','response_audio_ready','stop_server')}
            report['targets_met']={k:report['percentiles'][k]['p95']<=v for k,v in report['targets_seconds'].items()}
            report['passed']=all(report['targets_met'].values())
        finally:
            target=Path(__file__).resolve().parents[1]/'reports/voice-latency.json';target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'passed':report['passed'],'samples':len(report['rows']),'report':str(target)}),flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--fixture',required=True);p.add_argument('--repetitions',type=int,default=5,choices=range(1,11));p.add_argument('--url',default='http://127.0.0.1:8765');a=p.parse_args();main(a.fixture,a.repetitions,a.url)
