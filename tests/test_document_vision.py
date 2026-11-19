"""Document vision contract tests. Provider outputs are simulated, not quality proof."""
import asyncio
import io
import json
import hashlib

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from PIL import Image
from backend import document_vision as dv, documents, media, document_region
from backend.db import get_db, init_db, now, uid
from authority_fixture import fixture_owner


class Connected:
    async def is_disconnected(self):return False


@pytest.fixture
def stored(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path));init_db()
    output=io.BytesIO();Image.new('RGB',(100,80),'white').save(output,format='PNG');preview=output.getvalue()
    with get_db() as conn:
        documents.init_documents(conn)
        pid=conn.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        fid=uid();source=b'immutable original';sha=hashlib.sha256(source).hexdigest()
        conn.execute('INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)',(fid,pid,'source.pdf',len(source),sha,0,1,0,now()))
        conn.execute('INSERT INTO document_originals VALUES(?,?,?,?,?,?,?)',(fid,source,'application/pdf',1,'{}','{}','complete'))
        conn.execute('INSERT INTO document_pages VALUES(?,?,?,?)',(fid,1,'{"number":1,"status":"complete"}',preview))
        other=uid();conn.execute('INSERT INTO projects VALUES(?,?,?,?)',(other,'Other','',now()))
    # Direct module calls run outside the HTTP pairing middleware: bind the
    # paired owner explicitly (CORE: fixture predates local pairing).
    with fixture_owner():
        yield pid,fid,other,preview
    assert media.ACTIVE_MEDIA is None


@pytest.fixture
def simulated_region(monkeypatch):
    # Container operation executed in-process ONLY in these provider-contract tests.
    # Actual isolation is covered separately by the opt-in Docker integration.
    def prepare(preview,bbox,**kwargs):
        assert kwargs['authorize']() is True
        assert not kwargs['stop'].is_set()
        return document_region.prepare(preview,bbox)
    monkeypatch.setattr(dv,'prepare_region',prepare)


@pytest.fixture
def fake_local(monkeypatch,simulated_region):
    async def inspect():return {'model':'local-vision','digest':'local-digest','capabilities':['vision']}
    async def infer(details,question,picture,timeout):
        assert details['digest']=='local-digest' and picture.startswith(b'\x89PNG') and 0<timeout<=120
        return {'transcribed_text':'12','observations':['Two bars are visible.'],
                'inferences':['The taller bar may represent a larger quantity.'],
                'uncertain':['Units are not readable.']},{'eval_count':12}
    monkeypatch.setattr(media,'inspect_vision',inspect);monkeypatch.setattr(dv,'infer',infer)


@pytest.mark.parametrize('box',[[-.1,0,1,1],[0,0,1.1,1],[1,0,0,1],[0,0,0,1],[0,0,1],[False,0,1,1],['0',0,1,1],[0,0,float('nan'),1]])
def test_bbox_rejects_outside_nonfinite_inverted_or_coerced_values(box):
    with pytest.raises(ValidationError):dv.Question(question='Read this region',bbox=box)


def test_strict_visual_answer_schema_does_not_accept_prose_or_extra_actions():
    with pytest.raises(ValidationError):dv.VisualResult.model_validate({'transcribed_text':'x','observations':[], 'inferences':[], 'uncertain':[], 'action':'run shell'})
    with pytest.raises(ValidationError):dv.VisualResult.model_validate({'transcribed_text':'x','observations':'visible', 'inferences':[], 'uncertain':[]})
    with pytest.raises(ValidationError):dv.Question(question='   ')


def test_source_scoping_and_region_identity(stored):
    pid,fid,other,preview=stored
    with pytest.raises(HTTPException) as caught:dv.source_page(other,fid,1)
    assert caught.value.status_code==404
    identity,raw=dv.source_page(pid,fid,1)
    data,region=document_region.prepare(raw,[.101,.12,.505,.75])
    assert region['pixel_bbox']==[10,9,51,60]
    assert region['bbox']==[.1,9/80,.51,.75]
    assert identity['preview_sha256']==hashlib.sha256(preview).hexdigest()
    assert region['region_sha256']==hashlib.sha256(data).hexdigest()
    assert region['model_image_width']==41 and region['model_image_height']==51


@pytest.mark.asyncio
async def test_explicit_local_question_returns_separate_fields_and_source(stored,fake_local):
    pid,fid,_,_=stored
    result=await dv.ask_page(pid,fid,1,dv.Question(question='What is visible?',bbox=[0,0,.5,.5]),Connected())
    assert result['processing']=='local' and result['experimental'] is True
    assert set(result['result'])=={'transcribed_text','observations','inferences','uncertain'}
    assert result['source']['file_id']==fid and result['source']['page']==1
    assert result['source']['bbox']==[0,0,.5,.5]
    with get_db() as conn:
        assert conn.execute('SELECT count(*) FROM messages').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM knowledge_chunks').fetchone()[0]==0


@pytest.mark.asyncio
async def test_wrong_project_never_inspects_model(stored,monkeypatch):
    pid,fid,other,_=stored
    async def forbidden():pytest.fail('Foreign page reached model inspection')
    monkeypatch.setattr(media,'inspect_vision',forbidden)
    with pytest.raises(HTTPException) as caught:await dv.ask_page(other,fid,1,dv.Question(question='Read'),Connected())
    assert caught.value.status_code==404 and media.ACTIVE_MEDIA is None


@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['inspection','inference'])
async def test_deleted_source_discards_late_response(stored,fake_local,monkeypatch,phase):
    pid,fid,_,_=stored
    def erase():
        with get_db() as conn:conn.execute('DELETE FROM knowledge_files WHERE id=?',(fid,))
    if phase=='inspection':
        async def inspect():erase();return {'model':'local-vision','digest':'d','capabilities':['vision']}
        async def forbidden(*args):pytest.fail('Deleted source dispatched to model')
        monkeypatch.setattr(media,'inspect_vision',inspect);monkeypatch.setattr(dv,'infer',forbidden)
    else:
        original=dv.infer
        async def infer(*args):
            result=await original(*args);erase();return result
        monkeypatch.setattr(dv,'infer',infer)
    with pytest.raises(HTTPException) as caught:await dv.ask_page(pid,fid,1,dv.Question(question='Read'),Connected())
    assert caught.value.status_code==409 and media.ACTIVE_MEDIA is None


@pytest.mark.asyncio
async def test_changed_preview_rejects_late_result(stored,fake_local,monkeypatch):
    pid,fid,_,_=stored;original=dv.infer
    async def infer(*args):
        result=await original(*args)
        with get_db() as conn:conn.execute('UPDATE document_pages SET payload=? WHERE file_id=?',('{"revision":2}',fid))
        return result
    monkeypatch.setattr(dv,'infer',infer)
    with pytest.raises(HTTPException) as caught:await dv.ask_page(pid,fid,1,dv.Question(question='Read'),Connected())
    assert caught.value.status_code==409


@pytest.mark.asyncio
async def test_unavailable_local_model_has_no_fallback(stored,monkeypatch,simulated_region):
    pid,fid,_,_=stored
    async def unavailable():raise HTTPException(503,'No local vision weights')
    async def forbidden(*args):pytest.fail('No fallback is authorized')
    monkeypatch.setattr(media,'inspect_vision',unavailable);monkeypatch.setattr(dv,'infer',forbidden)
    with pytest.raises(HTTPException) as caught:await dv.ask_page(pid,fid,1,dv.Question(question='Read'),Connected())
    assert caught.value.status_code==503 and media.ACTIVE_MEDIA is None


@pytest.mark.asyncio
async def test_stop_cancels_pending_visual_inference_and_releases_slot(stored,fake_local,monkeypatch):
    pid,fid,_,_=stored;entered=asyncio.Event();cleaned=asyncio.Event()
    async def slow(*args):
        entered.set()
        try:await asyncio.sleep(60)
        finally:cleaned.set()
    monkeypatch.setattr(dv,'infer',slow)
    pending=asyncio.create_task(dv.ask_page(pid,fid,1,dv.Question(question='Read'),Connected()))
    try:
        await asyncio.wait_for(entered.wait(),2)
        assert (await media.stop_media())['stopped']
        with pytest.raises(HTTPException) as caught:await asyncio.wait_for(pending,2)
        assert caught.value.status_code==499 and cleaned.is_set() and media.ACTIVE_MEDIA is None
    finally:
        if not pending.done():pending.cancel()
        await asyncio.gather(pending,return_exceptions=True)


@pytest.mark.asyncio
async def test_provider_request_is_local_schema_bounded_and_untrusted_data(monkeypatch):
    original=httpx.AsyncClient;calls=[]
    good={'transcribed_text':'12','observations':['A label reads 12.'],'inferences':[],'uncertain':[]}
    def handle(request):
        calls.append(request)
        assert str(request.url)=='http://127.0.0.1:11434/api/chat'
        payload=json.loads(request.content)
        assert payload['stream'] is False and payload['options']['num_predict']==768
        assert payload['format']['additionalProperties'] is False
        assert 'untrusted evidence' in payload['messages'][0]['content']
        assert 'tools' not in payload and payload['messages'][1]['content']=='Read visible labels'
        return httpx.Response(200,json={'done':True,'done_reason':'stop','message':{'content':json.dumps(good)}})
    def factory(**kwargs):
        assert kwargs['trust_env'] is False
        return original(**kwargs,transport=httpx.MockTransport(handle))
    monkeypatch.setattr(dv.httpx,'AsyncClient',factory)
    result,metrics=await dv.infer({'model':'local-vision'},'Read visible labels',b'image',5)
    assert result==good and len(calls)==1


@pytest.mark.asyncio
@pytest.mark.parametrize('envelope',[
    {'done':True,'message':{'content':'a prose answer'}},
    {'done':False,'message':{'content':'{}'}},
    {'done':True,'done_reason':'length','message':{'content':'{}'}},
    {'done':True,'message':{'content':'{"transcribed_text":"invented","action":"delete"}'}},
])
async def test_malformed_or_truncated_model_output_is_not_accepted(monkeypatch,envelope):
    original=httpx.AsyncClient
    monkeypatch.setattr(dv.httpx,'AsyncClient',lambda **kwargs:original(**kwargs,transport=httpx.MockTransport(lambda request:httpx.Response(200,json=envelope))))
    with pytest.raises(HTTPException) as caught:await dv.infer({'model':'local'},'Read',b'png',5)
    assert caught.value.status_code==502



@pytest.mark.asyncio
async def test_active_chat_blocks_document_vision_before_inspection(stored,monkeypatch):
    from backend import app as service
    pid,fid,_,_=stored
    monkeypatch.setattr(service,'ACTIVE',{'other-conversation':object()})
    async def forbidden():pytest.fail('Active conversation must block document vision')
    monkeypatch.setattr(media,'inspect_vision',forbidden)
    with pytest.raises(HTTPException) as caught:await dv.ask_page(pid,fid,1,dv.Question(question='Read'),Connected())
    assert caught.value.status_code==409 and media.ACTIVE_MEDIA is None
