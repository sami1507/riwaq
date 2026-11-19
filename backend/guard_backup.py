"""Windows DPAPI database recovery drill. Restore always lands in quarantine.

The database includes persisted document/media blobs; external workspaces,
runtime models, operator JSON and OS-keyring credentials are excluded. Recovery
is quarantined pending offline deletion/rights reconciliation; never replaces
an active database or claims a runnable production restore.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from backend.db import data_dir
from backend.local_auth import _dpapi, _private, _master_key

MAGIC=b'RIWAQ-DPAPI-SQLITE-1\n'
MAX_BYTES=128*1024*1024

def _safe_path(value, *, existing=False):
    path=Path(value).absolute()
    for part in (path,*path.parents):
        if part.is_symlink() or getattr(part,'is_junction',lambda:False)():raise ValueError('Linked backup paths are not supported')
    if existing and (not path.is_file() or path.stat().st_nlink!=1):raise ValueError('Backup source must be one regular file')
    return path.resolve(strict=existing)

def inspect_database(c):
    """Physical checks plus persisted-blob fingerprints, not mere export exit0."""
    if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Database integrity check failed')
    if c.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Database reference integrity check failed')
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    checked={};total=0
    for table,blob,sha in [('skill_artifacts','content','sha256'),('visual_assets','data','sha256'),('video_assets','data','sha256'),('video_outputs','movie','sha256'),('public_versions','original','sha256')]:
        if table not in tables:continue
        count=0
        for raw,expected in c.execute(f'SELECT {blob},{sha} FROM {table}'):
            if not isinstance(raw,bytes) or not raw or hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Persisted file integrity check failed')
            count+=1;total+=len(raw)
            if count>10000 or total>MAX_BYTES:raise ValueError('Backup inspection budget exceeded')
        checked[table]=count
    if {'document_originals','knowledge_files'}<=tables:
        count=0
        for raw,expected in c.execute('SELECT d.original,k.sha256 FROM document_originals d JOIN knowledge_files k ON k.id=d.file_id'):
            if not isinstance(raw,bytes) or hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Document original integrity check failed')
            count+=1
        checked['document_originals']=count
    if {'skill_runs','skill_artifacts'}<=tables:
        for run_id,raw_document in c.execute("SELECT id,document FROM skill_runs WHERE state IN ('completed','partial')"):
            try:
                outputs=json.loads(raw_document).get('result',{}).get('artifacts',[])
                if not isinstance(outputs,list) or len(outputs)>8:raise ValueError()
                for item in outputs:
                    if not isinstance(item,dict):raise ValueError()
                    saved=c.execute('SELECT sha256 FROM skill_artifacts WHERE run_id=? AND name=?',(run_id,item.get('name'))).fetchone()
                    if not saved or saved[0]!=item.get('sha256'):raise ValueError()
            except (ValueError,TypeError,AttributeError):raise ValueError('Referenced output integrity check failed') from None
    missing_originals=c.execute('SELECT COUNT(*) FROM knowledge_files k LEFT JOIN document_originals d ON d.file_id=k.id WHERE d.file_id IS NULL').fetchone()[0] if {'document_originals','knowledge_files'}<=tables else None
    return {'integrity':'ok','references':'ok','verified_blob_rows':checked,'verified_blob_bytes':total,'legacy_extracted_files_without_stored_original':missing_originals,
      'external_workspaces_and_runtime_files':'excluded','operator_config_and_keyring':'excluded; reconfigure separately',
      'deletion_reconciliation':'required before activation; backup can contain later-deleted data'}

def _quarantine_effects(c):
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    changes={}
    statements={
      'productivity_schedule_grants':"UPDATE productivity_schedule_grants SET revoked=1",
      'productivity_reminders':"UPDATE productivity_reminders SET state='revoked' WHERE state IN ('active','paused')",
      'productivity_occurrences':"UPDATE productivity_occurrences SET state='revoked',reason='recovery_quarantine' WHERE state='pending'",
      'productivity_notifications':"DELETE FROM productivity_notifications",
      'mail_send_grants':"UPDATE mail_send_grants SET state='revoked'",
      'mail_submissions':"UPDATE mail_submissions SET state='unknown',receipt='{\"state\":\"unknown\",\"reason\":\"recovery_requires_effect_reconciliation\"}' WHERE state='dispatching'",
      'mail_followups':"UPDATE mail_followups SET state='paused'",
      'workspace_grants':"UPDATE workspace_grants SET active=0",
      'skill_run_controls':"UPDATE skill_run_controls SET deadline_epoch=0",
      'skill_checkpoints':"UPDATE skill_checkpoints SET lease_hash=NULL,lease_expires=0,lease_epoch=lease_epoch+1",
      'skill_budget_reservations':"UPDATE skill_budget_reservations SET state='unknown' WHERE state='in_flight'",
      # Removing the outer recovery quarantine must not reactivate an old
      # lesson or evaluation. A fresh source/consent review is still required.
      'learning_releases':"UPDATE learning_releases SET status='quarantined',version=version+1 WHERE status='active'",
      'learning_candidates':"UPDATE learning_candidates SET status='stale',version=version+1 WHERE status NOT IN ('revoked','rolled_back','stale')",
      'learning_evaluations':"UPDATE learning_evaluations SET status='revoked' WHERE status='passed'",
    }
    for table,sql in statements.items():
        if table in tables:changes[table]=c.execute(sql).rowcount
    for table,column in [('skill_runs','state'),('document_jobs','status'),('public_jobs','status'),('public_job_items','status'),('visual_jobs','status'),('video_jobs','status'),('coding_tasks','status')]:
        if table in tables:changes[table]=c.execute(f"UPDATE {table} SET {column}='interrupted' WHERE {column} IN ('queued','running','pending','cancelling','verifying')").rowcount
    from backend.personal_memory import quarantine
    changes.update({'personal_memory_'+key:value for key,value in quarantine(c).items()})
    return changes


def seal(destination):
    if os.name!='nt':raise ValueError('This recovery tool has only been implemented for Windows DPAPI')
    started=time.monotonic();source=_safe_path(data_dir()/'riwaq.sqlite3',existing=True);target=_safe_path(destination)
    if target.exists():raise ValueError('Backup destination must be new')
    folder=data_dir()/'local-auth'
    if not folder.is_dir():raise ValueError('Initialize Guard before creating a recovery backup')
    with tempfile.TemporaryDirectory(prefix='backup-',dir=folder) as tmp:
        snapshot=Path(tmp)/'snapshot.sqlite3'
        with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(snapshot)) as out:
            src.backup(out)
            # Snapshot-only identity commitment. The authentication key itself
            # is never exported, restored or replaced with a freshly made key.
            out.execute("INSERT OR REPLACE INTO guard_control VALUES('recovery_key_commitment',?)",(hashlib.sha256(_master_key(create=False)).hexdigest(),))
            out.execute("INSERT OR REPLACE INTO guard_control VALUES('recovery_snapshot_at',?)",(__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),))
            out.commit()
            inspection=inspect_database(out)
        if snapshot.stat().st_size>MAX_BYTES:raise ValueError('Recovery drill database budget exceeded')
        raw=snapshot.read_bytes()
        envelope=MAGIC+_dpapi(raw,True)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('xb') as out:out.write(envelope);out.flush();os.fsync(out.fileno())
    _private(target)
    return {'format':'windows-dpapi-current-user','database_bytes':len(raw),'encrypted_bytes':len(envelope),
            'encrypted_sha256':hashlib.sha256(envelope).hexdigest(),'seconds':time.monotonic()-started,
            'media_included':'database_persisted_blobs_only','portable_to_another_os_user':False,'inspection':inspection,
            'created_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
            'rpo_target':'operator-defined; manual snapshot has no schedule','rto_target':'not agreed; report measures this drill only'}


def recover(backup,destination,*,reconcile_current=False):
    if os.name!='nt':raise ValueError('Windows DPAPI is required for this backup')
    started=time.monotonic();source=_safe_path(backup,existing=True);target=_safe_path(destination)
    if target.exists():raise ValueError('Recovery directory must be new; active data cannot be replaced')
    if source.stat().st_size>MAX_BYTES+1024*1024:raise ValueError('Encrypted backup budget exceeded')
    envelope=source.read_bytes()
    if not envelope.startswith(MAGIC):raise ValueError('Unsupported backup format')
    raw=_dpapi(envelope[len(MAGIC):],False)
    if len(raw)>MAX_BYTES or not raw.startswith(b'SQLite format 3\0'):raise ValueError('Invalid backup database')
    reference=None
    if reconcile_current:
        # Only the configured current installation, never a client-uploaded
        # manifest. Reading happens in a stable read-only SQLite transaction.
        from backend.recovery_reconciliation import manifest
        reference_path=_safe_path(data_dir()/'riwaq.sqlite3',existing=True)
        key_commitment=hashlib.sha256(_master_key(create=False)).hexdigest()
        with closing(sqlite3.connect(reference_path.as_uri()+'?mode=ro',uri=True)) as current:
            current.execute('PRAGMA query_only=ON');current.execute('BEGIN')
            reference=manifest(current)
        reference['captured_at']=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
    # Output remains inaccessible to the web service until an operator performs
    # an offline rights/deletion reconciliation. Old valid sessions never return.
    # A refusal after this point must not leave the raw database copy behind: the raw
    # snapshot still holds its old sessions and permits until the statements below run.
    try:
        target.mkdir(mode=0o700);_private(target,directory=True)
        (target/'RECOVERY_QUARANTINE').write_text('Offline deletion and permission reconciliation required. Do not serve this directory.\n',encoding='utf-8')
        db=target/'riwaq.sqlite3'
        with db.open('xb') as out:out.write(raw)
        _private(db)
        with closing(sqlite3.connect(db)) as c:
            inspection=inspect_database(c)
            for table in ('guard_control','guard_file_policies','guard_permits','local_auth_sessions'):
                if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                    raise ValueError('Pre-Guard backup requires separate offline migration; it cannot be activated')
            reconciliation={'status':'blocked','reason':'current_trusted_reference_required_to_check_later_deletions','activation_authorized':False}
            if reference is not None:
                binding=c.execute("SELECT value FROM guard_control WHERE key='recovery_key_commitment'").fetchone()
                if not binding or binding[0]!=key_commitment:
                    raise ValueError('Original installation authentication key does not match this backup')
                from backend.recovery_reconciliation import reconcile
                reconciliation=reconcile(c,reference)
                reconciliation['reference_captured_at']=reference['captured_at']
            snapshot=c.execute("SELECT value FROM guard_control WHERE key='recovery_snapshot_at'").fetchone()
            c.execute("INSERT OR REPLACE INTO guard_control VALUES('restore_quarantine','1')")
            c.execute("UPDATE guard_control SET value='disabled' WHERE key='local_summary'")
            c.execute('UPDATE guard_file_policies SET revoked=1,revision=revision+1')
            c.execute("UPDATE guard_results SET status='revoked',content=''")
            c.execute('DELETE FROM guard_permits')
            c.execute('DELETE FROM local_auth_sessions')
            c.execute('DELETE FROM local_auth_pairings')
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_auth_state'").fetchone():
                c.execute('UPDATE sync_auth_state SET revision=revision+1,token_hash=NULL')
            effects=_quarantine_effects(c)
            c.execute("INSERT OR REPLACE INTO guard_control VALUES('recovery_reconciliation',?)",(json.dumps(reconciliation,sort_keys=True),))
            c.commit()
            inspection_after=inspect_database(c)
            # SQLite may keep deleted text in freelist pages. Vacuum only this new,
            # quarantined copy, never the source/backup, before handing it back.
            c.execute('VACUUM')
    except BaseException:
        # Keep the directory and its RECOVERY_QUARANTINE marker (the destination stays
        # refused for reuse and for serving); remove only the unreconciled database copy.
        for leftover in ('riwaq.sqlite3','riwaq.sqlite3-wal','riwaq.sqlite3-shm','riwaq.sqlite3-journal'):
            try:(target/leftover).unlink()
            except OSError:pass
        raise
    return {'integrity':'ok','quarantined':True,'sessions_restored':False,'permits_restored':False,
            'old_rights_reactivated':False,'seconds':time.monotonic()-started,'complete_media_restore':False,
            'inspection':inspection,'inspection_after_reconciliation':inspection_after,
            'quarantine_changes':effects,'reconciliation':reconciliation,'production_activation':False,
            'snapshot_at':snapshot[0] if snapshot else None,
            'rpo_target':'not agreed; snapshot time identifies the recoverable point, later writes are not imported',
            'rto_target':'not agreed; measured seconds cover this quarantined drill only',
            'remaining':'Keep quarantine. Reconcile later changes after the reference snapshot and external effects; operator policy, credentials and a separately reviewed activation procedure remain required.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    backup=sub.add_parser('seal');backup.add_argument('destination')
    restore=sub.add_parser('recover');restore.add_argument('backup');restore.add_argument('destination');restore.add_argument('--reconcile-current',action='store_true',help='Exclude old rows against the configured current installation, read-only. Never activates recovery.')
    args=parser.parse_args()
    try:report=seal(args.destination) if args.action=='seal' else recover(args.backup,args.destination,reconcile_current=args.reconcile_current)
    except Exception:parser.exit(1,'Backup/recovery refused. Check source, permissions, OS identity and a new destination. No active database was replaced.\n')
    print(json.dumps(report))


if __name__=='__main__':main()
