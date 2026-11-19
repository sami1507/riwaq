"""Measure a synthetic Windows DPAPI drill; never opens an existing user DB."""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


@contextmanager
def synthetic_environment(path):
    updates={'RIWAQ_DATA_DIR':str(path),'RIWAQ_KNOWLEDGE_REGISTRY':str(path/'absent-registry.json')}
    before={key:os.environ.get(key) for key in updates};os.environ.update(updates)
    try:yield
    finally:
        for key,value in before.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value


def run():
    from backend import db, guard_backup, local_auth
    started=time.monotonic();checks={}
    with tempfile.TemporaryDirectory(prefix='riwaq-recovery-drill-') as folder:
        folder=Path(folder);live=folder/'live'
        with synthetic_environment(live):
            db.init_db()
            with db.get_db() as c:
                pid=c.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
                for fid in ('retained','withdrawn'):
                    raw=('SYNTHETIC_ORIGINAL_'+fid).encode()
                    c.execute('INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)',(fid,pid,fid+'.txt',len(raw),hashlib.sha256(raw).hexdigest(),1,1,len(raw),db.now()))
                    c.execute('INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?)',(fid+'chunk',fid,pid,1,1,1,raw.decode()))
                    c.execute('INSERT INTO document_originals VALUES(?,?,?,?,?,?,?)',(fid,raw,'text/plain',1,'{}','{}','complete'))
                c.execute("INSERT INTO memories VALUES('forgotten','preference',NULL,'SYNTHETIC_FORGOTTEN_DRILL','now')")
            backup=folder/'sealed.rwb';seal=guard_backup.seal(backup)
            with db.get_db() as c:
                c.execute("DELETE FROM memories WHERE id='forgotten'")
                c.execute("DELETE FROM knowledge_files WHERE id='withdrawn'")
                c.execute("INSERT INTO memories VALUES('post-snapshot','project',?,'SYNTHETIC_NEWER_NOT_RECOVERED',?)",(pid,db.now()))
            source_hash=hashlib.sha256((live/'riwaq.sqlite3').read_bytes()).hexdigest()
            backup_hash=hashlib.sha256(backup.read_bytes()).hexdigest()
            recovered=folder/'recovered';report=guard_backup.recover(backup,recovered,reconcile_current=True)
            with closing(sqlite3.connect(recovered/'riwaq.sqlite3')) as c:
                raw=c.execute("SELECT original FROM document_originals WHERE file_id='retained'").fetchone()[0]
                checks['retained_original_reopened']=raw==b'SYNTHETIC_ORIGINAL_retained'
                checks['withdrawn_source_removed']=not c.execute("SELECT 1 FROM knowledge_files WHERE id='withdrawn'").fetchone()
                checks['forgotten_preference_removed']=not c.execute("SELECT 1 FROM memories WHERE id='forgotten'").fetchone()
                checks['newer_write_not_claimed_recovered']=not c.execute("SELECT 1 FROM memories WHERE id='post-snapshot'").fetchone()
                checks['no_session_or_permit_restored']=c.execute('SELECT COUNT(*) FROM local_auth_sessions').fetchone()[0]==0 and c.execute('SELECT COUNT(*) FROM guard_permits').fetchone()[0]==0
                checks['all_file_grants_revoked']=not c.execute('SELECT 1 FROM guard_file_policies WHERE revoked=0').fetchone()
            content=(recovered/'riwaq.sqlite3').read_bytes()
            checks['deleted_bytes_removed_from_recovered_database']=b'SYNTHETIC_FORGOTTEN_DRILL' not in content and b'SYNTHETIC_ORIGINAL_withdrawn' not in content
            checks['quarantine_marker_present']=(recovered/'RECOVERY_QUARANTINE').is_file()
            checks['no_auth_key_copied']=not (recovered/'local-auth').exists()
            damaged=folder/'tampered.rwb';data=bytearray(backup.read_bytes());data[-20]^=1;damaged.write_bytes(data)
            try:guard_backup.recover(damaged,folder/'tampered-output')
            except Exception:checks['tampered_dpapi_refused']=not (folder/'tampered-output').exists()
            else:checks['tampered_dpapi_refused']=False
            key=live/'local-auth'/'owner.key';held=key.with_suffix('.held');key.rename(held)
            try:
                try:guard_backup.recover(backup,folder/'missing-key-output',reconcile_current=True)
                except local_auth.AuthStorageError:checks['missing_installation_key_refused']=not (folder/'missing-key-output').exists()
                else:checks['missing_installation_key_refused']=False
            finally:held.rename(key)
            blocked=guard_backup.recover(backup,folder/'no-reference')
            checks['no_reference_stays_blocked']=blocked['reconciliation']['status']=='blocked' and blocked['quarantined']
            checks['current_database_unchanged']=source_hash==hashlib.sha256((live/'riwaq.sqlite3').read_bytes()).hexdigest()
            checks['original_backup_unchanged']=backup_hash==hashlib.sha256(backup.read_bytes()).hexdigest()
            now=datetime.now(timezone.utc)
            result={'version':1,'environment':'Windows DPAPI current OS user; temporary synthetic SQLite data',
                'status':'passed' if all(checks.values()) else 'failed','checks':checks,
                'backup':seal,'recovery':report,
                'measurements':{'total_drill_seconds':time.monotonic()-started,
                    'snapshot_age_at_drill_end_seconds':(now-datetime.fromisoformat(report['snapshot_at'])).total_seconds(),
                    'known_post_snapshot_synthetic_rows_not_recovered':1},
                'rpo_rto_targets':'not supplied or agreed; these measurements do not establish a production SLA',
                'limits':['No production data or production activation','OS-user DPAPI key loss/other Windows identity was not tested',
                    'Installation key is required for reconciliation and is never replaced or copied',
                    'No trusted current state after disaster remains blocked; a permitted reference/deletion ledger is required',
                    'Reference snapshot does not cover later changes; repeat before a separately reviewed activation',
                    'External workspace files, OS keyring, runtime models and operator configuration are excluded']}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--report',required=True);args=parser.parse_args()
    path=Path(args.report).resolve()
    if path.exists():parser.error('Report must be new')
    result=run();path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as out:json.dump(result,out,ensure_ascii=False,indent=2)
    print(json.dumps({'status':result['status'],'checks':len(result['checks']),'report':str(path)}))
    raise SystemExit(0 if result['status']=='passed' else 1)


if __name__=='__main__':main()
