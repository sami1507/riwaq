"""The measured accuracy rules reach ordinary chat only, never shrink a small context, and can be switched off."""
from backend import grounding

CFG = {'context_size': 8192, 'max_tokens': 1024}
PROJECT = {'instructions': 'Keep answers short.'}


def system(grounded=False, cfg=CFG, chunks=()):
    messages, _ = grounding.compose(PROJECT, [], [], 'ما عاصمة الأردن؟', list(chunks), dict(cfg), grounded)
    return messages[0]['content']


def test_ordinary_chat_receives_the_rules_after_the_base_instruction_and_project_text():
    text = system()
    assert text.index('You are Riwaq') < text.index('Keep answers short.') < text.index('Accuracy rules:')
    assert 'Check the premise first' in text and 'Do not hedge or refuse simple, well-known questions' in text


def test_grounded_answers_keep_only_the_evidence_policy():
    assert 'Accuracy rules:' not in system(grounded=True)


def test_small_context_keeps_its_whole_budget():
    assert 'Accuracy rules:' not in system(cfg={'context_size': 2048, 'max_tokens': 512})


def test_rules_yield_to_a_long_conversation_instead_of_forcing_compaction():
    turn = lambda role, n: {'role': role, 'content': 'س' * n, 'status': 'complete'}
    cfg = {'context_size': 4096, 'max_tokens': 256}
    short, _ = grounding.compose(PROJECT, [], [turn('user', 100), turn('assistant', 100)], 'سؤال', [], dict(cfg), False)
    assert 'Accuracy rules:' in short[0]['content']
    # 1,400 Arabic letters are 2,800 bytes: the history fits alone but not together with the rules.
    cfg_long = dict(cfg)
    long, _ = grounding.compose(PROJECT, [], [turn('user', 700), turn('assistant', 700)], 'سؤال', [], cfg_long, False)
    assert 'Accuracy rules:' not in long[0]['content'] and cfg_long['_accuracy_rules'] is False
    assert [m['content'] for m in long[1:3]] == ['س' * 700, 'س' * 700]
    assert 'history_compaction' not in cfg_long['_context_budget']


def test_switch_disables_the_rules(monkeypatch):
    monkeypatch.setenv('RIWAQ_ACCURACY_POLICY', '0')
    assert 'Accuracy rules:' not in system()


def test_rules_match_the_measured_prompt_file():
    from pathlib import Path
    measured = (Path(__file__).resolve().parents[1] / 'training' / 'arabic_eval' / 'prompts' / 'accuracy-en.txt').read_text(encoding='utf-8')
    assert grounding.ACCURACY_POLICY in measured
