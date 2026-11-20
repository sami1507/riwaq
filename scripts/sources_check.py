"""Live comparison on the knowledge and no-fabrication items: the local model alone, and with Wikipedia passages.

Sends the text of these fixed test questions to Wikipedia (HTTPS) and to the local Ollama only.
Usage: python scripts/sources_check.py --model qwen3:4b-instruct --out <folder>
"""
import argparse, asyncio, json, sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
import arabic_eval  # noqa: E402
from backend import grounding, text_checks, wiki_sources  # noqa: E402

CATEGORIES = ('معرفة ولغة', 'الأمانة وعدم الاختلاق', 'إجابة المعروف (ضد التحفظ الزائد)')


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--model', required=True); parser.add_argument('--out', required=True)
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434'); parser.add_argument('--timeout', type=float, default=240)
    args = parser.parse_args()
    if urlparse(args.endpoint).hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('The endpoint must be loopback.')
    rows = []
    for item in [i for i in arabic_eval.load() if i['category'] in CATEGORIES and i['check']['type'] != 'human']:
        question = item['prompt']
        try:
            sources = asyncio.run(wiki_sources.fetch(question))
            error = ''
        except wiki_sources.SourceError as exc:
            sources, error = None, str(exc)
        def answer(extra):
            messages, _ = grounding.compose({'instructions': ''}, [], [], question, [], {'context_size': 8192, 'max_tokens': 768}, False, tool_context=text_checks.context(question, extra))
            return arabic_eval.chat(args.endpoint, args.model, messages, args.timeout)[0]
        alone = answer({})
        sourced = answer(wiki_sources.context(sources)) if sources is not None else ''
        rows.append({'id': item['id'], 'category': item['category'], 'sources': [s['title'] for s in sources or []], 'fetch_error': error,
                     'alone': alone[:500], 'sourced': sourced[:500], 'cited': '[' in sourced and ']' in sourced,
                     'passed_alone': bool(arabic_eval.grade(item['check'], alone)), 'passed_sourced': bool(arabic_eval.grade(item['check'], sourced)) if sources is not None else None})
        print(item['id'], rows[-1]['passed_alone'], rows[-1]['passed_sourced'], len(sources or []), flush=True)
    done = [r for r in rows if r['passed_sourced'] is not None]
    cats = {}
    for r in done:
        c = cats.setdefault(r['category'], {'items': 0, 'alone': 0, 'sourced': 0}); c['items'] += 1; c['alone'] += r['passed_alone']; c['sourced'] += r['passed_sourced']
    summary = {'model': args.model, 'items': len(rows), 'reached_wikipedia': len(done), 'with_at_least_one_passage': sum(bool(r['sources']) for r in done),
               'correct_alone': sum(r['passed_alone'] for r in done), 'correct_with_sources': sum(r['passed_sourced'] for r in done),
               'fixed_by_sources': sum(r['passed_sourced'] and not r['passed_alone'] for r in done), 'broken_by_sources': sum(r['passed_alone'] and not r['passed_sourced'] for r in done), 'by_category': cats}
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    (out / 'sources_check.json').write_text(json.dumps({'summary': summary, 'rows': rows}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if done else 1


if __name__ == '__main__':
    sys.exit(main())
