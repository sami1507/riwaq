"""Reproducible reviewed pilot registry; does not import or train anything."""
import hashlib
import json
import argparse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description='Historical pilot seed; use the checked-in reviewed registry for normal startup.')
parser.add_argument('--replace-registry',action='store_true',help='Explicitly replace the current editable registry with the original pilot candidates')
args=parser.parse_args()
if (ROOT/'knowledge/sources.json').exists() and not args.replace_registry:
    raise SystemExit('Existing registry preserved. Normal startup reads it; regeneration requires --replace-registry.')
stamp='2026-11-12'
raw='https://raw.githubusercontent.com/'
def source(id,name,base,hosts,paths,license_name,license_url,evidence_name,owner,version,urls,**extra):
    evidence=ROOT/'reports/source-verification'/f'{evidence_name}.txt'
    return {'id':id,'name':name,'base_url':base,'allowed_hosts':hosts,'allowed_prefixes':paths,'allowed_paths':paths,
        'status':'allowed','rights_confirmed':True,'pack':'project-stack','domain':'programming','language':'en',
        'content_type':'markdown','source_type':'documentation','access_method':'official repository','access_type':'official repository',
        'version':version,'owner':owner,'priority':10,'max_documents':30,'update_policy':{'interval_days':30},
        'fetch_limits':{'delay_seconds':1.0,'max_documents':30},
        'license':{'name':license_name,'url':license_url,'storage_allowed':True,'redistribution_allowed':True,'training_allowed':False,
            'attribution':owner+'; original URL and license retained. Extraction/chunking only; no generated translation.',
            'reviewed_at':stamp,'evidence':f'Official license verified HTTP 200 on {stamp}; snapshot reports/source-verification/{evidence_name}.txt',
            'sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()},
        'urls':[{'url':u,'title':t,'version':v or version,'language':lang or 'en'} for u,t,v,lang in urls],**extra}

sources=[source('python312','Python 3.12 documentation','https://docs.python.org/3.12/', ['docs.python.org'],['/3.12/'],
    'PSF-2.0; documentation examples also 0BSD','https://docs.python.org/3.12/license.html','python_license','Python Software Foundation','3.12',
    [('https://docs.python.org/3.12/tutorial/'+p+'.html',t,None,None) for p,t in [('controlflow','Python 3.12 — Control flow'),('datastructures','Python 3.12 — Data structures')]],content_type='html',access_method='official HTML documentation'),
source('react19','React documentation','https://react.dev/',['raw.githubusercontent.com'],['/reactjs/react.dev/main/'],
    'CC-BY-4.0',raw+'reactjs/react.dev/main/LICENSE-DOCS.md','react_license','Meta Platforms and React documentation contributors','19 (rolling docs snapshot)',
    [(raw+'reactjs/react.dev/main/src/content/reference/react/'+p+'.md','React — '+p,None,None) for p in ['useState','useEffect']]),
source('typescript59','TypeScript documentation','https://www.typescriptlang.org/',['raw.githubusercontent.com'],['/microsoft/TypeScript-Website/v2/'],
    'CC-BY-4.0',raw+'microsoft/TypeScript-Website/v2/LICENSE','typescript_license','Microsoft and TypeScript documentation contributors','handbook (rolling snapshot)',
    [(raw+'microsoft/TypeScript-Website/v2/packages/documentation/copy/en/handbook-v2/'+p+'.md','TypeScript — '+t,None,None) for p,t in [('Narrowing','Narrowing'),('Everyday Types','Everyday types')]]+
    [(raw+'microsoft/TypeScript-Website/v2/packages/documentation/copy/en/release-notes/TypeScript 5.9.md','TypeScript 5.9 release notes','5.9',None)]),
source('fastapi0162','FastAPI 0.162.5 documentation','https://fastapi.tiangolo.com/',['raw.githubusercontent.com'],['/fastapi/fastapi/0.162.5/'],
    'MIT',raw+'fastapi/fastapi/0.162.5/LICENSE','fastapi_license','Sebastián Ramírez and FastAPI contributors','0.162.5',
    [(raw+'fastapi/fastapi/0.162.5/docs/en/docs/tutorial/'+p+'.md','FastAPI — '+t,None,None) for p,t in [('body','Request body'),('path-params','Path parameters'),('query-params','Query parameters'),('dependencies/index','Dependencies')]]+
    [(raw+'fastapi/fastapi/0.162.5/docs_src/body/tutorial001.py','FastAPI request-body code example',None,None)]),
source('wikidata-general','Wikidata structured descriptions','https://www.wikidata.org/',['www.wikidata.org'],['/w/api.php','/wiki/Wikidata:Licensing'],
    'CC0-1.0 (structured entity data only)','https://www.wikidata.org/wiki/Wikidata:Licensing','wikidata_license','Wikidata contributors','live entity snapshot',
    [('https://www.wikidata.org/w/api.php?action=wbgetentities&ids='+qid+'&props=labels%7Cdescriptions&languages=en%7Car%7Che&format=json&maxlag=5','Wikidata '+qid+' — '+title,None,'mul') for qid,title in [('Q2','Earth'),('Q405','Moon'),('Q525','Sun'),('Q111','Mars'),('Q283','Water'),('Q629','Oxygen'),('Q395','Mathematics'),('Q413','Physics'),('Q13955','Arabic'),('Q9288','Hebrew')]],
    pack='general-starter',domain='general',language='mul',content_type='wikidata-json',access_method='MediaWiki Action API',priority=20)]

for item in sources:
    for doc in item['urls']: doc['url']=doc['url'].replace(' ','%20')
for id,name,url,pack,domain in [
    ('mdn','MDN Web Docs','https://developer.mozilla.org/','programming','web'),
    ('android','Android Developers','https://developer.android.com/','programming','android'),
    ('kotlin','Kotlin documentation','https://kotlinlang.org/docs/','programming','kotlin'),
    ('java','OpenJDK','https://openjdk.org/','programming','java'),
    ('cpp','C/C++ references','https://en.cppreference.com/','programming','cpp'),
    ('postgresql','PostgreSQL documentation','https://www.postgresql.org/docs/','programming','sql'),
    ('git','Git documentation','https://git-scm.com/docs','programming','git'),
    ('linux','Linux kernel documentation','https://docs.kernel.org/','programming','linux'),
    ('pytorch','PyTorch documentation','https://docs.pytorch.org/','ai','machine-learning'),
    ('owasp','OWASP prompt injection guidance','https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html','ai','security'),
    ('wikimedia','Wikimedia content','https://www.wikimedia.org/','general','encyclopedia'),
    ('openstax','OpenStax books','https://openstax.org/','open-references','education'),
]:
    sources.append({'id':id,'name':name,'base_url':url,'allowed_hosts':[],'allowed_prefixes':[], 'urls':[],
        'pack':pack,'domain':domain,'language':'mul','status':'candidate','priority':50,'rights_confirmed':False,
        'license':{'name':'Unverified per material','url':'','storage_allowed':False,'redistribution_allowed':False,'training_allowed':False,
            'attribution':'Pending material-specific review','reviewed_at':None,'evidence':'Discovery only; no content imported.'}})
verification=json.loads((ROOT/'reports/knowledge-source-verification.json').read_text(encoding='utf-8'))
manifest=Path('D:/RiwaqRuntime/models/manifests/registry.ollama.ai/library/embeddinggemma-2/270m-q8_0-text')
model=json.loads(manifest.read_text())
model_bytes=model['config']['size']+sum(x['size'] for x in model['layers'])+manifest.stat().st_size
registry={'version':1,'reviewed_at':stamp,'limits':{'max_documents':100,'max_sources':5,'download_bytes':1073741824,'file_bytes':26214400,'run_seconds':1800,'concurrent_domains':1},
    'embedding':{'model':'embeddinggemma-2:270m-q8_0-text','dimensions':768,'license':'Apache-2.0','model_card':'https://ai.google.dev/gemma/docs/embeddinggemma/model_card_2'},
    'external_downloads':[{'id':'embeddinggemma2-text-pilot','name':'EmbeddingGemma 2 text weights and manifest','bytes':model_bytes},
        {'id':'source-verification-pilot','name':'Official licenses and verification samples','bytes':verification['total_download_bytes']}],
    'sources':sources}
(ROOT/'knowledge').mkdir(exist_ok=True)
(ROOT/'knowledge/sources.json').write_text(json.dumps(registry,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'sources':len(sources),'allowed':5,'pilot_documents':sum(len(x['urls']) for x in sources),'external_bytes':sum(x['bytes'] for x in registry['external_downloads'])}))
