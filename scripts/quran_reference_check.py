"""Live check (python scripts/quran_reference_check.py --model qwen3:4b-instruct --out DIR): does the local model copy verified verses exactly?

Builds the same messages the chat sends (grounding.compose with the Qur'an application check data), asks the local model,
and compares its answer with the installed text character for character. Loopback only. Verse wording is never written to the report:
it records references, verdicts and lengths.
"""
import argparse, json, re, sys, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import grounding, quran_reference as q  # noqa: E402

CASES = [('ما الآية 2 من سورة الفاتحة؟', [(1, 2)], None), ('اكتب آية الكرسي', [(2, 255)], None), ('اكتب الآيات 1-4 من سورة الإخلاص', [(112, 1), (112, 2), (112, 3), (112, 4)], None),
         ('ما نص الآية 1 من سورة يس؟', [(36, 1)], None), ('اكتب سورة العصر الآيات 1 إلى 3', [(103, 1), (103, 2), (103, 3)], None), ('ما الآية 286 من سورة البقرة؟', [(2, 286)], None),
         ('اشرح باختصار الآية 13 من سورة الحجرات واذكر نصها', [(49, 13)], None), ('اكتب نص الآية رقم 300 من سورة البقرة.', [], '286'), ('ما الآية 10 من سورة الكوثر؟', [], '3'),
         ('اكتب الآية 7 من سورة الناس', [], '6')]
MARKS = re.compile(r'[\u064B-\u0652\u0670]')


def ask(endpoint, model, messages):
    body = json.dumps({'model': model, 'stream': False, 'think': False, 'messages': messages, 'options': {'temperature': 0.3, 'seed': 7, 'num_predict': 900, 'num_ctx': 8192}}).encode()
    with urllib.request.urlopen(urllib.request.Request(endpoint + '/api/chat', data=body, headers={'Content-Type': 'application/json'}), timeout=240) as res:
        return (json.loads(res.read().decode('utf-8')).get('message') or {}).get('content', '')


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--model', default='qwen3:4b-instruct'); parser.add_argument('--out', required=True)
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434'); args = parser.parse_args()
    data = q.load()
    if 'error' in data:
        print(json.dumps({'ok': False, 'reason': data['error']})); return 1
    rows = []
    for question, refs, count in CASES:
        messages, _ = grounding.compose({'instructions': ''}, [], [], question, [], {'context_size': 8192, 'max_tokens': 1024}, False, tool_context=q.context(question))
        raw = ask(args.endpoint, args.model, messages)
        guarded, report = q.render(raw, question)
        def judge(answer):
            flat = re.sub(r'\s+', ' ', answer)
            if refs:
                exact = [re.sub(r'\s+', ' ', data['verses'][r]) in flat for r in refs]
                return ('exact' if all(exact) else 'altered_or_missing'), exact
            # A verse that does not exist: the count must be stated and no vocalised passage may appear.
            invented = bool(q._runs(q._words(answer.replace(q.REMOVED, ''))))
            return ('invented_text' if invented else 'correct_denial' if count in answer.translate(q.DIGITS) else 'denial_without_count'), []
        before, _ = judge(raw); verdict, exact = judge(guarded)
        # After the guard, every vocalised passage left in the answer must be an exact stretch of the verified file.
        corpus = q._corpus(); words = q._words(guarded.replace(q.REMOVED, ''))
        unverified = sum(1 for i, j in q._runs(words) if q.flat(' '.join(w['core'] for w in words[i:j + 1])) not in corpus)
        rows.append({'question': question, 'references': [f'{s}:{a}' for s, a in refs], 'model_alone': before, 'verdict': verdict, 'exact_per_verse': exact,
                     'guard': report, 'unverified_passages_left': unverified, 'answer_chars': len(guarded)})
        print(question, '=>', before, '->', verdict, report, 'unverified left:', unverified)
    good = sum(r['verdict'] in ('exact', 'correct_denial') for r in rows)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    (out / 'quran_reference_check.json').write_text(json.dumps({'model': args.model, 'file_sha256': data['sha256'], 'passed': good, 'model_alone_passed': sum(r['model_alone'] in ('exact', 'correct_denial') for r in rows), 'unverified_passages_left': sum(r['unverified_passages_left'] for r in rows), 'total': len(rows), 'rows': rows}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({'passed': good, 'total': len(rows)}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
