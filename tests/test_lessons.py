"""Lessons the user teaches are stored locally, recalled by overlap, and always marked."""
import asyncio
import sqlite3

import pytest

from backend import lessons


@pytest.fixture()
def c():
    conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
    lessons.init(conn)
    return conn


def test_recall_finds_the_taught_answer_and_ignores_unrelated_questions(c):
    lessons.add(c, 'me', 'كم عدد موظفي شركة النخيل؟', 'عدد موظفي شركة النخيل 42 موظفًا حتى نوفمبر.')
    lessons.add(c, 'me', 'إعدادات محرك الناقل رقم 3', 'التردد 37 هرتز وزمن التسارع 4 ثوان.')
    found = lessons.recall(c, 'me', 'كم موظفًا في شركة النخيل؟')
    assert found and '42' in found[0]['content']
    assert lessons.recall(c, 'me', 'ما عاصمة فرنسا؟') == []
    assert lessons.recall(c, 'me', 'ما هي اعدادات المحرك للناقل 3')[0]['title'].startswith('إعدادات')


def test_lessons_belong_to_their_owner_and_can_be_switched_off(c):
    item = lessons.add(c, 'me', 'What is the door code policy?', 'Codes are never written down; ask the shift manager.')
    assert lessons.recall(c, 'other', 'what is the door code policy') == []
    with pytest.raises(KeyError):
        lessons.get(c, 'other', item['id'])
    lessons.update(c, 'me', item['id'], enabled=False)
    assert lessons.recall(c, 'me', 'what is the door code policy') == []
    lessons.delete(c, 'me', item['id'])
    assert lessons.list_lessons(c, 'me') == []


def test_limits_and_empty_values_are_refused(c):
    for title, content in (('', 'x'), ('x', ' '), ('x' * 401, 'y'), ('x', 'y' * 4001), ('x\x00', 'y')):
        with pytest.raises(lessons.LessonError):
            lessons.add(c, 'me', title, content)


def test_context_is_data_and_the_answer_is_marked(c):
    lessons.add(c, 'me', 'موعد صيانة الضاغط', 'كل 500 ساعة تشغيل.')
    found = lessons.recall(c, 'me', 'متى موعد صيانة الضاغط؟')
    data = lessons.context(found)['saved_lessons']
    assert data['lessons'][0]['content'] == 'كل 500 ساعة تشغيل.' and 'exactly' in data['rule']
    assert lessons.context([]) == {} and lessons.note([]) == ''

    async def run():
        async def source():
            yield {'content': 'كل 500 ساعة.'}
            yield {'done': True}
        return [p async for p in lessons.mark_stream(source(), found)]
    packets = asyncio.run(run())
    assert packets[0]['content'].startswith('⟦من دروسك المحفوظة: «موعد صيانة الضاغط»') and packets[-1] == {'done': True}
    lessons.mark_used(c, 'me', found)
    assert lessons.get(c, 'me', found[0]['id'])['uses'] == 1

