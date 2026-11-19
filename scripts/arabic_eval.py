"""Arabic quality measurement for local chat models (python scripts/arabic_eval.py --models qwen3:4b-instruct qwen3:8b --out DIR).

Sends each item in training/arabic_eval/items.json to Ollama at temperature 0 and
grades objective items with deterministic checks. Free-writing items are never
auto-graded: they are printed in the report for a human to rate. A pass here means
"matched the expected answer", not "writes well". `--selftest` checks the graders
against each item's reference and wrong answer without calling any model.
Standard library only. Nothing leaves the machine: the endpoint must be loopback.
"""
import argparse, json, re, sys, time, urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
ITEMS = ROOT / 'training' / 'arabic_eval' / 'items.json'
DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')


def norm(text):
    text = re.sub(r'<think>.*?</think>', '', text or '', flags=re.S)
    text = re.sub(r'[ً-ٰٟـ]', '', text).translate(DIGITS)
    text = re.sub('[أإآٱ]', 'ا', text).replace('ى', 'ي').replace('ة', 'ه')
    return re.sub(r'\s+', ' ', text).strip().lower()


def words(text):
    return [w for w in re.split(r'[\s،,.؛;:!؟?«»"()\[\]{}\-—*`]+', norm(text)) if w]


def numbers(text):
    return [float(n.replace(',', '')) for n in re.findall(r'-?\d[\d,]*\.?\d*', norm(text).replace('٬', ',').replace('٫', '.'))]


def grade(check, answer):
    kind, text = check['type'], norm(answer)
    if kind == 'human':
        return None
    if kind == 'mcq':
        want = norm(check['answer'])
        found = re.findall(r'(?<![ء-ي])([ابجد])(?![ء-ي])', text)
        return bool(found) and found[0] == want and all(f == want for f in found[:1])
    if kind == 'number':
        lines = [l for l in re.sub(r'<think>.*?</think>', '', answer or '', flags=re.S).splitlines() if numbers(l)]
        found = numbers(lines[-1]) if lines else []
        return bool(found) and abs(found[-1] - check['answer']) < 1e-6
    if kind == 'contains':
        ok = all(norm(t) in text for t in check.get('all', []))
        return ok and all(any(norm(t) in text for t in group) for group in check.get('any_groups', []))
    if kind == 'last_line':
        lines = [l.strip().strip('`').strip() for l in re.sub(r'<think>.*?</think>', '', answer, flags=re.S).strip().splitlines() if l.strip().strip('`').strip()]
        return bool(lines) and any(lines[-1].lower() == a.lower() or lines[-1].lower().endswith(' ' + a.lower()) for a in check['answers'])
    if kind == 'list':
        parts = [p.strip() for p in re.split('[،,]', norm(answer).rstrip('.')) if p.strip()]
        return len(parts) == check['count'] and all(len(p.split()) <= check['max_words_each'] for p in parts)
    if kind == 'exact_words':
        got = words(answer)
        return 0 < len(got) <= check['max_words'] and any(norm(a) in got for a in check['answers'])
    if kind == 'word_count':
        got = words(answer)
        return check['min'] <= len(got) <= check['max'] and all(norm(t) in text for t in check.get('all', []))
    if kind == 'order':
        pos = [text.find(norm(w)) for w in check['sequence']]
        return all(p >= 0 for p in pos) and pos == sorted(pos)
    if kind == 'json':
        raw = re.sub(r'<think>.*?</think>', '', answer, flags=re.S).strip()
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw)
        try:
            obj = json.loads(raw)
        except ValueError:
            return False
        if not isinstance(obj, dict):
            return False
        same = all(obj.get(k) == v or str(obj.get(k)) == str(v) for k, v in check.get('equals', {}).items())
        return same and all(v in str(obj.get(k, '')) for k, v in check.get('contains', {}).items())
    raise ValueError(f'unknown check type: {kind}')


def load():
    return json.loads(ITEMS.read_text(encoding='utf-8'))['items']


def selftest():
    bad = []
    for item in load():
        if item['check']['type'] == 'human':
            continue
        if grade(item['check'], item['ref']) is not True:
            bad.append((item['id'], 'reference answer rejected'))
        if grade(item['check'], item['bad']) is not False:
            bad.append((item['id'], 'wrong answer accepted'))
    return bad


def ask(endpoint, model, prompt, timeout):
    body = json.dumps({'model': model, 'stream': False, 'messages': [{'role': 'user', 'content': prompt}],
                       'options': {'temperature': 0, 'seed': 7, 'num_predict': 700}}).encode()
    req = urllib.request.Request(endpoint + '/api/chat', data=body, headers={'Content-Type': 'application/json'})
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as res:
        data = json.loads(res.read().decode('utf-8'))
    seconds = time.monotonic() - started
    count, dur = data.get('eval_count') or 0, data.get('eval_duration') or 0
    return (data.get('message') or {}).get('content', ''), seconds, (count / (dur / 1e9) if dur else None)


def run(endpoint, model, items, timeout, log=print):
    rows = []
    for i, item in enumerate(items, 1):
        try:
            answer, seconds, tps = ask(endpoint, model, item['prompt'], timeout)
            verdict, error = grade(item['check'], answer), None
        except Exception as exc:  # a failed request is a failed item, recorded as such
            answer, seconds, tps, verdict, error = '', None, None, False if item['check']['type'] != 'human' else None, type(exc).__name__
        rows.append({'id': item['id'], 'category': item['category'], 'passed': verdict, 'seconds': seconds, 'tokens_per_second': tps,
                     'error': error, 'answer': re.sub(r'<think>.*?</think>', '', answer, flags=re.S).strip()[:1200]})
        log(f'  [{i}/{len(items)}] {item["id"]} {"PASS" if verdict else "human" if verdict is None else "FAIL"}')
    return rows


def summarize(rows):
    cats = {}
    for r in rows:
        if r['passed'] is None:
            continue
        c = cats.setdefault(r['category'], [0, 0])
        c[0] += bool(r['passed']); c[1] += 1
    done = [r for r in rows if r['passed'] is not None]
    speeds = [r['tokens_per_second'] for r in rows if r['tokens_per_second']]
    times = [r['seconds'] for r in rows if r['seconds']]
    return {'passed': sum(bool(r['passed']) for r in done), 'graded': len(done), 'errors': sum(1 for r in rows if r['error']),
            'by_category': {k: {'passed': v[0], 'total': v[1]} for k, v in cats.items()},
            'median_tokens_per_second': sorted(speeds)[len(speeds) // 2] if speeds else None,
            'median_seconds': sorted(times)[len(times) // 2] if times else None}


def report(results, items):
    lines = ['# قياس العربية للنماذج المحلية', '', 'القياس آلي على أسئلة لها جواب محدد. النجاح يعني مطابقة الجواب المتوقع، لا جودة الأسلوب. الكتابة الحرة في آخر التقرير للتقييم البشري.', '',
             '| النموذج | الصحيح | النسبة | أخطاء اتصال | وسيط السرعة (رمز/ث) | وسيط زمن الجواب (ث) |', '|---|---|---|---|---|---|']
    for model, data in results.items():
        s = data['summary']
        pct = f"{100 * s['passed'] / s['graded']:.0f}%" if s['graded'] else '—'
        tps = f"{s['median_tokens_per_second']:.1f}" if s['median_tokens_per_second'] else '—'
        sec = f"{s['median_seconds']:.1f}" if s['median_seconds'] else '—'
        lines.append(f"| {model} | {s['passed']}/{s['graded']} | {pct} | {s['errors']} | {tps} | {sec} |")
    cats = []
    for item in items:
        if item['check']['type'] != 'human' and item['category'] not in cats:
            cats.append(item['category'])
    lines += ['', '## حسب الفئة', '', '| الفئة | ' + ' | '.join(results) + ' |', '|---|' + '---|' * len(results)]
    for cat in cats:
        cells = [f"{d['summary']['by_category'].get(cat, {}).get('passed', 0)}/{d['summary']['by_category'].get(cat, {}).get('total', 0)}" for d in results.values()]
        lines.append(f'| {cat} | ' + ' | '.join(cells) + ' |')
    lines += ['', '## الأسئلة التي أخطأ فيها كل نموذج', '']
    for model, data in results.items():
        failed = [r['id'] for r in data['rows'] if r['passed'] is False]
        lines.append(f"- **{model}**: {', '.join(failed) if failed else 'لا شيء'}")
    lines += ['', '## الكتابة الحرة (للتقييم البشري)', '']
    for item in items:
        if item['check']['type'] != 'human':
            continue
        lines += [f"### {item['id']} — {item['prompt']}", '']
        for model, data in results.items():
            row = next(r for r in data['rows'] if r['id'] == item['id'])
            lines += [f'**{model}:**', '', row['answer'] or '_(لا جواب)_', '']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--models', nargs='+'); parser.add_argument('--out', default=str(ROOT / 'reports' / 'arabic-eval'))
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434'); parser.add_argument('--timeout', type=float, default=240)
    parser.add_argument('--selftest', action='store_true')
    args = parser.parse_args()
    if args.selftest:
        bad = selftest()
        print(json.dumps({'ok': not bad, 'problems': bad}, ensure_ascii=False))
        return 1 if bad else 0
    if not args.models:
        parser.error('--models is required')
    if urlparse(args.endpoint).hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('The endpoint must be loopback; this measurement never sends prompts off the machine.')
    items, out, results = load(), Path(args.out), {}
    out.mkdir(parents=True, exist_ok=True)
    for model in args.models:
        print(f'{model}:')
        rows = run(args.endpoint, model, items, args.timeout)
        results[model] = {'summary': summarize(rows), 'rows': rows}
        (out / 'arabic_eval.json').write_text(json.dumps({'items': len(items), 'results': results}, ensure_ascii=False, indent=1), encoding='utf-8')
    (out / 'ARABIC_EVAL.md').write_text(report(results, items), encoding='utf-8')
    print(json.dumps({m: d['summary'] for m, d in results.items()}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
