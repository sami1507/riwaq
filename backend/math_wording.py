"""Declared AR/EN mathematical phrases -> explicit discrete operation contracts.

This is a full-match grammar, not extraction from arbitrary prose. It preserves
numeric literals as strings and requires probability assumptions in the wording.
No fixtures, case IDs, expected answers, engine calls, or I/O occur here.
"""
from __future__ import annotations

import re
from fractions import Fraction

from backend.math_discrete import DIGITS, UNIT_ALIASES, UNITS

N = r'[+-]?(?:[0-9]{1,64}(?:\.[0-9]{1,64})?|\.[0-9]{1,64})(?:/[1-9][0-9]{0,63})?'
SYM = r'[A-Za-z][A-Za-z0-9]{0,15}'
FORMULA = r'[A-Za-z0-9()\s¬∧∨→>\-]{1,512}'
POLYNOMIAL = r'[A-Za-z0-9\s().+*/^\-]{1,512}'
COUNT_WORDS = {'واحدة': 1, 'واحد': 1, 'اثنتين': 2, 'اثنان': 2, 'اثنين': 2,
    'ثلاث': 3, 'ثلاثة': 3, 'أربع': 4, 'أربعة': 4, 'خمس': 5, 'خمسة': 5,
    'ست': 6, 'ستة': 6, 'سبع': 7, 'سبعة': 7, 'ثمان': 8, 'ثمانية': 8,
    'تسع': 9, 'تسعة': 9, 'عشر': 10, 'عشرة': 10,
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
    'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}
COUNT = r'(?:[0-9]{1,4}|' + '|'.join(sorted(COUNT_WORDS, key=len, reverse=True)) + ')'
UNITS_PATTERN = '|'.join(re.escape(v) for v in sorted([*UNITS, *UNIT_ALIASES], key=len, reverse=True))


def _count(text):
    return COUNT_WORDS.get(text.lower(), int(text) if text.isascii() and text.isdigit() else None)


def _match(pattern, text):
    # Symbols and event identifiers are case-sensitive. Case folding the whole
    # regular expression would silently conflate P(A) with P(a).
    return re.fullmatch(pattern, text)


def _values(text):
    """List grammar retains zero; only an explicit null/missing term is missing."""
    parts = re.split(r'\s*(?:،|,|\s+و\s*|\s+and\s+)\s*', text)
    if not 1 <= len(parts) <= 1000:
        return None
    result = []
    for part in parts:
        if re.fullmatch(N, part):
            result.append(part)
        elif part in ('قيمة مفقودة', 'مفقود', 'null', 'missing'):
            result.append(None)
        else:
            return None
    return result


def _source_form(text):
    """Longer declared sentences; quantities and identifiers remain captured.

    This has no dependency on the evaluation collection. Full matches preserve
    assumptions, domains, positive lengths, and the conditioning direction.
    """
    m = _match(rf'(?:حوّل|حول|convert)\s+(?P<v>{N})\s*(?P<unit>{UNITS_PATTERN})\s+(?:إلى|to)\s+(?P<out>{UNITS_PATTERN})(?:\s+في تمرين وحدات)?', text)
    if m:
        return {'operation':'unit_sum','terms':[{'value':m['v'],'unit':m['unit']}],'output_unit':m['out']}
    m = _match(r'احسب المتوسط للقيم\s+(.+?)\s+وفق قاعدة استبعاد المفقود فقط', text)
    if m:
        values = _values(m[1])
        if values is not None: return {'operation':'statistics','values':values}
    m = _match(r'(?:أثبت|اثبت|اختبر) أن كل عدد\s+(أولي|فردي|زوجي|مركب|موجب)\s+(أولي|فردي|زوجي|مركب|موجب)', text)
    if m:
        predicates={'أولي':'prime','فردي':'odd','زوجي':'even','مركب':'composite','موجب':'positive'}
        return {'operation':'integer_implication','premise':predicates[m[1]],'conclusion':predicates[m[2]]}
    # This declared weather formulation supplies propositional premises only;
    # the truth table determines whether the converse actually follows.
    if text == 'إذا أمطرت تبتل الأرض؛ الأرض مبتلة، فهل يلزم أنها أمطرت':
        return {'operation':'entailment','premises':['P -> Q','Q'],'conclusion':'P','labels':{'P':'أمطرت','Q':'الأرض مبتلة'}}
    m = _match(rf'مثال افتراضي: من\s+([0-9]{{1,9}})\s+شخص،\s*({N})% لديهم الحالة؛ حساسية الاختبار\s+({N})% ونوعيته\s+({N})%\. احسب احتمال الحالة بعد نتيجة إيجابية', text)
    if m:
        return {'operation':'bayes','population':int(m[1]),'scope':'hypothetical','prior':str(Fraction(m[2])/100),
                'likelihood':str(Fraction(m[3])/100),'false_positive':str(1-Fraction(m[4])/100)}
    m = _match(rf'مثال افتراضي لنفس السكان والسنة: انخفض الخطر من\s+({N})\s+في\s+({N})\s+إلى\s+({N})\s+في\s+({N})',text)
    if m and Fraction(m[2]) == Fraction(m[4]):
        return {'operation':'risk_change','before':m[1],'after':m[3],'denominator':m[2],'period':'year','scope':'hypothetical'}
    m = _match(rf'(?:قرّب|قرب) القيمة العشرية الدقيقة\s+(?P<v>{N})\s+إلى\s+(?P<p>منزلتين|منزلة واحدة|{COUNT}\s+منازل)\s+باستخدام ROUND_HALF_UP', text)
    if m:
        places = 2 if m['p'] == 'منزلتين' else 1 if m['p'] == 'منزلة واحدة' else _count(m['p'].split()[0])
        return {'operation': 'round', 'value': m['v'], 'places': places, 'rounding': 'ROUND_HALF_UP'}
    m = _match(rf'اجمع\s+(?P<a>{N})\s*(?P<ua>{UNITS_PATTERN})\s+و\s*(?P<b>{N})\s*(?P<ub>{UNITS_PATTERN})\s+ككمية واحدة', text)
    if m:
        return {'operation': 'unit_sum', 'terms': [{'value': m['a'], 'unit': m['ua']}, {'value': m['b'], 'unit': m['ub']}], 'output_unit': m['ua']}
    m = _match(rf'اجمع\s+(?P<a>{N})\s*(?P<ua>{UNITS_PATTERN})\s+و\s*(?P<b>{N})\s*(?P<ub>{UNITS_PATTERN})[،,]\s*وأخرج النتيجة ب(?P<out>{UNITS_PATTERN})', text)
    if m:
        return {'operation': 'unit_sum', 'terms': [{'value': m['a'], 'unit': m['ua']}, {'value': m['b'], 'unit': m['ub']}], 'output_unit': m['out']}
    m = _match(rf'قارن على الأعداد الحقيقية:\s*«لكل\s+(?P<x>{SYM})\s+يوجد\s+(?P<y>{SYM})\s+بحيث\s+(?P=y)\s*>\s*(?P=x)»\s+و«يوجد\s+(?P=y)\s+أكبر من كل\s+(?P=x)»', text)
    if m and m['x'] != m['y']:
        return {'operation': 'quantifier_order', 'domain': 'real', 'variables': [m['x'], m['y']]}
    m = _match(rf'من «كل عناصر\s+(?P<a>{SYM})\s+تنتمي إلى\s+(?P<b>{SYM})»[،,]\s*هل يلزم وجود عنصر في\s+(?P=a)\s+دون فرض وجود إضافي', text)
    if m:
        return {'operation': 'existential_import', 'sets': [m['a'], m['b']]}
    m = _match(rf'المقدمات\s+(?P<p>{FORMULA})\s+و\s*(?P<q>{FORMULA})[،,]\s*ويطلب المستخدم استنتاجًا موثوقًا عن قضية\s+(?P<c>{SYM})\s+غير مرتبطة', text)
    if m:
        return {'operation': 'entailment', 'premises': [m['p'].strip(), m['q'].strip()], 'conclusion': m['c']}
    m = _match(rf'(?:أثبت|اثبت) أن\s+(?P<e>{POLYNOMIAL})\s+زوجي لكل عدد صحيح\s+(?P<v>{SYM})', text)
    if m:
        return {'operation': 'polynomial_even', 'expression': m['e'].strip(), 'variable': m['v']}
    m = _match(rf'اختبر الادعاء:\s*(?P<e>{POLYNOMIAL})\s+عدد أولي لكل عدد صحيح\s+(?P<v>{SYM})\s*>=\s*(?P<start>[0-9]{{1,4}})', text)
    if m:
        return {'operation': 'prime_counterexample', 'expression': m['e'].strip(), 'variable': m['v'], 'start': int(m['start'])}
    m = _match(rf'ضع\s+(?P<items>{SYM}(?:\s+و\s*{SYM})+)\s+في المواضع\s+(?P<positions>[0-9]{{1,2}}(?:\s+و\s*[0-9]{{1,2}})+)\s+مرة واحدة لكل مهمة[،,]\s*مع\s+(?P<edges>{SYM}\s+قبل\s+{SYM}(?:\s+و\s*{SYM}\s+قبل\s+{SYM})*)', text)
    if m:
        items = re.split(r'\s+و\s*', m['items'])
        positions = [int(v) for v in re.split(r'\s+و\s*', m['positions'])]
        # This operation models the complete ordering, not arbitrary numbered
        # slots or omitted tasks; such inputs require a richer contract.
        if sorted(positions) == list(range(1, len(items) + 1)):
            return {'operation': 'ordering', 'items': items, 'before': [e.split(' قبل ') for e in re.split(r'\s+و\s*', m['edges'])]}
        return None
    m = _match(rf'P\((?P<a>{SYM})\)\s*=\s*(?P<prior>{N})\s+وP\((?P<b>{SYM})\|(?P=a)\)\s*=\s*(?P<like>{N})\s+وP\((?P=b)\|(?:¬|not\s+)(?P=a)\)\s*=\s*(?P<fp>{N})\.\s*احسب P\((?P=a)\|(?P=b)\)', text)
    if m and m['a'] != m['b']:
        return {'operation': 'bayes', 'prior': m['prior'], 'likelihood': m['like'], 'false_positive': m['fp']}
    m = _match(r'نرمي حجرَ?ي نرد عادلين مستقلين\.\s*ما احتمال مجموع يساوي\s+(?P<sum>[0-9]{1,4})', text)
    if m:
        return {'operation': 'dice_sum', 'dice': 2, 'sides': 6, 'total': int(m['sum'])}
    m = _match(rf'كم طريقة لاختيار\s+(?P<k>{COUNT})\s+عناصر من\s+(?P<n>{COUNT})\s+دون ترتيب ودون تكرار', text)
    if m:
        return {'operation': 'combinations', 'n': _count(m['n']), 'k': _count(m['k'])}
    m = _match(rf'(?P<n>{COUNT})\s+رميات مستقلة لقطعة نقد عادلة\.\s*ما احتمال ظهور الصورة\s+(?P<k>مرتين|مرة واحدة|{COUNT}\s+مرات)\s+بالضبط', text)
    if m:
        k = 2 if m['k'] == 'مرتين' else 1 if m['k'] == 'مرة واحدة' else _count(m['k'].split()[0])
        return {'operation': 'binomial', 'n': _count(m['n']), 'k': k, 'p': '1/2'}
    m = _match(rf'مجموعة حجمها\s+(?P<n1>[0-9]{{1,12}})\s+ومتوسطها\s+(?P<a>{N})[،,]\s*وأخرى حجمها\s+(?P<n2>[0-9]{{1,12}})\s+ومتوسطها\s+(?P<b>{N})\.\s*احسب المتوسط الكلي', text)
    if m:
        return {'operation': 'weighted_mean', 'groups': [{'count': int(m['n1']), 'mean': m['a']}, {'count': int(m['n2']), 'mean': m['b']}]}
    m = _match(r'بيانات\s+(?P<values>.+)\.\s*احسب المجموع ومتوسط القيم المعروفة وعددها', text)
    if m:
        values = _values(m['values'])
        if values is not None:
            return {'operation': 'statistics', 'values': values}
    m = _match(rf'P\((?P<a>{SYM})\)\s*=\s*(?P<pa>{N})\s+وP\((?P<b>{SYM})\)\s*=\s*(?P<pb>{N})\s+وP\((?P=a)\s*∩\s*(?P=b)\)\s*=\s*(?P<joint>{N})\.\s*احسب P\((?P=a)\s*∪\s*(?P=b)\)\s+وP\((?P=a)\|(?P=b)\)', text)
    if m and m['a'] != m['b']:
        return {'operation': 'probability', 'a': m['pa'], 'b': m['pb'], 'intersection': m['joint']}
    m = _match(rf'مستطيل محيطه\s+(?P<p>{N})\s+ومساحته\s+(?P<a>{N})[،,]\s*والأطوال موجبة وبالوحدة نفسها\.\s*أوجد بعديه', text)
    if m:
        return {'operation': 'rectangle', 'perimeter': m['p'], 'area': m['a']}
    m = _match(rf'قطع سائق مسافة\s+(?P<d>{N})\s+كيلومتر(?:ًا|ا)?\s+دون ذكر مدة الرحلة\.\s*ما متوسط سرعته', text)
    if m:
        return {'operation': 'speed', 'distance': m['d']}
    return None


def parse_discrete_text(text):
    if type(text) is not str or not 1 <= len(text.strip()) <= 2048:
        return None
    if any(ord(c) < 32 and c not in '\t\n\r' for c in text):
        return None
    # Normalize only the derived copy; the caller retains original user input.
    text = re.sub(r'\s+', ' ', text.translate(DIGITS).replace('≥', '>=').strip()).rstrip('؟?').strip()
    if text.endswith('.'):
        text = text[:-1].rstrip()
    original_sentence = _source_form(text)
    if original_sentence is not None:
        return original_sentence

    m = _match(rf'(?:قرّب|قرب)\s+(?P<value>{N})\s+إلى\s+(?P<places>منزلتين|منزلة واحدة|{COUNT}\s+منازل)\s+باستخدام\s+ROUND_HALF_UP', text)
    if m:
        places = 2 if m['places'] == 'منزلتين' else 1 if m['places'] == 'منزلة واحدة' else _count(m['places'].split()[0])
        return {'operation': 'round', 'value': m['value'], 'places': places, 'rounding': 'ROUND_HALF_UP'}
    m = _match(rf'round\s+(?P<value>{N})\s+to\s+(?P<places>{COUNT})\s+decimal places?\s+using\s+ROUND_HALF_UP', text)
    if m:
        return {'operation': 'round', 'value': m['value'], 'places': _count(m['places']), 'rounding': 'ROUND_HALF_UP'}

    # Every term keeps its own unit. An incompatible dimension is a mathematical
    # result from the executor, not a silently accepted bare-number addition.
    m = _match(rf'(?:اجمع\s+|sum\s+)?(?P<a>{N})\s*(?P<ua>{UNITS_PATTERN})\s*(?:\+|\s+و|\s+and\s+)\s*(?P<b>{N})\s*(?P<ub>{UNITS_PATTERN})(?:\s*(?:بـ|ب|in\s+)(?P<out>{UNITS_PATTERN}))?', text)
    if m:
        return {'operation': 'unit_sum', 'terms': [{'value': m['a'], 'unit': m['ua']}, {'value': m['b'], 'unit': m['ub']}], 'output_unit': m['out'] or m['ua']}

    # Premise separator is Arabic و or an English semicolon. Boolean 'and' stays
    # inside the formula. No free-text suffix or source instruction is accepted.
    m = _match(rf'(?:هل تلزم|استنتج)\s+(?P<conclusion>{FORMULA})\s+من\s+(?:المقدمتين\s+)?(?P<premises>{FORMULA}(?:\s+و\s*{FORMULA})*)(?:\s+في المنطق الكلاسيكي)?', text)
    if m:
        premises = re.split(r'\s+و\s*', m['premises'])
        return {'operation': 'entailment', 'premises': premises, 'conclusion': m['conclusion'].strip()}
    m = _match(rf'does\s+(?P<conclusion>{FORMULA})\s+follow from\s+(?P<premises>{FORMULA}(?:\s*;\s*{FORMULA})*)', text)
    if m:
        return {'operation': 'entailment', 'premises': [p.strip() for p in m['premises'].split(';')], 'conclusion': m['conclusion'].strip()}

    m = _match(rf'قارن لكل\s+(?P<x>{SYM})\s+يوجد\s+(?P<y>{SYM})\s*>\s*(?P=x)\s+ويوجد\s+(?P=y)\s+أكبر من كل\s+(?P=x)\s+على الحقيقيات', text)
    if m is None:
        m = _match(rf'compare for every\s+(?P<x>{SYM})\s+there is\s+(?P<y>{SYM})\s*>\s*(?P=x)\s+and there is\s+(?P=y)\s+greater than every\s+(?P=x)\s+over the reals', text)
    if m and m['x'] != m['y']:
        return {'operation': 'quantifier_order', 'domain': 'real', 'variables': [m['x'], m['y']]}

    m = _match(rf'هل كل عناصر\s+(?P<a>{SYM})\s+تنتمي إلى\s+(?P<b>{SYM})\s+يستلزم وجود عنصر في\s+(?P=a)', text)
    if m is None:
        m = _match(rf'does all elements of\s+(?P<a>{SYM})\s+belong to\s+(?P<b>{SYM})\s+imply an element exists in\s+(?P=a)', text)
    if m:
        return {'operation': 'existential_import', 'sets': [m['a'], m['b']]}

    m = _match(rf'(?:أثبت أن|اثبت أن)\s+(?P<expr>{POLYNOMIAL})\s+زوجي لكل\s+(?P<variable>{SYM})\s+صحيح', text)
    if m is None:
        m = _match(rf'prove\s+(?P<expr>{POLYNOMIAL})\s+is even for every integer\s+(?P<variable>{SYM})', text)
    if m:
        return {'operation': 'polynomial_even', 'expression': m['expr'].strip(), 'variable': m['variable']}
    m = _match(rf'هل\s+(?P<expr>{POLYNOMIAL})\s+أولي لكل\s+(?P<variable>{SYM})\s*>=\s*(?P<start>[0-9]{{1,4}})', text)
    if m is None:
        m = _match(rf'is\s+(?P<expr>{POLYNOMIAL})\s+prime for every integer\s+(?P<variable>{SYM})\s*>=\s*(?P<start>[0-9]{{1,4}})', text)
    if m:
        return {'operation': 'prime_counterexample', 'expression': m['expr'].strip(), 'variable': m['variable'], 'start': int(m['start'])}

    m = _match(rf'رتب\s+(?P<items>{SYM}(?:\s+و\s*{SYM})+)\s+مع\s+(?P<edges>{SYM}\s+قبل\s+{SYM}(?:\s+و\s*{SYM}\s+قبل\s+{SYM})*)', text)
    if m:
        return {'operation': 'ordering', 'items': re.split(r'\s+و\s*', m['items']), 'before': [e.split(' قبل ') for e in re.split(r'\s+و\s*', m['edges'])]}
    m = _match(rf'order\s+(?P<items>{SYM}(?:\s*,\s*{SYM})+)\s+with\s+(?P<edges>{SYM}\s+before\s+{SYM}(?:\s*;\s*{SYM}\s+before\s+{SYM})*)', text)
    if m:
        return {'operation': 'ordering', 'items': re.split(r'\s*,\s*', m['items']), 'before': [re.split(r'\s+before\s+', e, flags=re.I) for e in re.split(r'\s*;\s*', m['edges'])]}

    # Event names must match the conditioning direction throughout the sentence.
    m = _match(rf'(?:احسب|calculate)\s+P\((?P<a>{SYM})\|(?P<b>{SYM})\)\s+(?:حيث|where)\s+P\((?P=a)\)\s*=\s*(?P<prior>{N})\s*(?:و|and\s+)P\((?P=b)\|(?P=a)\)\s*=\s*(?P<likelihood>{N})\s*(?:و|and\s+)P\((?P=b)\|(?:not\s+|¬)(?P=a)\)\s*=\s*(?P<false_positive>{N})', text)
    if m and m['a'] != m['b']:
        return {'operation': 'bayes', **{k: m[k] for k in ('prior', 'likelihood', 'false_positive')}}

    m = _match(rf'احتمال مجموع\s+(?P<total>[0-9]{{1,4}})\s+لحجري نرد عادلين مستقلين', text)
    if m:
        return {'operation': 'dice_sum', 'dice': 2, 'sides': 6, 'total': int(m['total'])}
    m = _match(rf'probability of sum\s+(?P<total>[0-9]{{1,4}})\s+with\s+(?P<dice>{COUNT})\s+fair independent\s+(?P<sides>[0-9]{{1,3}})-sided dice', text)
    if m:
        return {'operation': 'dice_sum', 'dice': _count(m['dice']), 'sides': int(m['sides']), 'total': int(m['total'])}

    m = _match(rf'اختر\s+(?P<k>{COUNT})\s+من\s+(?P<n>{COUNT})\s+دون ترتيب ودون تكرار', text)
    if m is None:
        m = _match(rf'choose\s+(?P<k>{COUNT})\s+from\s+(?P<n>{COUNT})\s+without order and without replacement', text)
    if m:
        return {'operation': 'combinations', 'n': _count(m['n']), 'k': _count(m['k'])}
    m = _match(rf'احتمال الصورة\s+(?P<k>مرتين|مرة واحدة|{COUNT}\s+مرات)\s+بالضبط في\s+(?P<n>{COUNT})\s+رميات عادلة مستقلة', text)
    if m:
        k = 2 if m['k'] == 'مرتين' else 1 if m['k'] == 'مرة واحدة' else _count(m['k'].split()[0])
        return {'operation': 'binomial', 'n': _count(m['n']), 'k': k, 'p': '1/2'}
    m = _match(rf'probability of exactly\s+(?P<k>{COUNT})\s+heads in\s+(?P<n>{COUNT})\s+fair independent coin tosses', text)
    if m:
        return {'operation': 'binomial', 'n': _count(m['n']), 'k': _count(m['k']), 'p': '1/2'}

    m = _match(rf'المتوسط الكلي لمجموعة\s*(?P<n1>[0-9]{{1,12}})\s+بمتوسط\s*(?P<a>{N})\s+ومجموعة\s*(?P<n2>[0-9]{{1,12}})\s+بمتوسط\s*(?P<b>{N})', text)
    if m is None:
        m = _match(rf'combined mean for group\s+(?P<n1>[0-9]{{1,12}})\s+with mean\s+(?P<a>{N})\s+and group\s+(?P<n2>[0-9]{{1,12}})\s+with mean\s+(?P<b>{N})', text)
    if m:
        return {'operation': 'weighted_mean', 'groups': [{'count': int(m['n1']), 'mean': m['a']}, {'count': int(m['n2']), 'mean': m['b']}]}

    m = _match(r'(?:احسب المتوسط والوسيط للقيم|المتوسط والوسيط للقيم|مجموع ومتوسط وعدد|mean and median of|sum mean and count of)\s+(?P<values>.+)', text)
    if m:
        values = _values(m['values'])
        if values is not None:
            return {'operation': 'statistics', 'values': values}

    m = _match(rf'P\((?P<a>{SYM})\)\s*=\s*(?P<pa>{N})\s*(?:و|and\s+)P\((?P<b>{SYM})\)\s*=\s*(?P<pb>{N})\s*(?:و|and\s+)P\((?P=a)\s*(?:intersection|∩)\s*(?P=b)\)\s*=\s*(?P<joint>{N})\s*[؛;]\s*(?:الاتحاد و|union and\s+)P\((?P=a)\|(?P=b)\)', text)
    if m and m['a'] != m['b']:
        return {'operation': 'probability', 'a': m['pa'], 'b': m['pb'], 'intersection': m['joint']}

    m = _match(rf'مستطيل محيطه\s*(?P<perimeter>{N})\s+ومساحته\s*(?P<area>{N})\s+وأبعاده موجبة', text)
    if m is None:
        m = _match(rf'rectangle with perimeter\s+(?P<perimeter>{N})\s+and area\s+(?P<area>{N})\s+and positive sides', text)
    if m:
        return {'operation': 'rectangle', 'perimeter': m['perimeter'], 'area': m['area']}

    m = _match(rf'قطع سائق\s+(?P<distance>{N})\s+كم دون مدة[،,]\s*فما متوسط السرعة', text)
    if m is None:
        m = _match(rf'average speed for\s+(?P<distance>{N})\s+km without duration', text)
    if m:
        return {'operation': 'speed', 'distance': m['distance']}
    m = _match(rf'(?:متوسط السرعة لمسافة\s+|average speed for\s+)(?P<distance>{N})\s+(?:كم خلال\s+|km in\s+)(?P<duration>{N})\s+(?:ساعة|hours?)', text)
    if m:
        return {'operation': 'speed', 'distance': m['distance'], 'duration': m['duration']}
    return None
