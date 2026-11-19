"""Explicit local-operator incident controls. Never exposed as model tools/API."""
import argparse
import json
import time
from backend import guard, local_auth
from backend.db import get_db,now


def operate(action):
    if action not in {'status','revoke-sessions','quarantine-summary','resume-summary','purge-expired-audit'}:
        raise ValueError('Unknown operator action')
    # Unlock the current OS user's DPAPI key before any operator mutation.
    local_auth._master_key()
    with get_db() as c:
        c.execute('BEGIN IMMEDIATE')
        owner=local_auth.owner_id(c);who=guard.Actor(owner,'local-operator')
        if action=='revoke-sessions':
            c.execute('UPDATE local_auth_sessions SET revoked_at=? WHERE owner_id=?',(time.time(),owner))
            c.execute("UPDATE guard_permits SET state='revoked' WHERE owner_id=? AND state='pending'",(owner,))
        elif action=='quarantine-summary':
            c.execute("UPDATE guard_control SET value='disabled' WHERE key='local_summary'")
            c.execute("UPDATE guard_permits SET state='revoked' WHERE state='pending'")
            c.execute("UPDATE guard_results SET status='cancelled',content='',updated_at=? WHERE status IN ('pending','running')",(now(),))
        elif action=='resume-summary':
            # This never clears restore quarantine or enables cloud/memory.
            c.execute("UPDATE guard_control SET value='enabled' WHERE key='local_summary'")
        elif action=='purge-expired-audit':
            c.execute("DELETE FROM guard_audit WHERE julianday(created_at)<julianday('now','-30 days')")
            c.execute('DELETE FROM guard_permits WHERE expires<?',(time.time()-30*86400,))
        if action!='status':guard.audit(c,who,None,'operator',action,'operator','explicit_local_operator')
        control=c.execute("SELECT value FROM guard_control WHERE key='local_summary'").fetchone()
        alerts=[dict(r) for r in c.execute("SELECT reason,COUNT(*) count FROM guard_audit WHERE decision='deny' AND julianday(created_at)>=julianday('now','-1 day') GROUP BY reason")]
        return {'policy_version':guard.POLICY_VERSION,'summary_tool':control[0] if control else 'missing',
                'active_sessions':c.execute('SELECT COUNT(*) FROM local_auth_sessions WHERE owner_id=? AND revoked_at IS NULL AND expires_at>? AND last_seen>?',(owner,time.time(),time.time()-local_auth.IDLE_SECONDS)).fetchone()[0],
                'denial_counts_last_24h':alerts,'personal_memory_gate':'blocked',
                'cloud_generation':'quarantined','external_alert_delivery':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['status','revoke-sessions','quarantine-summary','resume-summary','purge-expired-audit'])
    args=parser.parse_args()
    try:report=operate(args.action)
    except Exception:parser.exit(1,'Operator control failed closed; inspect local storage and OS identity.\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
