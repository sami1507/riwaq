"""Paired local evaluation. Read-only corpus; no reference answers enter prompts.

The eight questions are selected before this run from an older heldout suite.
They are not newly blind: earlier project evaluation already used that suite.
No automatic correctness score: all outputs and evidence are retained for review.
"""
import asyncio
from contextlib import aclosing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import db, public_knowledge as public, grounding, providers, research, research_answer
from backend.research_schema import ResearchOptions
from scripts.evaluate_knowledge import deny_external_connections

IDS = ['py02', 'react02', 'ts02', 'fastapi03', 'general04', 'general10', 'absent03', 'safety05']
OUT = ROOT / 'reports/research-answer-comparison.json'


@contextmanager
def readonly_db():
    conn = sqlite3.connect((db.data_dir() / 'riwaq.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try: yield conn
    finally: conn.close()


async def main():
    os.environ.setdefault('RIWAQ_EMBED_MODEL', 'embeddinggemma-2:270m-q8_0-text')
    public.get_db = readonly_db
    cfg = db.settings() | {'temperature': 0, 'seed': 1729}
    assert cfg['provider'] == 'ollama'
    await providers.validate_provider('ollama', cfg['model'], 'local')
    tests = json.loads((ROOT / 'evaluation/knowledge-eval.json').read_text(encoding='utf-8'))
    cases = [next(c for c in tests['cases'] if c['id'] == key) for key in IDS]
    report = {'version': 'research-answer-eval-v1', 'started_at': db.now(), 'settings': cfg,
              'case_ids_fixed_before_run': IDS, 'dataset_sha256': hashlib.sha256((ROOT / 'evaluation/knowledge-eval.json').read_bytes()).hexdigest(),
              'conditions': ['legacy_original_candidates', 'legacy_selected_candidates', 'structured_selected_candidates'],
              'reference_answers_sent_to_model': False, 'human_review_completed': False,
              'paid_provider_requests': 0, 'model_weights_changed': False,
              'acceptance': 'No ready claim from serialization alone. Compare supported answer, refusal, unsupported claims and latency; retain ordinary chat policy unless a quality gain is established.',
              'caveat': 'Single sample per condition, same model/config; earlier heldout cases are historically exposed. Selection was tuned only on py01 development. No retuning after this run.',
              'cases': []}
    if OUT.exists():
        OUT.rename(OUT.with_name('research-answer-comparison-prior-' + str(int(time.time())) + '.json'))
    def save(): OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    async def authorized(): pass
    with deny_external_connections() as guard:
        for index, case in enumerate(cases):
            row = {'id': case['id'], 'query': case['query'], 'language': case['language'],
                   'expected_behavior': case['expected_behavior'], 'reference': case.get('gold_answer'), 'conditions': []}
            report['cases'].append(row)
            candidates = await public.retrieve_public(case['query'], limit=8)
            original = candidates[:6]; selected = research.select_evidence(case['query'], candidates)[:6]
            # Alternating order limits one-direction warm-up bias; no cherry-picking.
            order = [0, 1, 2] if index % 2 == 0 else [2, 1, 0]
            for condition in order:
                chunks = original if condition == 0 else selected
                messages, chunks = grounding.compose({'instructions': ''}, [], [], case['query'], chunks, cfg, True, policy='legacy-v1')
                entry = {'condition': report['conditions'][condition], 'evidence': chunks, 'content': '', 'metrics': {}, 'error': None}
                row['conditions'].append(entry); began = time.monotonic()
                try:
                    async with asyncio.timeout(120):
                        if condition == 2:
                            receipt = research.new_receipt(ResearchOptions(scopes=['knowledge'], response_language=case['language']))
                            async with aclosing(research_answer.stream(cfg['model'], case['query'], chunks, ResearchOptions(scopes=['knowledge'], response_language=case['language']), cfg, receipt, authorized)) as output:
                                async for packet in output:
                                    entry['content'] += packet.get('content', '')
                                    entry['metrics'].update(packet.get('metrics', {}))
                            entry['receipt'] = receipt
                        else:
                            async with aclosing(providers.stream('ollama', cfg['model'], messages, cfg)) as output:
                                async for packet in output:
                                    entry['content'] += packet.get('content', '')
                                    entry['metrics'].update(packet.get('metrics', {}))
                except Exception as exc:
                    entry['error'] = type(exc).__name__ + ': ' + str(exc)[:300]
                entry['seconds'] = round(time.monotonic() - began, 3)
                save()
                print(json.dumps({'id': case['id'], 'condition': condition, 'seconds': entry['seconds'], 'error': entry['error']}, ensure_ascii=False), flush=True)
        report['network_guard'] = guard
    report['completed_at'] = db.now(); save()


if __name__ == '__main__': asyncio.run(main())
