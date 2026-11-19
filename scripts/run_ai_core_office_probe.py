"""Bound the optional native Office fixture probe; never kill unrelated Office."""
from pathlib import Path
import json
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]

def run(kind, source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    source.relative_to(ROOT / 'reports'); output.relative_to(ROOT / 'reports')
    if output.exists() or source.is_symlink():
        raise ValueError('New report directory and an unlinked synthetic source required')
    started = time.monotonic()
    command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Sta', '-File',
               str(ROOT / 'scripts/probe_ai_core_office.ps1'), '-Kind', kind,
               '-InputPath', str(source), '-OutputDirectory', str(output)]
    try:
        environment = {key:value for key,value in os.environ.items() if key.lower() != 'psmodulepath'}
        # Windows PowerShell5 must resolve its own built-in modules rather than
        # inherit the hosting PowerShell7 runtime's module directory.
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=35, creationflags=subprocess.CREATE_NO_WINDOW, env=environment)
        execution = {'exit_code': result.returncode, 'timed_out': False,
                     'stderr': result.stderr.decode('utf-8', errors='replace')[:2500]}
    except subprocess.TimeoutExpired:
        execution = {'exit_code': None, 'timed_out': True,
                     'reason': 'Native Office COM did not finish within35 seconds; renderer not certified. No unrelated Office process was terminated.'}
    execution['seconds'] = round(time.monotonic() - started, 3)
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'receipt.json'
    receipt = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {'status': 'blocked', 'phase': 'no_native_receipt'}
    if execution['timed_out'] or execution['exit_code'] != 0:
        receipt.update(status='blocked', native_rendered=False)
    receipt['execution'] = execution
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return receipt

if __name__ == '__main__':
    print(json.dumps(run(*sys.argv[1:]), ensure_ascii=False))
