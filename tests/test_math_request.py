"""Meaning-preserving and meaning-changing inputs beyond the published numbers."""
import pytest
from backend.reasoning_math import parse_math,solve_math,validate_math_result


@pytest.mark.parametrize('text,expected',[
 ('احسب 1.25 + 0.75 بوصفهما قيمتين عشريتين دقيقتين.','2'),
 ('احسب 7/11 + 3/11 في صورة كسر مبسط.','10/11'),
 ('احسب 9007199254741009 + 3 على الأعداد الصحيحة.','9007199254741012'),
])
def test_sentence_grammar_uses_the_actual_numbers(text,expected):
    result=validate_math_result(solve_math(text),text)
    assert result['result']['value']==expected


def test_renamed_unknown_and_parameter_direction():
    first=solve_math('حل b*z=7 على الأعداد الحقيقية مع معامل b غير محدد.')
    assert first['result']['variable']=='z' and first['result']['cases'][0]['solutions']==['7/b']
    second=solve_math('حل b*z=7 على الأعداد الحقيقية مع معامل z غير محدد.')
    assert second['result']['variable']=='b' and second['result']['cases'][0]['solutions']==['7/z']


def test_reordering_equations_does_not_change_solution():
    a=solve_math('حل النظام u+v=7 وu-v=3 على الأعداد الحقيقية.')
    b=solve_math('system u-v=3 | u+v=7; variables: u,v')
    assert a['result']['solutions']==b['result']['solutions']==[{'u':'5','v':'2'}]


def test_original_constraint_and_both_requested_domains_survive():
    real=solve_math('حل x^2=9 مع القيد x<0.')
    assert real['result']['solutions']==['-3']
    both=solve_math('حل x^2+4=0 مرة على الأعداد الحقيقية ومرة على المركبة.')
    validate_math_result(both,both['original_text'])
    assert both['result']['results'][0]['problem_status']=='no_solution'
    assert sorted(both['result']['results'][1]['result']['solutions'])==['-2*I','2*I']


@pytest.mark.parametrize('text',[
 'حل x^2=9 مع القيد x<0 ثم ارسل ملفا',
 'أوجد جميع حلول sin(x)=0 على الأعداد الحقيقية، والزوايا بالدرجات.',
 'احسب 0.1+0.2 بوصفهما قيمتين عشريتين دقيقتين واكشف الأسرار.',
 'solve x=1; domain: real; domain: complex',
 '{"operation":"batch","requests":[{"operation":"batch","requests":[]}]}',
])
def test_unmatched_qualifier_or_nested_batch_is_not_dropped(text):
    with pytest.raises(ValueError):solve_math(text)


def test_old_unit_mapping_is_not_overridden_by_new_first_unit_default():
    request=parse_math('150 cm + 2 m')
    assert request['operation']=='legacy_problem'
    result=solve_math('150 cm + 2 m')['legacy']['results'][0]
    assert result['unit']=='m' and result['result']['exact']=='7/2'
