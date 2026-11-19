"""Shared bounded document parsers; no application, database or network imports.

Production hostile-input entrypoints invoke this module only inside the pinned
Linux parser image. Pure functions remain available for trusted unit fixtures.
"""
from __future__ import annotations
import base64
import hashlib
import io
import json
import re
import sys
import zipfile
from html.parser import HTMLParser
from urllib.parse import urlsplit
from xml.etree import ElementTree

MAX_UPLOAD=8*1024**2
MAX_PUBLIC_INPUT=25*1024**2
MAX_EXTRACTED=2*1024**2
MAX_ARCHIVE_EXPANDED=32*1024**2
MAX_PAGES=200
VERSION='isolated-parsers-v2'
MAX_PAYLOAD=36*1024**2
MAX_RESULT=6*MAX_EXTRACTED+65536

class ParserError(Exception):
    def __init__(self,status_code,detail):
        self.status_code=status_code; self.detail=detail; super().__init__(detail)

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".py", ".js", ".jsx",
    ".ts", ".tsx", ".json", ".jsonl", ".html", ".css", ".scss", ".rs",
    ".go", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php",
    ".sh", ".ps1", ".yaml", ".yml", ".toml", ".sql", ".xml", ".vue",
    ".svelte", ".kt", ".swift", ".r", ".log", ".ini", ".cfg",
}

def safe_filename(filename: str | None) -> str:
    # No supplied path is ever written to disk. Reject paths to avoid misleading citations.
    if not filename or len(filename) > 200 or any(c in filename for c in ("/", "\\", "\x00")):
        raise ParserError(400, "Choose a file with a plain filename (no paths)")
    if any(ord(c) < 32 for c in filename) or filename in {".", ".."}:
        raise ParserError(400, "Invalid filename")
    if filename.lower() == ".env" or filename.lower().startswith(".env."):
        raise ParserError(400, "Secret environment files cannot be indexed")
    return filename

def _docx_pages(data: bytes) -> list[tuple[int | None, str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(e.file_size for e in entries) > MAX_ARCHIVE_EXPANDED:
                raise ParserError(413, "DOCX expanded content exceeds safe limits")
            for entry in entries:
                if entry.flag_bits & 1:
                    raise ParserError(422, "Encrypted DOCX is not supported")
                if entry.file_size > MAX_ARCHIVE_EXPANDED or entry.file_size > max(entry.compress_size, 1) * 250:
                    raise ParserError(413, "DOCX compression ratio exceeds safe limits")
                if entry.filename.startswith(("/", "\\")) or ".." in entry.filename.replace("\\", "/").split("/"):
                    raise ParserError(400, "Unsafe DOCX archive path")
            with archive.open("word/document.xml") as source:
                xml = source.read(MAX_EXTRACTED * 4 + 1)
            if len(xml) > MAX_EXTRACTED * 4:
                raise ParserError(413, "DOCX document XML exceeds safe limits")
            try:
                xml_text = xml.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ParserError(422, "DOCX XML must use UTF-8 encoding") from exc
            if "<!DOCTYPE" in xml_text.upper() or "<!ENTITY" in xml_text.upper():
                raise ParserError(422, "XML entities are not accepted")
            root = ElementTree.fromstring(xml_text)
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            paragraphs = []
            for paragraph in root.iter(f"{ns}p"):
                parts = []
                for node in paragraph.iter():
                    if node.tag == f"{ns}t":
                        parts.append(node.text or "")
                    elif node.tag == f"{ns}tab":
                        parts.append("\t")
                    elif node.tag in {f"{ns}br", f"{ns}cr"}:
                        parts.append("\n")
                paragraphs.append("".join(parts))
            return [(None, "\n".join(paragraphs))]
    except ParserError:
        raise
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, RuntimeError, ValueError) as exc:
        raise ParserError(422, "The DOCX document is invalid or unsupported") from exc

def _pdf_pages(data: bytes) -> list[tuple[int | None, str]]:
    try:
        from pypdf import PdfReader
        from pypdf import filters
        # Recent pypdf versions bound FlateDecode output. Retain a smaller process-wide cap.
        if hasattr(filters, "ZLIB_MAX_OUTPUT_LENGTH"):
            filters.ZLIB_MAX_OUTPUT_LENGTH = min(filters.ZLIB_MAX_OUTPUT_LENGTH, MAX_ARCHIVE_EXPANDED)
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ParserError(422, "Encrypted PDFs must be decrypted before import")
        if len(reader.pages) > MAX_PAGES:
            raise ParserError(413, f"PDF limit is {MAX_PAGES} pages")
        result = []
        extracted_size = 0
        for index, page in enumerate(reader.pages, 1):
            content = page.get_contents()
            if content is not None and len(content.get_data()) > MAX_ARCHIVE_EXPANDED:
                raise ParserError(413, "PDF page content exceeds extraction limits")
            text = page.extract_text() or ""
            extracted_size += len(text)
            if extracted_size > MAX_EXTRACTED:
                raise ParserError(413, "PDF extracted text exceeds the 2 MB limit")
            result.append((index, text))
        if not any(text.strip() for _, text in result):
            raise ParserError(422, "No extractable PDF text. Scanned documents require OCR, which is not configured.")
        return result
    except ParserError:
        raise
    except ImportError as exc:
        raise ParserError(503, "PDF parser is not installed") from exc
    except Exception as exc:
        raise ParserError(422, "PDF extraction failed; the file may be damaged or exceed parser limits") from exc

def extract_document(filename: str, data: bytes) -> list[tuple[int | None, str]]:
    filename = safe_filename(filename)
    if len(data) > MAX_UPLOAD:
        raise ParserError(413, "Upload limit is 8 MB")
    if not data:
        raise ParserError(422, "The file is empty")
    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension == ".pdf":
        pages = _pdf_pages(data)
    elif extension == ".docx":
        pages = _docx_pages(data)
    elif extension in TEXT_EXTENSIONS:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ParserError(422, "Text files must use UTF-8 encoding") from exc
        if "\x00" in text:
            raise ParserError(422, "Binary content is not accepted as text")
        pages = [(None, text)]
    elif extension in {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}:
        raise ParserError(422, "Image OCR is not configured; import a text document instead")
    else:
        raise ParserError(415, "Supported formats: PDF, DOCX, TXT, Markdown, CSV and UTF-8 source code")
    if sum(len(text) for _, text in pages) > MAX_EXTRACTED:
        raise ParserError(413, "Extracted text exceeds the 2 MB limit")
    if not any(text.strip() for _, text in pages):
        raise ParserError(422, "No readable text was found")
    return pages

class LegacyPublicHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skipping = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg", "nav", "footer"}:
            self.skipping += 1
        if not self.skipping and re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n" + "#" * int(tag[1]) + " ")
        elif not self.skipping and tag in {"p", "div", "li", "pre", "br", "tr", "section"}:
            self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg", "nav", "footer"} and self.skipping:
            self.skipping -= 1
        if not self.skipping and tag in {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre", "tr", "section"}:
            self.parts.append("\n")
    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)

class PublicHTML(HTMLParser):
    """Normalize prose while retaining preformatted code exactly."""
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer"}
    START_BLOCK = {"p", "div", "li", "tr", "section", "br"}
    END_BLOCK = {"p", "div", "li", "tr", "section", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._plain, self._pre, self._blocks = [], [], []
        self._hidden, self._pre_depth = [], 0

    def _flush_plain(self):
        value = "\n".join(line.strip() for line in "".join(self._plain).splitlines() if line.strip())
        self._plain.clear()
        if value:
            self._blocks.append(value)

    def _flush_pre(self):
        value = "".join(self._pre).replace("\r\n", "\n").replace("\r", "\n")
        self._pre.clear()
        if not value:
            return
        width = max(3, 1 + max((len(run) for run in re.findall(chr(96) + "+", value)), default=0))
        fence = chr(96) * width
        self._blocks.append(fence + "\n" + value + ("" if value.endswith("\n") else "\n") + fence)

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._hidden.append(tag)
            return
        if self._hidden:
            return
        if tag == "pre":
            if self._pre_depth == 0:
                self._flush_plain()
            self._pre_depth += 1
            return
        if self._pre_depth:
            if tag == "br":
                self._pre.append("\n")
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._plain.append("\n" + "#" * int(tag[1]) + " ")
        elif tag in self.START_BLOCK:
            self._plain.append("\n")
        elif tag in {"td", "th"}:
            self._plain.append("\t")

    def handle_endtag(self, tag):
        if self._hidden:
            if tag == self._hidden[-1]:
                self._hidden.pop()
            return
        if tag == "pre":
            if self._pre_depth:
                self._pre_depth -= 1
                if self._pre_depth == 0:
                    self._flush_pre()
            return
        if not self._pre_depth and tag in self.END_BLOCK:
            self._plain.append("\n")

    def handle_data(self, data):
        if not self._hidden:
            (self._pre if self._pre_depth else self._plain).append(data)

    def get_text(self):
        self.close()
        self._flush_pre()
        self._flush_plain()
        return "\n".join(self._blocks)

def extract_public(body, source, url, content_type, *, extractor=None):
    if extractor not in {None, "legacy-v1", "html-pre-v2"}:
        raise ValueError("Unknown persisted text extractor")
    declared = source.get("content_type", "")
    path = urlsplit(url).path.lower()
    if body.startswith(b"%PDF-") or path.endswith(".pdf"):
        pages = _pdf_pages(body)
    elif path.endswith(".docx"):
        pages = _docx_pages(body)
    else:
        try:
            text = body.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ParserError(422, "Public text must decode as UTF-8; no guessed encoding") from exc
        if "\x00" in text:
            raise ParserError(422, "Binary content cannot be indexed as text")
        if declared == "wikidata-json":
            parsed = json.loads(text)
            if parsed.get("error"):
                raise ParserError(422, "Wikidata API reported an error")
            lines = []
            for entity_id, entity in parsed.get("entities", {}).items():
                lines.append(f"Entity {entity_id}")
                for field in ("labels", "descriptions"):
                    for language in ("en", "ar", "he"):
                        value = entity.get(field, {}).get(language, {}).get("value")
                        if isinstance(value, str):
                            lines.append(f"{field}[{language}]: {value}")
                for property_id, statements in entity.get("claims", {}).items():
                    for statement in statements:
                        datavalue = statement.get("mainsnak", {}).get("datavalue", {})
                        if datavalue.get("type") in {"quantity", "time", "globecoordinate"}:
                            lines.append(f"claim[{property_id}]: {json.dumps(datavalue['value'], ensure_ascii=False, sort_keys=True)}")
            text = "\n".join(lines)
        elif "html" in content_type or declared == "html":
            parser = LegacyPublicHTML() if extractor == "legacy-v1" else PublicHTML()
            parser.feed(text)
            text = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip()) if extractor == "legacy-v1" else parser.get_text()
        pages = [(None, text)]
    if not any(text.strip() for _, text in pages) or sum(len(text) for _, text in pages) > MAX_EXTRACTED:
        raise ParserError(422, "Document has no extractable text or exceeds the 2 MiB extracted-text limit")
    return pages


def validate_metadata(value):
    if not isinstance(value, dict) or value.get('mode') not in {'private', 'public'}:
        raise ParserError(422, 'Invalid extraction mode')
    if value['mode'] == 'private':
        if set(value) != {'mode', 'filename'}: raise ParserError(422, 'Invalid private metadata')
        name = safe_filename(value['filename'])
        if not name.lower().endswith(('.pdf', '.docx')): raise ParserError(415, 'Isolated private parser accepts PDF and DOCX')
    else:
        required = {'mode', 'source', 'url', 'content_type'}
        if not required <= set(value) or set(value) - required - {'extractor'}:
            raise ParserError(422, 'Invalid public metadata')
        if 'extractor' in value and value['extractor'] not in ('legacy-v1', 'html-pre-v2'):
            raise ParserError(422, 'Unknown persisted text extractor')
        if not isinstance(value['source'], dict) or set(value['source']) != {'content_type'}:
            raise ParserError(422, 'Invalid declared source type')
        if not isinstance(value['source']['content_type'], str) or len(value['source']['content_type']) > 80:
            raise ParserError(422, 'Invalid declared source type')
        if not isinstance(value['content_type'], str) or len(value['content_type']) > 256:
            raise ParserError(422, 'Invalid media type')
        url = value['url']
        if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 32 for c in url):
            raise ParserError(422, 'Invalid source URL')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ParserError(422, 'Invalid source URL')
    return value


def main():
    """Fixed image entrypoint: input stdin, bounded UTF-8 JSON result stdout."""
    digest = ''; result = None
    try:
        raw = sys.stdin.buffer.read(MAX_PAYLOAD + 1)
        if len(raw) > MAX_PAYLOAD: raise ParserError(413, 'Parser payload exceeds limit')
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {'body', 'metadata'}:
            raise ParserError(422, 'Invalid parser payload')
        metadata = validate_metadata(data['metadata'])
        body = base64.b64decode(data['body'], validate=True)
        maximum = MAX_UPLOAD if metadata['mode'] == 'private' else MAX_PUBLIC_INPUT
        if not 1 <= len(body) <= maximum: raise ParserError(413, 'Parser input exceeds limit')
        digest = hashlib.sha256(body).hexdigest()
        pages = (extract_document(metadata['filename'], body) if metadata['mode'] == 'private'
                 else extract_public(body, metadata['source'], metadata['url'], metadata['content_type'],
                                     extractor=metadata.get('extractor')))
        if not 1 <= len(pages) <= MAX_PAGES or sum(len(t) for _, t in pages) > MAX_EXTRACTED:
            raise ParserError(413, 'Extracted result exceeds limit')
        result = {'version': VERSION, 'success': True, 'input_sha256': digest, 'pages': pages}
    except ParserError as exc:
        result = {'version': VERSION, 'success': False, 'input_sha256': digest,
                  'error_status': exc.status_code, 'error_detail': exc.detail, 'pages': []}
    except Exception:
        result = {'version': VERSION, 'success': False, 'input_sha256': digest,
                  'error_status': 422, 'error_detail': 'Document parser rejected invalid content', 'pages': []}
    raw = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(raw) > MAX_RESULT:
        raw = json.dumps({'version': VERSION, 'success': False, 'input_sha256': digest,
                          'error_status': 413, 'error_detail': 'Parser result exceeds limit', 'pages': []}).encode()
    sys.stdout.buffer.write(raw); sys.stdout.buffer.flush()
    # The protocol can report a legitimate parse rejection without conflating
    # it with container launch/cleanup failure. The parent checks success.
    return 0
