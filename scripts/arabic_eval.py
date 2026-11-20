"""Arabic quality measurement for local chat models (python scripts/arabic_eval.py --models qwen3:4b-instruct qwen3:8b --out DIR).

Sends each item in training/arabic_eval/items.json to Ollama at temperature 0 and
grades objective items with deterministic checks. Free-writing items are never
auto-graded: they are printed in the report for a human to rate. A pass here means
"matched the expected answer", not "writes well". `--selftest` checks the graders
against each item's reference and wrong answer without calling any model.
Standard library only. Nothing leaves the machine: the endpoint must be loopback.
"""
import argparse, json, re, sys, time, urllib.error, urllib.request
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


CONTRADICTION = ['لكن هذا غير صحيح', 'هذا غير صحيح ايضا', 'الاجابه الصحيحه هي', 'عفوا، الصحيح']
ABSTAIN = ['لا اعرف', 'لا اعلم', 'لست متاكد', 'لا استطيع التاكد', 'لا استطيع تاكيد', 'لا املك معلومات', 'ليس لدي معلومات', 'غير متاكد']


def abstained(answer):
    text = norm(answer)
    return any(m in text for m in ABSTAIN)


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
        ok = all(norm(t) in text for t in check.get('all', [])) and not any(norm(t) in text for t in check.get('none', []))
        return ok and all(any(norm(t) in text for t in group) for group in check.get('any_groups', []))
    if kind == 'language':
        # Same-language reply: the right script, several of the language's commonest words, and not an English answer.
        plain = re.sub(r'<think>.*?</think>', '', answer or '', flags=re.S).lower()
        letters = [c for c in plain if c.isalpha()]
        if len(letters) < 20:
            return False
        ranges = {'hebrew': ('\u0590', '\u05ff'), 'greek': ('\u0370', '\u03ff'), 'cyrillic': ('\u0400', '\u04ff'), 'latin': ('a', '\u024f')}
        lo, hi = ranges[check.get('script', 'latin')]
        if sum(lo <= c <= hi for c in letters) / len(letters) < 0.7:
            return False
        tokens = set(re.split(r"[^\w'-]+", plain))
        hits = sum(1 for w in set(check['words']) if w in tokens or (w.endswith('-') and any(t.startswith(w) for t in tokens)))
        english = sum(1 for w in ('the', 'and', 'is', 'of', 'because', 'than', 'we', 'before') if w in tokens)
        return hits >= 3 and (check.get('english') or english < 3)
    if kind == 'refusal':
        # Strict: the denial must open the answer. An answer that first asserts something and then takes it back,
        # or that degenerates into a repeated character, fabricated before it refused and fails.
        if re.search(r'(.)\1{15,}', answer or '') or any(norm(m) in text for m in CONTRADICTION):
            return False
        return any(norm(m) in text[:170] for m in check['markers'])
    if kind == 'last_line':
        # Lines in ⟦…⟧ are Riwaq's own notes about the answer, not part of the stated output.
        lines = [l.strip().strip('`').strip() for l in re.sub(r'<think>.*?</think>', '', answer, flags=re.S).strip().splitlines() if l.strip().strip('`').strip() and not l.strip().startswith('⟦')]
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
    return json.loads(Path(ITEMS).read_text(encoding='utf-8'))['items']


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


APP = object()  # variant marker: measure through Riwaq's own chat pipeline
VERIFY = ('راجع إجابتك السابقة قبل اعتمادها: هل فيها اسم أو رقم أو تاريخ أو اقتباس أو دالة لست متأكدًا منها؟ هل في السؤال افتراض خاطئ؟ '
          'هل خالفت صيغة الإخراج المطلوبة؟ إن وجدت خطأ فصحّحه، وإن لم تكن متأكدًا فصرّح بذلك. اكتب الآن الإجابة النهائية فقط بالصيغة التي طلبها السؤال، دون شرح للمراجعة.')


def ask_app(endpoint, model, prompt, timeout):
    """The answer as Riwaq's ordinary chat produces it: its system text and accuracy rules, application check data, word-count repair and Qur'an guard."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from backend import grounding, quran_reference, text_checks
    from backend import code_run
    ran = code_run.run(code_run.extract(prompt)) if code_run.extract(prompt) else None
    messages, _ = grounding.compose({'instructions': ''}, [], [], prompt, [], {'context_size': 8192, 'max_tokens': 1024}, False,
                                    tool_context=text_checks.context(prompt, code_run.context(ran)))
    answer, seconds, tps = chat(endpoint, model, messages, timeout)
    rule = text_checks.constraint(prompt)
    if rule and answer.strip() and not text_checks.satisfied(answer, rule) and len(answer) <= 2000:
        second, more, tps = chat(endpoint, model, messages + [{'role': 'assistant', 'content': answer}, {'role': 'user', 'content': text_checks.feedback(answer, rule)}], timeout)
        seconds += more
        if second.strip() and (text_checks.satisfied(second, rule) or abs(text_checks.word_count(second) - rule[1]) < abs(text_checks.word_count(answer) - rule[1])):
            answer = second
        if not text_checks.satisfied(answer, rule):
            answer = answer.rstrip() + text_checks.note(answer, rule)
    if quran_reference.context(prompt):
        answer = quran_reference.render(answer, prompt)[0]
    answer = code_run.finalize(answer, ran)
    return answer, seconds, tps


def ask(endpoint, model, prompt, timeout, system=None, verify=False):
    if system == APP:
        return ask_app(endpoint, model, prompt, timeout)
    messages = ([{'role': 'system', 'content': system}] if system else []) + [{'role': 'user', 'content': prompt}]
    answer, seconds, tps = chat(endpoint, model, messages, timeout)
    if verify:
        final, more, tps = chat(endpoint, model, messages + [{'role': 'assistant', 'content': answer}, {'role': 'user', 'content': VERIFY}], timeout)
        return final, seconds + more, tps
    return answer, seconds, tps


def chat(endpoint, model, messages, timeout):
    # Thinking is switched off so a reasoning model spends its budget on the answer, not on hidden text that leaves the answer empty.
    payload = {'model': model, 'stream': False, 'think': False, 'messages': messages,
               'options': {'temperature': 0, 'seed': 7, 'num_predict': 1500}}
    started = time.monotonic()
    for attempt in (0, 1):
        req = urllib.request.Request(endpoint + '/api/chat', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                data = json.loads(res.read().decode('utf-8'))
            break
        except urllib.error.HTTPError as exc:
            if attempt or exc.code != 400:
                raise
            payload.pop('think')  # a model without a thinking switch rejects the field
    seconds = time.monotonic() - started
    count, dur = data.get('eval_count') or 0, data.get('eval_duration') or 0
    return (data.get('message') or {}).get('content', ''), seconds, (count / (dur / 1e9) if dur else None)


def run(endpoint, model, items, timeout, log=print, system=None, verify=False):
    rows = []
    for i, item in enumerate(items, 1):
        try:
            answer, seconds, tps = ask(endpoint, model, item['prompt'], timeout, system, verify)
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
            'empty_answers': sum(1 for r in rows if not r.get('answer') and not r['error']),
            # A wrong answer that says "I don't know" is an honest miss; a wrong answer stated as fact is the failure that matters most.
            'abstained_wrong': sum(1 for r in done if not r['passed'] and abstained(r.get('answer', ''))),
            'confident_wrong': sum(1 for r in done if not r['passed'] and not abstained(r.get('answer', ''))),
            'by_category': {k: {'passed': v[0], 'total': v[1]} for k, v in cats.items()},
            'median_tokens_per_second': sorted(speeds)[len(speeds) // 2] if speeds else None,
            'median_seconds': sorted(times)[len(times) // 2] if times else None}


def report(results, items):
    lines = ['# قياس العربية للنماذج المحلية', '', 'القياس آلي على أسئلة لها جواب محدد. النجاح يعني مطابقة الجواب المتوقع، لا جودة الأسلوب. الكتابة الحرة في آخر التقرير للتقييم البشري.', '',
             '| النموذج | الصحيح | النسبة | خطأ بثقة | «لا أعرف» | أجوبة فارغة | وسيط السرعة (رمز/ث) | وسيط زمن الجواب (ث) |', '|---|---|---|---|---|---|---|---|']
    for model, data in results.items():
        s = data['summary']
        pct = f"{100 * s['passed'] / s['graded']:.0f}%" if s['graded'] else '—'
        tps = f"{s['median_tokens_per_second']:.1f}" if s['median_tokens_per_second'] else '—'
        sec = f"{s['median_seconds']:.1f}" if s['median_seconds'] else '—'
        lines.append(f"| {model} | {s['passed']}/{s['graded']} | {pct} | {s.get('confident_wrong', 0)} | {s.get('abstained_wrong', 0)} | {s.get('empty_answers', 0)} | {tps} | {sec} |")
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
    parser.add_argument('--items', help='another items file in the same format (for example training/multilingual_eval/items.json)')
    parser.add_argument('--rescore', help='re-grade a saved arabic_eval.json with the current graders (saved answers are truncated to 1200 characters)')
    parser.add_argument('--variants', nargs='+', help='name=system_prompt_file, name=none, or name=app for Riwaq\'s own chat pipeline; a name ending in +verify adds a self-review pass')
    args = parser.parse_args()
    if args.items:
        global ITEMS
        ITEMS = Path(args.items)
    if args.selftest:
        bad = selftest()
        print(json.dumps({'ok': not bad, 'problems': bad}, ensure_ascii=False))
        return 1 if bad else 0
    if args.rescore:
        data = json.loads(Path(args.rescore).read_text(encoding='utf-8'))
        checks = {i['id']: i['check'] for i in load()}
        for entry in data['results'].values():
            for row in entry['rows']:
                if row['id'] in checks and not row['error']:
                    row['passed'] = grade(checks[row['id']], row['answer'])
            entry['summary'] = summarize(entry['rows'])
        out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
        (out / 'arabic_eval.json').write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        (out / 'ARABIC_EVAL.md').write_text(report(data['results'], [i for i in load() if any(r['id'] == i['id'] for e in data['results'].values() for r in e['rows'])]), encoding='utf-8')
        print(json.dumps({m: d['summary'] for m, d in data['results'].items()}, ensure_ascii=False, indent=1))
        return 0
    if not args.models:
        parser.error('--models is required')
    if urlparse(args.endpoint).hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('The endpoint must be loopback; this measurement never sends prompts off the machine.')
    items, out, results = load(), Path(args.out), {}
    out.mkdir(parents=True, exist_ok=True)
    variants = [('', None, False)]
    if args.variants:
        variants = []
        for spec in args.variants:
            name, _, path = spec.partition('=')
            verify = name.endswith('+verify')
            variants.append((name, APP if path == 'app' else None if path in ('', 'none') else Path(path).read_text(encoding='utf-8').strip(), verify))
    for model in args.models:
        for name, system, verify in variants:
            label = f'{model} · {name}' if name else model
            print(f'{label}:')
            rows = run(args.endpoint, model, items, args.timeout, system=system, verify=verify)
            results[label] = {'summary': summarize(rows), 'rows': rows}
            (out / 'arabic_eval.json').write_text(json.dumps({'items': len(items), 'results': results}, ensure_ascii=False, indent=1), encoding='utf-8')
    (out / 'ARABIC_EVAL.md').write_text(report(results, items), encoding='utf-8')
    print(json.dumps({m: d['summary'] for m, d in results.items()}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
