"""The Arabic measurement is only as honest as its graders: every item's reference must pass and its wrong answer must fail."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('arabic_eval', Path(__file__).resolve().parents[1] / 'scripts' / 'arabic_eval.py')
ae = importlib.util.module_from_spec(spec); spec.loader.exec_module(ae)


def test_every_item_accepts_its_reference_and_rejects_its_wrong_answer():
    assert ae.selftest() == []


def test_items_have_unique_ids_and_free_writing_is_never_auto_graded():
    items = ae.load()
    assert len({i['id'] for i in items}) == len(items) >= 60
    assert all(ae.grade(i['check'], 'أي نص') is None for i in items if i['check']['type'] == 'human')


def test_normalization_ignores_diacritics_alef_forms_and_arabic_digits():
    assert ae.norm('إنَّ الْعِلْمَ ٢٨') == ae.norm('ان العلم 28')
    assert ae.grade({'type': 'number', 'answer': 28}, 'العدد هو ٢٨ حرفًا') is True
    assert ae.grade({'type': 'mcq', 'answer': 'ب'}, 'الإجابة: ب) فاعل') is True
    assert ae.grade({'type': 'mcq', 'answer': 'ب'}, '<think>ربما أ</think>ج') is False


def test_summary_counts_request_errors_as_failures_not_as_skips():
    rows = [{'category': 'x', 'passed': True, 'seconds': 1, 'tokens_per_second': 10, 'error': None},
            {'category': 'x', 'passed': False, 'seconds': None, 'tokens_per_second': None, 'error': 'URLError'},
            {'category': 'w', 'passed': None, 'seconds': 2, 'tokens_per_second': 12, 'error': None}]
    s = ae.summarize(rows)
    assert (s['passed'], s['graded'], s['errors']) == (1, 2, 1)
