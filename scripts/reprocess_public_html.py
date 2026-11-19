"""Explicit offline HTML migration; preserves original versions and never downloads."""
import argparse, asyncio, json, os, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend import public_knowledge as public
from backend.db import get_db
from evaluate_knowledge import deny_external_connections, audit_originals

async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--report',default='reports/html-reprocess-v2.json')
    args=parser.parse_args()
    destination=ROOT/args.report
    if destination.exists():raise SystemExit('Refuse to overwrite an existing migration report')
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM messages WHERE status='streaming' LIMIT 1").fetchone() or conn.execute("SELECT 1 FROM public_jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise SystemExit('Stop generation/imports before reprocessing')
        planned=[dict(row) for row in conn.execute("SELECT d.id,d.current_version_id FROM public_documents d JOIN public_versions v ON v.id=d.current_version_id JOIN public_sources s ON s.id=d.source_id WHERE s.status='allowed' AND v.content_type LIKE '%html%'")]
        budget_before=conn.execute('SELECT COALESCE(SUM(bytes),0) FROM public_download_ledger').fetchone()[0]
    if not args.apply:
        print(json.dumps({'preview':planned,'network_downloads':0}));return
    report={'operation':'local retained-original reprocessing','documents':[],'network_downloads':0,'model_downloads':0}
    with deny_external_connections() as proof:
        for item in planned:
            with get_db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                result=public.reprocess_html_document(conn,item['id'],item['current_version_id'])
            try:
                await public.embed_document(item['id'],asyncio.Event())
            except Exception as exc:result['embedding_error']=str(exc)[:300]
            report['documents'].append(result)
            print(json.dumps(result),flush=True)
        with get_db() as conn:
            rows=public.corpus_rows(conn)
            report['replay']=audit_originals(conn,rows,public)
            report['current_chunks']=len(rows)
            report['current_vectors']=conn.execute('SELECT COUNT(*) FROM public_embeddings e JOIN public_chunks c ON c.id=e.chunk_id JOIN public_documents d ON d.current_version_id=c.version_id').fetchone()[0]
            report['budget_unchanged']=budget_before==conn.execute('SELECT COALESCE(SUM(bytes),0) FROM public_download_ledger').fetchone()[0]
        report['network_guard']=proof
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='documents'}),flush=True)
if __name__=='__main__':asyncio.run(main())
