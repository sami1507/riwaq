import json
import os
import sqlite3
import pytest
from backend import guard_backup
from backend.db import init_db,get_db


@pytest.mark.skipif(os.name!='nt',reason='DPAPI recovery is Windows-only')
def test_encrypted_backup_recovers_only_into_quarantine(tmp_path,monkeypatch):
    original=tmp_path/'original';monkeypatch.setenv('RIWAQ_DATA_DIR',str(original));init_db()
    with get_db() as c:c.execute("INSERT INTO memories VALUES('m','preference',NULL,'SYNTHETIC_BACKUP_CANARY','2026-11-15')")
    output=tmp_path/'sealed.rwbackup';report=guard_backup.seal(output)
    assert report['encrypted_bytes']>0 and b'SYNTHETIC_BACKUP_CANARY' not in output.read_bytes()
    with get_db() as c:c.execute("DELETE FROM memories WHERE id='m'")
    restored=tmp_path/'quarantine';recovery=guard_backup.recover(output,restored)
    assert recovery['quarantined'] and not recovery['old_rights_reactivated']
    with sqlite3.connect(restored/'riwaq.sqlite3') as c:
        assert c.execute("SELECT value FROM guard_control WHERE key='restore_quarantine'").fetchone()[0]=='1'
        assert c.execute('SELECT COUNT(*) FROM local_auth_sessions').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM guard_permits').fetchone()[0]==0
    with get_db() as c:assert c.execute('SELECT COUNT(*) FROM memories').fetchone()[0]==0
    with pytest.raises(ValueError):guard_backup.recover(output,original)
    with pytest.raises(ValueError):guard_backup.seal(output)
    tampered=tmp_path/'tampered.rwbackup';data=bytearray(output.read_bytes());data[-20]^=1;tampered.write_bytes(data)
    with pytest.raises(Exception):guard_backup.recover(tampered,tmp_path/'must-not-appear')
    assert not (tmp_path/'must-not-appear').exists()


@pytest.mark.skipif(os.name!='nt',reason='DPAPI recovery is Windows-only')
def test_refused_recovery_leaves_no_unquarantined_copy(tmp_path,monkeypatch):
    """GAP-005: a recovery that is refused after the target directory was created must not
    leave the raw snapshot (old sessions and permits included) on disk."""
    original=tmp_path/'original';monkeypatch.setenv('RIWAQ_DATA_DIR',str(original));init_db()
    output=tmp_path/'sealed.rwbackup';guard_backup.seal(output)
    def refuse(connection):raise ValueError('synthetic refusal after the copy was written')
    monkeypatch.setattr(guard_backup,'inspect_database',refuse)
    target=tmp_path/'refused'
    with pytest.raises(ValueError):guard_backup.recover(output,target)
    assert (target/'RECOVERY_QUARANTINE').exists()  # the marker stays, as for a key mismatch
    assert not any(target.glob('riwaq.sqlite3*'))
    monkeypatch.undo();monkeypatch.setenv('RIWAQ_DATA_DIR',str(original))
    with pytest.raises(ValueError):guard_backup.recover(output,target)  # a refused destination is not reused
    assert guard_backup.recover(output,tmp_path/'fresh')['quarantined']
