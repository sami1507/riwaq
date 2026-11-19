"""Language metadata and authority boundaries, not speech-accuracy benchmarks."""
import json
from pathlib import Path
import pytest
from backend import voice_capabilities as v

@pytest.fixture
def model(tmp_path,monkeypatch):
    for name in ('model.bin','config.json'):(tmp_path/name).write_bytes(b'{}')
    (tmp_path/'tokenizer.json').write_text(json.dumps({'added_tokens':[{'content':'<|'+code+'|>'} for code in ['ar','en','he','es','fr','pt','zh','ja','ko','hi','ur','tr','sw','jw','notalanguage']]}),encoding='utf-8')
    monkeypatch.setenv('RIWAQ_WHISPER_MODEL',str(tmp_path))
    monkeypatch.setattr(v.media,'whisper_config',lambda:{'configured':True,'model_path':str(tmp_path),'model':'fixture','fingerprint':'fixture'})
    return tmp_path

@pytest.mark.parametrize('tag,code',[
    ('ar-PS','ar'),('ar-LB','ar'),('ar-EG','ar'),('ar-SA','ar'),('ar-IQ','ar'),('ar-MA','ar'),('ar-YE','ar'),
    ('es-MX','es'),('pt-BR','pt'),('fr-CA','fr'),('he','he'),('zh-Hant','zh'),('ja','ja'),('ko','ko'),
    ('hi','hi'),('ur','ur'),('tr','tr'),('sw-TZ','sw'),('jv','jw'),('jw','jw')])
def test_supported_model_tokens_with_explicit_regional_preferences(model,tag,code):
    assert v.resolve_language(tag)==code

@pytest.mark.parametrize('tag',['ar-Palestinian','ar-Gulf','ar-ZZ','en_US',' en','en;curl','../../en','<|en|>','',None])
def test_invalid_or_unlisted_tags_rejected(model,tag):
    with pytest.raises(ValueError):v.resolve_language(tag)

def test_engine_announcement_does_not_override_installed_model(model):
    info=v.model_languages(model)
    assert 'yue' in info['engine_declared_codes'] and 'yue' not in info['codes']
    assert 'notalanguage' not in info['codes']
    with pytest.raises(ValueError):v.resolve_language('yue')
    assert v.resolve_language('auto') is None

def test_all_dialect_quality_is_explicitly_unmeasured(model):
    result=v.status([])
    assert result['stt']['available'] and not result['stt']['tested']
    assert all(not r['measured'] and r['evaluation'] is None and not r['dialect_verified'] for r in result['languages'])
    assert not result['direct_audio']['available'] and not result['diarization']['available']
    assert not result['tts']['arabic_available']

def test_runtime_voice_list_does_not_imply_other_region_or_language(model):
    voices=[{'id':'sapi-'+'a'*32,'name':'Actual English fixture','language':'en-US','enabled':True,'local':True,'secret':'never expose'}]
    result=v.status(voices)
    rows={r['tag']:r for r in result['languages']}
    assert result['tts']['available'] and result['tts']['discovery_performed']
    assert rows['en-US']['tts_available'] and rows['en']['tts_available']
    assert not rows['en-GB']['tts_available'] and not rows['ar']['tts_available']
    assert not result['tts']['synthesis_tested'] and not result['tts']['quality_measured']
    assert 'never expose' not in json.dumps(result)

def test_registry_name_is_not_live_sapi_voice(model):
    result=v.status([{'id':'registry-key','name':'Arabic','language':'ar-SA','enabled':True,'local':True}])
    assert not result['tts']['available'] and not result['tts']['arabic_available']
    assert not v.status()['tts']['discovery_performed']
    assert v.status([],tts_error='probe_failed')['tts']['error_code']=='probe_failed'
    assert v.status([],tts_error='C:/sensitive/file')['tts']['error_code'] is None

def test_disabled_remote_or_duplicate_voices_are_filtered(model):
    item={'id':'sapi-'+'a'*32,'name':'Voice','language':'en-US','enabled':True,'local':True}
    assert len(v.safe_voices([item,item,item|{'enabled':False},item|{'local':False}]))==1
    assert v.safe_voices({'voices':[item]})==[]

def test_absent_model_does_not_show_available(model,monkeypatch):
    monkeypatch.setattr(v.media,'whisper_config',lambda:{'configured':False,'reason':'not configured'})
    result=v.status()
    assert not result['stt']['available'] and not any(r['stt_available'] for r in result['languages'])

def test_bounded_metadata_and_missing_tokenizer(model,monkeypatch):
    monkeypatch.setattr(v,'MAX_TOKENIZER',16)
    with pytest.raises(ValueError):v.model_languages(model)
    assert not v.status()['stt']['available']
    (model/'tokenizer.json').unlink()
    with pytest.raises(ValueError):v.model_languages(model)

def test_no_provider_calls_from_status(model,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('Status made a network request'))
    monkeypatch.setattr(v.media,'validation_report',lambda:pytest.fail('Old media receipt used as broad voice quality proof'))
    assert v.status([])['privacy']['provider_requests_made'] is False

def test_registry_preserves_preferred_javanese_tag_and_required_languages():
    rows={r['tag']:r for r in v.registry()['languages']}
    assert rows['jv']['engine_code']=='jw' and 'jw' not in rows
    assert {'ar-PS','ar-JO','ar-EG','ar-SA','ar-IQ','ar-MA','ar-YE','en','es','fr','pt','he','zh','ja','ko','hi','ur','tr','sw'} <= set(rows)


def test_language_tokens_in_model_vocabulary_are_recognized(model):
    (model/'tokenizer.json').write_text(json.dumps({'model':{'vocab':{'<|ar|>':1,'<|en|>':2}}}),encoding='utf-8')
    assert v.model_languages(model)['codes']==['ar','en']
