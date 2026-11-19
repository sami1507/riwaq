# Verified machine-readable codes

The Images & design panel creates QR and Code128 assets through the existing
project-owned visual asset API. It accepts literal text, generates a black/white
PNG inside the pinned `document_page` container, and decodes that exact PNG using
a separate library before saving. An unsuccessful decode produces no asset.
URLs in payloads are data and are never opened. Model output is not involved.

QR accepts 1–512 UTF-8 bytes, including Arabic and leading zeroes. Code128 accepts
1–80 printable ASCII characters. Control characters, unsupported code types and
unbounded scale values are rejected. Fixed scale is 3–6; the UI uses 4. The PNG
is bounded to 2 MiB/four million pixels. The result records original/decoded and
PNG SHA-256 values, actual encoder/decoder versions and container cleanup.
The proof establishes that these PNG bytes decode to these payload bytes. It
does not certify a resized image, physical print, CMYK profile, scene text or
all future camera conditions.

`GET /api/projects/{id}/visual/codes/status` reports the configured tested pin.
`POST /api/projects/{id}/visual/codes` accepts `kind`, `text`, `scale`, and
`request_key`; it returns an existing visual-asset record. A repeated key and
payload returns the same saved asset; a different payload with the same key
returns 409. Failed/interrupted attempts require an explicit new attempt.
Project/session checks run before dispatch, during execution, and before the
atomic request/asset publication. Cancellation waits for container cleanup.

## Runtime and acquisition

The final combined image is
`sha256:83c57a1aa99e26afa3e6f3ab185ad69dac84f78b013a20aa4b7377d1edbf3518`.
It also supports existing document extraction, image-region preparation, media
RGB normalization and visual RGBA normalization. No host installation occurred.
The offline build uses the previously verified document image and exactly:

| Package | Version | License |
|---|---|---|
| [Segno](https://pypi.org/project/segno/1.6.6/) | 1.6.6 | BSD-3-Clause |
| [python-barcode](https://pypi.org/project/python-barcode/0.16.1/) | 0.16.1 | MIT |
| [zxing-cpp](https://pypi.org/project/zxing-cpp/3.1.1/) | 3.1.1 | Apache-2.0 |

Official PyPI metadata and exact wheels, including complete licenses, are kept
in `reports/closure-20261116/visual-codes/acquisition/`. Its immutable ledger
records **1,779,196 bytes**, including metadata, within the approved 16 MiB
reservation. The aggregate at that handoff was 9,843,817,385 bytes; later agents'
acquisitions have their own linked ledgers. A first local wheel selector failed
to recognize the ABI3 wheel; it reused downloaded files and made no duplicate or
failed network transfer. No package installer or package script ran during build.

## Evidence and scope

- `visual-normalize/live-final/report.json`: four independent code decode cases
  on the final image, alongside ten actual visual normalization checks.
- `visual-normalize/code-api-final/report.json`: authenticated API creation,
  download-hash verification, replay/conflict behavior, invalid input, project
  revocation and controlled rejection after actual worker execution.
- `visual-codes/ui/2026-11-16T17-39-45.152Z/report.json`: actual Chrome controls,
  isolated upload, English/Arabic QR, Code128 `001`, downloads, invalid input,
  explicit retry, Arabic mobile view, six persisted assets and source hashes.
- `visual-normalize/affected-final.xml`: 157 passing affected checks and one
  separately reported shared packaged-document test failure. It must
  not be represented as an all-green run.
- `visual-codes/worker-results.json` and earlier UI/API reports retain their
  earlier image pins and are historical evidence.

This completes the bounded QR/Code128 part of VIS-12 / GAP-019. Scene-text
realism and physical print readiness remain separate requirements. Docker
shares its kernel with the host; this is not a separate-VM isolation claim.

```powershell
.venv/Scripts/python.exe scripts/closure_visual_normalize.py --config reports/closure-20261116/visual-normalize/isolated-workers.json --output reports/closure-new/visual-normalize
$env:RIWAQ_TEST_VISUAL_CODES_LIVE='1'
.venv/Scripts/python.exe -m pytest tests/test_visual_codes_live.py -q
```
