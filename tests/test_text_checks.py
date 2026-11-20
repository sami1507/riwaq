"""Ordering is computed by the application; a stated word count is verified on the finished answer."""
import asyncio
import json

import pytest

from backend import text_checks as t


def test_alphabetical_numeric_and_descending_orders():
    assert t.ordering('رتّب هذه الكلمات ترتيبًا ألفبائيًا: تفاح، برتقال، جزر، ثوم')['sorted'] == ['برتقال', 'تفاح', 'ثوم', 'جزر']
    assert t.ordering('رتب تصاعديًا: 15، 3، ٢٢، 7')['sorted'] == ['3', '7', '15', '٢٢']
    assert t.ordering('رتب تنازليا: 1.5, 10, 2')['sorted'] == ['10', '2', '1.5']
    assert t.ordering('رتب أبجديًا: الكتاب، قلم، البيت، أرض')['sorted'] == ['أرض', 'البيت', 'قلم', 'الكتاب']


@pytest.mark.parametrize('question', ['رتّب أفكاري في الموضوع', 'رتب هذه: تفاح، موز', 'ما الترتيب الأبجدي؟', 'رتب أبجديًا: واحد'])
def test_ordering_needs_an_explicit_instruction_and_a_list(question):
    assert t.ordering(question) is None


@pytest.mark.parametrize('question,rule', [('اكتب ثلاث كلمات فقط تصف البحر، مفصولة بفواصل.', ('exact', 3)), ('أجب بكلمة واحدة فقط: ما عاصمة مصر؟', ('exact', 1)),
                                           ('اكتب جملة واحدة من خمس كلمات بالضبط عن المطر.', ('exact', 5)), ('اكتب ١٠ كلمات فقط عن الصيف', ('exact', 10)),
                                           ('لخّص النص الآتي في جملة لا تتجاوز 15 كلمة:\nنص طويل', ('max', 15))])
def test_stated_word_counts_are_recognised(question, rule):
    assert t.constraint(question) == rule


@pytest.mark.parametrize('question', ['ما معنى كلمة واحدة؟', 'اشرح لي الكلمات', 'اكتب عنوانًا من ثلاث كلمات فقط ثم فقرة طويلة', 'كم كلمة في هذه الجملة؟'])
def test_other_questions_carry_no_word_count_rule(question):
    assert t.constraint(question) is None


def test_word_count_ignores_punctuation():
    assert t.word_count('هطل المطر غزيرًا هذا المساء.') == 5 and t.word_count('واسع، أزرق، عميق') == 3 and t.word_count(' — ') == 0


def run(first, second, question='اكتب جملة واحدة من خمس كلمات بالضبط عن المطر.'):
    calls = []
    async def model():
        for piece in first.split('|'):
            yield {'content': piece}
        yield {'finish_reason': 'stop', 'metrics': {'eval_count': 1}}
    async def retry(draft, feedback):
        calls.append((draft, feedback))
        yield {'content': second}
        yield {'finish_reason': 'stop', 'metrics': {'eval_count': 2}}
    async def collect():
        return [p async for p in t.enforce(model(), question, retry)]
    return asyncio.run(collect()), calls


def test_a_correct_answer_passes_through_without_a_second_call():
    packets, calls = run('هطل المطر |غزيرًا هذا المساء', 'غير مستخدم')
    assert packets == [{'content': 'هطل المطر غزيرًا هذا المساء'}, {'finish_reason': 'stop', 'metrics': {'eval_count': 1}}] and calls == []


def test_a_wrong_count_gets_one_corrective_attempt_with_the_real_numbers():
    packets, calls = run('المطر نعمة.', 'هطل المطر غزيرًا هذا المساء')
    assert packets[0] == {'content': 'هطل المطر غزيرًا هذا المساء'} and packets[1]['metrics'] == {'eval_count': 2}
    assert calls == [('المطر نعمة.', t.feedback('المطر نعمة.', ('exact', 5)))] and 'فيها 2 كلمة، والمطلوب 5 بالضبط' in calls[0][1]


def test_if_the_second_attempt_still_misses_the_user_is_told_the_real_count():
    packets, _ = run('المطر نعمة.', 'المطر يتساقط بسكون جميل جدًا الآن')
    assert packets[0]['content'].startswith('المطر يتساقط بسكون جميل جدًا الآن') and 'عدد كلمات هذه الإجابة 6، والمطلوب 5 بالضبط' in packets[0]['content']
    worse, _ = run('المطر نعمة كبيرة جدًا.', 'لا')
    assert worse[0]['content'].startswith('المطر نعمة كبيرة جدًا.') and 'عدد كلمات هذه الإجابة 4' in worse[0]['content']


def test_combined_context_carries_the_computed_order():
    data = json.loads(t.context('رتب تصاعديًا: 9، 2، 5'))
    assert data['ordering']['sorted'] == ['2', '5', '9'] and t.context('ما عاصمة مصر؟') == ''


def test_mixed_script_words_are_found_and_ordinary_text_is_not():
    assert t.garbled('عاصمة الأردن هي أмман.') == ['أмман']
    assert t.garbled('كلمة عر语بية وأخرى سلaمة') == ['عر语بية', 'سلaمة']
    for fine in ['استخدم الـAPI مع Python 3.12', 'ترجمتها: Москва', 'كلمة שלום بالعبرية', 'Wi-Fi و GPU', 'الآية ٱللَّهُ', 'بـPython']:
        assert t.garbled(fine) == [], fine


def test_flag_stream_keeps_streaming_and_adds_one_note_before_the_end():
    async def model():
        yield {'content': 'عاصمة الأردن '}
        yield {'content': 'هي أмман'}
        yield {'finish_reason': 'stop'}
    async def clean():
        yield {'content': 'عمّان'}
        yield {'finish_reason': 'stop'}
    async def collect(source):
        return [p async for p in t.flag_stream(source)]
    packets = asyncio.run(collect(model()))
    assert packets[:2] == [{'content': 'عاصمة الأردن '}, {'content': 'هي أмман'}] and '«أмман»' in packets[2]['content'] and packets[3] == {'finish_reason': 'stop'}
    assert asyncio.run(collect(clean())) == [{'content': 'عمّان'}, {'finish_reason': 'stop'}]
