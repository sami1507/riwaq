"""Live local smoke on a fresh authenticated scratch service, never port8765."""
import argparse
import json
import time
from scratch_smoke import ScratchService,chat,new_report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--report');args=parser.parse_args()
    path=new_report(args.report,'real-smoke')
    result={'started_at':time.strftime('%Y-%m-%dT%H:%M:%S'),'checks':[],'synthetic_database':True,
            'production_data_modified':False,'copied_user_database':False,'real_model':True,
            'provider':'ollama','model':'qwen3:4b-instruct','passed':False}
    try:
        with ScratchService() as service:
            project=service.call('POST','/api/projects',json={'name':'Synthetic local live validation','instructions':'Answer concisely. Preserve source facts.'})['id']
            result['checks'].append(chat(service,project,'Arabic comprehension','أجب بالعربية في جملتين: لماذا يساعد حفظ القرارات مع مصادرها على استئناف العمل؟'))
            result['checks'].append(chat(service,project,'Programming','Write a Python function add(a: int, b: int) -> int returning their sum. Only give code; do not execute it.'))
            fixture='Synthetic acceptance facts. The acceptance code is RIWAQ-427. The test budget is 730 shekels.'
            service.call('POST',f'/api/projects/{project}/files',files={'file':('synthetic-acceptance.txt',fixture.encode(),'text/plain')})
            answer=chat(service,project,'Grounded file answer','According to the source, state the acceptance code and test budget with citations.',files=True)
            answer['reference_check']=bool('730' in answer['response'] and '427' in answer['response'] and answer['citations'])
            answer['passed']=answer['passed'] and answer['reference_check'];result['checks'].append(answer)
            result['checks'].append(chat(service,project,'Cancellation','Write a very long essay of one thousand words about software design.',cancel=True))
            result['authentication']=service.auth_checks;result['network_fence']=service.call('GET','/api/offline-proof')
            result['passed']=all(item['passed'] for item in result['checks'])
    except Exception as exc:result['error_type']=type(exc).__name__
    result['limits']=['Functional smoke, not broad semantic quality certification.','Python network API guard, not OS isolation; separate Ollama is outside it.','No native UI test or production configuration change.']
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':result['passed'],'checks':len(result['checks']),'report':str(path)},ensure_ascii=True),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
