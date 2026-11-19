# Isolated image-upload normalization

`POST /api/media/vision` now normalizes uploads in the existing fixed
`document_page` Docker worker using operation `normalize`. The application
process never decodes untrusted image pixels and has no host fallback. Missing
configuration yields 503. Input limits remain 3 MiB, four million pixels and
single-frame JPEG/PNG; malformed input, unsupported formats and pixel-limit
errors remain distinct. The worker applies EXIF orientation, converts to RGB,
clears metadata and emits a plain PNG without resizing.

The host checks the bounded JSON envelope, source/output SHA-256, strict PNG
chunk structure/CRC and dimensions. This structural check is not a substitute
for the container boundary. A four-million-pixel JPEG can expand to nearly
12 MB PNG, so normalization has a separate 12,500,000-byte image limit and
16,732,204-byte envelope. Existing document/OCR envelope limits are unchanged.
The worker receives no host mounts or network and uses a 512 MiB/30-second
budget. It shares the Docker kernel; it is not a separate-VM security claim.

Session authorization is rechecked before dispatch, during worker execution,
before model invocation and before return. Cancellation/disconnection waits for
worker cleanup before releasing the media slot. No image or model answer is
returned after revocation. Packaged capabilities must be derived from actual
bundled workers and dependencies; an environment flag cannot assert readiness.

`media_status` requires a matching model digest **and** matching
`normalization_image_id` / `normalization_protocol` in its test evidence.
Old model-only reports do not certify a new normalization worker. The required
protocol is `isolated-image-normalize-v1`.

## Evidence, 16 November 2026

All evidence is under `reports/closure-20261116/image-normalize/`:

- `before.json`: application-process Pillow decoder before the change.
- `build-manifest-v2.json`: source hashes; offline layer over the existing image
  `sha256:19baa22d3d8ad8657a870bea38af4d339f6b66d21a043350c323f5fc4d0e8731`.
- Accepted image: `sha256:3af2ee5223f18fe18139795ec01580ad5b8ffa45efcaad5a81a1f15fa1a8696a`.
- `live-v2/report.json`: 9 passing real Docker/API cases, including installed
  local Ollama on known synthetic text, EXIF orientation/metadata removal,
  malformed/GIF/pixel-limit rejection, cancellation cleanup and session
  revocation. The high-detail 2000×2000 fixture is 2,671,080-byte JPEG and
  11,999,553-byte normalized PNG: no resolution reduction.
- `affected-final.xml`: 136 passing image/document/report/permission checks.
  `media-readiness-final.xml`: 15 passing checks including the packaged-worker
  environment-flag rejection. The pre-fix envelope-bound regression remains in
  `contracts-host.xml`; it was resolved with a dedicated normalization bound,
  preserving the existing document limit.
- `source-provenance-final.json`: the reviewed application sources.

To repeat with a fresh synthetic-data directory:

```powershell
.venv/Scripts/python.exe scripts/closure_image_normalize.py --config reports/closure-20261116/image-normalize/isolated-workers.json --output reports/closure-new/image-normalize --model
```

This is one synthetic local model example, not a general image/OCR quality
benchmark. The application worker config was not edited by this slice; activation
must use the reviewed pin and preserve the prior config. No dependencies were
downloaded. Pillow 13.0.0 behavior was checked against its official
[EXIF transpose](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html#PIL.ImageOps.exif_transpose)
and [image metadata](https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.info)
documentation.

## Visual workspace variant and combined image

The visual workspace upload route also uses this container now. It preserves
its existing input contract: single-frame PNG/JPEG/WebP, 6 MiB and four million
pixels. `variant: visual` applies EXIF orientation and preserves alpha in RGBA
for PNG/WebP; JPEG becomes RGB. All normalized assets are stored as plain PNG
with a `.png` filename. The UI describes this conversion. The original input
SHA-256 remains in provenance. No native Pillow decoder runs on the application
process and there is no host fallback.

RGBA has a separate 16,500,000-byte PNG bound and matching bounded envelope.
Visual storage accepts that bounded output while its project/global quotas are
unchanged. This avoids silently rejecting a supported detailed JPEG/WebP merely
because canonical PNG is larger. The actual 4MP visual JPEG fixture is
4,691,644 input bytes and 11,991,414 output bytes. Media retains its original
3 MiB/RGB input and output bounds. Default PNG protocol validation still rejects
alpha; only the explicit visual variant admits it.

Upload captures trusted project/session authority before dispatch, checks it
while awaiting the worker and again before saving. Cancellation/disconnection
sets the stop event and waits for cleanup before releasing the decoder slot.

The final combined pin is
`sha256:83c57a1aa99e26afa3e6f3ab185ad69dac84f78b013a20aa4b7377d1edbf3518`.
`../visual-normalize/live-final/report.json` records ten actual Docker/API cases
including alpha, WebP, metadata/orientation, size limits, cancel and revocation;
four QR/Code128 decode cases prove those existing operations remain available.
`live-combined/report.json` records all nine media checks including actual local
Ollama on the same image. Historical pin `3af2...` reports above remain scoped
to that earlier image. `tests/test_visual_normalize.py` additionally verifies
that route cancellation awaits cleanup and leaves no stored asset.
