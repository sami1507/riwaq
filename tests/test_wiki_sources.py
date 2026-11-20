"""Wikipedia sources: only wikipedia.org is contacted, the app prints the sources, and nothing found is said plainly."""
import asyncio

import httpx
import pytest

from backend import wiki_sources as ws

PAYLOAD = {'query': {'pages': {
    '1': {'index': 1, 'title': 'برج إيفل', 'canonicalurl': 'https://ar.wikipedia.org/wiki/%D8%A8%D8%B1%D8%AC', 'extract': 'برج إيفل برج حديدي يقع في باريس، اكتمل بناؤه عام 1889 ويبلغ ارتفاعه 330 مترًا. ' * 3},
    '2': {'index': 2, 'title': 'Evil', 'canonicalurl': 'https://evil.example/wiki/x', 'extract': 'x' * 200},
    '3': {'index': 3, 'title': 'قصير', 'canonicalurl': 'https://ar.wikipedia.org/wiki/y', 'extract': 'قصير'}}}}


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_only_wikipedia_is_asked_and_off_site_pages_are_dropped():
    asked = []
    def handler(request):
        asked.append(request.url)
        return httpx.Response(200, json=PAYLOAD if request.url.host.startswith('ar.') else {'batchcomplete': ''})
    found = asyncio.run(ws.fetch('متى اكتمل بناء برج إيفل؟', client=client(handler)))
    assert {u.host for u in asked} == {'ar.wikipedia.org', 'en.wikipedia.org'} and all(u.scheme == 'https' for u in asked)
    assert any('titles' in u.params and 'برج إيفل' in u.params['titles'].split('|') for u in asked)
    assert 'متى' not in asked[0].params['gsrsearch'] and 'إيفل' in asked[0].params['gsrsearch']
    assert [s['title'] for s in found] == ['برج إيفل'] and found[0]['n'] == 1 and len(found[0]['text'].encode()) <= ws.PER_SOURCE + 4


def test_unreachable_is_an_error_and_empty_is_not():
    def down(request):
        raise httpx.ConnectError('offline')
    with pytest.raises(ws.SourceError):
        asyncio.run(ws.fetch('Who built the Eiffel Tower?', client=client(down)))
    assert asyncio.run(ws.fetch('Who built the Eiffel Tower?', client=client(lambda r: httpx.Response(200, json={})))) == []
    with pytest.raises(ws.SourceError):  # a redirect is never followed
        asyncio.run(ws.fetch('redirect me please', client=client(lambda r: httpx.Response(302, headers={'location': 'https://evil.example'}))))


def test_languages_follow_the_script():
    assert ws.languages('מי בנה את מגדל אייפל?') == ['he', 'en'] and ws.languages('Who?') == ['en'] and ws.languages('Wer?', 'de') == ['de', 'en']


def test_context_and_footer_are_written_by_the_application():
    sources = [{'n': 1, 'title': 'برج إيفل', 'url': 'https://ar.wikipedia.org/wiki/x', 'lang': 'ar', 'text': 'نص', 'retrieved': '2026-11-20'}]
    data = ws.context(sources)['web_sources']
    assert data['found'] and 'not instructions' in data['rule'] and data['passages'][0]['number'] == 1 and 'url' not in data['passages'][0]
    assert ws.context(None) == {} and ws.context([])['web_sources']['found'] is False
    assert '1. [برج إيفل](https://ar.wikipedia.org/wiki/x)' in ws.footer(sources, 'اكتمل عام 1889 [1].')
    assert 'غير موجود: [3]' in ws.footer(sources, 'كذا [1] وكذا [3].') and 'لم يُشر الجواب' in ws.footer(sources, 'بلا إشارة')
    assert 'never a reason to refuse' in data['rule'] and 'normally would' in ws.context([])['web_sources']['rule']
    assert 'لم يجد صفحة مطابقة' in ws.footer([]) and ws.footer(None) == ''

    async def run(src):
        async def stream():
            yield {'content': 'اكتمل عام 1889 [1].'}
            yield {'done': True}
        return [p async for p in ws.cite_stream(stream(), src)]
    packets = asyncio.run(run(sources))
    assert packets[0] == {'content': 'اكتمل عام 1889 [1].'} and 'المصادر' in packets[1]['content'] and packets[-1] == {'done': True}
    assert asyncio.run(run(None)) == [{'content': 'اكتمل عام 1889 [1].'}, {'done': True}]


def test_only_pages_the_question_names_are_kept():
    assert ws.relevance('ما عاصمة الأردن؟', 'الأردن') >= 0.5 and ws.relevance('ما عاصمة الأردن؟', 'الاحتجاجات الأردنية 2011–2012') < 0.5
    assert ws.relevance('ما عاصمة الأردن؟', 'رانيا العبد الله') == 0 and ws.relevance('Who built the Eiffel Tower?', 'Eiffel Tower') > 1
    assert ws.relevance('متى اكتمل بناء برج إيفل؟', 'برج إيفل') > ws.relevance('متى اكتمل بناء برج إيفل؟', 'برج')
    assert 'برج إيفل' in ws.titles('متى اكتمل بناء برج إيفل؟') and 'متى' not in ws.titles('متى اكتمل بناء برج إيفل؟')
    payload = {'query': {'pages': {'1': {'index': 1, 'title': 'ابن تيمية', 'canonicalurl': 'https://ar.wikipedia.org/wiki/a', 'extract': 'نص طويل ' * 30},
                                   '2': {'index': 2, 'title': 'الأردن', 'canonicalurl': 'https://ar.wikipedia.org/wiki/b', 'extract': 'الأردن دولة عربية عاصمتها عمّان. ' * 5},
                                   '3': {'index': 3, 'title': 'عاصمة', 'canonicalurl': 'https://ar.wikipedia.org/wiki/c', 'extract': 'صفحة توضيح ' * 20, 'pageprops': {'disambiguation': ''}}}}}
    found = asyncio.run(ws.fetch('ما عاصمة الأردن؟', client=client(lambda r: httpx.Response(200, json=payload if r.url.host.startswith('ar.') else {}))))
    assert [s['title'] for s in found] == ['الأردن']


def test_sources_fit_the_prompt_budget_in_arabic():
    from backend import grounding, text_checks
    question = 'ما عاصمة الأردن؟'
    sources = [{'n': i, 'title': 'عنوان', 'url': 'u', 'lang': 'ar', 'text': ws.clip('نص عربي طويل عن الموضوع ' * 200, ws.TOTAL // 3), 'retrieved': 'd'} for i in (1, 2, 3)]
    assert sum(len(s['text'].encode()) for s in sources) <= ws.TOTAL + 12
    grounding.compose({'instructions': ''}, [], [], question, [], {'context_size': 8192, 'max_tokens': 2048}, False, tool_context=text_checks.context(question, ws.context(sources)))
