"""Fixed public parser tests. Synthetic bytes only; no remote fetch or model."""
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from backend import research_extract as parser, visual_runner
from backend.knowledge_fetch import FetchError, FetchCancelled

URL = "https://docs.example.org/guide"


@pytest.fixture
def run_stub(tmp_path, monkeypatch):
    from backend import isolated_extract
    from backend.isolated_parsers import VERSION
    monkeypatch.setenv("RIWAQ_DATA_DIR", str(tmp_path))
    control = {"calls": [], "pages": [[None, "Original text\n"]], "result_change": None,
               "resources": {"status":"completed", "cleanup_verified":True, "policy_inspected":True}}
    monkeypatch.setattr(isolated_extract, 'load_config', lambda path: type('Config', (), {'images':{'document_extract':'sha256:'+'a'*64}})())
    def execute(files, runner, targets, **kwargs):
        metadata=json.loads(files['metadata.json']);body=files['input.bin']
        control['calls'].append({'runner':runner,'targets':targets,'metadata':metadata,'body':body,**kwargs})
        result={'version':VERSION,'success':True,'input_sha256':hashlib.sha256(body).hexdigest(),'pages':control['pages']}
        if control['result_change']:result.update(control['result_change'])
        if control.get('cancel'):control['cancel'].set()
        return {**control['resources'],'stdout':json.dumps(result),'stderr':''}
    monkeypatch.setattr(isolated_extract, 'execute', execute)
    def forbidden(*a,**k): raise AssertionError('Host worker must never be invoked')
    monkeypatch.setattr(visual_runner,'execute',forbidden)
    return control,tmp_path


def test_fixed_command_exact_pages_stripped_metadata_and_cleanup(run_stub):
    control, root = run_stub
    pages = parser.extract_bounded(b"Original text\n", {"content_type": "markdown", "private_setting": "must-not-enter-worker"}, URL, "text/plain")
    assert pages == [(None, "Original text\n")]
    assert pages.resources["isolation"] == "linux_container_shared_kernel_no_host_mounts_network_none"
    call = control["calls"][0]
    assert call['runner']=='document_extract' and call['targets']==['input.bin']
    assert call['timeout_seconds']==45 and call['memory_mb']==768
    assert call['metadata']['source']=={'content_type':'markdown'}
    assert not (root/'research-workers').exists()



@pytest.mark.parametrize("pages", [[], [[True, "text"]], [[201, "text"]], [[None, ""]], [[None, "a\x00b"]], [[None, 13]], [[None, "x" * (parser.MAX_CHARACTERS + 1)]]])
def test_invalid_or_oversized_parser_results_rejected(run_stub, pages):
    control, _ = run_stub
    control["pages"] = pages
    with pytest.raises(FetchError, match="valid bounded"):
        parser.extract_bounded(b"x", {}, URL, "text/plain")
    assert control["calls"]


@pytest.mark.parametrize("result", [{"input_sha256": "0" * 64}, {"version": "other"}, {"success": False}])
def test_stale_or_failed_worker_result_not_accepted(run_stub, result):
    control, _ = run_stub
    control["result_change"] = result
    with pytest.raises(FetchError): parser.extract_bounded(b"x", {}, URL, "text/plain")


@pytest.mark.parametrize("resources", [
    {"status":"timeout","cleanup_verified":True,"policy_inspected":True},
    {"status":"resource_limit","cleanup_verified":True,"policy_inspected":True},
    {"status":"failed","cleanup_verified":True,"policy_inspected":True},
    {"status":"completed","cleanup_verified":False,"policy_inspected":True},
    {"status":"completed","cleanup_verified":True,"policy_inspected":False},
])
def test_supervisor_failure_overrides_even_a_valid_result(run_stub, resources):
    control, _ = run_stub
    control["resources"] = resources
    with pytest.raises(FetchError): parser.extract_bounded(b"x", {}, URL, "text/plain")


def test_cancel_before_start_and_after_worker_exit(run_stub):
    control, _ = run_stub
    stop = threading.Event(); stop.set()
    with pytest.raises(FetchCancelled): parser.extract_bounded(b"x", {}, URL, "text/plain", stop)
    assert not control["calls"]
    stop.clear(); control["cancel"] = stop
    with pytest.raises(FetchCancelled): parser.extract_bounded(b"x", {}, URL, "text/plain", stop)
    assert control["calls"]


def test_expired_deadline_and_frozen_runtime_never_spawn(run_stub, monkeypatch):
    control, _ = run_stub
    stop = threading.Event(); stop.deadline = time.monotonic() - 1
    with pytest.raises(FetchCancelled): parser.extract_bounded(b"x", {}, URL, "text/plain", stop)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(FetchError, match="packaged_parser_unavailable"):
        parser.extract_bounded(b"x", {}, URL, "text/plain")
    assert not control["calls"]


def test_busy_parser_does_not_start_second_worker(run_stub, monkeypatch):
    control, _ = run_stub
    slots = threading.BoundedSemaphore(1); slots.acquire()
    monkeypatch.setattr(parser, "_SLOTS", slots)
    with pytest.raises(FetchError, match="busy"): parser.extract_bounded(b"x", {}, URL, "text/plain")
    assert not control["calls"]
    slots.release()


def small_original_pdf():
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    content = b"BT /F1 12 Tf 24 72 Td (Original local test sentence.) Tj ET\n"
    objects.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream")
    output = bytearray(b"%PDF-1.4\n"); offsets = [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(output)); output.extend(str(number).encode() + b" 0 obj\n" + value + b"\nendobj\n")
    start = len(output)
    output.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]: output.extend(f"{offset:010} 00000 n \n".encode())
    output.extend(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(start).encode() + b"\n%%EOF\n")
    return bytes(output)


@pytest.mark.skipif(os.environ.get("RIWAQ_TEST_REAL_RESEARCH_WORKER") != "1" or not os.environ.get("RIWAQ_TEST_ISOLATED_CONFIG"), reason="Explicit central real-worker run required")
@pytest.mark.parametrize("kind", ["html", "pdf"])
def test_actual_fixed_worker_preserves_original_text_and_full_cleanup(tmp_path, monkeypatch, kind):
    config=Path(os.environ['RIWAQ_TEST_ISOLATED_CONFIG']).read_bytes()
    monkeypatch.setenv("RIWAQ_DATA_DIR",str(tmp_path));(tmp_path/'isolated-workers.json').write_bytes(config)
    if kind == "html":
        body = '<html><script>fetch("https://invalid.example/")</script><h1>أصل</h1><p>نص تجريبي أصلي.</p><pre>if x:\n    y = 3\n</pre></html>'.encode()
        source, content_type, url = {"content_type": "html"}, "text/html", URL
    else:
        body = small_original_pdf()
        source, content_type, url = {}, "application/pdf", URL + ".pdf"
    result = parser.extract_bounded(body, source, url, content_type)
    if kind == "html":
        assert "أصل" in result[0][1] and "    y = 3\n" in result[0][1]
        assert "fetch" not in result[0][1] and "invalid.example" not in result[0][1]
    else:
        assert result[0][0] == 1 and "Original local test sentence." in result[0][1]
    assert result.resources['exit_code']==0 and result.resources['cleanup_verified']
    assert result.resources['policy_inspected'] and result.resources['executed']
    assert not (tmp_path/'research-workers').exists()
