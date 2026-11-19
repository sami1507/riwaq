"""Execute a candidate EXE in disposable local data; never attach to user service."""
import argparse,hashlib,json,os,socket,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import httpx


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--exe',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--port',type=int,default=18777);args=parser.parse_args()
    exe=args.exe.resolve(strict=True);out=args.output.resolve();out.mkdir(parents=True,exist_ok=False);data=out/'data';data.mkdir()
    (data/'isolated-workers.json').write_bytes((ROOT/'data/isolated-workers.json').read_bytes())
    report={'scope':'Actual Windows packaged service and fixed workers; no native WebView journey, microphone or cloud','exe_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'cases':[]}
    def save(): (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    environment=os.environ|{'RIWAQ_DATA_DIR':str(data),'RIWAQ_SERVICE_PORT':str(args.port)}
    def check(name,kind,payload,predicate):
        started=time.monotonic()
        try:
            result=subprocess.run([str(exe),'--fixed-worker',kind],input=json.dumps(payload).encode(),capture_output=True,timeout=45,env=environment,creationflags=subprocess.CREATE_NO_WINDOW)
            value=json.loads(result.stdout);passed=predicate(value)
            row={'id':name,'passed':passed,'returncode':result.returncode,'result':value,'seconds':time.monotonic()-started}
        except Exception as exc:row={'id':name,'passed':False,'error':type(exc).__name__,'seconds':time.monotonic()-started}
        report['cases'].append(row);save();print(json.dumps({'id':name,'passed':row['passed']}),flush=True)
    check('exact_decimal','checker',{'tool':'calculate','input':'0.1+0.2'},lambda x:x['status']=='ok' and x['result']['exact']=='3/10')
    check('math_original_domain','checker',{'tool':'solve_math','input':'solve (x^2-1)/(x-1)=2'},lambda x:x['status']=='ok' and x['result']['problem_status']=='no_solution')
    check('math_unsafe_input','checker',{'tool':'solve_math','input':"evaluate __import__('os').system('x')"},lambda x:x['status']=='error' or x.get('result',{}).get('problem_status') in {'ambiguous_input','unresolved','invalid_input'})
    check('static_ast','ast',{'source':'def add(a,b):\n    return a+b\n'},lambda x:x['analysis_status']=='parsed_not_executed' and x['symbols'][0]['name']=='add')
    def voice_call(mode,payload,label):
        folder=out/label;folder.mkdir();request=folder/'input.json';request.write_text(json.dumps(payload),encoding='utf-8')
        result=subprocess.run([str(exe),'--fixed-worker','voice_tts','--mode',mode,'--input',str(request),'--output',str(folder)],capture_output=True,timeout=65,env=environment,creationflags=subprocess.CREATE_NO_WINDOW)
        path=folder/'voice-result.json'
        return result,json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {},folder
    try:
        result,discovery,_=voice_call('voices',{},'voices')
        voice=next(v for v in discovery.get('voices',[]) if v.get('local') and v.get('enabled') and v.get('language')=='en-US')
        result,spoken,folder=voice_call('tts',{'text':'Riwaq packaging verification. Zero remains a known value.','voice_id':voice['id'],'language':'en-US','rate':0,'volume':100},'speech')
        raw=(folder/'speech.wav').read_bytes()
        from backend.voice_audio import generated_wav_diagnostics
        diagnostics=generated_wav_diagnostics(raw)
        passed=(result.returncode==0 and spoken.get('success') is True and hashlib.sha256(raw).hexdigest()==spoken.get('audio',{}).get('sha256') and diagnostics['duration']>0 and diagnostics['peak']>.001)
        report['cases'].append({'id':'packaged_fixed_sapi_tts','passed':passed,'discovery':discovery,'speech':spoken,'diagnostics':diagnostics,'playback_performed':False});save()
        denied,_,folder=voice_call('stt',{},'denied-transcription')
        report['cases'].append({'id':'packaged_stt_dispatch_denied','passed':denied.returncode!=0 and not (folder/'voice-result.json').exists(),'returncode':denied.returncode});save()
    except Exception as exc:
        report['cases'].append({'id':'packaged_fixed_sapi_tts','passed':False,'error':type(exc).__name__,'detail':str(exc)[:600]});save()
    with socket.socket() as guard:guard.bind(('127.0.0.1',args.port))
    log=(out/'server.log').open('wb');process=None
    client=httpx.Client(base_url=f'http://127.0.0.1:{args.port}',headers={'X-Riwaq-Client':'local'},timeout=65,trust_env=False)
    previous=os.environ.get('RIWAQ_DATA_DIR');os.environ['RIWAQ_DATA_DIR']=str(data)
    def start():
        nonlocal process
        process=subprocess.Popen([str(exe)],env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
        for _ in range(250):
            if process.poll() is not None:raise RuntimeError('Candidate service exited during startup')
            try:
                r=client.get('/api/auth/status')
                if r.status_code==200:return
            except httpx.HTTPError:pass
            time.sleep(.1)
        raise RuntimeError('Candidate startup timeout')
    def stop():
        nonlocal process
        if process and process.poll() is None:
            import psutil
            owned=psutil.Process(process.pid);children=owned.children(recursive=True)
            for child in reversed(children):
                try:child.terminate()
                except psutil.NoSuchProcess:pass
            process.terminate();process.wait(timeout=15)
            psutil.wait_procs(children,timeout=10)
        process=None
    try:
        start()
        from backend.local_auth import issue_pairing_code
        assert client.get('/api/projects').status_code==401
        login=client.post('/api/auth/login',json={'pairing_code':issue_pairing_code()});assert login.status_code==200,login.text
        pid=client.get('/api/projects').json()[0]['id'];cid=client.post('/api/conversations',json={'project_id':pid}).json()['id']
        capabilities=client.get(f'/api/projects/{pid}/voice/capabilities')
        speech_caps=capabilities.json()
        report['cases'].append({'id':'packaged_voice_service_discovery','passed':capabilities.status_code==200 and speech_caps.get('tts',{}).get('available') is True and speech_caps.get('stt',{}).get('available') is False,'result':speech_caps});save()
        r=client.post(f'/api/conversations/{cid}/generate',json={'answer_mode':'problem','content':'solve sqrt(x+1)=x-1'})
        packets=[json.loads(x) for x in r.text.splitlines() if x];answer=packets[-1].get('message',{})
        math_checks=answer.get('run',{}).get('checks',[])
        math_result=json.loads(math_checks[0]['details']) if math_checks else {}
        math_passed=(answer.get('status')=='complete' and answer.get('model')=='riwaq-math-v1'
            and math_result.get('result',{}).get('solutions')==['3']
            and math_result.get('problem_status')=='solved'
            and any(v.get('check')=='substitute_original' and v.get('status')=='passed' for v in math_result.get('verification',[]))
            and 'x = 3' in answer.get('content','') and answer.get('run',{}).get('model_calls')==0)
        row={'id':'packaged_authenticated_math_chat','passed':math_passed,'packets':packets};report['cases'].append(row);save()
        doc=client.post(f'/api/projects/{pid}/documents',files={'file':('literal.txt',b'Packaged exact 731')},data={'languages':'eng'})
        if doc.status_code==202:
            fid=doc.json()['id'];deadline=time.monotonic()+35
            while time.monotonic()<deadline:
                saved=client.get(f'/api/projects/{pid}/documents/{fid}').json()
                if saved['status'] not in {'queued','running','cancelling'}:break
                time.sleep(.1)
            page=client.get(f'/api/projects/{pid}/documents/{fid}/pages/1').json()
            passed=saved['status']=='complete' and any('731' in e.get('text','') for e in page.get('elements',[]))
        else:passed=False;page=doc.json()
        report['cases'].append({'id':'packaged_document_container','passed':passed,'status':doc.status_code,'page':page});save()
        payload={'request':'Compute exact statistics','requestedSkills':['descriptive-statistics'],'inputs':{'dataOperation':{'operation':'stats','table':{'columns':['amount'],'rows':[[0],[10],[20],[None]]},'columns':['amount'],'format':'json'}},'idempotencyKey':'packaged-statistics-v1'}
        result=client.post(f'/api/projects/{pid}/skill-runs',json=payload)
        if result.status_code==202:
            task=result.json()['id'];deadline=time.monotonic()+50
            while time.monotonic()<deadline:
                saved=client.get(f'/api/projects/{pid}/skill-runs/{task}').json()
                if saved['status'] not in {'queued','running','cancelling'}:break
                time.sleep(.1)
            structured=saved.get('result',{}).get('structured',{}).get('descriptive-statistics',{})
            values=structured.get('output',{}).get('summary',{}).get('statistics',{}).get('amount',{})
            receipt=structured.get('execution_receipt',{})
            passed=(saved['status']=='completed' and values.get('sum')=='30' and values.get('mean')=='10'
                and values.get('numeric_count')==3 and values.get('missing')==1
                and receipt.get('cleanup_verified') is True and receipt.get('host_fallback') is False
                and receipt.get('executed') is True)
            result_data=saved
        else:passed=False;result_data=result.json()
        report['cases'].append({'id':'packaged_data_container','passed':passed,'status':result.status_code,'result':result_data});save()
        before=client.get(f'/api/conversations/{cid}').json();stop();start()
        # Valid persisted sessions may survive a restart; an anonymous client still cannot read.
        assert httpx.get(f'http://127.0.0.1:{args.port}/api/projects',headers={'X-Riwaq-Client':'local'},trust_env=False).status_code==401
        assert client.post('/api/auth/login',json={'pairing_code':issue_pairing_code()}).status_code==200
        after=client.get(f'/api/conversations/{cid}').json()
        report['cases'].append({'id':'packaged_restart_storage','passed':before==after,'session_reauthenticated':True});save()
    except Exception as exc:
        report['cases'].append({'id':'packaged_service_path','passed':False,'error':type(exc).__name__,'detail':str(exc)[:1200]});save()
    finally:
        stop();client.close();log.close()
        if previous is None:os.environ.pop('RIWAQ_DATA_DIR',None)
        else:os.environ['RIWAQ_DATA_DIR']=previous
    report['passed']=all(row['passed'] for row in report['cases']);save();print(json.dumps({'passed':report['passed'],'cases':len(report['cases'])}))
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
