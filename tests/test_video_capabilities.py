"""Offline discovery contracts; these tests do not validate rendered video quality."""
import json
import pytest
from backend import video_capabilities as caps


@pytest.fixture
def local_runtime(tmp_path,monkeypatch):
    monkeypatch.setattr(caps,'REPORT',tmp_path/'receipt.json')
    monkeypatch.setattr(caps,'_pyav',lambda:{'installed':True,'version':'19.0.2',
        'library_versions':{'libavcodec':[63,1,102]},'encoders':{'libx264':True,'aac':True}})
    monkeypatch.setattr(caps,'_text_runtime',lambda:{'node':True,'chrome':True,'playwright':True,'font_manifest':True})
    monkeypatch.setattr(caps,'implementation_fingerprint',lambda:'a'*64)
    return caps.REPORT


def receipt(path,**updates):
    value={'success':True,'pyav_version':'19.0.2','implementation_sha256':'a'*64,'qc':{'full_decode':True}}
    value.update(updates);path.write_text(json.dumps(value),encoding='utf-8')


def test_runtime_presence_is_not_validation(local_runtime):
    value=caps.status()
    assert value['render']['available'] and not value['render']['tested']
    assert value['render']['status']=='available_unvalidated'
    assert not value['audio_import']['tested']
    assert not value['generation']['available'] and not value['arabic_tts']['available']


def test_video_receipt_does_not_prove_audio_mix(local_runtime):
    receipt(local_runtime)
    value=caps.status()
    assert value['render']['tested'] and value['render']['experimental']
    assert not value['audio_import']['tested']
    receipt(local_runtime,qc={'full_decode':True,'audio_mix':True})
    assert caps.status()['audio_import']['tested']
    assert not caps.status()['arabic_tts']['available']


@pytest.mark.parametrize('updates',[
    {'success':False},{'pyav_version':'old'},{'implementation_sha256':'b'*64},
    {'qc':{'full_decode':False,'audio_mix':True}},{'qc':None}])
def test_failed_stale_or_malformed_receipt_is_not_validated(local_runtime,updates):
    receipt(local_runtime,**updates)
    assert not caps.status()['render']['tested']


def test_secret_name_never_reads_or_enables_provider(local_runtime,monkeypatch):
    secret='sensitive-fixture-value-not-for-logs'
    monkeypatch.setenv('RUNWAYML_API_SECRET',secret)
    value=caps.status()
    assert value['generation']['credential_name_present']
    assert secret not in json.dumps(value)
    assert value['generation']['status']=='needs_configuration'
    assert not value['generation']['credential_value_read']
    assert not value['generation']['keyring_inspected']
    assert not value['privacy']['provider_requests_made']


def test_registry_cannot_advertise_an_unimplemented_adapter(local_runtime,monkeypatch):
    value=caps.registry()
    for key in ('generation','arabic_tts'):
        value[key].update(available=True,tested=True,status='ready')
    monkeypatch.setattr(caps,'registry',lambda:value)
    result=caps.status()
    for key in ('generation','arabic_tts'):
        assert not result[key]['available'] and not result[key]['tested']
        assert result[key]['status']=='needs_configuration'


@pytest.mark.parametrize('missing',['codec','worker','text_runtime'])
def test_missing_runtime_refuses_readiness_even_with_receipt(local_runtime,monkeypatch,missing):
    receipt(local_runtime)
    if missing=='codec':
        monkeypatch.setattr(caps,'_pyav',lambda:{'installed':True,'version':'19.0.2','library_versions':{},'encoders':{'libx264':False,'aac':True}})
    elif missing=='worker':monkeypatch.setattr(caps,'implementation_fingerprint',lambda:None)
    else:monkeypatch.setattr(caps,'_text_runtime',lambda:{'node':False})
    assert not caps.status()['render']['available']
    assert not caps.status()['render']['tested']


def test_invalid_or_oversized_receipt_is_unvalidated(local_runtime):
    local_runtime.write_text('not JSON',encoding='utf-8')
    assert not caps.status()['render']['tested']
    local_runtime.write_bytes(b' '* (1024*1024+1))
    assert not caps.status()['render']['tested']


def test_implementation_fingerprint_covers_content_and_names(tmp_path,monkeypatch):
    monkeypatch.setattr(caps,'ROOT',tmp_path)
    monkeypatch.setattr(caps,'IMPLEMENTATION_FILES',('a.py','b.py'))
    (tmp_path/'a.py').write_text('a',encoding='utf-8')
    assert caps.implementation_fingerprint() is None
    (tmp_path/'b.py').write_text('b',encoding='utf-8')
    before=caps.implementation_fingerprint()
    (tmp_path/'a.py').write_text('changed',encoding='utf-8')
    assert caps.implementation_fingerprint()!=before


def test_limits_match_enforced_schema(local_runtime):
    value=caps.status()['limits'];schema=caps.video_schema
    assert value['upload_bytes']==schema.MAX_ASSET
    assert value['seconds']==schema.MAX_SECONDS and value['output_bytes']==schema.MAX_OUTPUT
    assert value['project_bytes']==schema.PROJECT_BYTES and value['global_bytes']==schema.GLOBAL_BYTES
