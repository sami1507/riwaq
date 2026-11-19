"""Bounded, subtraction-only reconciliation of an old database snapshot.

The caller supplies a read-only transaction on the current installation. No
current content, credentials or permissions are copied into the old database.
Matching rows are retained; changed/deleted rows and dependent rows are removed.
This deliberately sacrifices recovery coverage rather than resurrecting old data.
"""
import hashlib
import json
import sqlite3
import time

MAX_ROWS = 100000
MAX_BYTES = 128 * 1024 * 1024
MAX_SECONDS = 30
EXCLUDED = {'settings', 'guard_control', 'guard_rate_state', 'guard_audit',
            'guard_permits', 'guard_results', 'sync_auth_state',
            'personal_memory_settings'}


def _q(name):
    return '"' + name.replace('"', '""') + '"'


def _tables(c):
    result = {}
    for name, sql in c.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        if sql and 'CREATE VIRTUAL TABLE' in sql.upper():
            raise ValueError('Virtual-table recovery requires a reviewed index rebuild')
        if name in EXCLUDED or name.startswith(('local_auth_', 'recovery_')):
            continue
        columns = list(c.execute('PRAGMA table_info(' + _q(name) + ')'))
        primary = [r[1] for r in sorted(columns, key=lambda r:r[5]) if r[5]]
        result[name] = {'columns':[r[1] for r in columns], 'primary':primary or ['rowid']}
    return result


def _encoded(row):
    # Type tags avoid confusing bytes, text, null and numbers. Blobs stay local.
    return json.dumps([[type(x).__name__, hashlib.sha256(x).hexdigest() if isinstance(x,bytes) else x]
                       for x in row], ensure_ascii=True, separators=(',',':')).encode()


def manifest(c):
    started = time.monotonic(); tables = _tables(c); count = size = 0; result = {}
    owner = c.execute('SELECT owner_id FROM local_auth_owner WHERE slot=1').fetchone()
    if not owner: raise ValueError('Current installation identity is missing')
    for table, schema in tables.items():
        rows = {}; key_columns = schema['primary']; columns = schema['columns']
        query = ','.join(_q(x) for x in key_columns + columns)
        for row in c.execute('SELECT '+query+' FROM '+_q(table)):
            key = tuple(row[:len(key_columns)]); values = row[len(key_columns):]
            count += 1; size += sum(len(x.encode('utf-8')) if isinstance(x,str) else len(x) if isinstance(x,bytes) else 16 for x in values)
            if count>MAX_ROWS or size>MAX_BYTES or time.monotonic()-started>MAX_SECONDS:
                raise ValueError('Reconciliation manifest budget exceeded')
            rows[key] = hashlib.sha256(_encoded(values)).hexdigest()
        result[table] = {**schema,'rows':rows}
    fingerprint = hashlib.sha256()
    for table, data in sorted(result.items()):
        fingerprint.update(table.encode())
        for key,value in sorted(data['rows'].items(),key=lambda item:repr(item[0])):
            fingerprint.update(_encoded(key)); fingerprint.update(value.encode())
    return {'owner':owner[0],'tables':result,'rows':count,'fingerprint':fingerprint.hexdigest()}


def _reconcile(c, current):
    """Mutate only c. Foreign keys must be off; caller validates before commit."""
    if c.execute('PRAGMA foreign_keys').fetchone()[0]:
        raise ValueError('Reconciliation requires an isolated offline connection')
    old = manifest(c)
    if old['owner'] != current['owner']:
        raise ValueError('Recovery reference belongs to a different installation')
    tables = old['tables']; removed = {}; reasons = {}
    def delete(table, where, args=(), reason='changed_or_deleted'):
        if table not in tables: return
        count=c.execute('DELETE FROM '+_q(table)+' WHERE '+where,args).rowcount
        if count:
            removed[table]=removed.get(table,0)+count
            reasons[reason]=reasons.get(reason,0)+count
    def differs(table,key):
        left=tables.get(table);right=current['tables'].get(table)
        return not left or not right or left['columns']!=right['columns'] or left['rows'].get(key)!=right['rows'].get(key)
    # Membership/policy changes are reverse dependencies: deleting only the
    # permission row would let a future migration recreate an old permission.
    if 'guard_projects' in tables:
        for pid, in c.execute('SELECT project_id FROM guard_projects').fetchall():
            if differs('guard_projects',(pid,)):delete('projects','id=?',(pid,),'ownership_changed')
    if 'guard_file_policies' in tables:
        for fid, in c.execute('SELECT file_id FROM guard_file_policies').fetchall():
            if differs('guard_file_policies',(fid,)):delete('knowledge_files','id=?',(fid,),'file_policy_changed')
    if 'personal_memory_entries' in tables:
        for mid, in c.execute('SELECT memory_id FROM personal_memory_entries').fetchall():
            if differs('personal_memory_entries',(mid,)):delete('memories','id=?',(mid,),'personal_metadata_changed')
    # Recovered lessons must never become plain project memories after their
    # release metadata is excluded, even if an operator later removes quarantine.
    if 'learning_releases' in tables:
        delete('memories','id IN (SELECT memory_id FROM learning_releases WHERE memory_id IS NOT NULL)',reason='learned_memory_requires_fresh_review')
    # Equality to a live cache is not a deletion proof: JSON sourceRefs,
    # previousRunId, correction evidence and provenance are not foreign keys.
    # Until every such lineage edge is independently revalidated, exclude all
    # these derived roots; the FK sweep below removes their children. Keep
    # external-effect receipts separately for operator reconciliation, never
    # as executable grants or reusable generated content.
    for table in ('skill_runs','learning_feedback','artifacts','fact_entries',
                  'studio_entries','visual_designs','visual_assets','visual_jobs',
                  'video_timelines','video_assets','coding_tasks','mail_drafts',
                  'events','voice_saved_turns'):
        delete(table,'1=1',reason='unverified_json_lineage_requires_regeneration')
    if 'messages' in tables and 'role' in tables['messages']['columns']:
        delete('messages',"role='assistant'",reason='assistant_answer_requires_regeneration')
    # Cached answers can contain withdrawn material. Revalidation cannot prove
    # that all old text no longer incorporates it, so remove derived messages.
    if 'message_context_links' in tables:
        delete('messages',"id IN (SELECT message_id FROM message_context_links WHERE links!='[]')",reason='derived_answer_requires_regeneration')
    for table, data in tables.items():
        for key in list(data['rows']):
            if differs(table,key):
                clause=' AND '.join(_q(column)+' IS ?' for column in data['primary'])
                delete(table,clause,key)
    # Honor the schema's FK graph, including legacy NO ACTION references. No
    # source grants are introduced, and dependency order/cycles need no guessing.
    for _ in range(len(tables)+1):
        before=sum(removed.values())
        for table in tables:
            grouped={}
            for fk in c.execute('PRAGMA foreign_key_list('+_q(table)+')'):
                grouped.setdefault(fk[0],[]).append(fk)
            for group in grouped.values():
                parent=group[0][2]; pairs=[]; nonnull=[]
                parentpk=[r[1] for r in sorted(c.execute('PRAGMA table_info('+_q(parent)+')'),key=lambda r:r[5]) if r[5]]
                for fk in group:
                    source=fk[3]; target=fk[4] or parentpk[fk[1]]
                    pairs.append('p.'+_q(target)+'=child.'+_q(source));nonnull.append('child.'+_q(source)+' IS NOT NULL')
                where=' AND '.join(nonnull)+' AND NOT EXISTS(SELECT 1 FROM '+_q(parent)+' p WHERE '+' AND '.join(pairs)+')'
                count=c.execute('DELETE FROM '+_q(table)+' AS child WHERE '+where).rowcount
                if count:removed[table]=removed.get(table,0)+count;reasons['dependent_row']=reasons.get('dependent_row',0)+count
        if sum(removed.values())==before:break
    if c.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Reconciled database still has orphan references')
    # Include deletions made by existing SQLite triggers in the table totals.
    removed={table:len(data['rows'])-c.execute('SELECT COUNT(*) FROM '+_q(table)).fetchone()[0]
             for table,data in tables.items()
             if len(data['rows'])>c.execute('SELECT COUNT(*) FROM '+_q(table)).fetchone()[0]}
    return {'status':'reconciled_to_reference_snapshot','mode':'conservative_exclusion_only',
            'reference_fingerprint':current['fingerprint'],'reference_rows':current['rows'],
            'excluded_rows_by_table':removed,'exclusions_by_reason':reasons,
            'content_or_credentials_imported':False,'current_database_modified':False,
            'later_changes_after_reference':'not covered; repeat reconciliation against a newer trusted reference',
            'complete_restore':False,'activation_authorized':False}


def reconcile(c,current):
    started=time.monotonic()
    c.set_progress_handler(lambda: int(time.monotonic()-started>MAX_SECONDS),1000)
    try:return _reconcile(c,current)
    except sqlite3.OperationalError as exc:
        if 'interrupted' in str(exc):raise ValueError('Reconciliation execution budget exceeded') from None
        raise
    finally:c.set_progress_handler(None,0)
