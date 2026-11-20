"""Run a short Python snippet from an ordinary chat question instead of letting the model guess its output.

Enabled by the user on 2026-11-20. Scope is deliberately small: a question that asks what a single fenced Python
block prints. The snippet runs only through the existing isolated worker (pinned image, no network, no host mounts,
bounded time and memory). There is no host fallback: if the worker is not configured or Docker is not running, nothing
is executed and the answer is marked as an unverified prediction. The measured output, not the model's wording, is what
the user is shown.
"""
import asyncio
import json
import os
import re

from backend.db import data_dir

MAX_CODE, MAX_SHOWN = 4000, 4000
TIMEOUT_SECONDS, MEMORY_MB = 15, 256
_BEGIN, _END = '<<RIWAQ-RUN>>', '<<RIWAQ-END>>'
_INTENT = re.compile(r'يطبع|مخرجات|مخرج|ناتج|نتيجه تشغيل|نتيجه تنفيذ|what does .{0,60}print|output of|what is the output|what will .{0,40}print', re.I | re.S)
_BLOCK = re.compile(r'```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)```', re.S)
_HARNESS = '''import contextlib, io, json, runpy, sys, unittest


class Run(unittest.TestCase):
    def test_run(self):
        out = io.StringIO()
        status = 'ok'
        try:
            with contextlib.redirect_stdout(out):
                runpy.run_path('snippet.py', run_name='__main__')
        except SystemExit:
            status = 'exit'
        except BaseException as exc:
            status = 'error: ' + type(exc).__name__ + (': ' + str(exc)[:300] if str(exc) else '')
        sys.__stdout__.write(%r + json.dumps({'status': status, 'stdout': out.getvalue()[:%d]}) + %r)
        sys.__stdout__.flush()
''' % (_BEGIN, MAX_SHOWN, _END)
RAN = 'الناتج الفعلي (شغّله رواق في حاوية معزولة بلا شبكة ولا ملفات):'
NOT_RUN = '⟦تنبيه من رواق: لم يُشغَّل هذا الكود، فالناتج المذكور توقّع غير متحقَّق منه.⟧'


def _fold(text):
    return re.sub('[أإآ]', 'ا', text or '').replace('ة', 'ه')


def extract(question):
    """The Python code of a question that asks what one fenced block prints, else None."""
    if os.environ.get('RIWAQ_CHAT_CODE_RUN', '1') == '0':
        return None
    blocks = _BLOCK.findall(question or '')
    if len(blocks) != 1:
        return None
    language, code = blocks[0][0].lower(), blocks[0][1]
    outside = _BLOCK.sub(' ', question)
    if not _INTENT.search(_fold(outside)):
        return None
    if language not in ('python', 'py', 'python3') and not (language == '' and re.search(r'بايثون|python', outside, re.I)):
        return None
    return code if code.strip() and len(code) <= MAX_CODE else None


def run(code, authorize=lambda: True):
    """Execute in the isolated worker. Returns {'ran': True, 'status', 'stdout'} or {'ran': False, 'reason'}; never runs on the host."""
    from backend import isolated_worker
    try:
        config = isolated_worker.load_config(data_dir() / 'isolated-workers.json')
        if 'python_unittest' not in config.images:
            return {'ran': False, 'reason': 'python_image_unconfigured'}
    except (OSError, ValueError, isolated_worker.IsolatedError) as exc:
        return {'ran': False, 'reason': getattr(exc, 'code', None) or 'isolation_unconfigured'}
    receipt = isolated_worker.execute({'snippet.py': code.encode('utf-8'), 'riwaq_run_test.py': _HARNESS.encode('utf-8')}, 'python_unittest', ['riwaq_run_test.py'],
                                      config=config, authorize=authorize, timeout_seconds=TIMEOUT_SECONDS, memory_mb=MEMORY_MB)
    if receipt.get('status') == 'timeout':
        return {'ran': True, 'status': f'stopped: exceeded {TIMEOUT_SECONDS} seconds', 'stdout': '', 'seconds': receipt.get('seconds')}
    if receipt.get('status') == 'resource_limit':
        return {'ran': True, 'status': f'stopped: exceeded {MEMORY_MB} MB of memory', 'stdout': '', 'seconds': receipt.get('seconds')}
    trusted = receipt.get('executed') is True and receipt.get('policy_inspected') is True and receipt.get('cleanup_verified') is True and receipt.get('host_fallback') is False
    found = re.search(re.escape(_BEGIN) + r'(\{.*\})' + re.escape(_END), receipt.get('stdout') or '', re.S)
    if not trusted or not found:
        return {'ran': False, 'reason': receipt.get('reason_code') or receipt.get('status') or 'no_result'}
    try:
        data = json.loads(found.group(1))
        return {'ran': True, 'status': str(data['status'])[:400], 'stdout': str(data['stdout'])[:MAX_SHOWN], 'seconds': receipt.get('seconds')}
    except (ValueError, KeyError, TypeError):
        return {'ran': False, 'reason': 'result_unreadable'}


async def check(question, authorize=lambda: True):
    """None when the question is not a code-output question; otherwise the run result."""
    code = extract(question)
    if code is None:
        return None
    try:
        return await asyncio.to_thread(run, code, authorize)
    except Exception:
        return {'ran': False, 'reason': 'runner_error'}


def context(result):
    """Application check data for the request."""
    if result is None:
        return {}
    if result['ran']:
        return {'code_run': {'rule': 'The application executed this code in an isolated container. The output below is the measured result: state it exactly and do not predict a different one.',
                             'program_status': result['status'], 'stdout': result['stdout']}}
    return {'code_run': {'rule': 'The application could NOT execute this code. Say clearly that any output you state is a prediction that was not verified by running it.',
                         'executed': False}}


def finalize(answer, result):
    """Make the measured output (or the fact that nothing ran) visible whatever the model wrote."""
    if result is None:
        return answer
    if not result['ran']:
        return answer.rstrip() + '\n\n' + NOT_RUN
    shown = result['stdout'].rstrip('\n')
    body = shown if shown else '(لا شيء مطبوع)'
    extra = '' if result['status'] == 'ok' else f"\nحالة التشغيل: {result['status']}"
    lines = [l.strip() for l in answer.replace('`', ' ').splitlines()]
    if result['status'] == 'ok' and shown and all(l.strip() in lines for l in shown.splitlines()) and RAN not in answer:
        return answer.rstrip() + '\n\n' + '⟦تحقّق رواق: طابق هذا الناتج تشغيلًا فعليًا في حاوية معزولة.⟧'
    return answer.rstrip() + '\n\n' + RAN + '\n```\n' + body + '\n```' + extra


async def guard_stream(stream, result):
    held, rest = [], []
    async for packet in stream:
        if packet.get('content'):
            held.append(packet['content'])
            packet = {k: v for k, v in packet.items() if k != 'content'}
        if packet:
            rest.append(packet)
    text = finalize(''.join(held), result)
    if text:
        yield {'content': text}
    for packet in rest:
        yield packet
