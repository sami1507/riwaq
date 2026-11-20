"""The signature look ships its own fonts and must not break the layout it sits on."""
import hashlib
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
FONTS = ROOT / 'frontend' / 'public' / 'fonts'
CSS = (ROOT / 'frontend' / 'src' / 'signature.css').read_text(encoding='utf-8')


def test_bundled_fonts_match_their_record():
    records = json.loads((FONTS / 'display-fonts.json').read_text(encoding='utf-8'))
    assert {r['license'] for r in records} == {'OFL-1.1'}
    for record in records:
        assert (FONTS / record['license_file']).is_file()
        for item in record['files']:
            data = (FONTS / item['file']).read_bytes()
            assert len(data) == item['bytes'] and hashlib.sha256(data).hexdigest() == item['sha256']


def test_fonts_are_local_and_noticed():
    urls = re.findall(r'url\(["\']?([^"\')]+)', CSS)
    assert urls and all(u.startswith(('/fonts/', 'data:image/svg+xml,', '%23')) for u in urls)
    assert 'NotoNaskhArabic' in (ROOT / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')


def test_signature_is_loaded_last_and_keeps_the_drawer():
    main = (ROOT / 'frontend' / 'src' / 'main.tsx').read_text(encoding='utf-8')
    assert main.index('polish.css') < main.index('signature.css')
    sidebar = re.search(r'\n\.sidebar\{(.*?)\n\}', CSS, re.S).group(1)
    assert not re.search(r'(?<![-\w])position:', sidebar)  # the mobile drawer depends on the sidebar's own positioning
    assert 'prefers-reduced-motion' in CSS
