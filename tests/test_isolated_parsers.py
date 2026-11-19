"""Synthetic parser/adapter tests; actual container acceptance is separate."""
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import threading
import zipfile

import pytest
from backend import isolated_parsers as p, isolated_extract as adapter, isolated_worker as boundary


def docx(text='Literal source 731'):
    result=io.BytesIO()
    with zipfile.ZipFile(result,'w') as archive:
        archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>'+text+'</w:t></w:r></w:p></w:document>')
    return result.getvalue()


def test_shared_docx_exact_text_and_malicious_archive_refusal():
    assert p._docx_pages(docx())==[(None,'Literal source 731')]
    result=io.BytesIO()
    with zipfile.ZipFile(result,'w') as archive:
        archive.writestr('../outside.xml','x');archive.writestr('word/document.xml','x')
    with pytest.raises(p.ParserError) as exc:p._docx_pages(result.getvalue())
    assert exc.value.status_code==400


def test_html_helper_is_shared_exact_code_preservation():
    from backend.public_knowledge import PublicHTML
    assert PublicHTML is p.PublicHTML
    data=b'<h1>Title</h1><pre>if x:\n    y = 3\n\n</pre><script>network()</script>'
    value=p.extract_public(data,{'content_type':'html'},'https://example.org/p','text/html')[0][1]
    assert '    y = 3\n\n' in value and 'network' not in value


def test_fixed_parser_main_protocol_on_trusted_fixture(monkeypatch):
    body=docx();raw=json.dumps({'body':base64.b64encode(body).decode(),'metadata':{'mode':'private','filename':'sample.docx'}}).encode()
    source=type('Input',(),{'buffer':io.BytesIO(raw)})();output=type('Output',(),{'buffer':io.BytesIO()})()
    monkeypatch.setattr(p.sys,'stdin',source);monkeypatch.setattr(p.sys,'stdout',output)
    assert p.main()==0
    result=json.loads(output.buffer.getvalue())
    assert result['success'] and result['input_sha256']==hashlib.sha256(body).hexdigest()
    assert result['pages']==[[None,'Literal source 731']]


@pytest.mark.parametrize('metadata',[
    {'mode':'private','filename':'../source.docx'}, {'mode':'private','filename':'program.py'},
    {'mode':'public','source':{'content_type':'html','secret':'x'},'url':'https://example.org','content_type':'text/html'},
    {'mode':'public','source':{'content_type':'html'},'url':'file:///secret','content_type':'text/html'},
    {'mode':'public','source':{'content_type':'html'},'url':'https://u:p@example.org','content_type':'text/html'},
    {'mode':'private','filename':'x.pdf','entrypoint':'shell'},
])
def test_metadata_cannot_select_paths_network_or_executable(metadata):
    with pytest.raises(p.ParserError):p.validate_metadata(metadata)


def test_document_profile_accepts_large_bounded_input_without_changing_code_profile():
    body=b'x'*(2*1024**2)
    metadata=json.dumps({'mode':'private','filename':'a.pdf'}).encode()
    payload,hashes=boundary.prepare_payload({'input.bin':body,'metadata.json':metadata},'document_extract',['input.bin'])
    assert json.loads(payload)['metadata']['filename']=='a.pdf' and hashes['input.bin']==hashlib.sha256(body).hexdigest()
    with pytest.raises(boundary.IsolatedError):boundary.prepare_payload({'t.py':body},'python_unittest',['t.py'])


@pytest.mark.parametrize('status,code',[('blocked',503),('timeout',504),('cancelled',499),('cleanup_failed',503),('failed',422)])
def test_adapter_does_not_parse_or_fallback_on_container_failure(monkeypatch,status,code,tmp_path):
    monkeypatch.setenv('RIWAQ_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(adapter,'load_config',lambda _:type('Config',(),{'images':{'document_extract':'pin'}})())
    monkeypatch.setattr(adapter,'execute',lambda *a,**k:{'status':status,'stdout':'{}'})
    def forbidden(*a,**k):raise AssertionError('Unexpected host parsing')
    monkeypatch.setattr(p,'extract_document',forbidden)
    with pytest.raises(p.ParserError) as exc:adapter.extract(docx(),{'mode':'private','filename':'a.docx'})
    assert exc.value.status_code==code


def test_offline_build_context_contains_only_selected_sources_and_license(tmp_path):
    script=Path(__file__).parents[1]/'scripts/build_isolated_parser_image.py'
    spec=importlib.util.spec_from_file_location('parser_builder',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    output=tmp_path/'new-context';result=module.prepare(output,'python@sha256:'+'a'*64)
    assert not result['build_executed'] and result['pypdf']=='6.21.4' and not result['network_required']
    assert (output/'licenses/pypdf-LICENSE').is_file()
    dockerfile=(output/'Dockerfile').read_text()
    assert not any(line.startswith(('RUN','ADD')) for line in dockerfile.splitlines())
    assert 'USER 65534:65534' in dockerfile
    assert all(not p.name.endswith(('.pyc','.pyd','.dll','.so')) for p in output.rglob('*'))
    for row in result['files']:assert hashlib.sha256((output/row['path']).read_bytes()).hexdigest()==row['sha256']
    with pytest.raises(ValueError):module.prepare(output,'python@sha256:'+'a'*64)
