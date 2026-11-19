"""One real Qwen/SAPI Chrome flow on an isolated paired scratch service."""
import argparse
import json
from pathlib import Path
import subprocess
from scratch_smoke import ScratchService,ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);args=parser.parse_args()
    output=Path(args.output).resolve()
    if output.exists():parser.error('Output must be new')
    with ScratchService() as service:
        from backend.local_auth import issue_pairing_code
        data={'base':'http://127.0.0.1:18765','code':issue_pairing_code(),'output':str(output)}
        process=subprocess.run(['node',str(ROOT/'frontend/scripts/voice-parts-ui.mjs')],input=json.dumps(data),capture_output=True,text=True,timeout=240,cwd=ROOT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        data.clear()
        print(process.stdout.strip())
        if process.returncode and process.stderr:print('Browser test failed; see output/report.json')
        return process.returncode


if __name__=='__main__':raise SystemExit(main())
