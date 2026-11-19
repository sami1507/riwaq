"""Evaluator correctness/security; these are algorithm fixtures, not speech quality data."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import pytest
from scripts import voice_evaluate as v


def pair(**changes):
    row = {'id': 'p1', 'reference': 'hello world', 'hypothesis': 'hello world', 'language': 'en',
           'dialect': None, 'condition': 'controlled', 'source_id': 'source1', 'speaker_id': 'speaker1',
           'recording_id': 'recording1', 'recording_sha256': 'a' * 64, 'split': 'dev', 'sample_kind': 'unit_fixture',
           'license_verification': {'status': 'verified', 'scope': 'transcript_evaluation_only',
                                    'evidence': 'Original algorithm unit fixture; no human recording.',
                                    'reviewed_by': 'test fixture', 'reviewed_at': '2026-11-15'}}
    row.update(changes)
    return row


def another(**changes):
    values=dict(id='p2', source_id='source2', speaker_id='speaker2', recording_id='recording2',
                recording_sha256='b' * 64, reference='other text', hypothesis='other text')
    values.update(changes)
    return pair(**values)


def test_exact_and_operation_counts():
    assert v.edit_counts('abc', 'abc')['errors'] == 0
    sub = v.edit_counts(['one', 'two'], ['one', 'three'])
    assert (sub['substitutions'], sub['deletions'], sub['insertions'], sub['rate']) == (1, 0, 0, .5)
    assert v.edit_counts('abc', 'ab')['deletions'] == 1
    assert v.edit_counts('ab', 'abc')['insertions'] == 1
    assert v.edit_counts('kitten', 'sitting')['errors'] == 3


@pytest.mark.parametrize('reference,hypothesis', [
    ('أكل', 'اكل'), ('عَلَم', 'علم'), ('١٢', '12'), ('لم يذهب', 'ذهب'),
    ('لا تطفئ الضوء', 'أطفئ الضوء'), ('إلى', 'الى'), ('12.50', '12.05'), ('é', 'e\u0301'),
])
def test_raw_cer_does_not_erase_arabic_marks_negation_numbers_or_normalize(reference, hypothesis):
    result = v.evaluate([pair(language='ar', reference=reference, hypothesis=hypothesis)])
    assert result['pairs'][0]['cer']['errors'] > 0
    assert result['pairs'][0]['cer']['reference_units'] == len(reference)


def test_whitespace_wer_is_explicit_and_cer_preserves_spacing_case_punctuation():
    r = v.evaluate([pair(reference='A  b\r\nc.', hypothesis='A b c.')])
    assert r['pairs'][0]['cer']['errors'] > 0
    assert r['pairs'][0]['wer_whitespace']['rate'] == 0
    assert v.edit_counts('Test'.split(), 'test'.split())['errors'] == 1
    assert v.edit_counts('test.'.split(), 'test'.split())['errors'] == 1


def test_empty_reference_does_not_invent_zero_denominator_score():
    r = v.evaluate([pair(reference='', hypothesis='invented words')])
    assert r['pairs'][0]['cer']['rate'] is None
    assert r['pairs'][0]['wer_whitespace']['rate'] is None
    assert r['strata'][0]['wer_whitespace']['insertions'] == 2
    assert r['strata'][0]['wer_whitespace']['rate'] is None
    assert v.edit_counts('', '')['rate'] is None


def test_rates_can_exceed_one_and_denominators_are_corpus_weighted():
    a = pair(reference='a', hypothesis='abcd')
    b = another(reference='longer', hypothesis='longer')
    result = v.evaluate([a, b])
    assert result['pairs'][0]['cer']['rate'] == 3
    assert result['strata'][0]['cer']['rate'] == 3 / 7


def test_language_dialect_condition_split_and_sample_kind_never_mixed():
    rows = [pair()]
    for index, changes in enumerate([{'language': 'ar'}, {'dialect': 'declared-region'},
                                    {'condition': 'noise'}, {'split': 'test'}, {'sample_kind': 'synthetic_audio'}], 2):
        rows.append(pair(id=str(index), source_id='src'+str(index), speaker_id='sp'+str(index),
                         recording_id='rec'+str(index), recording_sha256=hashlib.sha256(str(index).encode()).hexdigest(),
                         reference='reference'+str(index), hypothesis='result'+str(index), **changes))
    result = v.evaluate(rows)
    assert len(result['strata']) == 6
    assert 'overall' not in result and 'aggregate' not in result
    assert result['audit']['complete_identity_metadata']


def test_cjk_whitespace_warning_not_claimed_word_accuracy():
    r = v.evaluate([pair(language='zh-Hant', reference='你好世界', hypothesis='你好')])
    assert r['pairs'][0]['wer_whitespace']['reference_units'] == 1
    assert 'does not provide' in r['strata'][0]['wer_caution']


@pytest.mark.parametrize('field', ['source_id', 'speaker_id'])
def test_identity_leakage_across_split_rejected(field):
    a, b = pair(), another(split='test')
    b[field] = a[field]
    with pytest.raises(v.EvaluationError, match='overlaps'):
        v.evaluate([a, b])


@pytest.mark.parametrize('field', ['recording_id', 'recording_sha256'])
def test_duplicate_recording_rejected_even_if_text_or_metadata_differs(field):
    a, b = pair(), another(split='test')
    b[field] = a[field]
    with pytest.raises(v.EvaluationError) as err:
        v.evaluate([a, b])
    assert err.value.code == 'duplicate_recording'


def test_exact_reference_content_cannot_cross_splits_with_changed_hypothesis():
    a, b = pair(), another(split='train')
    b['reference'] = a['reference']
    with pytest.raises(v.EvaluationError, match='reference_sha256'):
        v.evaluate([a, b])


def test_identical_row_hash_prevents_relabelled_duplicate():
    a = pair()
    b = deepcopy(a);b['id'] = 'relabeled'
    with pytest.raises(v.EvaluationError) as err:
        v.evaluate([a, b])
    assert err.value.code == 'duplicate_row'


def test_missing_identity_is_visible_not_falsely_certified():
    r = v.evaluate([pair(source_id=None, speaker_id=None, recording_id=None, recording_sha256=None)])
    audit = r['audit']
    assert not audit['complete_identity_metadata']
    assert audit['missing_metadata_counts'] == {'recording_id': 1, 'recording_sha256': 1, 'source_id': 1, 'speaker_id': 1}
    assert 'declared_only' in audit['audio_hash_verification']


def test_rights_and_hash_validation():
    row = pair()
    row['license_verification']['status'] = 'pending'
    with pytest.raises(v.EvaluationError) as err:v.evaluate([row])
    assert err.value.code == 'license_unverified'
    with pytest.raises(v.EvaluationError) as err:v.evaluate([pair(reference_sha256='f'*64)])
    assert err.value.code == 'text_hash_mismatch'
    with pytest.raises(v.EvaluationError):v.evaluate([pair(recording_sha256='../audio')])


@pytest.mark.parametrize('change', [{'reference': '\ud800'}, {'reference': 'x\x00'}, {'language': '../en'},
                                    {'split': 'heldout'}, {'unexpected': 'field'}, {'hypothesis': 42}])
def test_bad_schema_and_unicode_rejected(change):
    with pytest.raises(v.EvaluationError):v.evaluate([pair(**change)])


def test_memory_and_cpu_work_bounds(tmp_path, monkeypatch):
    monkeypatch.setattr(v, 'MAX_PAIR_CELLS', 3)
    with pytest.raises(v.EvaluationError) as err:v.evaluate([pair(reference='ab', hypothesis='ab')])
    assert err.value.code == 'pair_cost'
    monkeypatch.setattr(v, 'MAX_PAIR_CELLS', 1000)
    monkeypatch.setattr(v, 'MAX_TOTAL_CELLS', 2)
    with pytest.raises(v.EvaluationError) as err:v.evaluate([pair(reference='a', hypothesis='ab')])
    assert err.value.code == 'total_cost'
    monkeypatch.setattr(v, 'MAX_LINE_BYTES', 8)
    f=tmp_path/'pairs.jsonl';f.write_text('0123456789\n',encoding='utf-8')
    with pytest.raises(v.EvaluationError) as err:v.load_jsonl([f])
    assert err.value.code == 'input_size'


def test_json_duplicate_keys_nonfinite_utf8_rejected(tmp_path):
    f=tmp_path/'pairs.jsonl'
    for data in [b'{"id":"a","id":"b"}\n', b'{"x":NaN}\n', b'\xff\n']:
        f.write_bytes(data)
        with pytest.raises(v.EvaluationError):v.load_jsonl([f])


def test_cli_reads_all_splits_without_network_or_overwriting_input(tmp_path, monkeypatch, capsys):
    def blocked(*args,**kwargs):pytest.fail('Offline pair evaluator attempted network')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    f=tmp_path/'pairs.jsonl';f.write_text(json.dumps(pair())+'\n',encoding='utf-8')
    report=tmp_path/'metrics.json'
    original=f.read_bytes()
    assert v.main([str(f),'--report',str(report)]) == 0
    r=json.loads(report.read_text(encoding='utf-8'))
    assert r['inputs'][0]['sha256'] == hashlib.sha256(original).hexdigest()
    assert r['pairs'][0]['cer']['errors']==0
    assert 'reference' not in r['pairs'][0] and 'hypothesis' not in r['pairs'][0]
    assert v.main([str(f),'--report',str(f)])==2 and f.read_bytes()==original
    assert v.main([str(f),'--report',str(report)])==2
    assert 'report_exists' in capsys.readouterr().err


def test_two_files_checked_as_one_split_audit(tmp_path):
    a=tmp_path/'dev.jsonl';b=tmp_path/'test.jsonl'
    a.write_text(json.dumps(pair())+'\n',encoding='utf-8')
    row=another(split='test');row['speaker_id']='speaker1'
    b.write_text(json.dumps(row)+'\n',encoding='utf-8')
    assert v.main([str(a),str(b)]) == 2


def test_real_smoke_manifest_contains_only_one_disclosed_synthetic_english_case():
    root=Path(__file__).resolve().parents[1]
    folder=root/'evaluation'/'voice'
    manifest=json.loads((folder/'synthetic-smoke-manifest.json').read_text(encoding='utf-8'))
    rows, inputs=v.load_jsonl([folder/'synthetic-smoke-en.jsonl'])
    assert len(rows)==1 and rows[0]['sample_kind']=='synthetic_audio' and rows[0]['split']=='dev'
    assert rows[0]['language']=='en-US' and rows[0]['dialect'] is None
    assert inputs[0]['sha256']==manifest['pairs_sha256']
    assert manifest['human_recordings']==0 and manifest['audio_included'] is False
    assert v.evaluate(rows)['strata'][0]['cer']['rate']==0
