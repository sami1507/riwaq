"""Consistent SQLite backup of this device's data; refuses overwriting files."""
import argparse
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.guard_backup import _safe_path,inspect_database
parser=argparse.ArgumentParser()
parser.add_argument('--source',default=str(ROOT/'data/riwaq.sqlite3'))
parser.add_argument('--output',required=True)
args=parser.parse_args()
source=_safe_path(args.source,existing=True);destination=_safe_path(args.output)
if not source.is_file():raise SystemExit('Source database does not exist')
if destination.exists() or source==destination:raise SystemExit('Choose a new backup filename; existing data is never overwritten')
destination.parent.mkdir(parents=True,exist_ok=True)
with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as origin:
    with sqlite3.connect(destination) as backup:
        origin.backup(backup)
        inspect_database(backup)
print('Verified local backup:',destination)
print('Unencrypted local database copy, including persisted blobs; external workspaces, operator configuration and OS-keyring secrets are excluded. Prefer the DPAPI recovery tool on Windows. This export is not a tested production restore.')
