import base64,io,json,struct,threading,zlib
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from PIL import Image,PngImagePlugin
from backend import visuals as v,visual_jobs,providers
from backend.db import get_db,init_db,now,uid
from visual_auth_fixture import trusted_visual_session,synthetic_visual_decoder
from backend.visual_authority import worker_scope,actor

@pytest.fixture(autouse=True)
def worker_simulation(monkeypatch):
 synthetic_visual_decoder(monkeypatch)

@pytest.fixture
def database(tmp_path,monkeypatch):
 monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));init_db()
 with get_db() as c:
  visual_jobs.init_jobs(c)
  for pid in ('p1','p2'):c.execute('INSERT INTO projects VALUES(?,?,?,?)',(pid,pid,'',now()))
 with trusted_visual_session():yield

@pytest.fixture
def client(database):
 app=FastAPI();app.include_router(v.router);app.include_router(visual_jobs.router)
 with TestClient(app) as client:yield client

def png(metadata=False):
 out=io.BytesIO();image=Image.new('RGBA',(4,3),(100,20,30,96));options={}
 if metadata:
  info=PngImagePlugin.PngInfo();info.add_text('private-location','PRIVATE METADATA');exif=Image.Exif();exif[274]=6;options={'pnginfo':info,'exif':exif}
 image.save(out,format='PNG',**options);return out.getvalue()
def spec(text='اَلْعَرَبِيَّة 👩🏽\u200d💻\r\ncafé / cafe\u0301 / ١٢٣\tfin',asset_id=None):
 return {'width':512,'height':512,'background':'#ffffff','background_asset_id':asset_id,'layers':[{'id':'text1','text':text,'language':'ar','direction':'rtl','font':'arabic','size':24,'weight':400,'x':10,'y':10,'width':490,'height':250,'color':'#112233','align':'right','protected':True}],'shapes':[]}
def create(client,s=None,pid='p1'):
 r=client.post(f'/api/projects/{pid}/visual/designs',json={'title':'Literal source','spec':s or spec()});assert r.status_code==201,r.text;return r.json()
def upload(client,pid='p1',raw=None,name='image.png'):
 r=client.post(f'/api/projects/{pid}/visual/assets',files={'file':(name,raw or png(),'image/png')});assert r.status_code==201,r.text;return r.json()
def patch(client,item,s,**extra):return client.patch('/api/projects/p1/visual/designs/'+item['id'],json={'title':item['title'],'spec':s,'expected_version':item['version'],**extra})

def test_project_isolation_and_asset_binding(client):
 asset=upload(client);item=create(client,spec(asset_id=asset['id']))
 for suffix in ('','/content'):assert client.get(f"/api/projects/p2/visual/assets/{asset['id']}"+suffix).status_code==404
 for suffix in ('','/versions','/bundle'):assert client.get(f"/api/projects/p2/visual/designs/{item['id']}"+suffix).status_code==404
 assert client.delete(f"/api/projects/p2/visual/assets/{asset['id']}?confirm=true").status_code==404
 assert client.patch(f"/api/projects/p2/visual/designs/{item['id']}",json={'title':'x','spec':spec(),'expected_version':1}).status_code==404
 assert client.post('/api/projects/p2/visual/designs',json={'title':'foreign','spec':spec(asset_id=asset['id'])}).status_code==404
 assert client.get('/api/projects/p2/visual/designs').json()==[] and client.get('/api/projects/p2/visual/assets').json()['items']==[]
 assert client.post('/api/projects/p1/visual/assets',files={'file':('../secret.png',png(),'image/png')}).status_code==400

def test_literal_text_history_protection_and_stale_updates(client):
 s=spec();exact=s['layers'][0]['text'];item=create(client,s);path='/api/projects/p1/visual/designs/'+item['id']
 source=item['provenance']['text_sources'][0];assert source['text']==exact and source['utf8_sha256']==v.digest(exact.encode())
 moved=json.loads(json.dumps(s));moved['layers'][0]['x']=11
 response=patch(client,item,moved);assert response.status_code==200;second=response.json()
 assert second['spec']['layers'][0]['text']==exact and second['provenance']['text_sources']==item['provenance']['text_sources']
 assert patch(client,item,moved).status_code==409
 changed=json.loads(json.dumps(moved));changed['layers'][0]['text']=exact.replace('\r\n','\n')
 assert patch(client,second,changed).status_code==409
 assert patch(client,second,changed,confirm_text_changes='true').status_code==422
 response=patch(client,second,changed,confirm_text_changes=True);assert response.status_code==200 and response.json()['version']==3
 history=client.get(path+'/versions').json();assert [x['version'] for x in history]==[3,2,1] and history[-1]['spec']['layers'][0]['text']==exact
 restored=client.post(path+'/restore',json={'expected_version':3,'from_version':1});assert restored.status_code==200
 assert restored.json()['version']==4 and restored.json()['spec']['layers'][0]['text']==exact
 assert client.get(path).json()['provenance']['text_sources'][0]['utf8_sha256']==source['utf8_sha256']

def test_spec_rejects_geometry_controls_and_forged_provenance():
 mutations=[lambda s:s.update(width=True),lambda s:s.update(width=2048,height=2048),lambda s:s['layers'][0].update(x=-1),lambda s:s['layers'][0].update(width=503),lambda s:s['layers'][0].update(size=float('nan')),lambda s:s['layers'][0].update(protected=False),lambda s:s['layers'][0].update(text='x'*4001),lambda s:s['layers'][0].update(text='a\u202eb'),lambda s:s['layers'][0].update(text='\ud800'),lambda s:s.update(provenance={'verified':True}),lambda s:s.update(layers=[s['layers'][0]]*17),lambda s:s.update(shapes=[{'id':'text1','kind':'rect','x':0,'y':0,'width':10,'height':10,'fill':'#000000'}])]
 for change in mutations:
  value=spec();change(value)
  with pytest.raises(ValidationError):v.DesignSpec.model_validate(value)

def test_image_orientation_alpha_metadata_and_storage(client):
 original=png(True);item=upload(client,raw=original,name='misleading.jpg')
 assert item['mime']=='image/png' and (item['width'],item['height'])==(3,4)
 assert item['origin']['original_sha256']==v.digest(original) and item['origin']['kind']=='upload'
 response=client.get(f"/api/projects/p1/visual/assets/{item['id']}/content")
 assert response.headers['content-type']=='image/png' and response.headers['cache-control']=='private, no-store'
 assert v.digest(response.content)==item['sha256'] and len(response.content)==item['byte_size']
 with Image.open(io.BytesIO(response.content)) as image:assert not image.getexif() and 'private-location' not in image.info and image.getpixel((0,0))[3]==96

def test_malformed_bomb_animation_and_byte_limits(monkeypatch):
 def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
 bomb=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2048,2048,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b''))+chunk(b'IEND',b'')
 animated=io.BytesIO();Image.new('RGB',(3,3),'red').save(animated,format='PNG',save_all=True,append_images=[Image.new('RGB',(3,3),'blue')],duration=100,loop=0)
 for raw,expected in [(bomb,413),(animated.getvalue(),415),(b'<svg onload="evil()"/>',422),(b'broken',422)]:
  with pytest.raises(HTTPException) as error:v.normalize_image(raw,authorize=lambda:True)
  assert error.value.status_code==expected
 monkeypatch.setattr(v,'MAX_ASSET',16)
 with pytest.raises(HTTPException) as error:v.normalize_image(b'x'*17,authorize=lambda:True)
 assert error.value.status_code==413

def test_bundle_hashes_and_referenced_asset_retention(client):
 image=upload(client);item=create(client,spec(asset_id=image['id']));path='/api/projects/p1/visual/designs/'+item['id']
 response=patch(client,item,spec());assert response.status_code==200 and response.json()['version']==2
 assert client.delete(f"/api/projects/p1/visual/assets/{image['id']}?confirm=true").status_code==409
 bundle=client.get(path+'/bundle').json();assert bundle['format']=='riwaq-visual-bundle' and len(bundle['versions'])==2
 assert len(bundle['assets'])==1;stored=bundle['assets'][0];assert v.digest(base64.b64decode(stored['data_base64'],validate=True))==stored['sha256']
 for revision in bundle['versions']:
  assert v.digest(v.packed(revision['spec']).encode())==revision['provenance']['spec_sha256']
  for source in revision['provenance']['text_sources']:assert v.digest(source['text'].encode())==source['utf8_sha256']
 assert client.delete(path+'?expected_version=1&confirm=true').status_code==409
 assert client.delete(path+'?expected_version=2').status_code==422
 result=client.delete(path+'?expected_version=2&confirm=true').json();assert result['history_deleted'] and result['assets_retained']
 assert client.delete(f"/api/projects/p1/visual/assets/{image['id']}?confirm=true").json()['deleted']
 with get_db() as c:assert v.used_bytes(c,'p1')==0

@pytest.mark.parametrize('quota,second_pid',[('PROJECT_BYTES','p1'),('GLOBAL_BYTES','p2')])
def test_concurrent_quota_admission_is_atomic(database,monkeypatch,quota,second_pid):
 data,meta=v.normalize_image(png(),authorize=lambda:True);limit=2*len(data)-1;monkeypatch.setattr(v,quota,limit);gate=threading.Barrier(2)
 acting=actor()
 def save(pid):
  gate.wait(timeout=5)
  try:
   with worker_scope(acting),get_db() as c:c.execute('BEGIN IMMEDIATE');v.store_asset(c,pid,data,meta,'race.png')
   return 201
  except HTTPException as error:return error.status_code
 with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(save,['p1',second_pid]))
 assert sorted(results)==[201,413]
 with get_db() as c:assert v.used_bytes(c)<=limit and c.execute('SELECT COUNT(*) FROM visual_assets').fetchone()[0]==1

def test_decoder_slot_is_held_until_worker_returns(monkeypatch):
 entered,release=threading.Event(),threading.Event();original=v._normalize_image
 def blocked(raw,**kwargs):
  entered.set()
  if not release.wait(timeout=5):raise RuntimeError('Test worker not released')
  return original(raw,**kwargs)
 monkeypatch.setattr(v,'_normalize_image',blocked)
 with ThreadPoolExecutor(max_workers=1) as pool:
  pending=pool.submit(v.normalize_image,png(),authorize=lambda:True)
  try:
   assert entered.wait(timeout=5)
   with pytest.raises(HTTPException) as error:v.normalize_image(png(),authorize=lambda:True)
   assert error.value.status_code==409
  finally:release.set()
  assert pending.result(timeout=5)[1]['width']==4
 assert v.normalize_image(png(),authorize=lambda:True)[1]['width']==4

def test_saving_brief_is_not_generation(client,monkeypatch):
 async def forbidden(*args,**kwargs):raise AssertionError('Storage must not call a model')
 monkeypatch.setattr(providers,'validate_provider',forbidden);monkeypatch.setattr(providers,'stream',forbidden)
 s=spec();s['brief']={'task':'image','visual_prompt':'A mountain landscape'};item=create(client,s)
 assert item['provenance']['operation']=='user_saved' and 'pixels and OCR not certified' in item['provenance']['text_assurance']
 assert 'run' not in item and 'model' not in item and client.get('/api/projects/p1/visual/assets').json()['items']==[]

def add_job(c,pid,did,aid,status,secret):
 jid=uid();stamp=now();report={'layers':[{'source':secret,'lines':[secret]}]}
 c.execute('INSERT INTO visual_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(jid,pid,'render',status,did,1,v.packed({'prompt':secret}),'{}',aid,v.packed(report),None,stamp,stamp))
 return jid,report

@pytest.mark.parametrize('terminal',['completed','failed','cancelled','interrupted'])
def test_design_deletion_purges_derived_text_and_preserves_other_owners(client,terminal):
 secret='DELETE_DESIGN_PRIVATE_LITERAL';item=create(client,spec(secret));other=create(client,spec('OTHER_DESIGN_TEXT'));image=upload(client);foreign=upload(client,'p2')
 with get_db() as c:
  jid,report=add_job(c,'p1',item['id'],image['id'],terminal,secret)
  keep,_=add_job(c,'p1',other['id'],None,'completed','OTHER_DESIGN_TEXT')
  c.execute('UPDATE visual_assets SET origin=? WHERE id=?',(v.packed({'kind':'render','job_id':jid,'report':report}),image['id']))
  c.execute('UPDATE visual_assets SET origin=? WHERE id=?',(v.packed({'kind':'render','design_id':item['id'],'report':{'source':'OTHER_PROJECT_TEXT'}}),foreign['id']))
 response=client.delete(f"/api/projects/p1/visual/designs/{item['id']}?expected_version=1&confirm=true");assert response.status_code==200,response.text
 result=response.json();assert result['history_deleted'] and result['jobs_deleted']==1 and result['asset_reports_removed']==1 and result['retained_asset_ids']==[image['id']] and not result['secure_erasure']
 assert 'retained' in result['retention_notice']
 assert client.get(f'/api/projects/p1/visual/jobs/{jid}').status_code==404
 retained=client.get(f"/api/projects/p1/visual/assets/{image['id']}").json();assert 'report' not in retained['origin'] and retained['origin']['design_deleted']
 assert secret not in json.dumps(retained) and retained['sha256']==image['sha256']
 assert client.get(f'/api/projects/p1/visual/jobs/{keep}').json()['report']['layers'][0]['source']=='OTHER_DESIGN_TEXT'
 assert client.get(f"/api/projects/p2/visual/assets/{foreign['id']}").json()['origin']['report']['source']=='OTHER_PROJECT_TEXT'
 with get_db() as c:assert c.execute('SELECT COUNT(*) FROM visual_versions WHERE design_id=?',(item['id'],)).fetchone()[0]==0

def test_active_job_blocks_deletion_atomically(client):
 item=create(client);image=upload(client)
 with get_db() as c:jid,report=add_job(c,'p1',item['id'],image['id'],'running','ACTIVE_SOURCE')
 response=client.delete(f"/api/projects/p1/visual/designs/{item['id']}?expected_version=1&confirm=true");assert response.status_code==409
 assert client.get(f"/api/projects/p1/visual/designs/{item['id']}").status_code==200
 assert client.get(f'/api/projects/p1/visual/jobs/{jid}').json()['report']==report
