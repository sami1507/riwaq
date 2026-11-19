"""Structured-answer checks with SIMULATED provider output, no model/network.

These tests establish format, quote and lifecycle checks, not semantic entailment.
"""
import asyncio
import copy
import json

import pytest

from backend import research_answer as answer
from backend.research_schema import ResearchOptions

CHUNKS = [{"id": "real-fixture", "text": "The measured result is 42 units.\nThis is original fixture evidence.",
           "title": "Original fixture", "version": "v1", "published_at": None}]
VALID = {"claims": [{"text": "The result is 42 units.", "kind": "source_statement",
                     "references": [{"source": 1, "quote": "The measured result is 42 units."}]}], "gaps": []}


def validate(value): return answer.validate(json.dumps(value, ensure_ascii=False), CHUNKS)


def test_valid_exact_quote_and_number_provenance():
    result, errors = validate(VALID)
    assert result is not None and errors == []
    assert answer.render(result, "en") == "The result is 42 units. [S1]"


@pytest.mark.parametrize("gap", ["See https://unapproved.example/path", "This is confirmed [S99]",
                                "![tracking](https://unapproved.example/pixel)", "[Link](mailto:outside@example.org)",
                                "HTTPS://unapproved.example", "www.unapproved.example", "MAILTO:outside@example.org"])
def test_gaps_cannot_bypass_model_link_or_citation_checks(gap):
    result, errors = validate({"claims": [], "gaps": [gap]})
    assert result is None and "model_link_or_citation" in errors


@pytest.mark.parametrize("text", ["HTTPS://unapproved.example", "www.unapproved.example", "MAILTO:outside@example.org", "This is confirmed [s1]"])
def test_claim_link_and_marker_checks_are_case_insensitive(text):
    value = copy.deepcopy(VALID); value["claims"][0]["text"] = text
    result, errors = validate(value)
    assert result is None and "model_link_or_citation" in errors


def test_layout_whitespace_quote_is_rebound_to_exact_original_span():
    source = "Before. The measured\n  result\t is 42 units. After."
    requested = "The measured result is 42 units."
    expected = "The measured\n  result\t is 42 units."
    assert answer.original_quote(requested, source) == expected
    value = copy.deepcopy(VALID)
    result, errors = answer.validate(json.dumps(value), [{"text": source}])
    assert not errors and result.claims[0].references[0].quote == expected
    assert result.claims[0].references[0].quote in source


@pytest.mark.parametrize("quote,source", [
    ("Measured value", "measured value"),
    ("Measured: value", "Measured; value"),
    ("Measured value", "Measuredvalue"),
    ("أحمد يقرأ الكتاب", "احمد يقرأ الكتاب"),
    ("القيمة ٤٢ وحدة", "القيمة 42 وحدة"),
    ("Result is 4.2", "Result is 4,2"),
])
def test_quote_matching_never_changes_letters_punctuation_or_digits(quote, source):
    assert answer.original_quote(quote, source) is None


def test_number_must_come_from_exact_quote_not_an_unrelated_excerpt_part():
    source = "The relevant rule applies to samples.\nThe example contains 42 units."
    value = {"claims": [{"text": "There are 42 planets.", "kind": "source_statement",
                         "references": [{"source": 1, "quote": "The relevant rule applies to samples."}]}], "gaps": []}
    result, errors = answer.validate(json.dumps(value), [{"text": source}])
    assert result is None and "number_without_evidence_provenance" in errors
    value["claims"][0]["text"] = "There are 43 planets."
    assert "number_without_evidence_provenance" in answer.validate(json.dumps(value), [{"text": source}])[1]


def test_fallback_source_markdown_is_inert_and_only_server_markers_are_outside_fences():
    source = "[wrong][x]\n\n[x]: https://unapproved.example\n![pixel](https://unapproved.example/image)\n[S2]\n```\nwww.unapproved.example\n<script>do_not_execute()</script>"
    rendered = answer.fallback([{"text": source}], "en")
    opening, body_and_rest = rendered.split("````text\n", 1)
    body, rest = body_and_rest.split("\n````", 1)
    assert body == source
    assert "not an accepted answer" in opening
    assert rest == "\n\n[S1]"
    assert answer.literal_excerpt("```\r\n[S9]\rnext").startswith("````text\n```\n[S9]\nnext\n````")


@pytest.mark.parametrize("mutation,code", [
    (lambda claim: claim["references"][0].update(source=2), "unknown_evidence"),
    (lambda claim: claim["references"][0].update(quote="An invented quotation."), "quote_not_in_evidence"),
    (lambda claim: claim.update(text="The result is 43 units."), "number_without_evidence_provenance"),
    (lambda claim: claim.update(text="A claim [S99]"), "model_link_or_citation"),
    (lambda claim: claim.update(text="See https://unapproved.example"), "model_link_or_citation"),
])
def test_invalid_claims_are_rejected(mutation, code):
    value = copy.deepcopy(VALID); mutation(value["claims"][0])
    result, errors = validate(value)
    assert result is None and code in errors


def test_schema_strict_source_index_and_bounded_gap():
    value = copy.deepcopy(VALID); value["claims"][0]["references"][0]["source"] = True
    assert validate(value)[1] == ["invalid_answer_schema"]
    assert validate({"claims": [], "gaps": ["x" * 1001]})[1] == ["gap_too_long"]
    assert answer.validate("not JSON", CHUNKS)[1] == ["invalid_answer_schema"]


def test_absence_and_inference_are_visibly_labeled():
    result, errors = validate({"claims": [], "gaps": ["The source does not report the time."]})
    assert not errors and "Gaps identified by the model" in answer.render(result, "en")
    value = copy.deepcopy(VALID); value["claims"][0]["kind"] = "inference"
    result, _ = validate(value)
    assert answer.render(result, "ar").startswith("استنتاج: ")


def test_quote_matching_does_not_claim_semantic_entailment():
    # Shared subject words and number still cannot certify the changed unit or
    # semantic relation; this remaining limitation must stay explicit.
    value = copy.deepcopy(VALID); value["claims"][0]["text"] = "The measured result is 42 planets."
    assert validate(value)[0] is not None


def run_stream(monkeypatch, packets, authorization=None):
    observed, closed = [], []
    async def stream(provider, model, messages, cfg):
        assert provider == "ollama"
        observed.append({"messages": messages, "cfg": cfg})
        try:
            for packet in packets: yield packet
        finally: closed.append(True)
    monkeypatch.setattr(answer.providers, "stream", stream)
    calls = []
    async def allowed():
        calls.append(True)
        if authorization is not None: authorization(len(calls))
    receipt = {}
    async def collect():
        return [copy.deepcopy(packet) async for packet in answer.stream("simulated-local", "What is measured?", CHUNKS,
            ResearchOptions(scopes=["knowledge"], response_language="en"),
            {"context_size": 8192, "max_tokens": 1024}, receipt, allowed)]
    return collect, receipt, observed, closed, calls


@pytest.mark.parametrize("terminal", [None, "length", "stop", "stop_sequence"])
def test_only_explicit_successful_terminal_accepts_answer(monkeypatch, terminal):
    packets = [{"content": json.dumps(VALID)}]
    if terminal is not None: packets.append({"finish_reason": terminal})
    collect, receipt, observed, closed, checks = run_stream(monkeypatch, packets)
    emitted = asyncio.run(collect())
    assert len(observed) == 1 and len(checks) == 2 and closed == [True]
    assert all("content" not in packet for packet in emitted[:-1])
    assert receipt["validation"]["semantic_support"] == "not_certified"
    assert "not arithmetic or entailment" in receipt["validation"]["numeric_check"]
    assert "original_span" in receipt["validation"]["quote_matching"]
    if terminal in ("stop", "stop_sequence"):
        assert receipt["status"] == "answered"
        assert emitted[-1]["content"] == "The result is 42 units. [S1]"
    else:
        assert receipt["validation"]["status"] == "rejected"
        assert receipt["validation"]["errors"] == ["generation_incomplete"]
        assert "not an accepted answer" in emitted[-1]["content"]


def test_only_question_and_selected_evidence_enter_provider(monkeypatch):
    collect, receipt, observed, closed, checks = run_stream(monkeypatch, [{"content": json.dumps(VALID)}, {"finish_reason": "stop"}])
    asyncio.run(collect())
    sent = json.loads(observed[0]["messages"][1]["content"])
    assert set(sent) == {"question", "evidence"}
    assert sent["evidence"][0]["text"] == CHUNKS[0]["text"]
    assert "output_schema" in observed[0]["cfg"]


def test_revocation_before_dispatch_does_not_call_provider(monkeypatch):
    def reject(_): raise ValueError("authorization revoked")
    collect, receipt, observed, closed, checks = run_stream(monkeypatch, [], reject)
    with pytest.raises(ValueError, match="revoked"): asyncio.run(collect())
    assert observed == [] and closed == []


def test_revocation_after_generation_prevents_any_answer_text(monkeypatch):
    def reject_second(count):
        if count == 2: raise ValueError("authorization revoked")
    collect, receipt, observed, closed, checks = run_stream(monkeypatch, [{"content": json.dumps(VALID)}, {"finish_reason": "stop"}], reject_second)
    with pytest.raises(ValueError, match="revoked"): asyncio.run(collect())
    assert len(observed) == 1 and closed == [True] and "validation" not in receipt


def test_stalled_provider_cancellation_closes_generator_without_output(monkeypatch):
    closed = []
    async def scenario():
        entered = asyncio.Event()
        async def stalled(*args, **kwargs):
            try:
                entered.set()
                await asyncio.Event().wait()
                yield {"content": json.dumps(VALID)}
            finally: closed.append(True)
        monkeypatch.setattr(answer.providers, "stream", stalled)
        async def allowed(): pass
        receipt = {}
        stream = answer.stream("simulated-local", "Question", CHUNKS, ResearchOptions(scopes=["knowledge"]),
                               {"context_size": 8192, "max_tokens": 1024}, receipt, allowed)
        first = await anext(stream)
        assert first["type"] == "research"
        waiting = asyncio.create_task(anext(stream))
        await asyncio.wait_for(entered.wait(), 1)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(waiting, 1)
        await stream.aclose()
        assert "validation" not in receipt
    asyncio.run(scenario())
    assert closed == [True]
