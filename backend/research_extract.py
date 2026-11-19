"""Public extraction through the shared OS-isolated Linux parser image.

No host parsing fallback. Network fetch remains in the authorized parent; only
bounded bytes and content-type metadata enter the network-disabled container.
"""
from __future__ import annotations
import sys
from backend.packaged_workers import bundled
import threading
import time
from backend.isolated_parsers import VERSION, MAX_PUBLIC_INPUT as MAX_INPUT, MAX_EXTRACTED as MAX_CHARACTERS, MAX_PAGES, MAX_RESULT
MAX_METADATA=16384
SECONDS=45
CPU_SECONDS=45
MEMORY=768*1024**2
_SLOTS=threading.BoundedSemaphore(1)

def _types():
    # Lazy imports keep the worker startup and socket guard ahead of the existing
    # extractor's HTTP/database module imports. Those modules are never invoked.
    from backend.knowledge_fetch import FetchError, FetchCancelled
    return FetchError, FetchCancelled

class ExtractedPages(list):
    """List-compatible result; optional supervisor evidence for an audit report."""
    def __init__(self, pages, resources):
        super().__init__(pages)
        self.resources = resources
        self.extractor_version = VERSION

class _Stop:
    def __init__(self, original): self.original = original
    def is_set(self):
        return self.original is not None and (self.original.is_set() or time.monotonic() >= getattr(self.original, "deadline", float("inf")))

def _pages(value):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_PAGES:
        raise ValueError("Invalid page collection")
    output, total = [], 0
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("Invalid page")
        page, text = item
        if page is not None and (type(page) is not int or not 1 <= page <= MAX_PAGES):
            raise ValueError("Invalid page index")
        if not isinstance(text, str):
            raise ValueError("Invalid extracted text")
        total += len(text)
        if total > MAX_CHARACTERS or "\x00" in text:
            raise ValueError("Extracted text limit exceeded")
        text.encode("utf-8", errors="strict")
        output.append((page, text))
    if not any(text.strip() for _, text in output):
        raise ValueError("No extractable text")
    return output

def _metadata(source, url, content_type):
    from backend.knowledge_fetch import normalize_url
    if not isinstance(source, dict) or not isinstance(content_type, str) or len(content_type) > 256:
        raise ValueError("Invalid extraction metadata")
    declared = source.get("content_type", "")
    if not isinstance(declared, str) or len(declared) > 80:
        raise ValueError("Invalid declared content type")
    # extract_public only reads this field. Do not pass unrelated source rights,
    # project data, API keys, filesystem paths or settings into the worker.
    return {"source": {"content_type": declared}, "url": normalize_url(url), "content_type": content_type}

def extract_bounded(body, source, url, content_type, stop=None, *, extractor=None):
    FetchError, FetchCancelled = _types()
    if getattr(sys, 'frozen', False) and not bundled('document_extract',sys):
        raise FetchError('packaged_parser_unavailable: no configured extraction worker')
    cancelled = _Stop(stop)
    if cancelled.is_set(): raise FetchCancelled('Extraction cancelled before worker start')
    if not isinstance(body, bytes) or not 1 <= len(body) <= MAX_INPUT:
        raise FetchError('Public extraction input must be 1 byte to 25 MiB')
    try:
        metadata = _metadata(source, url, content_type)
        if extractor is not None:
            if extractor not in ('legacy-v1', 'html-pre-v2'):
                raise ValueError('Unknown persisted text extractor')
            metadata['extractor'] = extractor
    except (ValueError, TypeError, UnicodeError):
        raise FetchError('Public extraction metadata is invalid') from None
    if not _SLOTS.acquire(blocking=False):
        raise FetchError('Public extraction worker is busy; no additional parser was started')
    try:
        from backend.isolated_extract import extract
        from backend.isolated_parsers import ParserError
        try: pages, resources = extract(body, {'mode':'public', **metadata}, cancelled)
        except ParserError as exc:
            if exc.status_code == 499: raise FetchCancelled(exc.detail) from None
            raise FetchError(exc.detail) from None
        if cancelled.is_set(): raise FetchCancelled('Public extraction cancelled before accepting result')
        try: pages = _pages(pages)
        except ValueError: raise FetchError('Public extraction returned no valid bounded text result') from None
        return ExtractedPages(pages, resources)
    finally: _SLOTS.release()
