"""Verse wording comes from the verified file or not at all. Fixture text is synthetic; no Qur'anic text lives in the tests."""
import json

import pytest

from backend import grounding, quran_reference as q


def write(tmp_path, counts=None, mutate=None):
    counts = counts or q.COUNTS
    lines = [f'{s + 1}|{a + 1}|نص {s + 1}:{a + 1}' for s, c in enumerate(counts) for a in range(c)]
    if mutate:
        mutate(lines)
    folder = tmp_path / 'reference'; folder.mkdir(exist_ok=True)
    (folder / 'quran-uthmani.txt').write_text('\n'.join(lines) + '\n\n# Tanzil notice block stays in the file\n', encoding='utf-8')


@pytest.fixture
def installed(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path))
    q._cache.update(key=None, value=None)
    write(tmp_path)
    return tmp_path


def ref(question):
    text = q.context(question)
    return json.loads(text)['quran_reference'] if text else None


def test_table_is_complete():
    assert len(q.NAMES) == 114 and sum(q.COUNTS) == 6236 and len(set(q.NAMES)) == 114
    assert (q.NAMES[0], q.COUNTS[0], q.NAMES[1], q.COUNTS[1], q.NAMES[113], q.COUNTS[113]) == ('الفاتحة', 7, 'البقرة', 286, 'الناس', 6)


def test_exact_verse_is_supplied_verbatim_from_the_file(installed):
    for question in ('ما الآية 255 من سورة البقرة؟', 'اكتب آية الكرسي', 'سورة البقرة، الآية رقم ٢٥٥', 'فسّر قوله تعالى في 2:255'):
        assert ref(question)['verses'] == [{'surah': 2, 'name': 'البقرة', 'verse': 255, 'text': 'نص 2:255'}], question


def test_a_verse_that_does_not_exist_is_reported_not_invented(installed):
    out = ref('اكتب نص الآية رقم 300 من سورة البقرة.')
    assert 'verses' not in out and out['not_found'] == [{'surah': 2, 'name': 'البقرة', 'requested_verse': 300, 'surah_verse_count': 286}]


def test_range_alias_and_count_only(installed):
    assert [v['verse'] for v in ref('اشرح الآيات 1-3 من سورة الإخلاص')['verses']] == [1, 2, 3]
    assert ref('اقرأ سورة الصمد الآية 4')['verses'][0]['surah'] == 112
    assert ref('كم عدد آيات سورة يس؟')['surah'] == {'surah': 36, 'name': 'يس', 'verse_count': 83}
    assert ref('اذكر الآيات 285 إلى 290 من سورة البقرة')['not_found'][0]['requested_verse'] == 290


def test_open_request_gets_the_rule_but_no_wording(installed):
    out = ref('اذكر آية عن الصبر من القرآن')
    assert 'verses' not in out and 'do NOT write its wording from memory' in out['rule']


def test_everyday_text_is_left_alone(installed):
    for question in ('ما عاصمة مصر؟', 'هذه آية في الجمال', 'النسبة 2:255 في التقرير', 'سافر محمد إلى نوح', 'اكتب سورًا للحديقة'):
        assert q.context(question) == '', question


def test_missing_file_supplies_no_wording_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path)); q._cache.update(key=None, value=None)
    out = ref('ما الآية 255 من سورة البقرة؟')
    assert out['verified_text_installed'] is False and 'verses' not in out
    assert out['text_not_supplied'] == [{'surah': 2, 'name': 'البقرة', 'verse': 255, 'reason': 'not_installed'}]
    assert ref('اكتب الآية 300 من سورة البقرة')['not_found'][0]['surah_verse_count'] == 286


@pytest.mark.parametrize('damage', [lambda l: l.pop(100), lambda l: l.insert(5, '1|9|زيادة'), lambda l: l.__setitem__(0, '1|1|'), lambda l: l.append('115|1|زيادة')])
def test_a_file_with_the_wrong_shape_is_refused_whole(tmp_path, monkeypatch, damage):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path)); q._cache.update(key=None, value=None)
    write(tmp_path, mutate=damage)
    assert q.load() == {'error': 'invalid_file'}
    assert 'verses' not in ref('ما الآية 1 من سورة الفاتحة؟')


def test_long_requests_are_bounded(installed):
    out = ref('اكتب الآيات 1 إلى 60 من سورة البقرة')
    assert len(out['verses']) == q.MAX_VERSES and out['text_not_supplied'][0] == {'surah': 2, 'name': 'البقرة', 'verse': 11, 'reason': 'request_limit'}


def test_check_data_reaches_the_model_as_application_data(installed):
    messages, _ = grounding.compose({'instructions': ''}, [], [], 'ما الآية 2 من سورة الفاتحة؟', [], {'context_size': 8192, 'max_tokens': 1024}, False,
                                    tool_context=q.context('ما الآية 2 من سورة الفاتحة؟'))
    assert '<application_check_data>' in messages[-1]['content'] and 'نص 1:2' in messages[-1]['content']


# ---- output guard: synthetic vocalised sentences stand in for verse text ----
V1 = 'ذَهَبَ الْوَلَدُ إِلَى الْمَدْرَسَةِ صَبَاحًا'
V2 = 'وَعَادَ إِلَى الْبَيْتِ مَسَاءً فَرِحًا'


@pytest.fixture
def vocalised(tmp_path, monkeypatch):
    monkeypatch.setenv('RIWAQ_DATA_DIR', str(tmp_path)); q._cache.update(key=None, value=None)
    def put(lines):
        lines[0] = '1|1|' + V1; lines[1] = '1|2|' + V2
    write(tmp_path, mutate=put)


ASK = 'ما الآية 1 من سورة الفاتحة؟'


def test_exact_wording_is_kept_untouched(vocalised):
    answer = f'نص الآية: «{V1}» [الفاتحة: 1]، وهي واضحة.'
    assert q.render(answer, ASK) == (answer, {'kept': 1, 'replaced': 0, 'removed': 0, 'appended': 0})


def test_an_altered_copy_is_replaced_by_the_exact_text(vocalised):
    altered = 'ذَهَبَ الوَلَدُ إلى المَدرَسَةِ صَباحاً'
    text, report = q.render(f'الآية هي: {altered}. انتهى.', ASK)
    assert V1 in text and altered not in text and report == {'kept': 1, 'replaced': 1, 'removed': 0, 'appended': 0}
    assert text.startswith('الآية هي: ') and text.endswith('. انتهى.')


def test_vocalised_wording_from_memory_is_removed_and_marked(vocalised):
    invented = 'وَمَنْ يَكُنْ فِي الدَّارِ يَكْتُمُ مَا عَلِمَ'
    text, report = q.render(f'تقول الآية: {invented}', 'اذكر آية عن الصبر من القرآن')
    assert invented not in text and q.REMOVED in text and report['removed'] == 1


def test_a_passage_unrelated_to_the_requested_verse_is_removed_not_swapped(vocalised):
    text, report = q.render('قَالَ الرَّجُلُ كَلَامًا طَوِيلًا جِدًّا', ASK)
    assert text.startswith(q.REMOVED) and report == {'kept': 0, 'replaced': 0, 'removed': 1, 'appended': 1}
    assert text.endswith(f'{q.APPENDED}\n﴿{V1}﴾ [الفاتحة: 1]')


def test_plain_prose_and_single_vocalised_words_are_left_alone(vocalised):
    answer = 'هذه الآية تتحدث عن الذهاب إلى المدرسة، وهو أمر مهمٌّ جدًّا أيضًا. The verse is short.'
    assert q.render(answer, 'اذكر آية من القرآن') == (answer, {'kept': 0, 'replaced': 0, 'removed': 0, 'appended': 0})


def test_a_requested_verse_the_model_left_out_is_appended_exactly(vocalised):
    text, report = q.render('هذه آية قصيرة معناها واضح.', 'اكتب الآيات 1-2 من سورة الفاتحة')
    assert report['appended'] == 2 and text == f'هذه آية قصيرة معناها واضح.\n\n{q.APPENDED}\n﴿{V1}﴾ [الفاتحة: 1]\n﴿{V2}﴾ [الفاتحة: 2]'


def test_consecutive_verses_written_as_one_passage_are_replaced_together(vocalised):
    text, report = q.render('ذهب الوَلَدُ إلى المَدرَسَةِ صَباحاً وَعادَ إلى البَيتِ مَساءً فَرِحاً', 'اكتب الآيات 1-2 من سورة الفاتحة')
    assert f'{V1} {V2}' in text and report['replaced'] == 1


def test_guard_stream_releases_only_the_rendered_text_and_keeps_the_finish_packet(vocalised):
    import asyncio
    async def model():
        yield {'content': 'الآية: ذَهَبَ الوَلَدُ '}
        yield {'content': 'إلى المَدرَسَةِ صَباحاً'}
        yield {'finish_reason': 'stop', 'metrics': {'eval_count': 9}}
    async def collect():
        return [p async for p in q.guard_stream(model(), ASK)]
    packets = asyncio.run(collect())
    assert packets == [{'content': 'الآية: ' + V1}, {'finish_reason': 'stop', 'metrics': {'eval_count': 9}}]
