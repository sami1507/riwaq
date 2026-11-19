"""Explicit live reading of an approved catalogue, reusing the existing corpus.

This is not a general web search engine. Queries and private snippets stay local.
Only registered public URLs enter the existing rights/robots/budget gateway.
"""
import asyncio
import json
import re
import time
from fastapi import APIRouter, HTTPException, Response
from backend.db import get_db, now, settings
from backend import public_knowledge as public, knowledge, facts
from backend.research_schema import ResearchOptions

router = APIRouter(prefix='/api')


def initialize(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS research_receipts(message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,receipt TEXT NOT NULL)')


def save(conn, message_id, receipt):
    payload = json.dumps(receipt, ensure_ascii=False)
    if len(payload.encode()) > 60000:
        raise ValueError('Research receipt too large')
    conn.execute('INSERT OR REPLACE INTO research_receipts VALUES (?,?)', (message_id, payload))


def read(conn, message_id):
    row = conn.execute('SELECT receipt FROM research_receipts WHERE message_id=?', (message_id,)).fetchone()
    return json.loads(row[0]) if row else None


def new_receipt(options):
    return {'version': 'research-v1', 'status': 'preparing', 'provider': 'approved_catalogue' if 'web' in options.scopes else 'saved_index',
            'events': [], 'notices': [], 'documents_read': 0, 'downloaded_bytes': 0,
            'network_requests': 0, 'model_calls': 0, 'seconds': 0,
            'limits': {'documents': 3, 'download_bytes': 4 * 1024**2, 'requests': 12, 'fetch_seconds': 120, 'model_calls': 1},
            'options': options.model_dump(), 'external_query_sent': False,
            'monetary_cost_usd': 0, 'cost_scope': 'No paid provider; electricity/hardware not estimated'}


def event(receipt, kind, status, detail=''):
    receipt['events'].append({'kind': kind, 'status': status, 'at': now(), 'detail': detail[:500]})
    receipt['events'] = receipt['events'][-30:]
    receipt['status'] = kind


def select_evidence(question, chunks):
    """Cheap deterministic selection; no new model or modified source text."""
    identifiers = set(re.findall(r'\b([A-Za-z_]\w*(?:\.\w+)*)(?=\s*\()', question))
    selected = []
    navigation = {'navigation', 'index', 'modules', 'next', 'previous', 'theme', 'auto', 'light', 'dark', '|', 'python', 'the python tutorial'}
    for rank, chunk in enumerate(chunks):
        text = chunk['text']
        if len(text.splitlines()) == 1 and re.search(r' — .+ documentation$', text, re.I):
            continue
        lines = [line.strip('# ¶»| .\t').lower() for line in text.splitlines() if line.strip('# ¶»| .\t')]
        if lines and sum(line in navigation or bool(re.fullmatch(r'[\d.]+ documentation', line)) for line in lines) >= max(3, len(lines) - 2):
            continue
        hits = sum(bool(re.search(r'(?<!\w)' + re.escape(name) + r'\s*\(', text)) for name in identifiers)
        selected.append((hits, rank, chunk))
    selected.sort(key=lambda item: (-item[0], item[1]))
    return [c for _, _, c in selected]


def catalogue_candidates(question, chunks, options):
    """Local title/token ranking + existing cross-language retrieval, no outbound query."""
    terms = set(knowledge.tokenize(question[:4000]))
    ranked = []
    with get_db() as conn:
        sources = [public.source_dict(row) for row in conn.execute("SELECT * FROM public_sources WHERE status='allowed'")]
        documents = {r['id']: dict(r) for r in conn.execute('SELECT id,source_id,requested_url FROM public_documents')}
    retrieved = {(documents[c['document_id']]['source_id'], documents[c['document_id']]['requested_url']): 20 - i
                 for i, c in enumerate(chunks) if c['document_id'] in documents}
    for source in sources:
        if not source.get('rights_confirmed') or not source.get('license', {}).get('storage_allowed'):
            continue
        if any(options.filters().get(k) and source.get(k if k != 'source_id' else 'id') != v
               for k, v in options.filters().items() if k != 'version'):
            continue
        for item in source.get('urls', []):
            item = {'url': item} if isinstance(item, str) else item
            if options.version and (item.get('version') or source.get('version')) != options.version:
                continue
            score = len(terms & set(knowledge.tokenize(source['name'] + ' ' + item.get('title', '') + ' ' + item['url'])))
            score += retrieved.get((source['id'], item['url']), 0)
            if score:
                ranked.append((score, source['id'], item['url']))
    ranked.sort(key=lambda r: (-r[0], r[1], r[2]))
    return [{'source_id': source_id, 'url': url} for _, source_id, url in ranked[:3]]


async def prepare(question, options, stop, receipt):
    started = time.monotonic()
    filters = options.filters()
    if 'web' in options.scopes and (settings()['mode'] != 'hybrid' or not options.allow_network):
        raise HTTPException(403, 'Live reading requires hybrid mode and explicit network permission. Saved knowledge remains available locally.')
    event(receipt, 'retrieve', 'running', 'Searching the selected saved index locally')
    chunks = await public.retrieve_public(question, limit=8, filters=filters) if any(s in options.scopes for s in ('web', 'knowledge')) else []
    checked = {}
    if 'web' in options.scopes:
        receipt['notices'].append('Live reading searches only approved catalogue URLs; this is not general web search. No query or private file is sent externally.')
        selected = catalogue_candidates(question, chunks, options)
        event(receipt, 'catalogue', 'complete', f'{len(selected)} registered URLs selected')
        if selected and not stop.is_set():
            job = await public.start_selected_job(selected, allow_network=True, seconds=120, max_bytes=4 * 1024**2, max_requests=12)
            job_id = job['id']; receipt['job_id'] = job_id
            task = public.TASKS.get(job_id)
            event(receipt, 'reading', 'running', 'Checking rights and robots, fetching and indexing selected documents')
            try:
                while task and not task.done():
                    if stop.is_set():
                        await public.cancel_job(job_id)
                        raise asyncio.CancelledError()
                    job = public.get_job(job_id)
                    receipt.update(downloaded_bytes=job['downloaded_bytes'], network_requests=job.get('request_count', 0),
                                   documents_read=sum(i['status'] in ('accepted', 'unchanged') for i in job.get('items', [])))
                    await asyncio.sleep(.1)
                if task:
                    await asyncio.shield(task)
            except BaseException:
                await public.cancel_job(job_id)
                # JobControl owns its worker sockets/process; retain it until cleanup.
                if task:
                    await asyncio.shield(task)
                raise
            job = public.get_job(job_id)
            receipt.update(downloaded_bytes=job['downloaded_bytes'], network_requests=job.get('request_count', 0))
            for item in job.get('items', []):
                if item['status'] in ('accepted', 'unchanged') and item.get('document_id'):
                    checked[item['document_id']] = {'version_id': item.get('version_id'), 'checked_at': item['updated_at']}
                elif item.get('error'):
                    receipt['notices'].append(str(item['error'])[:400])
            receipt['documents_read'] = len(checked)
            event(receipt, 'reading', job['status'], f'{len(checked)} documents read or revalidated')
            if job.get('error'):
                receipt['notices'].append(job['error'][:400])
            chunks = await public.retrieve_public(question, limit=8, filters=filters)
        else:
            receipt['notices'].append('No eligible catalogue URL matched the question. No external request was made.')
        if 'knowledge' not in options.scopes:
            chunks = [c for c in chunks if checked.get(c['document_id'], {}).get('version_id') == c.get('version_id')]
    # Freshness cannot be certified from fetch time, especially with unknown publication dates.
    if options.require_current:
        chunks = []
        receipt['notices'].append('Current-event validity is not established by this catalogue. Freshness-required answers are withheld; choose a dated authoritative source or relax this requirement explicitly.')
    chunks = select_evidence(question, chunks)
    for chunk in chunks:
        binding = checked.get(chunk['document_id'], {})
        if binding.get('version_id') == chunk.get('version_id'):
            chunk['checked_at'] = binding['checked_at']
    receipt['checked_documents'] = checked
    receipt['seconds'] = round(time.monotonic() - started, 3)
    receipt['notices'] = list(dict.fromkeys(receipt['notices']))[:12]
    event(receipt, 'retrieve', 'complete', f'{len(chunks)} public evidence excerpts available')
    return chunks


def enrich(conn, chunks, receipt):
    seen = {}
    receipt['corroboration']={'independence_verified':False,'basis':'Exact content fingerprints can identify copies; distinct URLs, publishers or hashes do not prove independent sourcing.'}
    for chunk in chunks:
        if chunk.get('scope') != 'public':
            continue
        source = public.source_dict(public.source_row(conn, chunk['source_id']))
        publisher = source.get('publisher') or source.get('owner') or None
        chunk['publisher'] = publisher.get('name') if isinstance(publisher, dict) else publisher
        binding = receipt.get('checked_documents', {}).get(chunk['document_id'], {})
        chunk['checked_at'] = binding.get('checked_at') if binding.get('version_id') == chunk.get('version_id') else None
        chunk['event_at'] = None
        fingerprint = chunk.get('sha256')
        known_fingerprint=isinstance(fingerprint,str) and bool(re.fullmatch('[a-fA-F0-9]{64}',fingerprint))
        if known_fingerprint and fingerprint in seen and seen[fingerprint] != chunk['document_id']:
            receipt['notices'].append('Matching content fingerprints are not independent corroboration.')
        if known_fingerprint:seen[fingerprint] = chunk['document_id']
    return chunks


@router.get('/projects/{project_id}/research/evidence/{message_id}/{index}')
def evidence(project_id: str, message_id: str, index: int, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    with get_db() as conn:
        from backend.guard import require_project
        require_project(conn,project_id)
        message = conn.execute('SELECT m.citations FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.id=? AND c.project_id=?', (message_id, project_id)).fetchone()
        if not message:
            raise HTTPException(404, 'Evidence not available in this project')
        citations = json.loads(message['citations'])
        if not 0 <= index < len(citations):
            raise HTTPException(404, 'Evidence not found')
        citation = citations[index]
        if citation.get('provenance') == 'stored_excerpt':
            raise HTTPException(409, 'Imported snapshot evidence has no verified local binding')
        kind = 'public_chunk' if citation.get('scope') == 'public' else 'private_chunk'
        row, state = facts.source_raw(conn, project_id, kind, citation.get('id'))
        if not row or row['text'] != citation.get('text', citation.get('content')):
            raise HTTPException(404, 'Evidence was removed, changed or access was revoked')
        if kind == 'public_chunk' and (row['version_id'] != citation.get('version_id') or row['source_sha256'] != citation.get('sha256')):
            raise HTTPException(409, 'Evidence version binding changed')
        if kind=='private_chunk':
            # Private excerpt text can remain equal after a file is replaced.
            # Recover the original trusted source binding, not today's hash.
            original_hash=citation.get('sha256')
            if not original_hash and conn.execute("SELECT 1 FROM sqlite_master WHERE name='message_context_links'").fetchone():
                from backend import context_lineage
                links=context_lineage.read(conn,message_id) or []
                binding=next((link for link in links if link.get('kind')=='chunk' and link.get('source_kind')=='private_chunk' and link.get('id')==citation.get('id')),None)
                if binding:original_hash=binding.get('source_sha256')
            if original_hash and original_hash!=row['source_sha256']:
                raise HTTPException(409,'Evidence version binding changed')
            if not original_hash:
                return {'citation':citation,'excerpt':row['text'],'current':False,
                        'notice':'Legacy excerpt has no original source-version binding; it is not certified as current.'}
        return {'citation': citation, 'excerpt': row['text'], 'current': state == 'current',
                'notice': None if state == 'current' else 'Historical source version; not a live verification of current information.'}
