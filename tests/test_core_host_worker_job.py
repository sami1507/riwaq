"""GAP-003: the Windows Job Object around host-side model workers.

Kill-on-close is mandatory (existing behaviour). User-interface restrictions are added when
Windows accepts them. This is still a limited host process, not operating-system isolation:
it does not restrict files or the network."""
import os
import subprocess
import sys

import pytest

from backend import visual_runner

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows Job Objects only')

CHILD = r'''
import ctypes, sys
sys.stdin.readline()                      # wait until the parent attached the job
user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.CreateDesktopW.restype = ctypes.c_void_p
desktop = user32.CreateDesktopW('riwaq-job-probe-%d' % __import__('os').getpid(), None, None, 0, 0x10000000, None)
if desktop: user32.CloseDesktop(ctypes.c_void_p(desktop))
opened = bool(user32.OpenClipboard(None))
user32.GetClipboardData.restype = ctypes.c_void_p
data = user32.GetClipboardData(13) if opened else None   # read-only probe; nothing is written
if opened: user32.CloseClipboard()
print('desktop_created=%s clipboard_data=%s' % (bool(desktop), bool(data)))
'''


def _run_in_job():
    proc = subprocess.Popen([sys.executable, '-c', CHILD], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    close = visual_runner.windows_job(proc)
    try:
        out, _ = proc.communicate('go\n', timeout=30)
    finally:
        close()
    return out.strip()


def test_core_host_worker_job_applies_ui_restrictions_and_records_the_outcome():
    out = _run_in_job()
    assert visual_runner.LAST_JOB['ui_restricted'] in (True, False)
    if visual_runner.LAST_JOB['ui_restricted'] is False:
        pytest.skip('Windows refused user-interface restrictions for this (nested) job; kill-on-close still applied: ' + out)
    assert 'desktop_created=False' in out and 'clipboard_data=False' in out, out


def test_core_host_worker_job_kills_the_worker_when_closed():
    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    close = visual_runner.windows_job(proc)
    close()
    proc.wait(timeout=10)  # raises if the 60-second sleeper outlives its job; Windows reports exit code 0 for a job kill
    assert proc.poll() is not None


def test_core_real_voice_worker_still_runs_inside_the_restricted_job(tmp_path):
    """The real fixed speech worker (Windows voices, local) must keep working under the
    restrictions: list the installed voices, then synthesize one short sentence."""
    import threading
    from backend import voice_audio
    listed = voice_audio.discover_voices(tmp_path / 'voices', threading.Event())
    voices = listed.get('voices') or []
    if not voices:
        pytest.skip('no installed Windows voice to synthesize with: ' + str(listed)[:200])
    voice = voices[0]
    result = voice_audio.synthesize('Riwaq job check.', voice['id'], 'en-US', 0, 100, tmp_path / 'tts', threading.Event())
    assert result['audio']['bytes'] > 1000 and (tmp_path / 'tts' / 'speech.wav').is_file(), result
    assert visual_runner.LAST_JOB['ui_restricted'] in (True, False)
    print('ui_restricted for the real worker:', visual_runner.LAST_JOB['ui_restricted'], 'voice:', voice.get('id'))
