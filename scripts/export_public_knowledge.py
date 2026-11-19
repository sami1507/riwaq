"""Export ready, currently permitted, redistributable public evidence only.

The database is read-only. Per-version index state is provenance, not exported
embedding availability. This archive contains no private project tables.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.public_knowledge import source_allows


def redistributable(value):
    return (isinstance(value,dict) and value.get('storage_allowed') is True
            and value.get('redistribution_allowed') is True)


def permitted_source(spec,status,url):
    return (isinstance(spec,dict) and redistributable(spec.get('license'))
            and source_allows(spec|{'status':status},url))


def export_archive(database,output):
    database=Path(database).resolve(strict=True)
    destination=Path(output).resolve()
    if database==destination:
        raise ValueError('The archive cannot replace its source database')
    destination.parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    temporary=None
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='public_version_indexes'").fetchone():
            raise ValueError('Index readiness metadata is required; run the normal schema migration first')
        sources=[];documents=[];versions=[];indices=[];selected=[];excluded=Counter()
        for source in conn.execute('SELECT id,data,status FROM public_sources ORDER BY id'):
            try:spec=json.loads(source['data'])
            except (ValueError,TypeError):
                excluded['invalid_source_metadata']+=1;continue
            if source['status']!='allowed' or not isinstance(spec,dict) or spec.get('rights_confirmed') is not True or not redistributable(spec.get('license')):
                excluded['source_rights_unavailable']+=1;continue
            source_documents=[]
            for document_row in conn.execute('SELECT * FROM public_documents WHERE source_id=? ORDER BY id',(source['id'],)):
                document=dict(document_row);exported_ids=[]
                # Each version has its own URL and license. Neither the latest
                # license nor a current document URL can authorize an older one.
                for row in conn.execute('SELECT v.id,v.license,i.state,i.source_url,i.title,i.model,i.digest,i.dimensions,i.source_fingerprint FROM public_versions v LEFT JOIN public_version_indexes i ON i.version_id=v.id WHERE v.document_id=? ORDER BY v.revision',(document['id'],)):
                    if row['state'] not in {'lexical_ready','hybrid_ready'}:
                        excluded['version_not_ready']+=1;continue
                    try:license=json.loads(row['license'])
                    except (ValueError,TypeError):
                        excluded['invalid_version_rights']+=1;continue
                    if not redistributable(license):
                        excluded['version_redistribution_not_permitted']+=1;continue
                    if not row['source_url'] or not permitted_source(spec,source['status'],row['source_url']):
                        excluded['version_outside_current_scope']+=1;continue
                    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',row['id']):
                        raise ValueError('Unsafe stored version identifier; archive was not published')
                    exported_ids.append(row['id']);selected.append(row['id'])
                    indices.append({'version_id':row['id'],**{key:row[key] for key in ('state','source_url','title','model','digest','dimensions','source_fingerprint')}})
                if not exported_ids:
                    continue
                document['current_version_exported']=document['current_version_id'] in exported_ids
                if not document['current_version_exported']:
                    # Do not silently promote a historical version or leave a
                    # dangling active-version pointer to excluded content.
                    document['current_version_id']=None
                source_documents.append(document)
            if source_documents:
                sources.append(spec|{'status':source['status']});documents.extend(source_documents)
        handle=tempfile.NamedTemporaryFile(prefix=destination.stem+'-',suffix='.tmp',dir=destination.parent,delete=False)
        temporary=Path(handle.name);handle.close()
        chunk_count=0
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as archive:
            for identifier in selected:
                row=dict(conn.execute('SELECT * FROM public_versions WHERE id=?',(identifier,)).fetchone())
                raw=row.pop('original')
                if hashlib.sha256(raw).hexdigest()!=row['sha256'] or len(raw)!=row['byte_size']:
                    raise ValueError('Stored original hash/size mismatch; archive was not published')
                archive.writestr('originals/'+identifier+'.bin',raw)
                archive.writestr('extracted/'+identifier+'.txt',row.pop('extracted_text'))
                archive.writestr('licenses/'+identifier+'.txt',row.pop('license_text'))
                versions.append(row)
            with archive.open('chunks.jsonl','w') as stream:
                for identifier in selected:
                    for row in conn.execute('SELECT c.*,m.heading FROM public_chunks c LEFT JOIN public_chunk_metadata m ON m.chunk_id=c.id WHERE c.version_id=? ORDER BY c.rowid',(identifier,)):
                        stream.write((json.dumps(dict(row),ensure_ascii=False)+'\n').encode('utf-8'))
                        chunk_count+=1
            manifest={'format':'riwaq-public-evidence-1','sources':sources,'documents':documents,'versions':versions,
                      'index_metadata':indices,'index_metadata_scope':'Recorded source readiness only; embedding vectors are not included and must be rebuilt before semantic use.',
                      'excluded_counts':dict(excluded),
                      'notice':'Only ready public versions permitted by current source scope/storage/redistribution rights and each retained version license. No private projects, accounts, conversations, keys, or evaluation answers. Exact originals, extracted text, license evidence, and chunks are retained; no generated translation. This is an evidence archive, not an automatically authorized import or a live semantic index.'}
            archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        os.replace(temporary,destination)
        return {'path':str(destination),'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),'bytes':destination.stat().st_size,
                'sources':len(sources),'documents':len(documents),'versions':len(versions),'chunks_all_versions':chunk_count,
                'excluded_counts':dict(excluded),'private_tables_exported':False,'embedding_vectors_exported':False}
    finally:
        conn.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--database',default=str(ROOT/'data/riwaq.sqlite3'))
    parser.add_argument('--report',default='reports/public-knowledge-archive.json')
    args=parser.parse_args()
    report_path=(ROOT/args.report).resolve()
    if report_path in {Path(args.database).resolve(),Path(args.output).resolve()}:
        raise ValueError('Report, archive and database must be distinct paths')
    report=export_archive(args.database,args.output)
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
