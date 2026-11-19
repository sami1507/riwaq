"""Legacy PDF/DOCX extraction through the fixed OS-isolated parser image.

Absence or failure of the image is an explicit error, never host fallback.
"""
from __future__ import annotations
import sys
from backend.packaged_workers import bundled
import threading
from pathlib import Path
from backend.isolated_parsers import VERSION
_SLOTS=threading.BoundedSemaphore(1)


def extract_bounded(filename, body, stop=None):
    from fastapi import HTTPException
    from backend.knowledge import MAX_UPLOAD, safe_filename
    from backend.isolated_extract import extract
    from backend.isolated_parsers import ParserError
    safe_filename(filename)
    if not isinstance(body, bytes) or not body: raise HTTPException(422, 'The file is empty')
    if len(body)>MAX_UPLOAD: raise HTTPException(413, 'Upload limit is 8 MB')
    if Path(filename).suffix.lower() not in {'.pdf','.docx'}:
        raise HTTPException(415, 'This fixed worker accepts PDF and DOCX only')
    if getattr(sys,'frozen',False) and not bundled('document_extract',sys): raise HTTPException(503, 'The packaged document extraction worker is unavailable')
    stop=stop or threading.Event()
    if stop.is_set(): raise HTTPException(499, 'Document extraction cancelled')
    if not _SLOTS.acquire(blocking=False): raise HTTPException(503, 'Document extraction worker is busy')
    try:
        try: pages, _ = extract(body, {'mode':'private','filename':filename}, stop)
        except ParserError as exc: raise HTTPException(exc.status_code, exc.detail) from None
        if stop.is_set(): raise HTTPException(499, 'Document extraction cancelled before accepting result')
        return pages
    finally: _SLOTS.release()
