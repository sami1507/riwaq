"""Fixed synthetic ordinary-chat repair checks against installed local Ollama.

Cases are frozen outside this script; every run uses a new isolated database.
The diagnostic case has no automatic semantic pass grade.
"""
import asyncio,hashlib,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend import providers,research_answer


def main():
    if os.environ.get('RIWAQ_RUN_LOCAL_EVIDENCE_PROBE')!='1':raise SystemExit('Explicit local synthetic probe opt-in required')
    fixture=Path(sys.argv[1]);output=Path(sys.argv[2])
    if output.exists():raise SystemExit('Choose a new report path')
    cases=json.loads(fixture.read_text(encoding='utf-8'))['cases']
    isolated=output.parent/(output.stem+'-data')
    if isolated.exists():raise SystemExit('Choose a fresh isolated data path')
    model='qwen3:4b-instruct';settings={'context_size':8192,'max_tokens':1024,'temperature':0,'seed':19,'local_fallback_models':[]}
    report={'fixture_sha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),'provider':'ollama','model':model,'settings':settings,'synthetic_only':True,'environment':'Windows local API/SQLite/Ollama; no native UI/cloud','cases':[],
      'source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['backend/research_answer.py','backend/grounding.py','backend/app.py','backend/providers.py']}}
    report['capability']=asyncio.run(providers.runtime_status(model,settings,probe=False))
    if report['capability']['status']!='limited':raise SystemExit('Required installed local provider unavailable')
    from fastapi.testclient import TestClient
    from backend import app as service
    from backend.local_auth import issue_pairing_code
    previous=os.environ.get('RIWAQ_DATA_DIR');os.environ['RIWAQ_DATA_DIR']=str(isolated.resolve())
    original_stream=providers.stream;observed=[]
    async def capture(*args,**kwargs):
        row={'raw':'','finish':None,'configured_output_tokens':args[3].get('max_tokens'),'metrics':{}}
        observed.append(row)
        async for packet in original_stream(*args,**kwargs):
            row['raw']+=packet.get('content','');row['finish']=packet.get('finish_reason',row['finish']);row['metrics'].update(packet.get('metrics',{}))
            yield packet
    providers.stream=capture
    try:
        with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as client:
            assert client.post('/api/auth/login',json={'pairing_code':issue_pairing_code()}).status_code==200
            assert client.patch('/api/settings',json=settings).status_code==200
            for case in cases:
                observed.clear();started=time.monotonic()
                p=client.post('/api/projects',json={'name':'Synthetic '+case['id']}).json()['id']
                chat=client.post('/api/conversations',json={'project_id':p}).json()['id']
                assert client.post(f'/api/projects/{p}/files',files={'file':(case['id']+'.txt',case['source'].encode(),'text/plain')}).status_code==201
                response=client.post(f'/api/conversations/{chat}/generate',json={'content':case['question'],'use_files':True})
                result={'id':case['id'],'split':case['split'],'elapsed_seconds':round(time.monotonic()-started,3),'http_status':response.status_code,'actual_provider_calls':len(observed),'synthetic_provider_attempts':list(observed)}
                if response.status_code==200:
                    packets=[json.loads(line) for line in response.text.splitlines()];final=packets[-1].get('message',{})
                    receipt=final.get('research',{});claims=receipt.get('claims',[])
                    saved=client.get('/api/conversations/'+chat).json()['messages'][-1]
                    result.update(response=final.get('content'),receipt=receipt,saved_matches=saved.get('content')==final.get('content'),model=final.get('model'))
                    if case.get('automated_quality') is False:result.update(passed=None,review_required=case['expected'])
                    else:
                        quantities={pair for claim in claims for pair in research_answer.numeric_units(claim['text'])}
                        result['passed']=bool(receipt.get('status')=='answered' and (case['expected_number'],case['expected_unit']) in quantities and case['forbidden_wrong_quantity'] not in final.get('content','') and result['saved_matches'])
                else:result.update(passed=False,error='API request did not complete')
                report['cases'].append(result)
    finally:
        providers.stream=original_stream
        if previous is None:os.environ.pop('RIWAQ_DATA_DIR',None)
        else:os.environ['RIWAQ_DATA_DIR']=previous
    checks=[case for case in report['cases'] if case.get('passed') is not None]
    report.update(passed=all(case['passed'] for case in checks),counts={'passed':sum(case['passed'] for case in checks),'checked':len(checks),'semantic_review_required':len(report['cases'])-len(checks)})
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'counts':report['counts'],'development':[{'id':c['id'],'passed':c['passed'],'calls':c['actual_provider_calls']} for c in report['cases'] if c['split']=='development'],'report':str(output)}))
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
