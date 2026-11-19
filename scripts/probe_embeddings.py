"""Actual local multilingual embedding smoke, distinct from the held-out RAG evaluation."""
import json
import math
import time
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]
model='embeddinggemma-2:270m-q8_0-text'
docs=['Python list append adds one item to the end of a list.','Earth is the third planet from the Sun.','React useState returns current state and a setter function.','Oxygen is a chemical element.']
cases=[('ar','ما ترتيب الأرض بالنسبة للشمس؟',1),('en','How do I append an element to a Python list?',0),('he','מהו כוכב הלכת השלישי מהשמש?',1)]
with httpx.Client(base_url='http://127.0.0.1:11434',timeout=120,trust_env=False) as c:
    info=c.post('/api/show',json={'model':model}); info.raise_for_status()
    tags=c.get('/api/tags').json()
    selected=next(x for x in tags['models'] if x['name']==model)
    started=time.perf_counter()
    r=c.post('/api/embed',json={'model':model,'input':['title: none | text: '+x for x in docs]+['task: search result | query: '+x[1] for x in cases], 'truncate':False})
    r.raise_for_status(); vectors=r.json()['embeddings']
    def cosine(a,b):return sum(x*y for x,y in zip(a,b))/math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))
    result={'model':model,'digest':selected['digest'],'size':selected['size'],'dimensions':len(vectors[0]),'capabilities':info.json().get('capabilities'), 'elapsed_seconds':round(time.perf_counter()-started,3),'checks':[]}
    for (lang,q,wanted),v in zip(cases,vectors[len(docs):]):
        scores=[cosine(v,d) for d in vectors[:len(docs)]];actual=max(range(len(scores)),key=scores.__getitem__)
        result['checks'].append({'language':lang,'query':q,'expected':wanted,'retrieved':actual,'passed':wanted==actual,'scores':scores})
    result['passed']=all(x['passed'] for x in result['checks'])
    (ROOT/'reports/embedding-probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))
