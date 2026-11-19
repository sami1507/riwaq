"""Fresh synthetic OCR evidence using the existing bounded extractor/runtime.

This is a local engine probe, not an isolated-worker or model acceptance claim.
It retains raw output and review flags; never turns OCR confidence into accuracy.
"""
from pathlib import Path
import hashlib
import json
import os
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RUNTIME = Path(os.environ.get('RIWAQ_DOCUMENT_RUNTIME', 'D:/RiwaqRuntime/documents-v1'))
sys.path.insert(0, str(RUNTIME / 'python-packages'))
os.environ['RIWAQ_DOCUMENT_RUNTIME'] = str(RUNTIME)
from backend.document_extract import extract_document
from PIL import Image, ImageDraw, ImageFont

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def run(output):
    output = Path(output).resolve()
    output.relative_to(ROOT / 'reports')
    if output.exists():
        raise ValueError('Fresh report directory required')
    output.mkdir(parents=True)
    font_path = Path('C:/Windows/Fonts/arial.ttf')
    font = ImageFont.truetype(str(font_path), 72)
    image = Image.new('RGB', (1000, 280), 'white')
    draw = ImageDraw.Draw(image)
    draw.text((40, 30), 'TEST RECEIPT', font=ImageFont.truetype(str(font_path), 38), fill='black')
    draw.text((40, 115), 'TOTAL 10.50', font=font, fill='black')
    clear = output / 'receipt-clear.png'; image.save(clear)
    unclear_image = image.copy(); unclear_draw = ImageDraw.Draw(unclear_image)
    # The dot's pixels are intentionally lost. Metadata preserves the original
    # fixture value; that value must not be inferred from the degraded image.
    dot_x = 40 + draw.textlength('TOTAL 10', font=font)
    dot_width = draw.textlength('.', font=font)
    box = [int(dot_x), 115, int(dot_x + dot_width + 1), 215]
    unclear_draw.rectangle(box, fill='white')
    unclear = output / 'receipt-ambiguous.png'; unclear_image.save(unclear)
    cases = [
        {'id': 'clear_receipt', 'source': clear, 'page': 1, 'language': 'eng', 'expected': '10.50'},
        {'id': 'ambiguous_receipt', 'source': unclear, 'page': 1, 'language': 'eng', 'expected': 'manual_review_without_asserting1050'},
        {'id': 'arabic_columns_page12', 'source': ROOT / 'tests/fixtures/documents/arabic-columns-long.pdf', 'page': 12, 'language': 'ara+eng+heb', 'expected': 'RQ-742; correct reading order or explicit review requirement'},
    ]
    report = {'kind': 'actual_local_ocr_and_pdfium', 'synthetic': True, 'python': platform.python_version(),
              'runtime_manifest_sha256': digest(RUNTIME / 'runtime-manifest.json'),
              'font': {'path': str(font_path), 'sha256': digest(font_path), 'license': 'Installed Windows Arial; used locally, not redistributed'},
              'ambiguous_image_operation': {'removed_decimal_pixels': box, 'original_reference': '10.50', 'value_after_degradation': 'not recoverable with certainty'},
              'no_model': True, 'isolated_worker_tested': False, 'cases': []}
    for case in cases:
        folder = output / case['id']; started = time.monotonic()
        result = extract_document(case['source'], case['source'].name, folder, case['page'], case['language'])
        (folder / 'extraction.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        page = result['page']; elements = page['elements']
        text = '\n'.join(item.get('text', '') for item in elements if item.get('indexable', True))
        ocr = [item for item in elements if item.get('quality', {}).get('method') == 'tesseract-tsv']
        report['cases'].append({'id': case['id'], 'source': str(case['source'].relative_to(ROOT)), 'source_sha256': digest(case['source']),
            'expected': case['expected'], 'physical_page': case['page'], 'page_status': page['status'],
            'method': page['method'], 'seconds': round(time.monotonic() - started, 3),
            'text': text, 'ocr_raw': page.get('ocr', {}).get('text_raw'),
            'all_ocr_requires_review': bool(ocr) and all(item['quality'].get('review_required') for item in ocr),
            'low_confidence_words': sum(item['quality'].get('low_confidence_words', 0) for item in ocr),
            'warnings': page.get('warnings', []), 'visual_review': 'pending',
            'quality_pass': None, 'preview': str((folder / 'page.png').relative_to(ROOT))})
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report

if __name__ == '__main__':
    value = run(sys.argv[1])
    print(json.dumps({'cases': [{k:r[k] for k in ('id', 'page_status', 'seconds', 'all_ocr_requires_review', 'low_confidence_words')} for r in value['cases']]}, ensure_ascii=False))
