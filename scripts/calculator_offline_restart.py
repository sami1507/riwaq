"""Real fixed-worker/API/restart proof on an isolated database, without a model."""
import json,os,subprocess,sys,tempfile,time,uuid
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='riwaq-calculator-',dir='D:/tmp') as data:
    env=os.environ|{'RIWAQ_DATA_DIR':data,'PYTHONPATH':str(ROOT),'PYTHONUTF8':'1','RIWAQ_TEST_PORT':'18766'}
    client=httpx.Client(base_url='http://127.0.0.1:18766/api',headers={'X-Riwaq-Client':'local'},timeout=20,trust_env=False)
    def call(method,path,body=None):
        r=client.request(method,path,json=body);r.raise_for_status();return r.json()
    def start():
        env['RIWAQ_CALCULATOR_TEST_RUN']=uuid.uuid4().hex
        p=subprocess.Popen([sys.executable,str(ROOT/'scripts/calculator_offline_server.py')],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(100):
            if p.poll() is not None:raise RuntimeError(p.stderr.read().decode('utf-8',errors='replace'))
            try:
                if client.get('/projects').status_code==200:
                    if call('GET','/calculator-offline-proof')['instance']!=env['RIWAQ_CALCULATOR_TEST_RUN']:
                        p.terminate();p.wait(timeout=10);raise RuntimeError('Test port belongs to another server; no test writes were sent')
                    return p
            except httpx.HTTPError:pass
            time.sleep(.1)
        p.terminate();p.wait(timeout=10);raise RuntimeError('Test server startup timed out')
    proc=start()
    try:
        # Missing cloud selection in an isolated database must not block local arithmetic.
        call('PATCH','/settings',{'mode':'hybrid','provider':'anthropic','model':'unavailable-audit-model'})
        project=call('POST','/projects',{'name':'Calculator offline proof'})
        conv=call('POST','/conversations',{'project_id':project['id'],'title':'Explicit arithmetic'})
        cases=[]
        for expression,expected in [('(19.95*3)*1.17-10','120049/2000'),('0.1+0.2','3/10'),('1/3','1/3'),('\u0661\u0662\u066b\u0665 \u00d7 \u0662','25')]:
            started=time.monotonic()
            response=client.post('/conversations/'+conv['id']+'/generate',json={'content':expression,'answer_mode':'calculator','result_language':'ar'})
            response.raise_for_status();events=[json.loads(line) for line in response.text.splitlines()]
            msg=events[-1]['message'];run=msg['run'];result=json.loads(run['checks'][0]['details'])
            assert msg['status']=='complete' and result['exact']==expected
            assert run['model_calls']==0 and run['tool_calls']==1 and result['process_limited']
            assert msg['model']=='riwaq-calculator-v1' and not msg['citations']
            cases.append({'expression':expression,'result':result,'content':msg['content'],'seconds':round(time.monotonic()-started,4)})
        before=call('GET','/conversations/'+conv['id'])
        bad=client.post('/conversations/'+conv['id']+'/generate',json={'answer_mode':'calculator','content':'1/0'})
        assert bad.status_code==422 and call('GET','/conversations/'+conv['id'])==before
        fence=call('GET','/calculator-offline-proof');assert fence['blocked_attempts']==[]
        proc.terminate();proc.wait(timeout=10);proc.stderr.close()
        proc=start()
        after=call('GET','/conversations/'+conv['id']);assert before==after
        second=call('GET','/calculator-offline-proof');assert second['blocked_attempts']==[]
        report={'passed':True,'scope':'Real HTTP, fixed arithmetic worker, SQLite and server restart. No model generation; no simulated calculator answers.','cases':cases,'invalid_expression_status':bad.status_code,'invalid_expression_saved':False,'conversation_equal_after_restart':True,'selected_provider':'anthropic/unavailable-audit-model (temporary test settings only)','network':fence,'network_after_restart':second}
        (ROOT/'reports/calculator-offline.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':True,'cases':len(cases),'model_calls':0,'outbound_attempts':0,'restored':True}))
    finally:
        proc.terminate();proc.wait(timeout=10);proc.stderr.close();client.close()
