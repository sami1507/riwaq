"""Live check of "Teach Riwaq" with the local model: taught facts must come back, and unrelated questions must not recall a lesson.

The taught facts are invented, so the model cannot know them without the lesson. Loopback only.
Usage: python scripts/lessons_check.py --model qwen3:4b-instruct --out <folder>
"""
import argparse, json, sqlite3, sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
import arabic_eval  # noqa: E402
from backend import grounding, lessons, text_checks  # noqa: E402

TAUGHT = [
    ('متى موعد صيانة الضاغط رقم 4؟', 'يُصان الضاغط رقم 4 كل 730 ساعة تشغيل.', 'بعد كم ساعة تشغيل نصون الضاغط 4؟', ['730']),
    ('ما اسم مشرف وردية الليل في مستودع الزيتون؟', 'مشرف وردية الليل في مستودع الزيتون هو مروان الخطيب.', 'من هو مشرف الوردية الليلية في مستودع الزيتون؟', ['مروان']),
    ('رمز خطأ E-417 في آلة التعبئة', 'الرمز E-417 يعني انسداد حساس الوزن، والحل تنظيف الحساس ثم إعادة المعايرة.', 'ماذا يعني الخطأ E-417 في آلة التعبئة؟', ['حساس']),
    ('What port does the Sadaf internal service listen on?', 'The Sadaf internal service listens on port 48213.', 'Which port is the Sadaf internal service on?', ['48213']),
    ('מה שעת הסגירה של סניף אלון?', 'סניף אלון נסגר בשעה 21:40.', 'באיזו שעה נסגר סניף אלון?', ['21:40']),
    ('دالة تنسيق التاريخ في مشروعنا', 'في مشروعنا نستخدم الدالة fmt_riwaq_date(d) من الملف utils/dates.py ولا نستخدم strftime مباشرة.', 'أي دالة نستخدم لتنسيق التاريخ في مشروعنا؟', ['fmt_riwaq_date']),
]
UNRELATED = ['ما عاصمة اليابان؟', 'اكتب دالة بايثون تجمع عددين.', 'What is 12 times 12?']


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--model', required=True); parser.add_argument('--out', required=True)
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434'); parser.add_argument('--timeout', type=float, default=240)
    args = parser.parse_args()
    if urlparse(args.endpoint).hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('The endpoint must be loopback.')
    c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row; lessons.init(c)
    for title, content, _, _ in TAUGHT:
        lessons.add(c, 'check', title, content)
    rows = []
    for title, content, question, must in TAUGHT:
        found = lessons.recall(c, 'check', question)
        def answer(extra):
            messages, _ = grounding.compose({'instructions': ''}, [], [], question, [], {'context_size': 8192, 'max_tokens': 512}, False, tool_context=text_checks.context(question, extra))
            return arabic_eval.chat(args.endpoint, args.model, messages, args.timeout)[0]
        with_lesson, without = answer(lessons.context(found)), answer({})
        rows.append({'question': question, 'recalled': bool(found) and found[0]['title'] == title, 'with_lesson': with_lesson[:600], 'without_lesson': without[:600],
                     'passed_with': all(m in with_lesson for m in must), 'passed_without': all(m in without for m in must)})
    stray = [{'question': q, 'recalled': [f['title'] for f in lessons.recall(c, 'check', q)]} for q in UNRELATED]
    summary = {'model': args.model, 'taught': len(rows), 'recalled': sum(r['recalled'] for r in rows), 'correct_with_lesson': sum(r['passed_with'] for r in rows),
               'correct_without_lesson': sum(r['passed_without'] for r in rows), 'unrelated_questions': len(stray), 'unrelated_that_recalled_a_lesson': sum(bool(s['recalled']) for s in stray)}
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    (out / 'lessons_check.json').write_text(json.dumps({'summary': summary, 'rows': rows, 'unrelated': stray}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary['recalled'] == len(rows) and summary['correct_with_lesson'] == len(rows) and not summary['unrelated_that_recalled_a_lesson'] else 1


if __name__ == '__main__':
    sys.exit(main())
