"""Actual browser conflict review on a new isolated authenticated service."""
import argparse
import json
from pathlib import Path
import subprocess
from scratch_smoke import ScratchService, ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(); output = Path(args.output).resolve()
    if output.exists(): parser.error('Output must be new')
    with ScratchService():
        from backend.local_auth import issue_pairing_code
        settings = {'base':'http://127.0.0.1:18765','code':issue_pairing_code(),'output':str(output)}
        result = subprocess.run(['node', str(ROOT/'frontend/scripts/learning-conflicts-ui.mjs')],
            input=json.dumps(settings), text=True, capture_output=True, timeout=150, cwd=ROOT,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        settings.clear()
        print(result.stdout.strip())
        if result.returncode: print('Browser flow failed; inspect the redacted output report.')
        return result.returncode


if __name__ == '__main__': raise SystemExit(main())
