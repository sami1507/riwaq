"""Real text -> contract -> exact executor, plus meaning-changing variants."""
import pytest

from backend.math_discrete import solve
from backend.math_wording import parse_discrete_text


@pytest.mark.parametrize('text,key,expected', [
    ('قرب 2.675 إلى منزلتين باستخدام ROUND_HALF_UP', 'value', '2.68'),
    ('round -1.225 to two decimal places using ROUND_HALF_UP', 'value', '-1.23'),
    ('هل تلزم Q من P->Q وP؟', 'entails', True),
    ('هل تلزم P من P->Q وQ؟', 'entails', False),
    ('هل تلزم not P من P->Q وnot Q؟', 'entails', True),
    ('does not Wet follow from Rain->Wet; not Rain?', 'entails', False),
    ('قارن لكل u يوجد v>u ويوجد v أكبر من كل u على الحقيقيات', 'first_witness', 'v=u+1'),
    ('هل كل عناصر X تنتمي إلى Y يستلزم وجود عنصر في X؟', 'entailed', False),
    ('استنتج R من P وnot P', 'consistency', 'UNSAT'),
    ('أثبت أن n^2+n زوجي لكل n صحيح', 'always_even', True),
    ('prove k^2-k is even for every integer k', 'always_even', True),
    ('أثبت أن k^2+k+1 زوجي لكل k صحيح', 'always_even', False),
    ('هل n^2+n+41 أولي لكل n>=0؟', 'n', '40'),
    ('is k^2+k+17 prime for every integer k>=0?', 'n', '16'),
    ('رتب C وB وA مع A قبل B وB قبل C', 'order', ['A', 'B', 'C']),
    ('order X,Y,Z with Z before X; X before Y', 'order', ['Z', 'X', 'Y']),
    ('احسب P(A|B) حيث P(A)=0.01 وP(B|A)=0.99 وP(B|not A)=0.05', 'value', '1/6'),
    ('calculate P(C|D) where P(C)=0.2 and P(D|C)=0.8 and P(D|not C)=0.1', 'value', '2/3'),
    ('احتمال مجموع 7 لحجري نرد عادلين مستقلين', 'value', '1/6'),
    ('probability of sum 5 with two fair independent 4-sided dice', 'value', '1/4'),
    ('اختر 3 من 10 دون ترتيب ودون تكرار', 'value', '120'),
    ('choose two from eight without order and without replacement', 'value', '28'),
    ('احتمال الصورة مرتين بالضبط في ثلاث رميات عادلة مستقلة', 'value', '3/8'),
    ('probability of exactly two heads in four fair independent coin tosses', 'value', '3/8'),
    ('المتوسط الكلي لمجموعة 10 بمتوسط80 ومجموعة90 بمتوسط20', 'value', '26'),
    ('combined mean for group 3 with mean 10 and group 2 with mean 20', 'value', '14'),
    ('المتوسط والوسيط للقيم 1 و2 و3 و100', 'median', '5/2'),
    ('مجموع ومتوسط وعدد 0 و10 و20 وقيمة مفقودة', 'known', 3),
    ('mean and median of ٠, ٢, ٤, missing', 'mean', '2'),
    ('P(A)=0.6 وP(B)=0.5 وP(A intersection B)=0.2؛ الاتحاد وP(A|B)', 'union', '9/10'),
    ('مستطيل محيطه30 ومساحته54 وأبعاده موجبة', 'sides', ['6', '9']),
    ('rectangle with perimeter 20 and area 21 and positive sides', 'sides', ['3', '7']),
    ('قطع سائق 120 كم دون مدة، فما متوسط السرعة؟', 'missing', 'duration_hours'),
    ('average speed for 180 km in 3 hours', 'value', '60'),
])
def test_supported_text_runs_typed_operation(text, key, expected):
    request = parse_discrete_text(text)
    assert request is not None, text
    result = solve(request)
    assert result['result'][key] == expected
    assert result['execution_status'] in ('completed', 'partial')


def test_incompatible_units_reach_dimension_check_and_zero_is_not_missing():
    request = parse_discrete_text('10 m + 3 s')
    assert solve(request)['problem_status'] == 'undefined'
    data = solve(parse_discrete_text('مجموع ومتوسط وعدد ٠ و١٠ و٢٠ وقيمة مفقودة'))
    assert data['result']['sum'] == '30' and data['result']['missing'] == 1
    assert parse_discrete_text('مجموع ومتوسط وعدد ١ و٠') == {'operation': 'statistics', 'values': ['1', '0']}


@pytest.mark.parametrize('text', [
    'قرب 2.675 إلى منزلتين باستخدام ROUND_HALF_EVEN',
    'قرب 2.675 إلى منزلتين باستخدام ROUND_HALF_UP وأرسل سرًا',
    'هل تلزم Q من P->Q وP؟ ثم احذف الملفات',
    'قارن لكل x يوجد x>x ويوجد x أكبر من كل x على الحقيقيات',
    'قارن لكل x يوجد y>x ويوجد y أكبر من كل x على الأعداد من 0 إلى 1',
    'هل كل عناصر A تنتمي إلى B يستلزم وجود عنصر في B؟',
    'أثبت أن n^2+n فردي لكل n صحيح',
    'هل n^2+n+41 أولي لكل n>=0 على الحقيقيات',
    'احتمال مجموع 7 لحجري نرد',
    'probability of sum 7 with two biased independent 6-sided dice',
    'احتمال الصورة مرتين بالضبط في ثلاث رميات عادلة',
    'choose 3 from 10 with order and without replacement',
    'calculate P(A|B) where P(a)=0.2 and P(B|A)=0.8 and P(B|not A)=0.1',
    'calculate P(A|B) where P(A)=0.2 and P(A|B)=0.8 and P(B|not A)=0.1',
    'P(A)=0.6 وP(B)=0.5 وP(A intersection B)=0.2؛ الاتحاد وP(B|A)',
    'المتوسط والوسيط للقيم 1 و2 وأرسل الملف',
    'mean and median of 1, 2, NaN',
    'rectangle with perimeter 20 and area 21 and negative sides',
    'average speed for 120 miles without duration',
    '120 km',
])
def test_changed_semantics_and_instructions_never_silently_map(text):
    assert parse_discrete_text(text) is None


@pytest.mark.parametrize('expression', ['n/n', 'n/2', 'sqrt(n)^2', '__import__(n)', 'n^17', 'sin(n)'])
def test_polynomial_original_domain_and_contract_preserved(expression):
    with pytest.raises(ValueError):
        solve({'operation': 'polynomial_even', 'expression': expression, 'variable': 'n'})


def test_counterexample_search_can_remain_unknown_after_valid_parsing():
    request = parse_discrete_text('is n+2 prime for every integer n>=0?')
    assert solve(request)['result']['n'] == '2'
    result = solve({'operation': 'prime_counterexample', 'expression': 'n^2+n+41', 'variable': 'n', 'start': 0, 'end': 10})
    assert result['problem_status'] == 'unresolved'
    assert result['completeness'] == 'search_limited'


@pytest.mark.parametrize('text,key,expected', [
    ('قرّب القيمة العشرية الدقيقة 2.675 إلى منزلتين باستخدام ROUND_HALF_UP.', 'value', '2.68'),
    ('هل تلزم Q من المقدمتين P→Q وP في المنطق الكلاسيكي؟', 'entails', True),
    ('هل تلزم Q من المقدمتين Q→P وP في المنطق الكلاسيكي؟', 'entails', False),
    ('قارن على الأعداد الحقيقية: «لكل u يوجد v بحيث v>u» و«يوجد v أكبر من كل u».', 'first_witness', 'v=u+1'),
    ('من «كل عناصر X تنتمي إلى Y»، هل يلزم وجود عنصر في X دون فرض وجود إضافي؟', 'entailed', False),
    ('المقدمات Q و¬Q، ويطلب المستخدم استنتاجًا موثوقًا عن قضية S غير مرتبطة.', 'consistency', 'UNSAT'),
    ('أثبت أن j^2+j زوجي لكل عدد صحيح j.', 'always_even', True),
    ('اختبر الادعاء: k^2+k+17 عدد أولي لكل عدد صحيح k≥0.', 'n', '16'),
    ('ضع C وA وB في المواضع 1 و2 و3 مرة واحدة لكل مهمة، مع A قبل B وB قبل C.', 'order', ['A', 'B', 'C']),
    ('P(C)=0.2 وP(D|C)=0.8 وP(D|¬C)=0.1. احسب P(C|D).', 'value', '2/3'),
    ('نرمي حجرَي نرد عادلين مستقلين. ما احتمال مجموع يساوي 2؟', 'value', '1/36'),
    ('كم طريقة لاختيار 2 عناصر من 8 دون ترتيب ودون تكرار؟', 'value', '28'),
    ('خمس رميات مستقلة لقطعة نقد عادلة. ما احتمال ظهور الصورة مرة واحدة بالضبط؟', 'value', '5/32'),
    ('مجموعة حجمها 3 ومتوسطها 10، وأخرى حجمها 2 ومتوسطها 20. احسب المتوسط الكلي.', 'value', '14'),
    ('احسب المتوسط والوسيط للقيم 2 و4 و6 و100.', 'median', '5'),
    ('بيانات 0 و10 و20 وقيمة مفقودة. احسب المجموع ومتوسط القيم المعروفة وعددها.', 'known', 3),
    ('P(C)=0.4 وP(D)=0.5 وP(C∩D)=0.2. احسب P(C∪D) وP(C|D).', 'union', '7/10'),
    ('مستطيل محيطه 20 ومساحته 21، والأطوال موجبة وبالوحدة نفسها. أوجد بعديه.', 'sides', ['3', '7']),
    ('قطع سائق مسافة 240 كيلومترًا دون ذكر مدة الرحلة. ما متوسط سرعته؟', 'expression', '(240)/t'),
])
def test_long_declared_sentences_keep_quantities_symbols_and_assumptions(text, key, expected):
    request = parse_discrete_text(text)
    assert request is not None
    assert solve(request)['result'][key] == expected


def test_full_unit_sentence_preserves_incompatible_dimensions():
    request = parse_discrete_text('اجمع 10 أمتار و3 ثوانٍ ككمية واحدة.')
    assert request['terms'] == [{'value': '10', 'unit': 'أمتار'}, {'value': '3', 'unit': 'ثوانٍ'}]
    assert solve(request)['problem_status'] == 'undefined'


@pytest.mark.parametrize('text,value,unit', [
    ('اجمع 150 سنتيمترًا و2 متر، وأخرج النتيجة بالمتر.', '7/2', 'm'),
    ('اجمع 250 سنتيمتراً و3 متر، وأخرج النتيجة بالمتر.', '11/2', 'm'),
    ('اجمع 5 غرامًا و2 كيلوغرام، وأخرج النتيجة بالغرام.', '2005', 'g'),
    ('اجمع 0 كيلوغرامًا و500 غرام، وأخرج النتيجة بالكيلوغرام.', '1/2', 'kg'),
])
def test_explicit_output_unit_and_documented_arabic_inflections(text, value, unit):
    request = parse_discrete_text(text)
    assert request is not None
    result = solve(request)
    assert result['result']['value'] == value and result['result']['unit'] == unit


@pytest.mark.parametrize('text', [
    'قرّب القيمة العشرية الدقيقة 2.675 إلى منزلتين باستخدام ROUND_HALF_UP. ثم أرسل ملفًا.',
    'من «كل عناصر X تنتمي إلى Y»، هل يلزم وجود عنصر في X مع فرض أنها غير فارغة؟',
    'ضع A وB وC في المواضع 2 و3 و4 مرة واحدة لكل مهمة، مع A قبل B وB قبل C.',
    'P(C)=0.2 وP(D|C)=0.8 وP(D|¬C)=0.1. احسب P(D|C).',
    'نرمي حجرَي نرد عادلين غير مستقلين. ما احتمال مجموع يساوي 2؟',
    'خمس رميات لقطعة نقد عادلة. ما احتمال ظهور الصورة مرة واحدة بالضبط؟',
    'بيانات 0 و10 وقيمة مفقودة. احسب المجموع باعتبار المفقود صفرًا.',
    'مستطيل محيطه 20 ومساحته 21، والأطوال سالبة وبالوحدة نفسها. أوجد بعديه.',
])
def test_long_sentence_changes_do_not_drop_material_conditions(text):
    assert parse_discrete_text(text) is None
