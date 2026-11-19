"""Synthetic file/OCR contracts. These tests do not certify native Office layout."""
import hashlib
import json
from io import BytesIO
from pathlib import Path
import os
import sys
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import pytest
from backend import document_extract as extract, skill_artifacts

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/ai_core_visual'

def test_arabic_word_contains_editable_table_and_mixed_text_not_an_image(tmp_path):
    reference = json.loads((FIXTURES / 'word-reference.json').read_text(encoding='utf-8'))
    output = skill_artifacts.build_artifact('docx', reference['payload'])
    with ZipFile(BytesIO(output.content)) as package:
        assert not any(name.startswith('word/media/') for name in package.namelist())
        document = ET.fromstring(package.read('word/document.xml'))
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    table = document.findall('.//w:tbl', ns)
    assert len(table) == 1
    rows = [[''.join(cell.itertext()) for cell in row.findall('w:tc', ns)] for row in table[0].findall('w:tr', ns)]
    assert rows == [reference['payload']['tables'][0]['headers'], *reference['payload']['tables'][0]['rows']]
    text = ''.join(node.text or '' for node in document.findall('.//w:t', ns))
    assert 'שלום' in text and 'English' in text and '001' in text and '-5' in text
    assert document.findall('.//w:bidi', ns)
    assert output.verification['office_rendered'] is False
    path = tmp_path / 'generated.docx'; path.write_bytes(output.content)
    readback = extract.extract_document(path, path.name, tmp_path / 'readback', 1)
    assert readback['metadata']['page_units'] == 'logical_document_body'
    assert readback['warnings'] and 'pagination' in readback['warnings'][0]

def test_arabic_word_cell_can_be_edited_and_reopened_without_flattening(tmp_path):
    from docx import Document
    reference = json.loads((FIXTURES / 'word-reference.json').read_text(encoding='utf-8'))
    generated = skill_artifacts.build_artifact('docx', reference['payload']).content
    original = tmp_path / 'original.docx'; original.write_bytes(generated)
    document = Document(original)
    before = [paragraph.text for paragraph in document.paragraphs]
    document.tables[0].cell(3, 2).text = '25'
    edited = tmp_path / 'edited.docx'; document.save(edited)
    reopened = Document(edited)
    assert reopened.tables[0].cell(3, 2).text == '25'
    assert reopened.tables[0].cell(2, 2).text == '0'
    assert reopened.tables[0].cell(4, 2).text == 'غير متاحة'
    assert [paragraph.text for paragraph in reopened.paragraphs] == before
    assert original.read_bytes() == generated
    assert len(reopened.tables) == 1 and not reopened.inline_shapes

@pytest.fixture
def actual_ocr(monkeypatch):
    runtime = Path(os.environ.get('RIWAQ_DOCUMENT_RUNTIME', 'D:/RiwaqRuntime/documents-v1'))
    if not (runtime / 'runtime-manifest.json').is_file():
        pytest.skip('Existing local OCR runtime required; no installation or download')
    monkeypatch.setenv('RIWAQ_DOCUMENT_RUNTIME', str(runtime))
    monkeypatch.syspath_prepend(str(runtime / 'python-packages'))
    return runtime

@pytest.mark.parametrize('name,expected', [('receipt-clear.png', '10.50'), ('receipt-ambiguous.png', None)])
def test_real_receipt_ocr_preserves_decimal_or_review_required_raw_text(tmp_path, actual_ocr, name, expected):
    refs = json.loads((FIXTURES / 'receipt-reference.json').read_text(encoding='utf-8'))
    source = FIXTURES / name
    assert hashlib.sha256(source.read_bytes()).hexdigest() == refs['fixtures'][name]['sha256']
    page = extract.extract_document(source, source.name, tmp_path / name, 1, 'eng')['page']
    raw = page['ocr']['text_raw']
    if expected:
        assert expected in raw
    else:
        # Loss of a decimal pixel is not a supported numeric normalization.
        # The recognizer's raw string is retained, never a verified amount.
        assert '10 50' in raw and 'amount' not in page
    ocr = [element for element in page['elements'] if element['quality']['method'] == 'tesseract-tsv']
    assert ocr and all(element['quality']['review_required'] for element in ocr)
    assert all(element['quality']['assessment'] == 'engine_signal_only' for element in ocr)
    assert any('manual review' in warning for warning in page['warnings'])
    assert page['ocr']['runtime_manifest_sha256'] and page['ocr']['tsv_sha256']

def test_real_arabic_columns_order_is_explicitly_unverified(tmp_path, actual_ocr):
    source = ROOT / 'tests/fixtures/documents/arabic-columns-long.pdf'
    page = extract.extract_document(source, source.name, tmp_path / 'columns', 12)['page']
    assert page['number'] == 12 and page['status'] == 'partial'
    native = [element for element in page['elements'] if element['quality']['method'] == 'pdfium-native']
    assert native and all(element['indexable'] is False for element in native)
    assert all(element['quality']['reading_order_verified'] is False for element in native)
    assert any('RTL glyph order' in warning for warning in page['warnings'])
    assert all(element['quality']['review_required'] for element in page['elements'] if element['quality']['method'] == 'tesseract-tsv')
