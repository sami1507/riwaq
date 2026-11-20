"""Deterministic help for two things a small model gets wrong: ordering a given list and meeting a stated word count.

Ordering is computed here and handed to the request as application check data. A word-count requirement is checked on
the finished answer; one corrective attempt is requested, and if the count is still wrong the user is told the real count.
Both triggers are narrow on purpose: an explicit instruction in a short, single-task question.
"""
import json
import re

from backend import quran_reference

_MARKS = re.compile(r'[ً-ٰٟـ]')
_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')
_NUMBER = {'واحده': 1, 'واحد': 1, 'اثنتين': 2, 'اثنين': 2, 'ثلاث': 3, 'ثلاثه': 3, 'اربع': 4, 'اربعه': 4, 'خمس': 5, 'خمسه': 5, 'ست': 6, 'سته': 6,
           'سبع': 7, 'سبعه': 7, 'ثماني': 8, 'ثمان': 8, 'ثمانيه': 8, 'تسع': 9, 'تسعه': 9, 'عشر': 10, 'عشره': 10}
_NUM = r'(\d{1,3}|' + '|'.join(sorted(_NUMBER, key=len, reverse=True)) + ')'
_ALPHABET = 'ءابتثجحخدذرزسشصضطظعغفقكلمنهوي'
_RANK = {c: i for i, c in enumerate(_ALPHABET)}


def norm(text):
    text = _MARKS.sub('', text or '').translate(_DIGITS)
    return re.sub(r'\s+', ' ', re.sub('[أإآٱ]', 'ا', text).replace('ى', 'ي').replace('ة', 'ه').replace('ؤ', 'و').replace('ئ', 'ي')).strip()


def _value(token):
    return int(token) if token.isdigit() else _NUMBER[token]


def word_count(text):
    return sum(1 for token in re.split(r'\s+', text or '') if re.search(r'[\wء-ي]', token))


# ---------------------------------------------------------------- ordering
def ordering(question):
    """{'items': [...], 'sorted': [...], 'rule': ...} for an explicit «رتّب … ألفبائيًا/تصاعديًا/تنازليًا: a، b، c», else None."""
    text = norm(question)
    if not re.search(r'(?<![ء-ي])رتب', text) or len(question) > 600:
        return None
    alphabetical = bool(re.search(r'الفباءي|الفبايي|ابجدي|هجاءي|هجايي', text))
    descending = bool(re.search(r'تنازلي|من الاكبر|عكسي', text))
    ascending = bool(re.search(r'تصاعدي|من الاصغر', text))
    if not (alphabetical or descending or ascending) or ':' not in question:
        return None
    tail = question.rsplit(':', 1)[1]
    items = [part.strip().strip('.؟?!«»"') for part in re.split(r'[،,;؛\n]', tail)]
    items = [item for item in items if item]
    if not 2 <= len(items) <= 40 or any(len(item) > 60 for item in items):
        return None
    numbers = [item.translate(_DIGITS).replace('٫', '.') for item in items]
    if all(re.fullmatch(r'-?\d+(?:\.\d+)?', n) for n in numbers) and not alphabetical:
        order = sorted(items, key=lambda item: float(item.translate(_DIGITS).replace('٫', '.')), reverse=descending)
        rule = 'numeric, ' + ('descending' if descending else 'ascending')
    elif alphabetical:
        def key(item):
            letters = norm(item)
            letters = re.sub(r'^ال(?=.{2})', '', letters)  # the definite article does not count, as in Arabic indexes
            return [_RANK.get(c, len(_ALPHABET) + ord(c)) for c in letters]
        order = sorted(items, key=key, reverse=descending)
        rule = 'Arabic alphabetical order (ا ب ت ث …), definite article «ال» ignored' + (', reversed' if descending else '')
    else:
        return None
    return {'items': items, 'sorted': order, 'rule': rule}


# ---------------------------------------------------------------- word-count requirement
def constraint(question):
    """('exact'|'max', n) when a short single-task question states a word count for the whole answer, else None."""
    if len(question) > 160 and not re.search(r'لا\s*(?:تتجاوز|يتجاوز)', norm(question)[:200]):
        return None
    text = norm(question)
    if '\n' in question.strip() and not re.search(r'لا\s*(?:تتجاوز|يتجاوز|تزيد|يزيد)', text) or re.search(r'(?<![ء-ي])ثم(?![ء-ي])', text):
        return None
    most = re.search(r'لا\s*(?:تتجاوز|يتجاوز|تزيد|يزيد)\s*(?:عن|علي)?\s*' + _NUM + r'\s*(?:كلمات|كلمه)', text)
    if most:
        return ('max', _value(most.group(1)))
    if re.search(r'كلمتين\s*(?:فقط|بالضبط|تماما)|بكلمتين', text):
        return ('exact', 2)
    exact = re.search(_NUM + r'\s*(?:كلمات|كلمه)\s*(?:بالضبط|فقط|تماما)', text) or re.search(r'بكلمه\s+(واحده)', text) or re.search(r'كلمه\s+(واحده)\s+(?:فقط|بالضبط)', text)
    if exact:
        return ('exact', _value(exact.group(1)))
    return None


def satisfied(answer, rule):
    count = word_count(answer)
    return count == rule[1] if rule[0] == 'exact' else 0 < count <= rule[1]


def feedback(answer, rule):
    want = f'{rule[1]} بالضبط' if rule[0] == 'exact' else f'{rule[1]} على الأكثر'
    return (f'إجابتك السابقة فيها {word_count(answer)} كلمة، والمطلوب {want}. أعد كتابتها بالعدد المطلوب تمامًا، '
            'واكتب الإجابة فقط دون أي شرح أو تمهيد.')


def note(answer, rule):
    want = f'{rule[1]} بالضبط' if rule[0] == 'exact' else f'{rule[1]} على الأكثر'
    return f'\n\n⟦تنبيه من رواق: عدد كلمات هذه الإجابة {word_count(answer)}، والمطلوب {want}.⟧'


async def _collect(stream):
    held, rest = [], []
    async for packet in stream:
        if packet.get('content'):
            held.append(packet['content'])
            packet = {k: v for k, v in packet.items() if k != 'content'}
        if packet:
            rest.append(packet)
    return ''.join(held), rest


async def enforce(stream, question, retry):
    """Hold the answer, check the stated word count, ask once for a corrected answer, and say so honestly if it still misses."""
    rule = constraint(question)
    answer, rest = await _collect(stream)
    if rule and answer.strip() and not satisfied(answer, rule) and len(answer) <= 2000:
        second, second_rest = await _collect(retry(answer, feedback(answer, rule)))
        if second.strip() and (satisfied(second, rule) or abs(word_count(second) - rule[1]) < abs(word_count(answer) - rule[1])):
            answer, rest = second, second_rest
        if not satisfied(answer, rule):
            answer = answer.rstrip() + note(answer, rule)
    if answer:
        yield {'content': answer}
    for packet in rest:
        yield packet


# ---------------------------------------------------------------- words in mixed scripts
_ARABIC = r'ء-يٱ-ۓ'
_FOREIGN = r'Ͱ-ϿЀ-ԯ֐-׿ऀ-ॿ฀-๿぀-ヿ一-鿿가-힯'
_MIXED = re.compile(rf'(?:[{_ARABIC}][{_FOREIGN}]|[{_FOREIGN}][{_ARABIC}]|[{_ARABIC}][A-Za-z]+[{_ARABIC}])')


def garbled(answer):
    """Words that mix Arabic letters with another alphabet inside one word, a small-model glitch (for example «أмман»)."""
    found = []
    for token in re.findall(r'\S+', answer or ''):
        word = token.strip('«»"\'“”()[]{}*_`.,،؛:!?؟…')
        if _MIXED.search(word) and word not in found:
            found.append(word)
    return found[:5]


async def flag_stream(stream):
    """Pass the answer through as it arrives; if it contained mixed-script words, say so before the stream finishes."""
    text, flagged = [], False
    async for packet in stream:
        if packet.get('content'):
            text.append(packet['content'])
        if packet.get('finish_reason') and not flagged:
            flagged = True
            words = garbled(''.join(text))
            if words:
                yield {'content': '\n\n⟦تنبيه من رواق: كلمات كُتبت بحروف مخلوطة من أبجديتين وقد تكون خطأ: ' + '، '.join(f'«{w}»' for w in words) + '⟧'}
        yield packet


# ---------------------------------------------------------------- one application-check payload per request
def context(question, extra=None):
    """Combined application check data (Qur'an reference, ordering and any extra check such as a code run) as one JSON text, or ''."""
    out = dict(extra or {})
    quran = quran_reference.context(question)
    if quran:
        out.update(json.loads(quran))
    order = ordering(question)
    if order:
        out['ordering'] = {'rule': 'Computed by the application. Present the items in exactly this order: ' + order['rule'] + '.', 'sorted': order['sorted']}
    return json.dumps(out, ensure_ascii=False) if out else ''
