"""Small, explicit claim contracts; not a semantic entailment classifier.

Only complete, bounded request grammars are recognized. The original evidence
is retained. No model output is trusted to define a checker or a permission.
"""
import re
import unicodedata
from fractions import Fraction

VERSION = 'claim-checks-v2'

def normalized_digits(text):
    return ''.join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in text)

def sample_success(question):
    if not isinstance(question,str) or len(question)>600:return None
    text=normalized_digits(question).strip()
    match=re.fullmatch(
        r'(?:لدينا\s+)?عينة\s+من\s+(\d{1,6})\s+(?:حالة|حالات)\s*[,،؛.]\s*نجحت?\s+(\d{1,6})\s+منها\s*[.،؛]\s*'
        r'هل\s+(?:هذا\s+)?يثبت\s+نجاح\s+(?:كل|جميع)\s+(?:المجتمع|عناصر\s+المجتمع)\s*[؟?]'
        r'(?:\s*أجب\s+(?:مع\s+تبرير\s+مختصر|باختصار)\s*\.?)?',text)
    if not match:return None
    total,success=map(int,match.groups())
    if total<1 or success>total:return None
    failures=total-success
    return {'version':VERSION,'kind':'sample_success','given':{'sample_size':total,'successful':success},
            'known_counterexamples':failures,'universal_success_proved':False,
            'universal_success_refuted':failures>0,
            'scope':'The stated exact counts of successful cases in a sample, assuming the sample belongs to the queried population. No statistical estimate or population census is inferred.'}

def render_sample(result):
    n=result['given']['sample_size'];k=result['given']['successful'];failed=result['known_counterexamples']
    if failed:
        return (f'لا. نجحت {k} من {n} حالات؛ إذن توجد {failed} حالات لم تنجح. '
                'وجود حالة واحدة غير ناجحة من المجتمع يكفي لتفنيد أن جميع حالاته ناجحة. '
                'زيادة حجم العينة أو الثقة الإحصائية لا تزيل هذا المثال المضاد.')
    return (f'لا. ثبت نجاح الحالات الـ{n} المفحوصة فقط. '
            'نجاح عينة، ولو كانت كبيرة أو ممثلة، لا يثبت نجاح كل فرد في المجتمع؛ '
            'لم تُعطَ هنا نتائج تعداد كامل أو برهان على الجميع.')

_NUMBER=r'[-+]?\d{1,30}(?:\.\d{1,12})?'
_LABEL=r'[\u0621-\u064aA-Za-z][\u0621-\u064aA-Za-z ّ]{0,79}?'
_CORRECTION=re.compile(
    r'المسودة\s+تقول\s+(?:إن\s+)?(?P<label>'+_LABEL+r')\s+(?P<draft>'+_NUMBER+r')\s+(?P<unit>[^\W\d_]+)\s*\.\s*'
    r'صحح\s+(?P<target>المدة|الطول|الوزن|الكتلة|المسافة|الكمية|العدد)\s+من\s+الملف\s+وأجب\s+بالعربية\s*\.?',re.UNICODE)
_UNSAFE_LABEL=re.compile(r'\b(?:لا|ليس|ليست|لم|لن|غير|إذا|لو|ربما|قد|تقريبا|أو)\b')
_TARGET_UNITS={'المدة':{'second','minute','hour','day'},'الطول':{'m','cm','km'},'المسافة':{'m','cm','km'},
               'الوزن':{'kg','g'},'الكتلة':{'kg','g'},'الكمية':{'unit','item'},'العدد':{'unit','item'}}
_HE_CORRECTION=re.compile(
    r'בטיוטה\s+כתוב\s+ש(?P<label>[\u05d0-\u05eaA-Za-z][\u05d0-\u05eaA-Za-z ]{0,79}?)\s+הוא\s+(?P<draft>'+_NUMBER+r')\s+(?P<unit>[^\W\d_]+)\s*\.\s*'
    r'תקן\s+את\s+(?P=label)\s+לפי\s+הקובץ\s+וענה\s+בעברית\s*\.?',re.UNICODE)
_HE_UNSAFE_LABEL=re.compile(r'\b(?:לא|אין|אינו|אינה|אם|אולי|בערך|או)\b')

def literal_quantity_correction(question,chunks,unit_aliases):
    """Check one explicitly named field and unit against positive source rows.

    Recognizes a deliberately narrow Arabic correction request. Evidence must
    state that same literal field as a complete positive sentence containing
    exactly one number/unit pair. Multiple conflicting values, synonyms for
    the field, ranges, negation and conditional sentences are not resolved.
    This is an exact field/value association check, not quote-only recovery.
    """
    if not isinstance(question,str) or len(question)>600:return None
    # Layout can vary; Arabic/Latin number spelling is normalized only for
    # comparison. Returned claim/quote always use the original source span.
    text=normalized_digits(question).strip()
    match=_CORRECTION.fullmatch(text);language='ar';connector=''
    if not match:
        match=_HE_CORRECTION.fullmatch(text);language='he';connector=r'\s+הוא'
    if not match:return None
    field=match['label'].strip();unit=unit_aliases.get(match['unit'].lower())
    if not unit:return None
    if language=='ar' and (unit not in _TARGET_UNITS[match['target']] or _UNSAFE_LABEL.search(field)):return None
    if language=='he' and _HE_UNSAFE_LABEL.search(field):return None
    field_pattern=r'\s+'.join(re.escape(token) for token in field.split())
    pattern=re.compile(r'(?<![^\n.!?؟])\s*('+field_pattern+connector+r'\s+('+_NUMBER+r')\s+([^\W\d_]+)\s*[.!?؟]?)\s*(?=$|\n|[.!?؟])',re.UNICODE)
    if not isinstance(chunks,list) or len(chunks)>6:return None
    if any(not isinstance(c,dict) or not isinstance(c.get('text'),str) for c in chunks):return None
    if sum(len(c['text']) for c in chunks)>100000:return None
    found=[]
    for source,chunk in enumerate(chunks,1):
        document=chunk.get('document') or {}
        if chunk.get('needs_review') or not isinstance(document,dict) or document.get('review_required'):return None
        text=chunk.get('text')
        if not isinstance(text,str) or len(text)>100000:return None
        derived=normalized_digits(text)
        # Decimal digit normalization is one codepoint to one codepoint, so
        # spans refer to the unmodified source rather than fabricated quotes.
        matches=list(pattern.finditer(derived))
        # Both sequences are ordered; advance one cursor rather than comparing
        # every occurrence with every candidate (quadratic on repeated fields).
        cursor=0
        for occurrence in re.finditer(field_pattern,derived):
            while cursor<len(matches) and matches[cursor].end()<=occurrence.start():cursor+=1
            if cursor==len(matches) or not matches[cursor].start()<=occurrence.start()<matches[cursor].end():return None
        for value in matches:
            if unit_aliases.get(value[3].lower())!=unit:return None
            found.append({'source':source,'quote':text[value.start(1):value.end(1)],
                          'value':str(Fraction(value[2])),'unit':unit})
    if not found or len({(r['value'],r['unit']) for r in found})!=1:return None
    chosen=found[0]
    return {'version':VERSION,'kind':'literal_quantity_correction','field':field,'language':language,
            'draft_value':str(Fraction(match['draft'])),'value':chosen['value'],'unit':unit,
            'source':chosen['source'],'quote':chosen['quote'],
            'scope':'Only an exact positive field/value/unit statement in the authorized source is checked. No independent truth, freshness, paraphrase, inference or unit conversion is certified.'}
