"""API/security/lifecycle tests. Fake worker cases do not prove media quality.

Real codec, text rendering and playback validation live in test_video_render.py
and the explicitly separate video live/browser reports.
"""
import io
import json
import threading
import time
from pathlib import Path
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend import app as service,video,video_schema as s
from backend.db import get_db,now
from backend.visuals import packed

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    with TestClient(service.app,headers={'X-Riwaq-Client':'local'}) as c:
        from backend.local_auth import issue_pairing_code
        assert c.post('/api/auth/login', json={'pairing_code': issue_pairing_code()}).status_code == 200
        yield c
    assert not video.ACTIVE

def base(pid):return f'/api/projects/{pid}/video'
def project(c):return c.get('/api/projects').json()[0]['id']
def seed(pid,aid='image',kind='image',duration=10,has_audio=False):
    im=Image.new('RGB',(640,360),'green');out=io.BytesIO();im.save(out,format='PNG');raw=out.getvalue()
    with get_db() as c:
        c.execute('INSERT INTO video_assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(aid,pid,'image.png','image/png','.png',kind,video.digest(raw),len(raw),raw,packed({'kind':kind,'width':640,'height':360,'duration':duration,'has_audio':has_audio}),packed({'type':'test'}),now()))
    return aid
def spec(aid='image'):
    return {'width':640,'height':360,'clips':[{'id':'c1','asset_id':aid,'duration':2.0}]}
def create(c,pid,**changes):
    result=c.post(base(pid)+'/timelines',json={'title':'مسودة فيديو','spec':spec(),**changes});assert result.status_code==201,result.text
    return result.json()
def wait(c,pid,jid):
    for _ in range(200):
        j=c.get(base(pid)+'/jobs/'+jid).json()
        if j['status'] not in video.RUNNING:return j
        time.sleep(.02)
    raise AssertionError('Job did not settle')
def text(value='رواق — مساحة للأفكار'):
    return {'id':'t1','kind':'subtitle','text':value,'start':0.0,'end':2.0,'x':20.0,'y':40.0,'width':600.0,'height':200.0,'language':'ar','direction':'rtl','font':'arabic','size':30.0,'weight':400,'color':'#ffffff','align':'center'}

@pytest.mark.parametrize('changes',[{'width':0},{'width':3840,'height':2160},{'fps':120},{'clips':[{'id':'a','asset_id':'i','duration':61.0}]},{'texts':[text('x')|{'end':3.0}]},{'texts':[text('x')|{'x':639.0}]},{'texts':[text('x')|{'text':'x\u202efake'}]},{'audio':[{'id':'a','asset_id':'sound','start':1.0,'duration':2.0}]},{'clips':[{'id':'same','asset_id':'i','duration':1.0},{'id':'same','asset_id':'i','duration':1.0}]},{'clips':[{'id':'a','asset_id':'i','duration':float('nan')}]},{'command':'rm'}])
def test_invalid_spec_boundaries(changes):
    with pytest.raises(ValidationError):s.Timeline.model_validate(spec()|changes)

def test_exact_unicode_and_manual_subtitles():
    source='اللّغة العربيّة — خصم 25% — السعر 199 ₪\nRiwaq AI | إصدار 2.1'
    value=s.Timeline.model_validate(spec()|{'texts':[text(source)]}).model_dump()
    assert value['texts'][0]['text']==source
    assert source in s.subtitles(value,'srt')
    assert '00:00:00,000 --> 00:00:02,000' in s.subtitles(value)
    value['texts'][0]['text']='A <B> & C'
    assert 'A &lt;B&gt; &amp; C' in s.subtitles(value,'vtt')

@pytest.mark.parametrize('name,raw',[('a.png',b'fake'),('x.mp4',b'#EXTM3U\nhttp://169.254.169.254/'),('x.html',b'<script/>'),('file.mp4',b'\x89PNG\r\n\x1a\n')])
def test_upload_signature_rejection(client,name,raw):
    p=project(client);r=client.post(base(p)+'/assets',files={'file':(name,raw)})
    assert r.status_code==422
    assert client.get(base(p)+'/assets').json()==[]

def test_versions_source_restore_and_project_isolation(client):
    p=project(client);seed(p);t=create(client,p)
    q=client.post('/api/projects',json={'name':'Other'}).json()['id']
    for suffix in ('/timelines/'+t['id'],'/timelines/'+t['id']+'/versions','/timelines/'+t['id']+'/source','/timelines/'+t['id']+'/subtitles','/assets/image/content'):
        assert client.get(base(q)+suffix).status_code==404
    assert client.delete(base(q)+'/assets/image').status_code==404
    edit={'title':'تعديل','spec':spec()|{'texts':[text()]},'expected_version':1}
    r=client.patch(base(p)+'/timelines/'+t['id'],json=edit);assert r.status_code==200,r.text
    assert r.json()['version']==2
    assert client.patch(base(p)+'/timelines/'+t['id'],json=edit).status_code==409
    source=client.get(base(p)+'/timelines/'+t['id']+'/source?version=1').json()
    assert source['timeline']['spec']['texts']==[]
    restored=client.post(base(p)+'/timelines/'+t['id']+'/restore',json={'expected_version':2,'from_version':1})
    assert restored.json()['version']==3 and restored.json()['spec']['texts']==[]
    assert len(client.get(base(p)+'/timelines/'+t['id']+'/versions').json())==3
    assert client.delete(base(p)+'/timelines/'+t['id']+'?expected_version=2').status_code==409
    assert client.delete(base(p)+'/timelines/'+t['id']+'?expected_version=3').status_code==200
    assert client.get(base(p)+'/assets/image/content').status_code==200

@pytest.mark.parametrize('kind,has_audio,clip',[('image',False,{'start':1.0}),('image',False,{'muted':False}),('video',False,{'muted':False}),('video',True,{'duration':11.0}),('video',True,{'motion':'pan_zoom'}),('audio',True,{})])
def test_actual_asset_constraints(client,kind,has_audio,clip):
    p=project(client);seed(p,kind=kind,has_audio=has_audio)
    value=spec();value['clips'][0].update(clip)
    assert client.post(base(p)+'/timelines',json={'title':'x','spec':value}).status_code==422

def fake_render(payload,folder,stop,progress):
    """Controlled storage fixture, NOT a playable video generation test."""
    (folder/'movie.mp4').write_bytes(b'fake-test-output');(folder/'poster.png').write_bytes(b'fake-test-poster')
    progress({'stage':'encoded','step':48,'total':48})
    return {'success':True,'fixture':True}

def patch_worker(monkeypatch,worker):
    monkeypatch.setattr('backend.video_render.render',worker)
    monkeypatch.setattr('backend.video_capabilities.status',lambda:{'render':{'available':True}})

def test_idempotency_outdated_and_deletion_invalidate_output(client,monkeypatch):
    patch_worker(monkeypatch,fake_render)
    p=project(client);seed(p);t=create(client,p)
    body={'timeline_id':t['id'],'version':1,'idempotency_key':'test-key-1'}
    r=client.post(base(p)+'/jobs',json=body);assert r.status_code==202,r.text
    j=wait(client,p,r.json()['id']);assert j['status']=='completed'
    assert j['report']['generated_visuals'] is False and j['report']['manual_review_required'] is True
    again=client.post(base(p)+'/jobs',json=body);assert again.json()['id']==j['id']
    q=client.post('/api/projects',json={'name':'Other'}).json()['id']
    for suffix in ('','/content','/poster','/report'):
        assert client.get(base(q)+'/jobs/'+j['id']+suffix).status_code==404
    assert client.post(base(q)+'/jobs/'+j['id']+'/cancel').status_code==404
    updated=client.patch(base(p)+'/timelines/'+t['id'],json={'title':'later','spec':spec(),'expected_version':1})
    assert updated.status_code==200
    assert client.post(base(p)+'/jobs',json=body|{'version':2}).status_code==409
    assert client.get(base(p)+'/jobs/'+j['id']).json()['outdated'] is True
    assert client.get(base(p)+'/jobs/'+j['id']+'/content').status_code==200
    deleted=client.delete(base(p)+'/assets/image');assert deleted.json()['invalidated_exports']==1
    assert client.get(base(p)+'/jobs/'+j['id']+'/content').status_code==404
    assert client.get(base(p)+'/timelines/'+t['id']).json()['missing_assets']==['image']
    assert client.post(base(p)+'/jobs',json=body|{'idempotency_key':'new-key-2'}).status_code==404

def test_cancel_and_late_worker_cannot_publish(client,monkeypatch):
    started=threading.Event();released=threading.Event()
    def worker(payload,folder,stop,progress):
        started.set();released.wait(5);return fake_render(payload,folder,stop,progress)
    patch_worker(monkeypatch,worker)
    p=project(client);seed(p);t=create(client,p)
    body={'timeline_id':t['id'],'version':1,'idempotency_key':'cancel-key'}
    j=client.post(base(p)+'/jobs',json=body).json();assert started.wait(3)
    assert client.post(base(p)+'/jobs',json=body|{'idempotency_key':'second-key'}).status_code==409
    assert client.post(base(p)+'/jobs/'+j['id']+'/cancel').status_code==200
    released.set();assert wait(client,p,j['id'])['status']=='cancelled'
    assert client.get(base(p)+'/jobs/'+j['id']+'/content').status_code==404
    with get_db() as c:assert c.execute('SELECT COUNT(*) FROM video_outputs').fetchone()[0]==0

def test_delete_during_render_cannot_republish(client,monkeypatch):
    started=threading.Event();released=threading.Event()
    def worker(payload,folder,stop,progress):
        started.set();released.wait(5);return fake_render(payload,folder,stop,progress)
    patch_worker(monkeypatch,worker)
    p=project(client);seed(p);t=create(client,p)
    j=client.post(base(p)+'/jobs',json={'timeline_id':t['id'],'version':1,'idempotency_key':'delete-key'}).json();assert started.wait(3)
    assert client.delete(base(p)+'/assets/image').status_code==200
    # Deleted DB rows must not free the worker slot while its process is alive.
    seed(p,aid='replacement');other=create(client,p,spec=spec('replacement'))
    assert client.post(base(p)+'/jobs',json={'timeline_id':other['id'],'version':1,'idempotency_key':'replacement-key'}).status_code==409
    released.set()
    for _ in range(100):
        if j['id'] not in video.ACTIVE:break
        time.sleep(.02)
    assert client.get(base(p)+'/jobs/'+j['id']).status_code==404
    with get_db() as c:assert c.execute('SELECT COUNT(*) FROM video_outputs').fetchone()[0]==0

def test_false_success_does_not_store_output(client,monkeypatch):
    patch_worker(monkeypatch,lambda *args:{'success':True})
    p=project(client);seed(p);t=create(client,p)
    j=client.post(base(p)+'/jobs',json={'timeline_id':t['id'],'version':1,'idempotency_key':'no-output'}).json()
    assert wait(client,p,j['id'])['status']=='failed'
    assert client.get(base(p)+'/jobs/'+j['id']+'/content').status_code==404

def test_storage_limit_and_missing_renderer(client,monkeypatch):
    p=project(client);seed(p);t=create(client,p)
    monkeypatch.setattr('backend.video_capabilities.status',lambda:{'render':{'available':False}})
    body={'timeline_id':t['id'],'version':1,'idempotency_key':'limit-key'}
    assert client.post(base(p)+'/jobs',json=body).status_code==503
    monkeypatch.setattr('backend.video_capabilities.status',lambda:{'render':{'available':True}})
    monkeypatch.setattr(s,'PROJECT_BYTES',1024)
    assert client.post(base(p)+'/jobs',json=body).status_code==413

def test_html_media_csp_and_guard(client):
    assert client.get('/api/projects',headers={'X-Riwaq-Client':'wrong'}).status_code==403
    assert client.get('/api/projects',headers={'Origin':'https://outside.invalid'}).status_code==403
    assert "media-src 'self' blob:" in client.get('/').headers['content-security-policy']
