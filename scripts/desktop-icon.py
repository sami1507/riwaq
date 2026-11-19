"""Compatibility entry point for the canonical Riwaq SVG icon exporter.

Requires existing Node, project Playwright and installed Chrome. This script
never installs packages, fetches fonts, or draws a separate desktop mark.
"""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    node = shutil.which('node')
    if not node:
        raise SystemExit('Node is unavailable to this process; use the existing project Node installation.')
    return subprocess.run([node, str(ROOT / 'scripts' / 'identity_icon_export.mjs')], cwd=ROOT, shell=False).returncode

if __name__ == '__main__':
    sys.exit(main())
