"""The Arabic measurement is only as honest as its graders: every item's reference must pass and its wrong answer must fail."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('arabic_eval', Path(__file__).resolve().parents[1] / 'scripts' / 'arabic_eval.py')
ae = importlib.util.module_from_spec(spec); spec.loader.exec_module(ae)


def test_every_item_accepts_its_reference_and_rejects_its_wrong_answer():
    assert ae.selftest() == []


def test_items_have_unique_ids_and_free_writing_is_never_auto_graded():
    items = ae.load()
    assert len({i['id'] for i in items}) == len(items) >= 100
    assert all(ae.grade(i['check'], 'أي نص') is None for i in items if i['check']['type'] == 'human')


def test_normalization_ignores_diacritics_alef_forms_and_arabic_digits():
    assert ae.norm('إنَّ الْعِلْمَ ٢٨') == ae.norm('ان العلم 28')
    assert ae.grade({'type': 'number', 'answer': 28}, 'العدد هو ٢٨ حرفًا') is True
    assert ae.grade({'type': 'mcq', 'answer': 'ب'}, 'الإجابة: ب) فاعل') is True
    assert ae.grade({'type': 'mcq', 'answer': 'ب'}, '<think>ربما أ</think>ج') is False


def test_summary_counts_request_errors_as_failures_not_as_skips():
    rows = [{'category': 'x', 'passed': True, 'seconds': 1, 'tokens_per_second': 10, 'error': None, 'answer': 'a'},
            {'category': 'x', 'passed': False, 'seconds': None, 'tokens_per_second': None, 'error': 'URLError'},
            {'category': 'w', 'passed': None, 'seconds': 2, 'tokens_per_second': 12, 'error': None}]
    s = ae.summarize(rows)
    assert (s['passed'], s['graded'], s['errors']) == (1, 2, 1)


def test_refusal_traps_and_over_refusal_controls_pull_in_opposite_directions():
    items = {i['id']: i for i in ae.load()}
    # A model that refuses everything must fail the controls; one that answers everything must fail the traps.
    assert all(ae.grade(items[f'ok-{n:02d}']['check'], 'لا أعرف، لست متأكدًا.') is False for n in range(1, 11))
    assert all(ae.grade(items[f'hn-{n:02d}']['check'], 'الجواب هو كذا وكذا بكل تأكيد.') is False for n in range(1, 15))


def test_verify_pass_grades_the_final_answer_and_keeps_the_system_prompt(monkeypatch):
    calls = []
    def fake(endpoint, model, messages, timeout):
        calls.append(messages)
        return ('مسودة', 1.0, 10.0) if len(calls) == 1 else ('ب', 2.0, 12.0)
    monkeypatch.setattr(ae, 'chat', fake)
    answer, seconds, _ = ae.ask('http://127.0.0.1:1', 'm', 'سؤال', 5, system='نظام', verify=True)
    assert (answer, seconds) == ('ب', 3.0)
    assert calls[1][0] == {'role': 'system', 'content': 'نظام'} and calls[1][2]['content'] == 'مسودة' and calls[1][3]['content'] == ae.VERIFY


def test_app_variant_measures_riwaqs_own_pipeline(monkeypatch):
    sent = []
    def fake(endpoint, model, messages, timeout):
        sent.append(messages)
        return ('المطر نعمة.', 1.0, 10.0) if len(sent) == 1 else ('هطل المطر غزيرًا هذا المساء', 1.0, 10.0)
    monkeypatch.setattr(ae, 'chat', fake)
    answer, seconds, _ = ae.ask('http://127.0.0.1:1', 'm', 'اكتب جملة واحدة من خمس كلمات بالضبط عن المطر.', 5, system=ae.APP)
    assert answer == 'هطل المطر غزيرًا هذا المساء' and seconds == 2.0
    assert 'Accuracy rules:' in sent[0][0]['content'] and 'والمطلوب 5 بالضبط' in sent[1][-1]['content']
    sent.clear()
    ae.ask('http://127.0.0.1:1', 'm', 'رتب تصاعديًا: 9، 2، 5', 5, system=ae.APP)
    assert '"sorted": ["2", "5", "9"]' in sent[0][-1]['content']
