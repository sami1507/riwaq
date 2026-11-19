import json
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import httpx
import sqlite3
from contextlib import closing

ROOT=Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding='utf-8')
parser=argparse.ArgumentParser();parser.add_argument('--knowledge',action='store_true');parser.add_argument('--report');args=parser.parse_args()
with tempfile.TemporaryDirectory(prefix='riwaq-reasoning-offline-',dir='D:/tmp') as data:
    env=os.environ|{'RIWAQ_DATA_DIR':data,'PYTHONPATH':str(ROOT),'PYTHONUTF8':'1'}
    if args.knowledge:
        # A consistent temporary copy leaves all real projects and source evidence intact.
        with closing(sqlite3.connect((ROOT/'data/riwaq.sqlite3').as_uri()+'?mode=ro',uri=True)) as source:
            with closing(sqlite3.connect(Path(data)/'riwaq.sqlite3')) as destination:source.backup(destination)
        env['RIWAQ_EMBED_MODEL']='embeddinggemma-2:270m-q8_0-text'
    client=httpx.Client(base_url='http://127.0.0.1:18765',headers={'X-Riwaq-Client':'local'},timeout=200,trust_env=False)
    def start():
        p=subprocess.Popen([sys.executable,str(ROOT/'scripts/offline_server.py')],env=env,cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        for _ in range(100):
            if p.poll() is not None: raise RuntimeError(p.stderr.read().decode())
            try:
                if client.get('/api/projects').status_code==200:return p
            except httpx.HTTPError: pass
            time.sleep(.1)
        p.terminate();raise RuntimeError('Test server failed to start')
    proc=start()
    try:
        project=client.post('/api/projects',json={'name':'Offline isolated verification','instructions':'Answer briefly using the evidence.'}).json()
        conv=client.post('/api/conversations',json={'project_id':project['id'],'title':'Offline restart proof'}).json()
        prompt='وفق الوثيقة، ماذا يستخدم FastAPI لتعريف جسم الطلب؟ اذكر المصدر.' if args.knowledge else 'احسب التعبير المرفق، وأعد القيمة الدقيقة بجملة قصيرة.'
        r=client.post(f"/api/conversations/{conv['id']}/generate",json={'content':prompt,'use_knowledge':args.knowledge,'depth':'analytical','calculation':{'expression':'0.1+0.2'}});r.raise_for_status()
        last=json.loads(r.text.splitlines()[-1]);assert last['message']['status']=='complete'
        proof=client.get('/api/offline-proof').json()
        assert proof['asyncio_guard'] and proof['loopback_attempts']>0 and proof['external_blocked']==['203.0.113.1'],proof
        if args.knowledge:
            assert last['message']['citations'] and 'Pydantic' in last['message']['content']
        assert last['message']['run']['checks'][0]['status']=='pass'
        assert json.loads(last['message']['run']['checks'][0]['details'])['exact']=='3/10'
        before=client.get('/api/conversations/'+conv['id']).json()
        proc.terminate();proc.wait(timeout=10)
        proc=start()
        after=client.get('/api/conversations/'+conv['id']).json()
        assert before==after
        if args.knowledge:
            sources=client.get('/api/knowledge/search',params={'q':'FastAPI request body'});sources.raise_for_status();assert sources.json()['chunks']
        restarted_proof=client.get('/api/offline-proof').json()
        result={'passed':True,'server_restarted':True,'conversation_equal_after_restart':True,'real_model':True,'public_knowledge':args.knowledge,'response':last['message']['content'],'run_receipt':last['message']['run'],'citations':last['message']['citations'],'network_fence':proof,'after_restart_fence':restarted_proof,'network_condition':'Socket/DNS and active asyncio loop outbound APIs guarded; external probe was rejected by the guard, real loopback model calls observed. Separate Ollama process is outside the guard. Physical network not unplugged.'}
        (ROOT/args.report if args.report else ROOT/'reports'/('reasoning-knowledge-offline-restart.json' if args.knowledge else 'reasoning-offline-restart.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({key:value for key,value in result.items() if key!='citations'}))
    finally:
        proc.terminate();proc.wait(timeout=10);client.close()
