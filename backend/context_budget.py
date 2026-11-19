"""Explicit text-context accounting; never a silent truncation.

Two counting methods, recorded in every receipt:

* ``model_tokenizer_count_plus_message_overhead_v2`` -- exact text-token counts
  from the model family's own tokenizer file, used only when that file is
  configured, loads, and declares the expected chat-template tokens.  Template
  and control tokens are still covered by the per-message overhead and the
  fixed reserve below.
* ``utf8_bytes_plus_message_overhead_estimate_v1`` -- UTF-8 bytes, a strict
  upper bound for byte-level BPE tokenizers.  It over-counts Arabic roughly
  four times, so long Arabic conversations reach the limit early; it remains
  the fallback whenever the exact tokenizer is unavailable or unmatched.

Measured on the Qwen2/Qwen3 tokenizer: fully vocalised Arabic (Quranic text,
tashkeel), Arabic-Indic digits and random letter sequences can cost one to two
tokens per character, so a per-script ratio estimate is not a safe bound.  That
is why only an exact count may replace the byte bound.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from pathlib import Path

METHOD_BYTES = 'utf8_bytes_plus_message_overhead_estimate_v1'
METHOD_EXACT = 'model_tokenizer_count_plus_message_overhead_v2'
METHOD = METHOD_BYTES  # kept for callers that imported the original name
TEMPLATE_RESERVE = 512
MESSAGE_OVERHEAD = 16
DEFAULT_TOKENIZER = 'D:/RiwaqRuntime/training-v1/reconstructed-base/tokenizer.json'
# Model-name prefixes served by the Qwen2 byte-level BPE vocabulary.  Anything
# else (other local models, cloud providers) keeps the byte upper bound.
QWEN_FAMILY_PREFIXES = ('qwen3', 'qwen2.5', 'qwen2')
_LOADED: dict[tuple, object] = {}
_COUNTS: OrderedDict = OrderedDict()
_COUNT_CACHE_LIMIT = 4096


def _tokenizer_file():
    value = os.environ.get('RIWAQ_TOKENIZER_JSON', DEFAULT_TOKENIZER)
    return Path(value) if value else None


def _load(path: Path):
    """Return (tokenizer, fingerprint) or None; failures fall back to bytes."""
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key in _LOADED:
        return _LOADED[key]
    loaded = None
    try:
        from tokenizers import Tokenizer
        raw = path.read_bytes()
        spec = json.loads(raw)
        added = {t.get('content') for t in spec.get('added_tokens', []) if isinstance(t, dict)}
        pre = json.dumps(spec.get('pre_tokenizer') or {})
        if (spec.get('model') or {}).get('type') == 'BPE' and {'<|im_start|>', '<|im_end|>'} <= added and 'ByteLevel' in pre:
            loaded = (Tokenizer.from_str(raw.decode('utf-8')), 'qwen2-bpe:' + hashlib.sha256(raw).hexdigest()[:12])
    except Exception:
        loaded = None
    _LOADED.clear()
    _LOADED[key] = loaded
    return loaded


def counter(config=None):
    """Exact counter for the model that will receive the messages, if known."""
    if not isinstance(config, dict) or os.environ.get('RIWAQ_CONTEXT_COUNTER', '').lower() == 'bytes':
        return None
    provider = config.get('_accounting_provider', config.get('provider'))
    model = str(config.get('_accounting_model', config.get('model')) or '').lower()
    if provider != 'ollama' or not model.startswith(QWEN_FAMILY_PREFIXES):
        return None
    path = _tokenizer_file()
    return _load(path) if path else None


def _count(loaded, text):
    tokenizer, fingerprint = loaded
    key = (fingerprint, text)
    if key in _COUNTS:
        _COUNTS.move_to_end(key)
        return _COUNTS[key]
    value = len(tokenizer.encode(text, add_special_tokens=False).ids)
    _COUNTS[key] = value
    if len(_COUNTS) > _COUNT_CACHE_LIMIT:
        _COUNTS.popitem(last=False)
    return value


def estimate(messages, config=None):
    if not isinstance(messages, list) or any(not isinstance(m, dict) or not isinstance(m.get('content'), str) for m in messages):
        raise ValueError('text_context_messages_required')
    loaded = counter(config)
    if loaded is None:
        return sum(len(m['content'].encode('utf-8')) + MESSAGE_OVERHEAD for m in messages)
    return sum(_count(loaded, m['content']) + MESSAGE_OVERHEAD for m in messages)


def method(config=None):
    loaded = counter(config)
    return (METHOD_EXACT, loaded[1]) if loaded else (METHOD_BYTES, None)


def receipt(messages, config, *, omitted_assistant_turns=0, abbreviated_assistant_turns=0, source_candidates=0, selected_sources=0):
    capacity, output = config['context_size'], config['max_tokens']
    if type(capacity) is not int or type(output) is not int or output < 1 or capacity < output + TEMPLATE_RESERVE:
        raise ValueError('invalid_context_budget')
    used = estimate(messages, config)
    name, tokenizer = method(config)
    return {'method': name, 'exact_token_count': name == METHOD_EXACT,
            **({'tokenizer': tokenizer} if tokenizer else {}),
            'model_context_limit': capacity,
            'output_reserved': output, 'template_reserved': TEMPLATE_RESERVE,
            'estimated_input': used, 'remaining': capacity - output - TEMPLATE_RESERVE - used,
            'omitted_assistant_turns': omitted_assistant_turns,
            'abbreviated_assistant_turns': abbreviated_assistant_turns,
            'preserved_user_turns': sum(m.get('role') == 'user' for m in messages),
            'source_candidates': source_candidates, 'selected_sources': selected_sources,
            'source_coverage': 'selected_excerpts_only' if source_candidates else 'not_applicable'}


def enforce(messages, config):
    result = receipt(messages, config)
    if result['remaining'] < 0:
        raise ValueError('context_budget_exceeded_without_truncation')
    return result


# --- declared history compaction -------------------------------------------
COMPACTION_POLICY = 'preserve_user_turns_abbreviate_oldest_assistant_v1'
EXCERPT_CHARS = 280
ABBREVIATED_MARK = '\n[Riwaq: rest of this earlier reply abbreviated to fit context; user messages kept in full]'
OMITTED_MARK = '[Riwaq: earlier reply omitted to fit context; user messages kept in full]'


def compact_history(kept, fits):
    """Abbreviate earlier assistant replies, oldest first, until ``fits``.

    User messages are never changed or removed: they carry the constraints and
    corrections a later turn may depend on.  The latest assistant reply is
    touched only after every older one.  Returns ``(history, details)`` where
    details is None if nothing was needed, or raises ValueError if even
    omitting every assistant reply cannot fit the user turns.
    """
    if fits(kept):
        return kept, None
    history = [dict(m) for m in kept]
    assistant = [i for i, m in enumerate(history) if m['role'] == 'assistant']
    state = {i: 'full' for i in assistant}
    older, latest = assistant[:-1], assistant[-1:]
    # Older replies are abbreviated, then omitted, before the latest reply --
    # the one a short follow-up most often refers to -- is touched at all.
    plan = [('abbreviated', i) for i in older] + [('omitted', i) for i in older] + \
           [('abbreviated', i) for i in latest] + [('omitted', i) for i in latest]
    for stage, i in plan:
        original = kept[i]['content']
        if stage == 'abbreviated':
            if len(original) <= EXCERPT_CHARS + len(ABBREVIATED_MARK):
                continue
            history[i]['content'] = original[:EXCERPT_CHARS].rstrip() + ABBREVIATED_MARK
        else:
            if len(original.encode('utf-8')) <= len(OMITTED_MARK):
                continue
            history[i]['content'] = OMITTED_MARK
        state[i] = stage
        if fits(history):
            return history, _details(kept, state)
    raise ValueError('history_exceeds_after_compaction')


def _details(kept, state):
    def turn(i):
        return sum(1 for m in kept[:i + 1] if m['role'] == 'user')
    return {'policy': COMPACTION_POLICY,
            'history_messages': len(kept),
            'user_turns_preserved': sum(m['role'] == 'user' for m in kept),
            'assistant_turns': len(state),
            'assistant_turns_abbreviated': sum(v == 'abbreviated' for v in state.values()),
            'assistant_turns_omitted': sum(v == 'omitted' for v in state.values()),
            'affected_turns': [{'turn': turn(i), 'action': v} for i, v in sorted(state.items()) if v != 'full'],
            'latest_assistant_reply_full': not state or state[max(state)] == 'full'}
