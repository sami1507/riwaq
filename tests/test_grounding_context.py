import pytest
from backend import public_knowledge as public
from backend.snapshots import CitationChunking
from backend.grounding import compose

def test_html_preserves_pre_indentation_blank_lines_and_code_heading():
    raw=b'<h2>Example</h2><pre><span>def f():</span>\n    # comment\n\n    return 1<br/>    return 2</pre><p>Next</p>'
    text=public.extract_public(raw,{'content_type':'html'},'https://example.org/a','text/html')[0][1]
    assert 'def f():\n    # comment\n\n    return 1\n    return 2' in text
    chunks=public.chunk_public([(None,text)],{'content_type':'html'},'https://example.org/a','text/html')
    assert all(chunk['heading']=='Example' for chunk in chunks)
    assert 'return 1\nreturn 2' not in text

def test_html_hidden_markup_and_table_boundaries():
    raw=b'<nav><footer>HIDDEN</footer>ALSO HIDDEN</nav><table><tr><td>12</td><td>kg</td></tr></table><pre>x<script>HIDDEN</script>\n  y</pre>'
    text=public.extract_public(raw,{'content_type':'html'},'https://example.org/a','text/html')[0][1]
    assert 'HIDDEN' not in text
    assert '12\tkg' in text and 'x\n  y' in text

def test_html_legacy_replay_and_unknown_extractor():
    raw=b'<pre>def f():\n    return 1</pre>'
    legacy=public.extract_public(raw,{'content_type':'html'},'https://example.org/a','text/html',extractor='legacy-v1')[0][1]
    assert legacy=='def f():\nreturn 1'
    with pytest.raises(ValueError):
        public.extract_public(raw,{'content_type':'html'},'https://example.org/a','text/html',extractor='unknown')

def test_snapshot_accepts_old_and_new_processing_metadata():
    policy={'algorithm':'heading-lines-v2','max_characters':1400,'max_lines':24,'overlap_lines':2,'respect_headings':True}
    assert CitationChunking.model_validate(policy).extractor=='legacy-v1'
    assert CitationChunking.model_validate(policy|{'extractor':'html-pre-v2'}).extractor=='html-pre-v2'
    with pytest.raises(ValueError):CitationChunking.model_validate(policy|{'extractor':'unknown'})

def test_context_never_truncates_question_or_source_span():
    question='ما نتيجة الاختبار؟'
    sources=[{'filename':'f','line_start':1,'line_end':1,'text':'A'*1300},{'filename':'g','line_start':2,'line_end':2,'text':'B'*1300}]
    config={'context_size':4096,'max_tokens':1024}
    messages,chosen=compose({'instructions':''},[],[],question,sources,config,True,policy='legacy-v1')
    assert len(chosen)==1
    assert question in messages[-1]['content']
    assert chosen[0]['text'] in messages[-1]['content']
    assert sum(len(m['content'].encode('utf-8'))+16 for m in messages)<=4096-1024-512

def test_context_rejects_impossible_output_and_oversized_user_input():
    with pytest.raises(ValueError):compose({'instructions':''},[],[],'q',[],{'context_size':2048,'max_tokens':2048},False)
    with pytest.raises(ValueError):compose({'instructions':''},[],[],'ع'*5000,[],{'context_size':2048,'max_tokens':512},False)
