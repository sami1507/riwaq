"""Verified Qur'an reference for chat: the model never supplies verse wording from memory.

The text is the user's own verbatim download from Tanzil (https://tanzil.net, CC BY 3.0,
changes not allowed), kept outside the repository at <data>/reference/quran-uthmani.txt.
This module only reads it, checks its shape against the fixed verse counts below, and
hands the exact verse text (or the fact that a verse does not exist) to the request as
application check data. Parsing is deliberately narrow: an explicit «سورة <name>» plus
verse numbers, «آية الكرسي», or sura:aya digits next to a Qur'an word.
"""
import difflib
import hashlib
import json
import re
import sys

from backend.db import data_dir

NAMES = ('الفاتحة البقرة آل_عمران النساء المائدة الأنعام الأعراف الأنفال التوبة يونس هود يوسف الرعد إبراهيم الحجر النحل الإسراء الكهف مريم طه '
         'الأنبياء الحج المؤمنون النور الفرقان الشعراء النمل القصص العنكبوت الروم لقمان السجدة الأحزاب سبأ فاطر يس الصافات ص الزمر غافر '
         'فصلت الشورى الزخرف الدخان الجاثية الأحقاف محمد الفتح الحجرات ق الذاريات الطور النجم القمر الرحمن الواقعة الحديد المجادلة الحشر الممتحنة '
         'الصف الجمعة المنافقون التغابن الطلاق التحريم الملك القلم الحاقة المعارج نوح الجن المزمل المدثر القيامة الإنسان المرسلات النبأ النازعات عبس '
         'التكوير الانفطار المطففين الانشقاق البروج الطارق الأعلى الغاشية الفجر البلد الشمس الليل الضحى الشرح التين العلق القدر البينة الزلزلة العاديات '
         'القارعة التكاثر العصر الهمزة الفيل قريش الماعون الكوثر الكافرون النصر المسد الإخلاص الفلق الناس').replace('_', ' ').split(' ')
NAMES = [n.replace(' ', ' ') for n in NAMES]
COUNTS = [7, 286, 200, 176, 120, 165, 206, 75, 129, 109, 123, 111, 43, 52, 99, 128, 111, 110, 98, 135, 112, 78, 118, 64, 77, 227, 93, 88, 69, 60,
          34, 30, 73, 54, 45, 83, 182, 88, 75, 85, 54, 53, 89, 59, 37, 35, 38, 29, 18, 45, 60, 49, 62, 55, 78, 96, 29, 22, 24, 13,
          14, 11, 11, 18, 12, 12, 30, 52, 52, 44, 28, 28, 20, 56, 40, 31, 50, 40, 46, 42, 29, 19, 36, 25, 22, 17, 19, 26, 30, 20,
          15, 21, 11, 8, 8, 19, 5, 8, 8, 11, 11, 8, 3, 9, 5, 4, 7, 3, 6, 3, 5, 4, 5, 6]
assert len(NAMES) == len(COUNTS) == 114 and sum(COUNTS) == 6236
ALIASES = {'بني إسرائيل': 17, 'المؤمن': 40, 'حم السجدة': 41, 'الدهر': 76, 'عم': 78, 'الانشراح': 94, 'ألم نشرح': 94, 'تبت': 111, 'اللهب': 111,
           'الصمد': 112, 'براءة': 9, 'القتال': 47, 'الزلزال': 99, 'الملائكة': 35, 'المطففون': 83, 'الكافرين': 109, 'المنافقين': 63, 'المؤمنين': 23}
DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')
MAX_VERSES, MAX_TEXT_BYTES = 10, 3000
SOURCE = 'Tanzil Quran Text (Uthmani), https://tanzil.net, CC BY 3.0, verbatim'
RULE = ("Qur'an reference checked by the application. Quote Qur'anic wording ONLY by copying a `text` value below exactly, character for character, "
        "with its surah and verse number. For anything under `not_found`, say that this verse does not exist and give the surah's verse count. "
        "For any verse without a `text` value here, do NOT write its wording from memory: give only the reference and say the verified text is not available in this request.")


def norm(text):
    text = re.sub(r'[ً-ٰٟـ]', '', text or '').translate(DIGITS)
    return re.sub(r'\s+', ' ', re.sub('[أإآٱ]', 'ا', text).replace('ى', 'ي').replace('ة', 'ه')).strip().lower()


_BY_NAME = sorted([(norm(n), i + 1) for i, n in enumerate(NAMES)] + [(norm(n), s) for n, s in ALIASES.items()], key=lambda item: -len(item[0]))
# A bare «آية» is an everyday word; it counts only next to a number. The other words name the Qur'an outright.
_INTENT = re.compile(r'(?<![\u0621-\u064A])(?:القران|قران|قرانيه|سوره|المصحف)(?![\u0621-\u064A])|قوله تعالي|قال تعالي|قال الله|ايه الكرسي'
                     r'|(?:الايه|ايه|الايات|ايات)\s*(?:رقم\s*)?\d|\b(?:quran|qur.an|surah)\b')
_AYAT = re.compile(r'(?:الايه|ايه|الايات|ايات|الايتين|الايتان)\s*(?:رقم\s*|من\s*)?(\d{1,3})(?:\s*(?:-|–|الي|حتي|و)\s*(?:الايه\s*)?(\d{1,3}))?')
_PAIR = re.compile(r'(?<![\d:.])(\d{1,3})\s*:\s*(\d{1,3})(?:\s*-\s*(\d{1,3}))?(?![\d:])')


def path():
    return data_dir() / 'reference' / 'quran-uthmani.txt'


_cache = {'key': None, 'value': None}


def load():
    """Return {'verses': {(sura, aya): text}, 'sha256': ...} or {'error': reason}. Fails closed on any shape mismatch."""
    file = path()
    try:
        stat = file.stat()
    except OSError:
        return {'error': 'not_installed'}
    key = (str(file), stat.st_mtime_ns, stat.st_size)
    if _cache['key'] == key:
        return _cache['value']
    try:
        raw = file.read_bytes()
        verses, seen = {}, [0] * 114
        for line in raw.decode('utf-8-sig').splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            sura, aya, text = line.split('|', 2)
            sura, aya = int(sura), int(aya)
            if not 1 <= sura <= 114 or aya != seen[sura - 1] + 1 or not text.strip():
                raise ValueError('order')
            seen[sura - 1] = aya
            verses[(sura, aya)] = text.strip()
        if seen != COUNTS:
            raise ValueError('counts')
        value = {'verses': verses, 'sha256': hashlib.sha256(raw).hexdigest()}
    except (ValueError, UnicodeDecodeError, OSError):
        value = {'error': 'invalid_file'}
    _cache.update(key=key, value=value)
    return value


def find_surah(text):
    for match in re.finditer(r'سوره\s+', text):
        rest = text[match.end():]
        for name, number in _BY_NAME:
            if rest.startswith(name) and not re.match(r'[ء-ي]', rest[len(name):len(name) + 1] or ' '):
                return number
        bare = [(name[2:], number) for name, number in _BY_NAME if name.startswith('ال')]
        for name, number in bare:
            if len(name) > 2 and rest.startswith(name) and not re.match(r'[ء-ي]', rest[len(name):len(name) + 1] or ' '):
                return number
    return None


def requests(question):
    """Explicit verse requests in the question: (intent, [(sura, first, last)], sura_only)."""
    text = norm(question)
    if not _INTENT.search(text):
        return False, [], None
    found = []
    if 'ايه الكرسي' in text:
        found.append((2, 255, 255))
    sura = find_surah(text)
    scrubbed = text.replace('ايه الكرسي', ' ')
    if sura:
        for match in _AYAT.finditer(scrubbed):
            first, last = int(match.group(1)), int(match.group(2) or match.group(1))
            found.append((sura, min(first, last), max(first, last)))
    for match in _PAIR.finditer(scrubbed):
        s, first, last = int(match.group(1)), int(match.group(2)), int(match.group(3) or match.group(2))
        if 1 <= s <= 114:
            found.append((s, min(first, last), max(first, last)))
    unique = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return True, unique, sura if sura and not unique else None


def context(question):
    """JSON application-check text for the request, or '' when the question does not concern the Qur'an."""
    intent, wanted, sura_only = requests(question)
    if not intent:
        return ''
    data = load()
    out = {'rule': RULE, 'source': SOURCE, 'verified_text_installed': 'verses' in data}
    verses, missing, unavailable, used = [], [], [], 0
    for sura, first, last in wanted:
        count = COUNTS[sura - 1]
        if first < 1 or first > count:
            missing.append({'surah': sura, 'name': NAMES[sura - 1], 'requested_verse': first, 'surah_verse_count': count})
            continue
        if last > count:
            missing.append({'surah': sura, 'name': NAMES[sura - 1], 'requested_verse': last, 'surah_verse_count': count})
            last = count
        for aya in range(first, last + 1):
            ref = {'surah': sura, 'name': NAMES[sura - 1], 'verse': aya}
            text = data.get('verses', {}).get((sura, aya))
            size = len(text.encode('utf-8')) if text else 0
            if text and len(verses) < MAX_VERSES and used + size <= MAX_TEXT_BYTES:
                verses.append(ref | {'text': text}); used += size
            else:
                unavailable.append(ref | {'reason': 'not_installed' if not text else 'request_limit'})
    if verses: out['verses'] = verses
    if missing: out['not_found'] = missing
    if unavailable: out['text_not_supplied'] = unavailable[:40]
    if sura_only: out['surah'] = {'surah': sura_only, 'name': NAMES[sura_only - 1], 'verse_count': COUNTS[sura_only - 1]}
    return json.dumps({'quran_reference': out}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Output guard. A live check showed the local models alter verse wording even when the exact text is in
# front of them, so the application, not the model, decides what vocalised wording reaches the user:
# a passage is kept only if it is an exact stretch of the verified file; an attempted copy of a supplied
# verse is replaced by that verse's exact text; anything else is removed and marked.
MARK = re.compile(r'[ً-ْٰۖ-ۭ࣐-ࣿ]')
_STRIP = re.compile(r'[ً-ٰٟـۖ-ۭ࣐-ࣿ]')
_WRAP = '«»"\'“”‘’()[]{}﴿﴾*_`.,،؛:!?؟…-–—'
REMOVED = '⟦حُذف نصٌّ مشكول لم يطابق النص القرآني الموثَّق⟧'
APPENDED = 'النص الموثَّق (Tanzil):'


def flat(text):
    return re.sub(r'\s+', ' ', text).strip()


def skeleton(text):
    return flat(re.sub('[أإآٱ]', 'ا', _STRIP.sub('', text)).replace('ى', 'ي'))


def supplied(question):
    """Exact texts of the verses this question explicitly asked for, in request order."""
    _, wanted, _ = requests(question)
    verses, out = load().get('verses', {}), []
    for sura, first, last in wanted:
        for aya in range(max(first, 1), min(last, COUNTS[sura - 1]) + 1):
            if (sura, aya) in verses and len(out) < MAX_VERSES:
                out.append(((sura, aya), verses[(sura, aya)]))
    return out


def _corpus():
    data = load()
    if 'corpus' not in data and 'verses' in data:
        data['corpus'] = ' '.join(flat(text) for text in data['verses'].values())
    return data.get('corpus', '')


def _words(answer):
    """(start, end, core, vocalised, arabic, closes) per word; `core` is the word without wrapping punctuation."""
    out = []
    for m in re.finditer(r'\S+', answer):
        word = m.group(); core = word.strip(_WRAP); lead = len(word) - len(word.lstrip(_WRAP))
        arabic = bool(core) and not re.search(r'[A-Za-z0-9٠-٩]', core) and bool(re.search(r'[؀-ۿ]', core))
        letters = len(re.findall(r'[ء-يٱ]', core))
        marks = len(MARK.findall(core)) if arabic else 0
        closes = bool(re.search(r'[.،؛:!?؟]', word[lead + len(core):]))
        out.append({'start': m.start() + lead, 'end': m.start() + lead + len(core), 'core': core, 'arabic': arabic, 'letters': letters, 'marks': marks, 'closes': closes})
    return out


def _runs(words):
    """Index ranges (i, j) of vocalised passages. Up to two unvocalised Arabic words may sit inside one, because a
    model's altered copy often drops the marks of short words; ordinary prose with a stray tanween does not qualify."""
    runs, i, n = [], 0, len(words)
    while i < n:
        if not (words[i]['arabic'] and words[i]['marks'] and words[i]['letters']):
            i += 1; continue
        j, last, gap = i, i, 0
        while j + 1 < n and not words[j]['closes']:
            nxt = words[j + 1]
            if not nxt['arabic']:
                break
            if nxt['marks'] or not nxt['letters']:
                last, gap = j + 1, 0
            else:
                gap += 1
                if gap > 2:
                    break
            j += 1
        span = words[i:last + 1]
        vocalised = sum(1 for w in span if w['marks'] and w['letters'])
        marks, letters = sum(w['marks'] for w in span), max(1, sum(w['letters'] for w in span))
        if (vocalised >= 4 and marks / letters >= 0.35) or (vocalised >= 2 and marks >= 5 and marks / letters >= 0.7):
            runs.append((i, last))
        i = last + 1
    return runs


def _restore(answer, candidates, report):
    """Replace every attempted copy of a requested verse, vocalised or not, by its exact text."""
    words = _words(answer)
    keys = [skeleton(w['core']) for w in words]
    content = [n for n, k in enumerate(keys) if k]
    found = []
    for key, text in sorted(candidates, key=lambda c: -len(c[0])):
        size = len(key.split())
        if not size:
            continue
        for width in range(max(1, size - 2), size + 3):
            for a in range(0, len(content) - width + 1):
                i, j = content[a], content[a + width - 1]
                # Pause marks and similar signs are tokens without letters: they travel with the wording they follow.
                while j + 1 < len(words) and not keys[j + 1] and words[j + 1]['arabic'] and not words[j]['closes']:
                    j += 1
                if any(not w['arabic'] for w in words[i:j + 1]) or any(w['closes'] for w in words[i:j]):
                    continue
                window = ' '.join(k for k in keys[i:j + 1] if k)
                matcher = difflib.SequenceMatcher(None, window, key, autojunk=False)
                if size <= 2:
                    score = 1.0 if window == key and width == size else 0.0
                else:
                    score = matcher.ratio() if matcher.quick_ratio() >= 0.8 else 0.0
                if score >= 0.8:
                    found.append((score, len(key), i, j, text))
    taken, chosen = set(), []
    for score, _, i, j, text in sorted(found, key=lambda f: (-f[1], -f[0], f[3] - f[2])):
        if taken.isdisjoint(range(i, j + 1)):
            taken.update(range(i, j + 1)); chosen.append((i, j, text))
    out, cursor = [], 0
    for i, j, text in sorted(chosen):
        current = flat(' '.join(w['core'] for w in words[i:j + 1]))
        if current == flat(text):
            continue
        out.append(answer[cursor:words[i]['start']]); out.append(text); cursor = words[j]['end']; report['replaced'] += 1
    out.append(answer[cursor:])
    return ''.join(out)


def render(answer, question):
    """Return (text, report). Only exact verified wording survives as vocalised text."""
    report = {'kept': 0, 'replaced': 0, 'removed': 0, 'appended': 0}
    corpus = _corpus()
    given = supplied(question)
    candidates = []
    for i in range(len(given)):
        for j in range(i, len(given)):
            if all(given[k + 1][0] == (given[k][0][0], given[k][0][1] + 1) for k in range(i, j)):
                text = ' '.join(t for _, t in given[i:j + 1])
                candidates.append((skeleton(text), text))
    if candidates:
        answer = _restore(answer, candidates, report)
    # Whatever vocalised wording is left must be an exact stretch of the verified file, or it goes.
    words = _words(answer)
    out, cursor = [], 0
    for i, j in _runs(words):
        passage = flat(' '.join(w['core'] for w in words[i:j + 1]))
        if corpus and passage in corpus:
            report['kept'] += 1
            continue
        out.append(answer[cursor:words[i]['start']]); out.append(REMOVED); cursor = words[j]['end']; report['removed'] += 1
    out.append(answer[cursor:])
    text = ''.join(out)
    # An explicitly requested verse always reaches the user in its exact form, whatever the model wrote.
    shown = flat(text)
    absent = [(ref, verse) for ref, verse in given if flat(verse) not in shown]
    report['appended'] = len(absent)
    if absent:
        text = text.rstrip() + '\n\n' + APPENDED + '\n' + '\n'.join(f'﴿{verse}﴾ [{NAMES[ref[0] - 1]}: {ref[1]}]' for ref, verse in absent)
    return text, report


async def guard_stream(stream, question):
    """Hold the model's text until it is complete, pass it through render(), then release it once."""
    held, rest = [], []
    async for packet in stream:
        if packet.get('content'):
            held.append(packet['content'])
            rest.append({k: v for k, v in packet.items() if k != 'content'})
        else:
            rest.append(packet)
    text, report = render(''.join(held), question)
    if text:
        yield {'content': text}
    for packet in rest:
        if packet:
            yield packet


def main(argv=None):
    data = load()
    if 'error' in data:
        print(json.dumps({'ok': False, 'reason': data['error'], 'path': str(path())}, ensure_ascii=False)); return 1
    print(json.dumps({'ok': True, 'verses': len(data['verses']), 'surahs': 114, 'sha256': data['sha256'], 'path': str(path())}, ensure_ascii=False)); return 0


if __name__ == '__main__':
    sys.exit(main())
