"""Real local generation, authenticated process restart, and synthetic retrieval."""
import argparse
import json
from scratch_smoke import ScratchService,chat,new_report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--knowledge',action='store_true');parser.add_argument('--report');args=parser.parse_args()
    path=new_report(args.report,'knowledge-offline-restart' if args.knowledge else 'offline-restart')
    result={'passed':False,'real_model':True,'provider':'ollama','model':'qwen3:4b-instruct','synthetic_database':True,
            'synthetic_public_knowledge':args.knowledge,'copied_user_database':False,'production_data_modified':False}
    try:
        with ScratchService(knowledge=args.knowledge) as service:
            project=service.call('POST','/api/projects',json={'name':'Synthetic offline restart validation'})['id']
            prompt='According to the synthetic fixture, what models define request bodies? Cite the source.' if args.knowledge else 'Reply with just the word LOCAL.'
            checked=chat(service,project,'Offline restart proof',prompt,knowledge=args.knowledge)
            result['generation']=checked
            if not checked['passed']:raise AssertionError('Live generation did not complete')
            if args.knowledge and not (checked['citations'] and 'Pydantic' in checked['response']):raise AssertionError('Synthetic reference answer did not match')
            proof=service.call('GET','/api/offline-proof')
            if not (proof['asyncio_guard'] and proof['loopback_attempts']>0 and proof['external_blocked']==['203.0.113.1']):raise AssertionError('Network guard proof did not match')
            before=service.call('GET','/api/conversations/'+checked['conversation_id'])
            service.restart()
            after=service.call('GET','/api/conversations/'+checked['conversation_id'])
            if before!=after:raise AssertionError('Stored conversation changed across restart')
            if args.knowledge:
                search=service.call('GET','/api/knowledge/search',params={'q':'Pydantic request bodies'})
                if not search['chunks']:raise AssertionError('Synthetic corpus was not restored')
            result.update(passed=True,server_restarted=True,conversation_equal_after_restart=True,authentication=service.auth_checks,
                          network_fence=proof,after_restart_fence=service.call('GET','/api/offline-proof'))
    except Exception as exc:result['error_type']=type(exc).__name__
    result['network_condition']='Instrumented Python socket/DNS and active asyncio APIs rejected the external probe. Separate Ollama is outside this guard; physical network was not unplugged.'
    result['knowledge_source']='Self-authored synthetic database fixture; no existing user corpus was copied or indexed.'
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':result['passed'],'report':str(path)},ensure_ascii=True),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
