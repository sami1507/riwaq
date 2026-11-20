"""A code-output question is answered by an isolated run or marked as unverified; nothing ever runs on the host."""
import asyncio
import json
import re
import subprocess
import sys

import pytest

from backend import code_run as c, isolated_worker, text_checks

ASK = 'ما الذي يطبعه هذا الكود بلغة بايثون؟ اكتب الناتج فقط.\n```python\nx = [1, 2, 3]\nprint(len(x))\n```'


def test_only_a_single_python_block_with_an_output_question_qualifies():
    assert c.extract(ASK) == 'x = [1, 2, 3]\nprint(len(x))\n'
    assert c.extract('ما ناتج هذا الكود بلغة بايثون؟\n```\nprint(2)\n```') == 'print(2)\n'
    for other in ['اشرح هذا الكود:\n```python\nprint(1)\n```', 'ما ناتج 5+5؟', 'ما الذي يطبعه؟\n```js\nconsole.log(1)\n```', 'ما الذي يطبعه؟\n```\nprint(1)\n```',
                  'ما الذي يطبعه كل منهما؟\n```python\nprint(1)\n```\n```python\nprint(2)\n```', 'ما الذي يطبعه؟\n```python\n' + 'x=1\n' * 2000 + '```']:
        assert c.extract(other) is None


def test_switch_turns_the_feature_off(monkeypatch):
    monkeypatch.setenv('RIWAQ_CHAT_CODE_RUN', '0')
    assert c.extract(ASK) is None


def test_harness_reports_output_and_errors_when_run_like_the_container_bootstrap(tmp_path):
    (tmp_path / 'snippet.py').write_text('print("ab" * 2 + "c")\nprint(10 // 3, 10 % 3)\n1 / 0\n', encoding='utf-8')
    (tmp_path / 'riwaq_run_test.py').write_text(c._HARNESS, encoding='utf-8')
    boot = f"import os,sys,unittest;os.chdir({str(tmp_path)!r});sys.path.insert(0,{str(tmp_path)!r});sys.argv=['unittest','riwaq_run_test.py'];unittest.main(module=None)"
    done = subprocess.run([sys.executable, '-I', '-B', '-c', boot], capture_output=True, text=True)
    data = json.loads(re.search(re.escape(c._BEGIN) + r'(\{.*\})' + re.escape(c._END), done.stdout, re.S).group(1))
    assert done.returncode == 0 and data == {'status': 'error: ZeroDivisionError: division by zero', 'stdout': 'ababc\n3 1\n'}


def receipt(**changes):
    base = {'status': 'completed', 'executed': True, 'policy_inspected': True, 'cleanup_verified': True, 'host_fallback': False, 'seconds': 1.2,
            'stdout': c._BEGIN + json.dumps({'status': 'ok', 'stdout': '3\n'}) + c._END, 'reason_code': 'runner_exited'}
    return base | changes


@pytest.fixture
def worker(monkeypatch):
    calls = []
    monkeypatch.setattr(isolated_worker, 'load_config', lambda path: type('Config', (), {'images': {'python_unittest': 'sha256:' + '0' * 64}})())
    def install(result):
        def execute(files, runner, targets, **options):
            calls.append((files, runner, targets, options)); return result
        monkeypatch.setattr(isolated_worker, 'execute', execute)
    return install, calls


def test_run_goes_only_through_the_isolated_worker_with_tight_limits(worker):
    install, calls = worker; install(receipt())
    assert c.run('print(3)') == {'ran': True, 'status': 'ok', 'stdout': '3\n', 'seconds': 1.2}
    files, runner, targets, options = calls[0]
    assert runner == 'python_unittest' and targets == ['riwaq_run_test.py'] and files['snippet.py'] == b'print(3)'
    assert options['timeout_seconds'] == 15 and options['memory_mb'] == 256


@pytest.mark.parametrize('changes', [{'executed': False, 'status': 'blocked', 'reason_code': 'container_create_failed', 'stdout': ''}, {'policy_inspected': False},
                                     {'cleanup_verified': False}, {'host_fallback': True}, {'stdout': 'no marker here'}])
def test_an_unverified_run_is_reported_as_not_run(worker, changes):
    install, _ = worker; install(receipt(**changes))
    assert c.run('print(3)')['ran'] is False


def test_missing_configuration_means_nothing_runs(monkeypatch):
    def missing(path): raise OSError('no file')
    monkeypatch.setattr(isolated_worker, 'load_config', missing)
    monkeypatch.setattr(isolated_worker, 'execute', lambda *a, **k: pytest.fail('must not execute'))
    assert c.run('print(3)') == {'ran': False, 'reason': 'isolation_unconfigured'}


def test_timeout_is_reported_as_a_stopped_run(worker):
    install, _ = worker; install(receipt(status='timeout', stdout=''))
    assert c.run('while True: pass')['status'] == 'stopped: exceeded 15 seconds'


def test_the_measured_output_is_shown_whatever_the_model_wrote():
    ran = {'ran': True, 'status': 'ok', 'stdout': '3\n'}
    wrong = c.finalize('الناتج هو 4', ran)
    assert wrong.startswith('الناتج هو 4') and wrong.endswith(c.RAN + '\n```\n3\n```')
    right = c.finalize('3', ran)
    assert right.startswith('3\n\n⟦تحقّق رواق') and c.RAN not in right
    failed = c.finalize('يطبع 5', {'ran': True, 'status': 'error: ZeroDivisionError: division by zero', 'stdout': ''})
    assert '(لا شيء مطبوع)' in failed and 'حالة التشغيل: error: ZeroDivisionError' in failed
    assert c.finalize('الناتج 4', {'ran': False, 'reason': 'blocked'}).endswith(c.NOT_RUN)
    assert c.finalize('نص', None) == 'نص'


def test_check_data_tells_the_model_what_was_measured_or_that_nothing_ran():
    data = json.loads(text_checks.context(ASK, c.context({'ran': True, 'status': 'ok', 'stdout': '3\n'})))
    assert data['code_run']['stdout'] == '3\n' and 'measured result' in data['code_run']['rule']
    assert c.context({'ran': False, 'reason': 'blocked'})['code_run']['executed'] is False and c.context(None) == {}


def test_guard_stream_appends_the_result_once_and_keeps_the_finish_packet(worker):
    async def model():
        yield {'content': 'الناتج '}
        yield {'content': 'هو 4'}
        yield {'finish_reason': 'stop'}
    async def collect():
        return [p async for p in c.guard_stream(model(), {'ran': True, 'status': 'ok', 'stdout': '3\n'})]
    packets = asyncio.run(collect())
    assert packets == [{'content': 'الناتج هو 4\n\n' + c.RAN + '\n```\n3\n```'}, {'finish_reason': 'stop'}]
