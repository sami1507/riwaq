"""Protocol and fault simulations only; real Docker acceptance is separate."""
import base64
import copy
from dataclasses import asdict
import hashlib
import json
import struct
import threading
import time
from types import SimpleNamespace
import zlib
import pytest
from backend import isolated_document_page as p, document_isolation as adapter
from backend.document_extract import Limits
from backend import isolated_worker as worker

BODY=b'original source'

def png(width=1,height=1):
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\0'*(width*3))*height))+chunk(b'IEND',b'')

def metadata(page=1):return {'name':'original.pdf','page':page,'languages':'ara+eng+heb','limits':asdict(Limits())}

def result(page=1,preview=True):
    value={'success':True,'page_count':1,'mime':'application/pdf','metadata':{'sha256':hashlib.sha256(BODY).hexdigest(),'bytes':len(BODY),'filename':'original.pdf','source_network':False},'warnings':[]}
    if page:value['page']={'number':1,'width':1 if preview else None,'height':1 if preview else None,'kind':'native','status':'complete','preview_file':'page.png' if preview else None,'elements':[{'id':'p1-e00001','type':'text','text':'Exact 731','text_raw':'Exact 731','bbox':[0,0,1,1],'locator':{'page':1}}]}
    return value

def envelope(value=None,preview=True):
    raw=png() if preview else None
    return {'version':p.VERSION,'input_sha256':hashlib.sha256(BODY).hexdigest(),'success':True,'result':value or result(preview=preview),'preview':None if raw is None else {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'base64':base64.b64encode(raw).decode()}}

def decode(value,meta=None):return p.decode_response(p.dump(value),meta or metadata(),hashlib.sha256(BODY).hexdigest(),len(BODY))

def test_exact_result_and_png_without_host_decoder(monkeypatch):
    import PIL.Image
    monkeypatch.setattr(PIL.Image,'open',lambda *a,**k:pytest.fail('Host image decoding forbidden'))
    value,preview=decode(envelope())
    assert value['page']['elements'][0]['text_raw']=='Exact 731' and preview==png()

def test_inspection_has_no_page_or_preview():
    value,preview=decode(envelope(result(0,False),False),metadata(0))
    assert 'page' not in value and preview is None

@pytest.mark.parametrize('change',[
    lambda e:e.update(input_sha256='a'*64),
    lambda e:e['result'].update(page_count=True),
    lambda e:e['result']['page'].update(number=2),
    lambda e:e['result']['page'].update(width=True),
    lambda e:e['result']['metadata'].update(filename='other.pdf'),
    lambda e:e['result']['metadata'].update(bytes=True),
    lambda e:e['result']['metadata'].update(source_network=True),
    lambda e:e['result']['page']['elements'][0]['locator'].update(page=True),
    lambda e:e['result']['page']['elements'][0].update(bbox=[-1,0,1,1]),
    lambda e:e['result']['page']['elements'].append(copy.deepcopy(e['result']['page']['elements'][0])),
    lambda e:e['preview'].update(sha256='b'*64),
    lambda e:e['preview'].update(base64='not base64!'),
    lambda e:e.update(unexpected='field'),
])
def test_forged_results_rejected(change):
    value=envelope();change(value)
    with pytest.raises((ValueError,TypeError)):decode(value)

@pytest.mark.parametrize('raw',[b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":Infinity}'])
def test_ambiguous_json_rejected(raw):
    with pytest.raises(ValueError):p.loads(raw)

def test_preview_is_bounded_and_has_no_trailing_content():
    for raw in [png()+b'private trailing',png()[:-1],b'GIF89a',b'x'*(p.MAX_PREVIEW+1)]:
        with pytest.raises(ValueError):p.png_dimensions(raw)
    assert p.png_dimensions(png(5000,1))==(5000,1)  # Preserve the existing 8MP-only image bound.

@pytest.mark.parametrize('changes',[{'name':'../evil.pdf'},{'page':True},{'page':201},{'languages':'eng+xxx'},{'limits':{**asdict(Limits()),'max_pixels':8000001}}])
def test_metadata_cannot_select_paths_commands_or_raise_limits(changes):
    with pytest.raises(ValueError):p.validate_metadata({**metadata(),**changes})

def test_adapter_payload_is_data_only_and_old_config_still_valid():
    config=worker.IsolatedConfig(docker_executable='C:/trusted/docker.exe',docker_sha256='a'*64,endpoint='npipe:////./pipe/dockerDesktopLinuxEngine',daemon_id='synthetic',images={'python_unittest':'sha256:'+'b'*64})
    assert len(config.images)==1
    raw,hashes=worker.prepare_payload({'input.bin':BODY,'metadata.json':p.dump(metadata())},'document_page',['input.bin'])
    assert base64.b64decode(p.loads(raw)['body'])==BODY and hashes['input.bin']==hashlib.sha256(BODY).hexdigest()
    assert p.MAX_ENVELOPE<14*1024**2

@pytest.fixture
def runtime(tmp_path,monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(adapter,'load_config',lambda _:SimpleNamespace(images={'document_page':'sha256:'+'c'*64}))
    return tmp_path

def test_authority_required_and_missing_config_never_falls_back(runtime,monkeypatch):
    monkeypatch.setattr(adapter,'execute',lambda *a,**k:pytest.fail('Execution forbidden'))
    with pytest.raises(adapter.IsolationError,match='authority_required'):adapter.extract(BODY,'original.pdf',1,'ara+eng+heb')
    monkeypatch.setattr(adapter,'load_config',lambda _:SimpleNamespace(images={}))
    with pytest.raises(adapter.IsolationError,match='unconfigured'):adapter.extract(BODY,'original.pdf',1,'ara+eng+heb',authorize=lambda:True)

@pytest.mark.parametrize('status,expected',[('timeout',504),('cancelled',499),('revoked',403),('blocked',503),('cleanup_failed',503)])
def test_faults_never_accept_output_or_run_host(runtime,monkeypatch,status,expected):
    monkeypatch.setattr(adapter,'execute',lambda *a,**k:{'status':status,'stdout':p.dump(envelope()).decode(),'cleanup_verified':True,'policy_inspected':True})
    with pytest.raises(adapter.IsolationError) as error:adapter.extract(BODY,'original.pdf',1,'ara+eng+heb',authorize=lambda:True)
    assert error.value.status_code==expected

def test_late_authority_loss_discards_valid_output(runtime,monkeypatch):
    alive=[True]
    def execute(*a,**k):
        alive[0]=False
        return {'status':'completed','stdout':p.dump(envelope()).decode(),'cleanup_verified':True,'policy_inspected':True}
    monkeypatch.setattr(adapter,'execute',execute)
    with pytest.raises(adapter.IsolationError,match='authority_revoked'):
        adapter.extract(BODY,'original.pdf',1,'ara+eng+heb',authorize=lambda:alive[0])

def test_success_receipt_never_duplicates_document_text(runtime,monkeypatch):
    monkeypatch.setattr(adapter,'execute',lambda *a,**k:{'status':'completed','stdout':p.dump(envelope()).decode(),'stderr':'synthetic diagnostic','cleanup_verified':True,'policy_inspected':True})
    value,preview,receipt=adapter.extract(BODY,'original.pdf',1,'ara+eng+heb',authorize=lambda:True)
    assert value['page']['number']==1 and preview==png()
    assert 'stdout' not in receipt and 'stderr' not in receipt and receipt['host_fallback'] is False


def test_inspection_and_page_dispatch_are_serialized_and_keep_total_deadline(monkeypatch):
    from backend import documents
    monkeypatch.setattr(documents,'worker_status',lambda:{'available':True})
    monkeypatch.setattr(documents,'DISPATCH_SLOT',threading.BoundedSemaphore(1))
    entered=threading.Event();release=threading.Event();calls=[];errors=[]
    def extract(data,name,page,language,stop,authorize=None,deadline=None):
        calls.append((page,deadline-time.monotonic()))
        if page:entered.set();assert release.wait(3)
        return {},None,{'seconds':0}
    monkeypatch.setattr(adapter,'extract',extract)
    def run(page):
        try:documents.parse(BODY,'original.pdf',page,'eng',threading.Event(),authorize=lambda:True)
        except BaseException as exc:errors.append(exc)
    first=threading.Thread(target=run,args=(1,));second=threading.Thread(target=run,args=(0,))
    first.start();assert entered.wait(2);second.start();time.sleep(.05)
    assert [page for page,_ in calls]==[1]
    release.set();first.join(3);second.join(3)
    assert not errors and [page for page,_ in calls]==[1,0]
    assert 0<calls[1][1]<30 and 0<calls[0][1]<=90


def test_dispatch_wait_is_bounded_and_cancelled_without_start(monkeypatch):
    from backend import documents
    monkeypatch.setattr(documents,'worker_status',lambda:{'available':True})
    slot=threading.BoundedSemaphore(1);slot.acquire()
    monkeypatch.setattr(documents,'DISPATCH_SLOT',slot)
    monkeypatch.setattr(documents,'MAX_DISPATCH_WAIT',.03)
    monkeypatch.setattr(adapter,'extract',lambda *a,**k:pytest.fail('Busy queue dispatched'))
    with pytest.raises(adapter.IsolationError,match='dispatch_busy'):
        documents.parse(BODY,'original.pdf',1,'eng',threading.Event(),authorize=lambda:True)
    stop=threading.Event();stop.set()
    with pytest.raises(adapter.IsolationError) as cancelled:
        documents.parse(BODY,'original.pdf',1,'eng',stop,authorize=lambda:True)
    assert cancelled.value.status_code==499
    slot.release()
