"""CORE long-context acceptance: declared compaction, user turns never dropped.

The synthetic tokenizer below is a byte-level BPE trained on synthetic Arabic
text; it exercises the exact-count path without shipping a model file. The
real Qwen tokenizer test runs only where that file exists (it is not in the
repository) and is reported as skipped elsewhere.
"""
import json
import os
import random
from pathlib import Path

import pytest

from backend import context_budget
from backend.grounding import compose

WORDS = ('الطالب المعلم الكتاب الدرس القاعدة المثال السؤال الجواب المشروع الملف الجدول القيمة النتيجة '
         'يكتب يقرأ يشرح يراجع يحسب يقارن يلخص يحفظ يرسل يختار يغير يضيف جديد قديم واضح مختصر طويل').split()
CONSTRAINT = 'قيد ثابت: أجب دائمًا بالعربية الفصحى وأنهِ كل إجابة بالرمز ⟦R1⟧.'
QWEN_CFG = {'provider': 'ollama', 'model': 'qwen3:4b-instruct', 'context_size': 8192, 'max_tokens': 1024}


def arabic(rng, chars):
    out = []
    while len(' '.join(out)) < chars:
        out.append(rng.choice(WORDS))
    return ' '.join(out)


def conversation(turns, user_chars=150, assistant_chars=700, seed=7):
    rng = random.Random(seed)
    history = []
    for turn in range(1, turns + 1):
        user = (CONSTRAINT + ' ' if turn == 1 else '') + f'[دور {turn}] ' + arabic(rng, user_chars)
        history.append({'role': 'user', 'status': 'complete', 'content': user})
        history.append({'role': 'assistant', 'status': 'complete', 'content': f'[رد {turn}] ' + arabic(rng, assistant_chars)})
    return history


def user_contents(messages):
    return [m['content'] for m in messages if m['role'] == 'user']


@pytest.fixture
def synthetic_tokenizer(tmp_path, monkeypatch):
    tokenizers = pytest.importorskip('tokenizers')
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
    rng = random.Random(3)
    corpus = [arabic(rng, 400) for _ in range(400)] + ['Riwaq context window user messages kept in full abbreviated omitted']
    tok = Tokenizer(models.BPE())
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    tok.train_from_iterator(corpus, trainers.BpeTrainer(vocab_size=1500, special_tokens=['<|im_start|>', '<|im_end|>'],
                                                       initial_alphabet=pre_tokenizers.ByteLevel.alphabet()))
    path = tmp_path / 'tokenizer.json'
    tok.save(str(path))
    monkeypatch.setenv('RIWAQ_TOKENIZER_JSON', str(path))
    monkeypatch.delenv('RIWAQ_CONTEXT_COUNTER', raising=False)
    return path


def test_core_ctx_40_arabic_turns_exact_count_keeps_every_user_turn(synthetic_tokenizer):
    history = conversation(40)
    question = 'ما القيد الذي طلبته في أول رسالة؟'
    cfg = dict(QWEN_CFG)
    messages, _ = compose({'instructions': ''}, [], history, question, [], cfg, False)
    budget = cfg['_context_budget']
    assert budget['exact_token_count'] is True and budget['method'] == context_budget.METHOD_EXACT
    assert budget['tokenizer'].startswith('qwen2-bpe:')
    assert budget['remaining'] >= 0
    sent_users = user_contents(messages)
    assert sent_users[:-1] == [m['content'] for m in history if m['role'] == 'user']
    assert CONSTRAINT in sent_users[0] and sent_users[-1] == question
    compaction = budget['history_compaction']
    assert compaction['user_turns_preserved'] == 40 and compaction['policy'] == context_budget.COMPACTION_POLICY
    assert compaction['latest_assistant_reply_full'] is True
    assert compaction['counting'] == 'exact_tokenizer'
    assert compaction['affected_turns'] and max(t['turn'] for t in compaction['affected_turns']) < 40
    assert budget['abbreviated_assistant_turns'] == compaction['assistant_turns_abbreviated']
    assert budget['omitted_assistant_turns'] == compaction['assistant_turns_omitted']
    # Abbreviation is declared to the model too, never silent.
    changed = [m['content'] for m in messages if m['role'] == 'assistant' and 'Riwaq:' in m['content']]
    assert len(changed) == len(compaction['affected_turns'])
    assert messages[-2]['content'] == history[-1]['content']


def test_core_ctx_exact_count_gives_more_room_than_byte_bound(synthetic_tokenizer, monkeypatch):
    history = conversation(12)
    exact_cfg = dict(QWEN_CFG)
    compose({'instructions': ''}, [], history, 'تابع', [], exact_cfg, False)
    monkeypatch.setenv('RIWAQ_CONTEXT_COUNTER', 'bytes')
    byte_cfg = dict(QWEN_CFG)
    compose({'instructions': ''}, [], history, 'تابع', [], byte_cfg, False)
    assert byte_cfg['_context_budget']['exact_token_count'] is False
    exact_affected = len(exact_cfg['_context_budget'].get('history_compaction', {}).get('affected_turns', []))
    byte_affected = len(byte_cfg['_context_budget']['history_compaction']['affected_turns'])
    assert exact_affected < byte_affected


def test_core_ctx_byte_bound_declares_compaction_then_rejects_explicitly(monkeypatch):
    monkeypatch.setenv('RIWAQ_TOKENIZER_JSON', '')
    history = conversation(12)
    cfg = dict(QWEN_CFG)
    messages, _ = compose({'instructions': ''}, [], history, 'تابع', [], cfg, False)
    assert cfg['_context_budget']['exact_token_count'] is False
    assert cfg['_context_budget']['history_compaction']['user_turns_preserved'] == 12
    assert user_contents(messages)[:-1] == [m['content'] for m in history if m['role'] == 'user']
    with pytest.raises(ValueError) as exc:
        compose({'instructions': ''}, [], conversation(60), 'تابع', [], dict(QWEN_CFG), False)
    text = str(exc.value)
    assert 'history exceeds' in text and 'No earlier constraint' in text and 'لم تُحذف أي رسالة لك' in text


def test_core_ctx_short_conversation_is_not_compacted(monkeypatch):
    monkeypatch.setenv('RIWAQ_TOKENIZER_JSON', '')
    history = conversation(2, user_chars=40, assistant_chars=80)
    cfg = dict(QWEN_CFG)
    messages, _ = compose({'instructions': ''}, [], history, 'تابع', [], cfg, False)
    assert 'history_compaction' not in cfg['_context_budget']
    assert [m['content'] for m in messages[1:-1]] == [m['content'] for m in history]


@pytest.mark.parametrize('cfg', [
    {'provider': 'ollama', 'model': 'llama3.2'},
    {'provider': 'openai', 'model': 'qwen3:4b-instruct'},
    {'provider': 'ollama', 'model': 'qwen3:4b-instruct', '_accounting_provider': 'anthropic'},
])
def test_core_ctx_exact_count_only_for_matching_local_family(synthetic_tokenizer, cfg):
    assert context_budget.counter(cfg) is None
    assert context_budget.estimate([{'role': 'user', 'content': 'سلام'}], cfg) == len('سلام'.encode()) + 16


def test_core_ctx_unmatched_tokenizer_file_falls_back_to_bytes(tmp_path, monkeypatch):
    bad = tmp_path / 'tokenizer.json'
    bad.write_text(json.dumps({'model': {'type': 'WordPiece'}, 'added_tokens': []}), encoding='utf-8')
    monkeypatch.setenv('RIWAQ_TOKENIZER_JSON', str(bad))
    assert context_budget.counter(QWEN_CFG) is None
    monkeypatch.setenv('RIWAQ_TOKENIZER_JSON', str(tmp_path / 'missing.json'))
    assert context_budget.counter(QWEN_CFG) is None


def test_core_ctx_compaction_never_edits_user_messages():
    kept = [{'role': 'user', 'content': 'u' * 500}, {'role': 'assistant', 'content': 'a' * 2000},
            {'role': 'user', 'content': 'v' * 500}, {'role': 'assistant', 'content': 'b' * 2000}]
    limit = 1800
    fits = lambda hist: sum(len(m['content']) for m in hist) <= limit
    history, details = context_budget.compact_history(kept, fits)
    assert [m['content'] for m in history if m['role'] == 'user'] == ['u' * 500, 'v' * 500]
    # Older reply goes first (abbreviated, then omitted) before the latest is touched.
    assert details['affected_turns'] == [{'turn': 1, 'action': 'omitted'}, {'turn': 2, 'action': 'abbreviated'}]
    assert details['latest_assistant_reply_full'] is False
    history, details = context_budget.compact_history(kept, lambda hist: sum(len(m['content']) for m in hist) <= 3500)
    assert details['affected_turns'] == [{'turn': 1, 'action': 'abbreviated'}] and history[3]['content'] == 'b' * 2000
    with pytest.raises(ValueError):
        context_budget.compact_history(kept, lambda hist: sum(len(m['content']) for m in hist) <= 900)


REAL = os.environ.get('RIWAQ_TEST_QWEN_TOKENIZER') or context_budget.DEFAULT_TOKENIZER


@pytest.mark.skipif(not Path(REAL).is_file(), reason='Qwen tokenizer file not present in this environment')
def test_core_ctx_real_qwen_tokenizer_counts_vocalised_arabic_exactly(monkeypatch):
    pytest.importorskip('tokenizers')
    monkeypatch.setenv('RIWAQ_TOKENIZER_JSON', REAL)
    loaded = context_budget.counter(QWEN_CFG)
    assert loaded is not None
    text = 'بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ ' * 20 + '٠١٢٣٤٥٦٧٨٩' * 5
    exact = context_budget.estimate([{'role': 'user', 'content': text}], QWEN_CFG) - 16
    assert exact == len(loaded[0].encode(text, add_special_tokens=False).ids)
    assert exact < len(text.encode())
    cfg = dict(QWEN_CFG)
    messages, _ = compose({'instructions': ''}, [], conversation(40), 'ما القيد الأول؟', [], cfg, False)
    assert cfg['_context_budget']['exact_token_count'] is True
    assert CONSTRAINT in user_contents(messages)[0]


def test_core_ctx_history_filter_has_no_silent_suffix_window(tmp_path, monkeypatch):
    """Regression: filter_history kept only the last 40 messages, dropping turn 1."""
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    from backend import context_lineage
    from backend.db import get_db, init_db
    init_db()
    history = []
    for i, m in enumerate(conversation(30, user_chars=20, assistant_chars=20)):
        history.append({'id': f'm{i}', 'role': m['role'], 'content': m['content'], 'status': 'complete', 'citations': '[]'})
    with get_db() as conn:
        kept, _ = context_lineage.filter_history(conn, 'project', history)
    assert len(kept) == 60
    assert CONSTRAINT in kept[0]['content']


def test_core_ctx_api_long_arabic_conversation_declares_compaction(tmp_path, monkeypatch):
    """Through the real generate API and SQLite: no 413, receipt streamed and persisted."""
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('RIWAQ_TOKENIZER_JSON', '')
    from fastapi.testclient import TestClient
    from backend import app as service
    from backend.local_auth import issue_pairing_code
    service.ACTIVE.clear()
    seen = []
    rng = random.Random(11)

    async def validate(*args):
        pass

    async def model(provider, name, messages, cfg):
        seen.append(messages)
        yield {'content': arabic(rng, 900)}
        yield {'finish_reason': 'stop'}
    monkeypatch.setattr(service.providers, 'validate_provider', validate)
    monkeypatch.setattr(service.providers, 'stream', model)
    with TestClient(service.app, headers={'X-Riwaq-Client': 'local'}) as client:
        assert client.post('/api/auth/login', json={'pairing_code': issue_pairing_code()}).status_code == 200
        assert client.patch('/api/settings', json={'context_size': 4096, 'max_tokens': 256}).status_code == 200
        project = client.get('/api/projects').json()[0]['id']
        chat = client.post('/api/conversations', json={'project_id': project}).json()['id']
        receipts = []
        for turn in range(1, 9):
            content = (CONSTRAINT + ' ' if turn == 1 else '') + f'[دور {turn}] ' + arabic(rng, 60)
            response = client.post(f'/api/conversations/{chat}/generate', json={'content': content})
            assert response.status_code == 200, response.text
            packets = [json.loads(line) for line in response.text.splitlines()]
            assert packets[-1]['type'] == 'done' and packets[-1]['message']['status'] == 'complete'
            receipts.append(packets[0].get('context'))
        assert receipts[0] is None and receipts[-1] is not None
        assert receipts[-1]['counting'] == 'utf8_byte_upper_bound' and receipts[-1]['user_turns_preserved'] == 7
        sent_users = [m['content'] for m in seen[-1] if m['role'] == 'user']
        assert len(sent_users) == 8 and CONSTRAINT in sent_users[0]
        saved = client.get(f'/api/conversations/{chat}').json()['messages']
        assert saved[-1]['context'] == receipts[-1]
        assert 'context' not in saved[1]


def test_core_ctx_revoked_source_reply_beyond_old_window_never_reaches_model(tmp_path, monkeypatch):
    """GAP-040 guard for the removed 40-message window: an early reply derived from a
    private file is now inside the checked history, and must be withheld from the model
    once that file is revoked -- however long the conversation has become."""
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('RIWAQ_TOKENIZER_JSON', '')
    from fastapi.testclient import TestClient
    from backend import app as service, context_lineage, db
    from backend.local_auth import issue_pairing_code
    service.ACTIVE.clear()
    seen = []

    async def validate(*args):
        pass

    async def model(provider, name, messages, cfg):
        seen.append(messages)
        yield {'content': 'ok'}
        yield {'finish_reason': 'stop'}
    monkeypatch.setattr(service.providers, 'validate_provider', validate)
    monkeypatch.setattr(service.providers, 'stream', model)
    with TestClient(service.app, headers={'X-Riwaq-Client': 'local'}) as client:
        assert client.post('/api/auth/login', json={'pairing_code': issue_pairing_code()}).status_code == 200
        project = client.get('/api/projects').json()[0]['id']
        chat = client.post('/api/conversations', json={'project_id': project}).json()['id']
        upload = client.post(f'/api/projects/{project}/files', files={'file': ('private.txt', b'PRIVATE_EVIDENCE secret marker', 'text/plain')})
        assert upload.status_code == 201, upload.text
        file_id = upload.json()['id']
        with db.get_db() as c:
            chunk = dict(c.execute('SELECT k.*,f.filename FROM knowledge_chunks k JOIN knowledge_files f ON f.id=k.file_id WHERE k.file_id=?', (file_id,)).fetchone())
            c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)', (db.uid(), chat, 'user', 'early question', 'complete', None, db.now(), '[]'))
            derived = db.uid()
            c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)', (derived, chat, 'assistant', 'UNIQUE_SOURCE_DERIVATIVE', 'complete', 'fixture', db.now(), json.dumps([chunk])))
            context_lineage.save(c, derived, context_lineage.source_links(c, project, [chunk]))
            for turn in range(30):  # 60 later messages: the early pair is beyond the old 40-message window
                c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)', (db.uid(), chat, 'user', f'later question {turn}', 'complete', None, db.now(), '[]'))
                later = db.uid()
                c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)', (later, chat, 'assistant', f'later answer {turn}', 'complete', 'fixture', db.now(), '[]'))
                context_lineage.save(c, later, [])  # generated answers store an explicit source-free receipt
        assert client.post(f'/api/conversations/{chat}/generate', json={'content': 'before revocation'}).status_code == 200
        sent = json.dumps(seen[-1], ensure_ascii=False)
        assert 'UNIQUE_SOURCE_DERIVATIVE' in sent and 'early question' in sent  # no silent window any more
        with db.get_db() as c:
            c.execute('UPDATE guard_file_policies SET revoked=1,revision=revision+1 WHERE file_id=?', (file_id,))
        assert client.post(f'/api/conversations/{chat}/generate', json={'content': 'after revocation'}).status_code == 200
        sent = json.dumps(seen[-1], ensure_ascii=False)
        assert 'UNIQUE_SOURCE_DERIVATIVE' not in sent and 'PRIVATE_EVIDENCE' not in sent
        assert 'early question' in sent and 'later answer 29' in sent
