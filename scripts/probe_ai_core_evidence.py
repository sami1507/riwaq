"""Opt-in, fixed synthetic LOCAL evidence checks; no model download or cloud."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend import providers,research_answer
from backend.research_schema import ResearchOptions

async def main():
    if os.environ.get('RIWAQ_RUN_LOCAL_EVIDENCE_PROBE')!='1':raise SystemExit('Set RIWAQ_RUN_LOCAL_EVIDENCE_PROBE=1 for these fixed synthetic local probes.')
    output=Path(sys.argv[1])
    if output.exists():raise SystemExit('Choose a new report path; previous evidence is retained.')
    model='qwen3:4b-instruct'
    cfg={'context_size':8192,'max_tokens':512,'temperature':0,'seed':19,'local_fallback_models':[],'_runtime_scope':'synthetic-evidence-acceptance'}
    report={'provider':'ollama','model':model,'synthetic_only':True,'scope':'local research component with actual provider, not UI','claims_of_general_accuracy':False,'cases':[],
            'source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['backend/research_answer.py','backend/grounding.py','backend/output_contracts.py','backend/providers.py','backend/app.py']}}
    report['capability']=await providers.runtime_status(model,cfg,probe=False)
    if report['capability']['status']!='limited':raise SystemExit('Installed local provider unavailable; no alternate service was contacted.')
    chunk={'id':'synthetic-measured-v1','file_id':'synthetic-measured','text':'The measured amount is 20 units. This table excerpt has one observed row only.','title':'Synthetic measurement','version':'v1','published_at':'2026-11-15'}
    cases=[
        ('direct_supported_fact','What is the measured amount? Answer in English.','en',True),
        ('draft_200_corrected_to_source_20','A draft says the measured amount is 200 units. Check it against the source and state the supported measured amount. Answer in English.','en',True),
        ('arabic_supported_fact','ما القيمة المقاسة المذكورة في المصدر؟ أجب بالعربية.','ar',True),
        ('full_table_aggregate_withheld','Compute the total of all rows in the entire table.','en',False),
    ]
    for case_id,question,language,uses_model in cases:
        started=time.monotonic();receipt={};text='';checks=0
        async def allowed():
            nonlocal checks
            checks+=1
        try:
            async for packet in research_answer.stream(model,question,[dict(chunk)],ResearchOptions(scopes=['knowledge'],response_language=language),cfg|{'_deadline':time.monotonic()+45},receipt,allowed):text+=packet.get('content','')
            claims=receipt.get('claims',[])
            numeric={value for claim in claims for value in research_answer.numbers(claim['text'])}
            passed=(receipt.get('status')=='answered' and '20' in numeric and '200' not in numeric) if uses_model else (receipt.get('status')=='insufficient_evidence' and receipt.get('model_calls')==0)
            report['cases'].append({'id':case_id,'passed':passed,'model_calls':receipt.get('model_calls',0),'elapsed_seconds':round(time.monotonic()-started,3),'language':language,'response':text,'receipt':receipt,'authorization_checks':checks})
        except Exception as exc:
            report['cases'].append({'id':case_id,'passed':False,'elapsed_seconds':round(time.monotonic()-started,3),'error_type':type(exc).__name__,'error':'Local probe failed; no external fallback used.'})
    # One additional real authenticated API/SQLite conversation in its own data
    # directory. No ordinary user database is opened or reused.
    from fastapi.testclient import TestClient
    from backend import app as service
    from backend.local_auth import issue_pairing_code
    isolated=output.parent/(output.stem+'-data')
    if isolated.exists():raise SystemExit('Choose a new isolated live API data directory.')
    previous=os.environ.get('RIWAQ_DATA_DIR');os.environ['RIWAQ_DATA_DIR']=str(isolated.resolve())
    started=time.monotonic();synthetic_provider_output=[];original_stream=providers.stream
    async def captured_stream(*args,**kwargs):
        async for packet in original_stream(*args,**kwargs):
            if packet.get('content'):synthetic_provider_output.append(packet['content'])
            yield packet
    providers.stream=captured_stream
    try:
        with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as client:
            assert client.post('/api/auth/login',json={'pairing_code':issue_pairing_code()}).status_code==200
            project=client.get('/api/projects').json()[0]['id']
            chat=client.post('/api/conversations',json={'project_id':project}).json()['id']
            uploaded=client.post(f'/api/projects/{project}/files',files={'file':('synthetic-measured.txt',b'The measured amount is 20 units. Another shelf contains 200 items.','text/plain')})
            assert uploaded.status_code==201
            result=client.post(f'/api/conversations/{chat}/generate',json={'content':'A draft says the measured amount is 200 units. Verify the measured amount against the file and answer in English.','use_files':True})
            assert result.status_code==200
            final=[json.loads(line) for line in result.text.splitlines()][-1]['message']
            saved=client.get('/api/conversations/'+chat).json()['messages'][-1]
            checked=final.get('research',{})
            numeric={n for claim in checked.get('claims',[]) for n in research_answer.numbers(claim['text'])}
            passed=checked.get('status')=='answered' and checked.get('mode')=='ordinary_grounded_conversation' and '20' in numeric and '200' not in numeric and saved['content']==final['content']
            report['cases'].append({'id':'authenticated_ordinary_grounded_api_corrects_draft','passed':passed,'elapsed_seconds':round(time.monotonic()-started,3),'model_calls':checked.get('model_calls'),'scope':'actual local API, SQLite and Ollama; synthetic text-index file','response':final['content'],'receipt':checked,'synthetic_provider_output':''.join(synthetic_provider_output)})
            # Independent varied fact fixed before execution. Separate project
            # prevents the development measurement fixture affecting retrieval.
            synthetic_provider_output.clear();held_started=time.monotonic()
            held_project=client.post('/api/projects',json={'name':'Synthetic varied source acceptance'}).json()['id']
            held_chat=client.post('/api/conversations',json={'project_id':held_project}).json()['id']
            held_text=b'The maximum supported load is 17.5 kilograms. Packaging weighs 3 kilograms.'
            assert client.post(f'/api/projects/{held_project}/files',files={'file':('synthetic-load.txt',held_text,'text/plain')}).status_code==201
            result=client.post(f'/api/conversations/{held_chat}/generate',json={'content':'What is the maximum supported load according to the file? Include its unit and answer in English.','use_files':True})
            assert result.status_code==200
            final=[json.loads(line) for line in result.text.splitlines()][-1]['message'];checked=final.get('research',{})
            values={n for claim in checked.get('claims',[]) for n in research_answer.numbers(claim['text'])}
            saved=client.get('/api/conversations/'+held_chat).json()['messages'][-1]
            passed=checked.get('status')=='answered' and '17.5' in values and '3' not in values and 'kilogram' in final['content'].lower() and saved['content']==final['content']
            report['cases'].append({'id':'independent_varied_source_ordinary_api','passed':passed,'elapsed_seconds':round(time.monotonic()-held_started,3),'model_calls':checked.get('model_calls'),'scope':'actual local API, SQLite and Ollama; separate synthetic project and file; expected17.5 kilograms, not packaging3','response':final['content'],'receipt':checked,'synthetic_provider_output':''.join(synthetic_provider_output)})
    except Exception as exc:
        report['cases'].append({'id':'authenticated_ordinary_grounded_api_corrects_draft','passed':False,'elapsed_seconds':round(time.monotonic()-started,3),'error_type':type(exc).__name__,'error':'Isolated local API probe failed; no external fallback.'})
    finally:
        providers.stream=original_stream
        if previous is None:os.environ.pop('RIWAQ_DATA_DIR',None)
        else:os.environ['RIWAQ_DATA_DIR']=previous
    report['passed']=all(case['passed'] for case in report['cases'])
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'cases':[{'id':c['id'],'passed':c['passed'],'seconds':c['elapsed_seconds']} for c in report['cases']],'report':str(output)},ensure_ascii=False))
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(asyncio.run(main()))
