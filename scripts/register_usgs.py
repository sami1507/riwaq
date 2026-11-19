"""Register one reviewed public-domain text page after Wikidata robots denial.

Does not fetch source content. The authorized ingestion job verifies robots and
downloads the complete rights evidence before it activates any document.
"""
import json
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]
path=ROOT/'knowledge/sources.json'
registry=json.loads(path.read_text(encoding='utf-8'))
source={'id':'usgs-water','name':'USGS Water Science School — water facts','base_url':'https://www.usgs.gov/water-science-school/','allowed_hosts':['www.usgs.gov'],
    'allowed_prefixes':['/water-science-school/science/facts-about-water'],'status':'allowed','rights_confirmed':True,
    'pack':'general-starter','domain':'general','language':'en','content_type':'html','access_method':'official institutional HTML text; no media fetched',
    'version':'page-date 2026-08-24','owner':'U.S. Geological Survey — Water Science School','priority':20,'update_policy':{'interval_days':90},
    'fetch_limits':{'delay_seconds':2,'max_documents':1},'training_allowed':False,
    'license':{'name':'U.S. Public Domain — USGS-authored text only','url':'https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits',
        'storage_allowed':True,'redistribution_allowed':True,'training_allowed':False,
        'attribution':'U.S. Geological Survey, Water Science School: Facts About Water; original URL and rights statement retained. Text extraction/chunking only. No endorsement implied. No linked third-party media included.',
        'reviewed_at':'2026-11-12','evidence':'Official Copyrights and Credits policy explicitly covers USGS-authored data/information. Facts About Water credits Water Science School; displayed media is Public Domain but no media is downloaded. robots.txt permits this article path. Policy reviewed via official indexed text; importer must fetch policy successfully before activation.'},
    'urls':[{'url':'https://www.usgs.gov/water-science-school/science/facts-about-water','title':'USGS — Facts About Water','language':'en','version':'page-date 2026-08-24','published_at':None,'modified_at':None}]}
for entry in registry['sources']:
    if entry['id']=='wikidata-general':
        entry['status']='link_only'
        entry['access_note']='API and Special:EntityData paths disallowed by robots.txt for this importer. Ten attempted URLs were not imported. No override performed.'
registry['sources']=[s for s in registry['sources'] if s['id']!='usgs-water']+[source]
path.write_text(json.dumps(registry,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
with httpx.Client(base_url='http://127.0.0.1:8765/api/knowledge',headers={'X-Riwaq-Client':'local'},trust_env=False,timeout=30) as client:
    response=client.patch('/sources/wikidata-general',json={'status':'link_only'});response.raise_for_status()
    current=client.get('/sources').json()
    response=client.patch('/sources/usgs-water',json=source) if any(s['id']=='usgs-water' for s in current) else client.post('/sources',json=source)
    response.raise_for_status();print(json.dumps({'registered':response.json()['id'],'status':response.json()['status'],'wikidata':'link_only'}))
