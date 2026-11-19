"""Explicit, bounded project snapshots. Imports create a new project atomically.

Snapshot text is untrusted data; importing never grants filesystem permissions,
restores secrets, changes settings, runs tools, or contacts a provider.
"""
from __future__ import annotations

from datetime import datetime
import json
import hashlib
import math
import unicodedata
import re
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator, model_validator

from backend.db import event, get_db, now, uid
from backend import facts,context_lineage
from backend.knowledge import require_project, safe_filename

router = APIRouter()
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_SNAPSHOT_ENVELOPE_OVERHEAD = 65536
DOCUMENT_SNAPSHOT_NOTICE = "Lightweight snapshots exclude original documents and page previews. Imported document citations become text excerpts; re-upload originals to open them."
FILE_SCOPE_RESET_NOTICE = "Original-document file/page selections were cleared and file retrieval disabled. Choose sources again in the restored project."
ACCESS_OMISSION_NOTICE = "Access-restricted sources and dependent records are omitted. Unbound event summaries are excluded to protect copied source metadata."


class SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def plain_text(cls, value):
        if isinstance(value, str) and any(ord(c) < 32 and c not in "\t\n\r" for c in value):
            raise ValueError("Control characters are not accepted in snapshot text")
        return value

    @field_validator("*", mode="after")
    @classmethod
    def timestamps(cls, value, info):
        if value is not None and info.field_name in {"created_at", "updated_at", "published_at", "modified_at", "fetched_at", "message_created_at", "captured_at", "implementation_attested_at"}:
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                raise ValueError("Timestamp must be ISO 8601")
        return value


class ProjectData(SnapshotModel):
    name: str = Field(min_length=1, max_length=120)
    instructions: str = Field(max_length=12000)
    created_at: str = Field(max_length=64)


class ChunkData(SnapshotModel):
    id: str = Field(min_length=1, max_length=80)
    page: int | None = Field(default=None, ge=1, le=200)
    line_start: int = Field(ge=1, le=10000000)
    line_end: int = Field(ge=1, le=10000000)
    text: str = Field(max_length=20000)

    @model_validator(mode="after")
    def valid_lines(self):
        if self.line_end < self.line_start:
            raise ValueError("Source line range is reversed")
        return self


class DocumentLocatorData(SnapshotModel):
    page: int = Field(ge=1, le=200)
    lines: list[int] | None = Field(default=None, min_length=2, max_length=2)
    paragraph: int | None = Field(default=None, ge=1, le=1000000)
    table: int | None = Field(default=None, ge=1, le=1000000)
    sheet: str | None = Field(default=None, max_length=1000)
    cells: list[str] | None = Field(default=None, max_length=20000)
    slide: int | None = Field(default=None, ge=1, le=200)
    shape: int | None = Field(default=None, ge=1, le=1000000)
    pdf_char_start: int | None = Field(default=None, ge=0, le=10000000)
    pdf_char_end: int | None = Field(default=None, ge=0, le=10000000)
    pdf_image: int | None = Field(default=None, ge=1, le=1000000)
    ocr_block: int | None = Field(default=None, ge=0, le=1000000)
    ocr_paragraph: int | None = Field(default=None, ge=0, le=1000000)
    ocr_line: int | None = Field(default=None, ge=0, le=1000000)

    @model_validator(mode="after")
    def bounded_location(self):
        if self.lines is not None and (any(type(n) is not int or not 1 <= n <= 10000000 for n in self.lines) or self.lines[1] < self.lines[0]):
            raise ValueError("Invalid document line range")
        if self.pdf_char_start is not None and self.pdf_char_end is not None and self.pdf_char_end < self.pdf_char_start:
            raise ValueError("Invalid document character range")
        if self.cells is not None and any(not re.fullmatch(r"(?:[A-Z]{1,3}[1-9][0-9]{0,6}|R[1-9][0-9]{0,6}C[1-9][0-9]{0,4})", cell) for cell in self.cells):
            raise ValueError("Invalid spreadsheet cell locator")
        return self


class DocumentCitationData(SnapshotModel):
    """Historical source location only; excluded from imported live citations."""
    file_id: str = Field(min_length=1, max_length=80)
    version: int = Field(ge=1, le=1000000)
    element_id: str = Field(min_length=1, max_length=80)
    revision: int = Field(ge=1, le=1000000)
    page: int = Field(ge=1, le=200)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    locator: DocumentLocatorData
    mime: str = Field(min_length=1, max_length=200)
    method: str | None = Field(default=None,max_length=100,exclude_if=lambda value:value is None)
    review_required: bool | None = Field(default=None,exclude_if=lambda value:value is None)

    @model_validator(mode="after")
    def consistent_location(self):
        if self.locator.page != self.page:
            raise ValueError("Document page and locator disagree")
        if self.bbox is not None and (any(not math.isfinite(n) or not 0 <= n <= 1 for n in self.bbox) or self.bbox[2] <= self.bbox[0] or self.bbox[3] <= self.bbox[1]):
            raise ValueError("Invalid normalized document box")
        return self


class CitationData(ChunkData):
    document: DocumentCitationData | None = Field(default=None, exclude_if=lambda value: value is None)
    scope: Literal["private"] = "private"
    file_id: str = Field(min_length=1, max_length=80)
    filename: str = Field(min_length=1, max_length=200)
    score: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def document_matches_file(self):
        if self.document is not None and self.document.file_id != self.file_id:
            raise ValueError("Document citation must refer to the cited private file")
        return self


def provenance_url(value):
    """A stored link is inert provenance, never a fetch authorization."""
    if not value:
        return value
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or "\\" in value or any(c.isspace() for c in value):
            raise ValueError("Source provenance requires an HTTPS URL without credentials")
        parsed.port
    except ValueError as exc:
        raise ValueError("Source provenance requires a valid HTTPS URL") from exc
    return value


class CitationLicense(SnapshotModel):
    name: str = Field(max_length=300)
    url: str = Field(default="", max_length=4096)
    storage_allowed: StrictBool = False
    redistribution_allowed: StrictBool = False
    training_allowed: Literal[False] = False
    attribution: str = Field(default="", max_length=8000)
    reviewed_at: str | None = Field(default=None, max_length=64)
    evidence: str = Field(default="", max_length=8000)
    sha256: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")

    _url = field_validator("url")(provenance_url)


class CitationChunking(SnapshotModel):
    extractor: Literal["legacy-v1", "html-pre-v2"] = "legacy-v1"
    algorithm: Literal["lines-v1", "heading-lines-v2"]
    max_characters: int = Field(ge=400, le=4000)
    max_lines: int = Field(ge=8, le=80)
    overlap_lines: int = Field(ge=0, le=10)
    respect_headings: StrictBool


class PublicCitationData(ChunkData):
    """Copied excerpt and original public identifiers, not an imported index row.

    IDs retain their meaning as provenance. They are never resolved against the
    local corpus or private files, and the import confers no source permissions.
    """
    scope: Literal["public"]
    filename: str = Field(min_length=1, max_length=400)
    title: str = Field(min_length=1, max_length=400)
    source_id: str = Field(min_length=1, max_length=80)
    document_id: str = Field(min_length=1, max_length=80)
    version_id: str = Field(min_length=1, max_length=80)
    version: str = Field(max_length=200)
    revision: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    url: str = Field(min_length=1, max_length=4096)
    language: str = Field(max_length=30)
    languages: list[str] = Field(default_factory=list, max_length=10)
    domain: str = Field(max_length=100)
    license: CitationLicense
    published_at: str | None = Field(default=None, max_length=64)
    modified_at: str | None = Field(default=None, max_length=64)
    fetched_at: str | None = Field(default=None, max_length=64)
    checked_at: str | None = Field(default=None, max_length=64)
    event_at: str | None = Field(default=None, max_length=64)
    publisher: str | None = Field(default=None, max_length=300)
    heading: str | None = Field(default=None, max_length=20000)
    chunking: CitationChunking | None = None
    score: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    retrieval_method: Literal["bm25", "hybrid-rrf"] | None = None
    retrieval_notice: str | None = Field(default=None, max_length=2000)
    embedding_model: str | None = Field(default=None, max_length=200)
    embedding_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    provenance: Literal["stored_excerpt"] = "stored_excerpt"

    _url = field_validator("url")(provenance_url)

    @field_validator("languages")
    @classmethod
    def language_tags(cls, value):
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,30}", item) for item in value):
            raise ValueError("Invalid citation language tag")
        return value


from backend.reasoning import RunReceipt, RetrievalOptions, read_receipt, save_receipt

class ContextFactData(SnapshotModel):
    kind:Literal['fact']
    id:str=Field(min_length=1,max_length=80)
    version:int=Field(ge=1,le=100)
    payload_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
class ContextChunkData(SnapshotModel):
    kind:Literal['chunk']
    source_kind:Literal['private_chunk','public_chunk']
    id:str=Field(min_length=1,max_length=80)
    source_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    text_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    version_id:str|None=Field(default=None,min_length=1,max_length=80)
    file_id:str|None=Field(default=None,min_length=1,max_length=80)
class UnboundContextData(SnapshotModel):
    kind:Literal['unbound_history']

class ContextProjectMemoryData(SnapshotModel):
    kind:Literal['project_memory']
    id:str=Field(min_length=1,max_length=80)
    version:int=Field(ge=1,le=1000000000)
    content_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')

class ContextPersonalMemoryData(ContextProjectMemoryData):
    kind:Literal['personal_memory']
    consent_epoch:int=Field(ge=1,le=1000000000)

class ContextLearningReleaseData(SnapshotModel):
    kind:Literal['learning_release']
    id:str=Field(min_length=1,max_length=80)
    version:int=Field(ge=1,le=1000000000)
    candidate_version:int=Field(ge=1,le=1000000000)
    target_version:str=Field(min_length=1,max_length=200)
    rule_hash:str=Field(pattern=r'^[0-9a-f]{64}$')

class ContextLearningFeedbackData(SnapshotModel):
    kind:Literal['learning_feedback']
    id:str=Field(min_length=1,max_length=80)
    version:int=Field(ge=1,le=1000000000)
    target_version:str=Field(min_length=1,max_length=200)
    conversation_id:str|None=Field(default=None,min_length=1,max_length=80)

class MessageData(SnapshotModel):
    retrieval_options:RetrievalOptions|None=None
    context_dependencies:list[ContextFactData|ContextChunkData|ContextProjectMemoryData|ContextPersonalMemoryData|ContextLearningReleaseData|ContextLearningFeedbackData|UnboundContextData]|None=Field(default=None,max_length=64)
    role: Literal["user", "assistant"]
    content: str = Field(max_length=1000000)
    status: Literal["complete", "streaming", "cancelled", "error", "interrupted"]
    model: str | None = Field(default=None, max_length=100)
    created_at: str = Field(max_length=64)
    citations: list[CitationData | PublicCitationData] = Field(default_factory=list, max_length=10)
    run: RunReceipt | None = None


class ConversationData(SnapshotModel):
    title: str = Field(max_length=120)
    created_at: str = Field(max_length=64)
    messages: list[MessageData] = Field(max_length=2000)


class MemoryData(SnapshotModel):
    content: str = Field(min_length=1, max_length=2000)
    created_at: str = Field(max_length=64)


class EventData(SnapshotModel):
    kind: str = Field(min_length=1, max_length=80)
    summary: str = Field(max_length=4000)
    created_at: str = Field(max_length=64)


class FileData(SnapshotModel):
    id: str = Field(min_length=1, max_length=80)
    filename: str = Field(min_length=1, max_length=200)
    byte_size: int = Field(ge=0, le=8 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=0, le=200)
    created_at: str = Field(max_length=64)
    chunks: list[ChunkData] = Field(max_length=4000)

    @field_validator("filename")
    @classmethod
    def filename_only(cls, value):
        try:
            return safe_filename(value)
        except HTTPException as exc:
            raise ValueError(str(exc.detail)) from exc

    @model_validator(mode="after")
    def pages_within_file(self):
        for chunk in self.chunks:
            if chunk.page is not None and chunk.page > self.page_count:
                raise ValueError("Chunk page exceeds the file page count")
        return self


class VersionData(SnapshotModel):
    version: int = Field(ge=1, le=500)
    content: str = Field(max_length=200000)
    created_at: str = Field(max_length=64)


class StudioProvenanceData(SnapshotModel):
    origin: Literal['local_message','imported_snapshot']
    message_id: str | None = Field(default=None,min_length=1,max_length=80)
    conversation_id: str | None = Field(default=None,min_length=1,max_length=80)
    model: str | None = Field(default=None,max_length=100)
    message_created_at: str = Field(max_length=64)
    answer_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    excerpt: str = Field(max_length=20000)
    citations: list[CitationData | PublicCitationData] = Field(default_factory=list,max_length=10)
    captured_at: str = Field(max_length=64)

class GlossaryPayloadData(SnapshotModel):
    term: str = Field(min_length=1,max_length=200)
    term_ar: str = Field(default='',max_length=200)
    definition: str = Field(min_length=1,max_length=12000)
    language: Literal['ar','en','mixed'] = 'mixed'
    example: str = Field(default='',max_length=4000)
    source_label: str = Field(default='',max_length=500)
    provenance: StudioProvenanceData | None = None
    @field_validator('term','definition')
    @classmethod
    def nonblank(cls,value):
        if not value.strip(): raise ValueError('Glossary text cannot be blank')
        return value

class DecisionPayloadData(SnapshotModel):
    title: str = Field(min_length=1,max_length=200)
    decision: str = Field(min_length=1,max_length=12000)
    rationale: str = Field(default='',max_length=12000)
    alternatives: str = Field(default='',max_length=12000)
    status: Literal['proposal','adopted','implemented'] = 'proposal'
    implementation_evidence: str = Field(default='',max_length=4000)
    implementation_attested_at: str | None = Field(default=None,max_length=64)
    provenance: StudioProvenanceData | None = None
    @field_validator('title','decision')
    @classmethod
    def nonblank(cls,value):
        if not value.strip(): raise ValueError('Decision text cannot be blank')
        return value
    @model_validator(mode='after')
    def attestation(self):
        if self.status=='implemented':
            if not self.implementation_evidence.strip() or not self.implementation_attested_at: raise ValueError('Implemented history requires recorded evidence and timestamp')
        elif self.implementation_evidence or self.implementation_attested_at is not None: raise ValueError('Only implemented history can contain implementation attestation')
        return self

class StudioVersionData(SnapshotModel):
    version: int = Field(ge=1,le=500)
    payload: GlossaryPayloadData | DecisionPayloadData
    created_at: str = Field(max_length=64)

class StudioEntryData(SnapshotModel):
    kind: Literal['glossary','decision']
    payload: GlossaryPayloadData | DecisionPayloadData
    version: int = Field(ge=1,le=500)
    created_at: str = Field(max_length=64)
    updated_at: str = Field(max_length=64)
    versions: list[StudioVersionData] = Field(min_length=1,max_length=500)
    @model_validator(mode='after')
    def complete_history(self):
        expected=GlossaryPayloadData if self.kind=='glossary' else DecisionPayloadData
        if not isinstance(self.payload,expected) or any(not isinstance(v.payload,expected) for v in self.versions): raise ValueError('Studio kind and payload disagree')
        if [v.version for v in self.versions]!=list(range(1,self.version+1)): raise ValueError('Studio history must contain every version once, in order')
        if self.versions[-1].payload.model_dump()!=self.payload.model_dump(): raise ValueError('Current studio payload must equal latest history')
        return self

class ArtifactStudioSourceData(SnapshotModel):
    provenance: StudioProvenanceData
    template: Literal['note','brief','checklist']
    initial_content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

class ArtifactData(SnapshotModel):
    studio_source: ArtifactStudioSourceData | None = None
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=200000)
    version: int = Field(ge=1, le=500)
    created_at: str = Field(max_length=64)
    updated_at: str = Field(max_length=64)
    versions: list[VersionData] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def history_matches(self):
        if [v.version for v in self.versions] != list(range(1, self.version + 1)):
            raise ValueError("Artifact history must contain each version once, in order")
        if self.versions[-1].content != self.content:
            raise ValueError("Current artifact must match its latest version")
        if self.studio_source and hashlib.sha256(self.versions[0].content.encode('utf-8')).hexdigest()!=self.studio_source.initial_content_sha256:
            raise ValueError('Artifact origin must match creation version')
        return self


class FactEvidenceData(facts.Evidence):
    chunk_id:str=Field(min_length=1,max_length=80)
    source_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    chunk_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    quote_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    quote_start:int=Field(ge=0,le=20000)
    quote_end:int=Field(gt=0,le=20000)
    file_id:str|None=Field(default=None,min_length=1,max_length=80)
    source_id:str|None=Field(default=None,min_length=1,max_length=80)
    document_id:str|None=Field(default=None,min_length=1,max_length=80)
    version_id:str|None=Field(default=None,min_length=1,max_length=80)
    @model_validator(mode='after')
    def span(self):
        if self.quote_end<=self.quote_start:raise ValueError('Reversed fact evidence span')
        return self
class FactPayloadData(facts.Payload):
    evidence:list[FactEvidenceData]=Field(default_factory=list,max_length=4)
    reviewed_at:str|None=Field(default=None,max_length=64)
    @model_validator(mode='after')
    def review_dates(self):
        states=[(self.review_state,self.reviewed_at)]
        if self.archived_review:states.append((self.archived_review.review_state,self.archived_review.reviewed_at))
        for state,stamp in states:
            if (state=='user_reviewed') != (stamp is not None):raise ValueError('Review state/timestamp mismatch')
            if stamp is not None:datetime.fromisoformat(stamp.replace('Z','+00:00'))
        return self
class FactVersionData(SnapshotModel):
    version:int=Field(ge=1,le=100)
    payload:FactPayloadData
    created_at:str=Field(max_length=64)
class FactEntryData(SnapshotModel):
    id:str=Field(min_length=1,max_length=80)
    version:int=Field(ge=1,le=100)
    payload:FactPayloadData
    created_at:str=Field(max_length=64)
    updated_at:str=Field(max_length=64)
    versions:list[FactVersionData]=Field(min_length=1,max_length=100)
    @model_validator(mode='after')
    def complete_history(self):
        if [v.version for v in self.versions]!=list(range(1,self.version+1)):raise ValueError('Fact history must contain all revisions in order')
        if self.versions[-1].payload.model_dump()!=self.payload.model_dump():raise ValueError('Current fact differs from latest revision')
        return self

class Snapshot(SnapshotModel):
    warnings: list[Literal[DOCUMENT_SNAPSHOT_NOTICE, FILE_SCOPE_RESET_NOTICE, ACCESS_OMISSION_NOTICE]] = Field(default_factory=list, max_length=3)
    fact_entries:list[FactEntryData]=Field(default_factory=list,max_length=500)
    studio_entries: list[StudioEntryData] = Field(default_factory=list,max_length=1000)
    version: Literal[1]
    project: ProjectData
    conversations: list[ConversationData] = Field(default_factory=list, max_length=200)
    memories: list[MemoryData] = Field(default_factory=list, max_length=1000)
    events: list[EventData] = Field(default_factory=list, max_length=5000)
    files: list[FileData] = Field(default_factory=list, max_length=100)
    artifacts: list[ArtifactData] = Field(default_factory=list, max_length=100)

    @field_validator("version", mode="before")
    @classmethod
    def explicit_schema_integer(cls, value):
        if type(value) is not int:
            raise ValueError("Snapshot version must be the integer 1")
        return value

    @model_validator(mode="after")
    def identifiers_and_totals(self):
        if len({f.id for f in self.fact_entries})!=len(self.fact_entries):raise ValueError('Duplicate fact ID')
        if sum(len(f.versions) for f in self.fact_entries)>10000:raise ValueError('Too many fact revisions')
        terms=set()
        for entry in self.studio_entries:
            if entry.kind=='glossary':
                key=' '.join(unicodedata.normalize('NFKC',entry.payload.term).casefold().split())
                if key in terms: raise ValueError('Duplicate studio glossary term')
                terms.add(key)
        file_ids, chunk_ids, file_keys = set(), set(), set()
        for file in self.files:
            if file.id in file_ids or (file.filename, file.sha256) in file_keys:
                raise ValueError("Duplicate file reference")
            file_ids.add(file.id)
            file_keys.add((file.filename, file.sha256))
            for chunk in file.chunks:
                if chunk.id in chunk_ids:
                    raise ValueError("Duplicate source chunk reference")
                chunk_ids.add(chunk.id)
        if len(chunk_ids) > 10000 or sum(len(c.messages) for c in self.conversations) > 10000:
            raise ValueError("Snapshot exceeds the record count limit")
        return self


class ImportRequest(SnapshotModel):
    snapshot: Snapshot
    confirm: StrictBool

    @model_validator(mode="after")
    def confirmed(self):
        if not self.confirm:
            raise ValueError("Explicit confirmation is required to import this snapshot")
        return self


def select_fields(row, fields):
    return {key: row[key] for key in fields}


def citation_from_chunk(chunk, file_id, filename, score=None):
    citation = select_fields(chunk, ("id", "page", "line_start", "line_end", "text"))
    citation.update(file_id=file_id, filename=filename)
    if isinstance(score, (int, float)) and score >= 0:
        citation["score"] = float(score)
    return citation


def export_document_citation(reference, canonical):
    """Keep bounded historical locators only after the private chunk/file ACL check."""
    out = dict(canonical)
    if reference.get("document") is not None:
        try:
            CitationData.model_validate(out | {"document": reference["document"]})
        except ValidationError:
            # Invalid location metadata must not invalidate an otherwise scoped text excerpt.
            return out
        out["document"] = reference["document"]
    return out


def export_file_scope(options, owned_file_ids):
    out = dict(options)
    scope = out.get("file_scope") or []
    if any(choice["file_id"] not in owned_file_ids for choice in scope):
        out.pop("file_scope", None)
        out["use_files"] = False
        out.pop('research', None)
    return out


def imported_file_scope(options):
    out = dict(options)
    # A backup describes a previous action; it cannot authorize a new network
    # fetch or turn an unbound private selection into the whole new project.
    imported_research = out.pop('research', None)
    if imported_research and 'files' in imported_research.get('scopes', []):
        out['use_files'] = False
    had_scope = bool(out.pop("file_scope", None))
    if had_scope:
        # Removing a restricted selection must never silently broaden retrieval.
        out["use_files"] = False
    return out, int(had_scope)


def document_metadata_count(snapshot):
    count = 0
    def citations_count(citations):
        return sum(isinstance(c, CitationData) and c.document is not None for c in citations)
    for conversation in snapshot.conversations:
        for message in conversation.messages:
            count += citations_count(message.citations)
    for entry in snapshot.studio_entries:
        for payload in [entry.payload, *(v.payload for v in entry.versions)]:
            if payload.provenance:
                count += citations_count(payload.provenance.citations)
    for artifact in snapshot.artifacts:
        if artifact.studio_source:
            count += citations_count(artifact.studio_source.provenance.citations)
    return count


def remap_citation_markers(content, positions):
    """Removing a source must not silently attach its marker to another source."""
    return re.sub(r"\[S(\d+)\]", lambda match: f"[S{positions[int(match[1])]}]" if int(match[1]) in positions else "[unverified citation]", content)


def studio_json(raw):
    try:
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError('Not an object')
        return value
    except (ValueError,TypeError):raise HTTPException(409,'Stored studio metadata is invalid; backup was not created')

def rewrite_studio_provenance(value,sources,*,imported=False,origin_pairs=None):
    try:out=StudioProvenanceData.model_validate(value).model_dump()
    except ValidationError:raise HTTPException(409,'Stored studio provenance is invalid; backup was not created')
    kept,positions,dropped=[],{},0
    for index,reference in enumerate(out.get('citations',[]),1):
        if isinstance(reference,dict) and reference.get('scope')=='public':
            try:
                kept.append(PublicCitationData.model_validate(reference).model_dump());positions[index]=len(kept)
            except ValidationError: dropped+=1
        elif isinstance(reference,dict) and reference.get('scope','private')=='private':
            mapped=sources.get(reference.get('id')) if isinstance(reference.get('id'),str) else None
            if mapped and reference.get('file_id')==mapped[0]: kept.append(dict(mapped[1]) if imported else export_document_citation(reference,mapped[1]));positions[index]=len(kept)
            else: dropped+=1
        else: dropped+=1
    out['citations']=kept;out['excerpt']=remap_citation_markers(out['excerpt'],positions)
    if imported: out.update(origin='imported_snapshot',message_id=None,conversation_id=None)
    elif origin_pairs is not None and (out.get('message_id'),out.get('conversation_id')) not in origin_pairs: out.update(message_id=None,conversation_id=None)
    return out,dropped

def rewrite_studio_payload(value,sources,**kwargs):
    out=dict(value)
    if out.get('provenance') is None:return out,0
    out['provenance'],dropped=rewrite_studio_provenance(out['provenance'],sources,**kwargs)
    return out,dropped

@router.get("/api/projects/{project_id}/snapshot")
def export_snapshot(project_id: str):
    from backend import studio,guard
    delivery={'messages':[],'artifacts':[],'entries':[],'facts':[],'memories':[]}
    def policy_fingerprint(conn):
        return tuple(tuple(row) for row in conn.execute('SELECT file_id,owner_id,revision,revoked FROM guard_file_policies WHERE project_id=? ORDER BY file_id',(project_id,)))
    with get_db() as conn:
        actor=require_project(conn, project_id)
        # A read transaction gives one consistent view while chat streams write.
        conn.execute("BEGIN")
        policy_before=policy_fingerprint(conn)
        withheld=any(row[1]!=actor.owner_id or row[3] for row in policy_before)
        project = conn.execute("SELECT name,instructions,created_at FROM projects WHERE id=?", (project_id,)).fetchone()
        result = {"version": 1, "project": dict(project), "files": [], "conversations": [], "artifacts": [], "warnings": [DOCUMENT_SNAPSHOT_NOTICE]}
        sources = {}
        owned_file_ids = set()
        owned_origins=set()
        for file in conn.execute("SELECT * FROM knowledge_files WHERE project_id=? ORDER BY created_at,id", (project_id,)):
            policy=conn.execute('SELECT owner_id,revoked FROM guard_file_policies WHERE file_id=? AND project_id=?',(file['id'],project_id)).fetchone()
            if not policy or policy['revoked'] or policy['owner_id']!=actor.owner_id:
                withheld=True;continue
            owned_file_ids.add(file["id"])
            item = select_fields(file, ("id", "filename", "byte_size", "sha256", "page_count", "created_at"))
            item["chunks"] = []
            for chunk in conn.execute("SELECT * FROM knowledge_chunks WHERE file_id=? AND project_id=? ORDER BY page,line_start,id", (file["id"], project_id)):
                item["chunks"].append(select_fields(chunk, ("id", "page", "line_start", "line_end", "text")))
                sources[chunk["id"]] = citation_from_chunk(chunk, file["id"], file["filename"])
            result["files"].append(item)
        for conversation in conn.execute("SELECT * FROM conversations WHERE project_id=? ORDER BY created_at,id", (project_id,)):
            item = select_fields(conversation, ("title", "created_at"))
            item["messages"] = []
            for message in conn.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at,rowid", (conversation["id"],)):
                if not context_lineage.disclosable(conn,project_id,dict(message)):
                    withheld=True;continue
                owned_origins.add((message["id"],conversation["id"]))
                delivery['messages'].append(dict(message))
                saved = select_fields(message, ("role", "content", "status", "model", "created_at"))
                # Foreign/dangling source identifiers never enter an export.
                saved["citations"], positions = [], {}
                for index, citation in enumerate(json.loads(message["citations"]), 1):
                    if isinstance(citation, dict) and citation.get("scope") == "public":
                        try:
                            saved["citations"].append(PublicCitationData.model_validate(citation).model_dump())
                            positions[index] = len(saved["citations"])
                        except ValidationError:
                            # Malformed/unknown provenance cannot become a valid citation.
                            continue
                    elif isinstance(citation, dict) and citation.get("scope", "private") == "private" and citation.get("id") in sources and citation.get("file_id") == sources[citation["id"]]["file_id"]:
                        saved["citations"].append(export_document_citation(citation,sources[citation["id"]]))
                        positions[index] = len(saved["citations"])
                if saved["role"] == "assistant":
                    saved["content"] = remap_citation_markers(saved["content"], positions)
                run=read_receipt(conn,message["id"])
                if run is not None:
                    saved["run"]=RunReceipt.model_validate(run).model_dump()
                    saved["run"]["request_options"]=export_file_scope(saved["run"]["request_options"],owned_file_ids)
                if message['role']=='assistant':saved['context_dependencies']=context_lineage.read(conn,message['id'])
                options=context_lineage.read_retrieval(conn,message['id'])
                if options is not None:saved['retrieval_options']=export_file_scope(options,owned_file_ids)
                item["messages"].append(saved)
            result["conversations"].append(item)
        # A scoped learned rule needs its consent, source and revocation links.
        # Portable snapshots do not carry that state: never flatten it into an
        # unconditional ordinary memory on import into a different project.
        from backend.learning import managed_memory_ids
        managed = managed_memory_ids(conn, project_id)
        result["memories"] = [{"content":row['content'],"created_at":row['created_at']} for row in conn.execute("SELECT id,content,created_at FROM memories WHERE project_id=? AND scope='project' ORDER BY created_at,id", (project_id,)) if row['id'] not in managed]
        delivery['memories']=[dict(row) for row in conn.execute("SELECT id,content,created_at FROM memories WHERE project_id=? AND scope='project' ORDER BY created_at,id",(project_id,)) if row['id'] not in managed]
        studio_sources={key:(value["file_id"],value) for key,value in sources.items()}
        for artifact in conn.execute("SELECT * FROM artifacts WHERE project_id=? ORDER BY created_at,id", (project_id,)):
            if not studio.artifact_allowed(conn,project_id,artifact['id']):
                withheld=True;continue
            delivery['artifacts'].append((artifact['id'],artifact['version']))
            item = select_fields(artifact, ("title", "content", "version", "created_at", "updated_at"))
            item["versions"] = [dict(row) for row in conn.execute("SELECT version,content,created_at FROM artifact_versions WHERE artifact_id=? ORDER BY version", (artifact["id"],))]
            origin=conn.execute('SELECT provenance,template,initial_content_sha256 FROM studio_artifact_sources WHERE artifact_id=? AND project_id=?',(artifact['id'],project_id)).fetchone()
            if origin:
                prov,_=rewrite_studio_provenance(studio_json(origin['provenance']),studio_sources,origin_pairs=owned_origins)
                item['studio_source']={'provenance':prov,'template':origin['template'],'initial_content_sha256':origin['initial_content_sha256']}
            result["artifacts"].append(item)
        result['studio_entries']=[]
        for entry in conn.execute('SELECT * FROM studio_entries WHERE project_id=? ORDER BY created_at,id',(project_id,)):
            if not studio.entry_allowed(conn,entry):
                withheld=True;continue
            delivery['entries'].append((entry['id'],entry['version']))
            item=select_fields(entry,('kind','version','created_at','updated_at'))
            item['payload'],_=rewrite_studio_payload(studio_json(entry['payload']),studio_sources,origin_pairs=owned_origins)
            item['versions']=[]
            for version in conn.execute('SELECT version,payload,created_at FROM studio_versions WHERE entry_id=? ORDER BY version',(entry['id'],)):
                saved=select_fields(version,('version','created_at'))
                saved['payload'],_=rewrite_studio_payload(studio_json(version['payload']),studio_sources,origin_pairs=owned_origins)
                item['versions'].append(saved)
            result['studio_entries'].append(item)
        result['fact_entries']=[]
        for entry in conn.execute('SELECT e.*,v.payload FROM fact_entries e JOIN fact_versions v ON v.fact_id=e.id AND v.version=e.version WHERE e.project_id=? ORDER BY e.created_at,e.id',(project_id,)):
            history=conn.execute('SELECT version,payload,created_at FROM fact_versions WHERE fact_id=? ORDER BY version',(entry['id'],)).fetchall()
            if any(facts.view(conn,dict(entry)|{'payload':v['payload']})['source_state']=='revoked' for v in history):
                withheld=True;continue
            delivery['facts'].append((entry['id'],entry['version']))
            item=select_fields(entry,('id','version','created_at','updated_at'))
            item['payload']=facts.payload(entry)
            item['versions']=[{'version':v['version'],'payload':facts.payload(v),'created_at':v['created_at']} for v in conn.execute('SELECT version,payload,created_at FROM fact_versions WHERE fact_id=? ORDER BY version',(entry['id'],)).fetchall()]
            result['fact_entries'].append(item)
        # Old event summaries contain copied filenames/titles without lineage.
        # Exclude that derived audit text when any restricted content is omitted.
        result['events']=[] if withheld else [dict(row) for row in conn.execute('SELECT kind,summary,created_at FROM events WHERE project_id=? ORDER BY created_at,id',(project_id,))]
        if withheld:result['warnings'].append(ACCESS_OMISSION_NOTICE)
    try:
        Snapshot.model_validate(result)
    except ValidationError:
        raise HTTPException(413, "Project exceeds snapshot limits or contains records outside snapshot schema version 1")
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(413, "Project snapshot exceeds 4 MB")
    with get_db() as conn:
        guard.require_project(conn,project_id,actor)
        if policy_fingerprint(conn)!=policy_before:raise HTTPException(409,'Source access changed while preparing export; retry')
        # The first connection deliberately read one SQLite snapshot. Check a
        # fresh view before disclosing bytes; public rights and consent are not
        # represented by the private-file policy fingerprint above.
        def changed():raise HTTPException(409,'Source access or content changed while preparing export; retry')
        for prior in delivery['messages']:
            current=conn.execute('SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.id=? AND c.project_id=?',(prior['id'],project_id)).fetchone()
            if current is None or dict(current)!=prior or not context_lineage.disclosable(conn,project_id,dict(current)):changed()
        for identifier,version in delivery['artifacts']:
            current=conn.execute('SELECT version FROM artifacts WHERE id=? AND project_id=?',(identifier,project_id)).fetchone()
            if current is None or current['version']!=version or not studio.artifact_allowed(conn,project_id,identifier):changed()
        for identifier,version in delivery['entries']:
            current=conn.execute('SELECT * FROM studio_entries WHERE id=? AND project_id=?',(identifier,project_id)).fetchone()
            if current is None or current['version']!=version or not studio.entry_allowed(conn,current):changed()
        for identifier,version in delivery['facts']:
            current=conn.execute('SELECT * FROM fact_entries WHERE id=? AND project_id=?',(identifier,project_id)).fetchone()
            if current is None or current['version']!=version:changed()
            for history in conn.execute('SELECT payload FROM fact_versions WHERE fact_id=?',(identifier,)):
                if facts.view(conn,dict(current)|{'payload':history['payload']})['source_state']=='revoked':changed()
        for prior in delivery['memories']:
            current=conn.execute("SELECT id,content,created_at FROM memories WHERE id=? AND project_id=? AND scope='project'",(prior['id'],project_id)).fetchone()
            if current is None or dict(current)!=prior:changed()
    return Response(payload, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="riwaq-project.json"'})


@router.post("/api/projects/import", status_code=201)
async def import_snapshot(request: Request):
    raw = await request.body()
    if len(raw) > MAX_SNAPSHOT_BYTES + MAX_SNAPSHOT_ENVELOPE_OVERHEAD:
        raise HTTPException(413, "Snapshot import exceeds 4 MB")
    try:
        body = ImportRequest.model_validate_json(raw)
    except ValidationError as exc:
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors(include_input=False)]
        raise HTTPException(422, errors)
    if len(json.dumps(json.loads(raw)['snapshot'],ensure_ascii=False,separators=(',',':')).encode('utf-8'))>MAX_SNAPSHOT_BYTES:
        raise HTTPException(413,'Project snapshot exceeds 4 MB')
    snapshot = body.snapshot
    project_id = uid()
    source_map = {}
    dropped_citations = 0
    stripped_document_metadata = document_metadata_count(snapshot)
    reset_file_scopes = 0
    with get_db() as conn:
        # get_db commits only after the entire import; any failure rolls back.
        conn.execute("INSERT INTO projects VALUES(?,?,?,?)", (project_id, snapshot.project.name[:109] + " (restored)", snapshot.project.instructions, snapshot.project.created_at))
        for file in snapshot.files:
            file_id = uid()
            conn.execute("INSERT INTO knowledge_files VALUES(?,?,?,?,?,?,?,?,?)", (file_id, project_id, file.filename, file.byte_size, file.sha256, len(file.chunks), file.page_count, sum(len(c.text) for c in file.chunks), file.created_at))
            for chunk in file.chunks:
                chunk_id = uid()
                conn.execute("INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?)", (chunk_id, file_id, project_id, chunk.page, chunk.line_start, chunk.line_end, chunk.text))
                citation = citation_from_chunk(chunk.model_dump() | {"id": chunk_id}, file_id, file.filename)
                source_map[chunk.id] = (file.id, citation)
        for conversation in snapshot.conversations:
            conversation_id = uid()
            updated_at = conversation.messages[-1].created_at if conversation.messages else conversation.created_at
            conn.execute("INSERT INTO conversations VALUES(?,?,?,?,?,?)", (conversation_id, project_id, conversation.title, conversation.created_at, updated_at, None))
            for message in conversation.messages:
                citations, positions = [], {}
                for index, reference in enumerate(message.citations, 1):
                    if isinstance(reference, PublicCitationData):
                        # No public-corpus lookup, registration, download or embedding.
                        citations.append(reference.model_dump())
                        positions[index] = len(citations)
                        continue
                    mapped = source_map.get(reference.id)
                    if mapped and reference.file_id == mapped[0]:
                        citations.append(mapped[1])
                        positions[index] = len(citations)
                    else:
                        dropped_citations += 1
                status = "interrupted" if message.status == "streaming" else message.status
                content = remap_citation_markers(message.content, positions) if message.role == "assistant" else message.content
                new_message_id=uid()
                conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)", (new_message_id, conversation_id, message.role, content, status, message.model, message.created_at, json.dumps(citations, ensure_ascii=False)))
                if message.retrieval_options is not None:
                    options,cleared=imported_file_scope(message.retrieval_options.model_dump())
                    reset_file_scopes+=cleared
                    context_lineage.save_retrieval(conn,new_message_id,options)
                if message.role=='assistant':context_lineage.save(conn,new_message_id,[context_lineage.BLOCKED])
                if message.run:
                    restored_run=message.run.model_dump();restored_run.update(id=uid(),origin='imported_snapshot')
                    restored_run['fact_bindings']=[]
                    restored_run['request_options'],cleared=imported_file_scope(restored_run.get('request_options',{}))
                    reset_file_scopes+=cleared
                    restored_run['request_options']['fact_ids']=[]
                    if cleared:restored_run['remaining']=(restored_run['remaining']+[FILE_SCOPE_RESET_NOTICE])[-20:]
                    if restored_run['status'] in {'preparing','running'}:restored_run['status']='interrupted'
                    for stage in restored_run['stages']:
                        if stage['status']=='running':stage['status']='failed'
                    restored_run['remaining']=(restored_run['remaining']+['Historical imported checks; not re-executed or a new verification.'])[-20:]
                    for check in restored_run['checks']:
                        if check['kind']=='citation_identifiers':check.update(status='review_needed',summary='Historical citation identifiers; source positions may have changed during restoration.')
                    save_receipt(conn,new_message_id,restored_run)
        for memory in snapshot.memories:
            conn.execute("INSERT INTO memories VALUES(?,?,?,?,?)", (uid(), "project", project_id, memory.content, memory.created_at))
        for item in snapshot.events:
            conn.execute("INSERT INTO events VALUES(?,?,?,?,?)", (uid(), project_id, item.kind, item.summary, item.created_at))
        for artifact in snapshot.artifacts:
            artifact_id = uid()
            conn.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)", (artifact_id, project_id, artifact.title, artifact.content, artifact.version, artifact.created_at, artifact.updated_at))
            conn.executemany("INSERT INTO artifact_versions VALUES(?,?,?,?)", [(artifact_id, v.version, v.content, v.created_at) for v in artifact.versions])
            if artifact.studio_source:
                source=artifact.studio_source
                provenance,dropped=rewrite_studio_provenance(source.provenance.model_dump(),source_map,imported=True)
                dropped_citations+=dropped
                conn.execute('INSERT INTO studio_artifact_sources VALUES(?,?,?,?,?,?)',(artifact_id,project_id,json.dumps(provenance,ensure_ascii=False),'snapshot:'+uid(),source.template,source.initial_content_sha256))
        for entry in snapshot.studio_entries:
            entry_id=uid()
            payload,dropped=rewrite_studio_payload(entry.payload.model_dump(),source_map,imported=True)
            dropped_citations+=dropped
            term_key=' '.join(unicodedata.normalize('NFKC',payload['term']).casefold().split()) if entry.kind=='glossary' else None
            conn.execute('INSERT INTO studio_entries VALUES(?,?,?,?,?,?,?,?)',(entry_id,project_id,entry.kind,json.dumps(payload,ensure_ascii=False),entry.version,entry.created_at,entry.updated_at,term_key))
            for version in entry.versions:
                historical,dropped=rewrite_studio_payload(version.payload.model_dump(),source_map,imported=True)
                dropped_citations+=dropped
                conn.execute('INSERT INTO studio_versions VALUES(?,?,?,?)',(entry_id,version.version,json.dumps(historical,ensure_ascii=False),version.created_at))
        for entry in snapshot.fact_entries:
            fact_id=uid();versions=[]
            for version in entry.versions:
                data=version.payload.model_dump()
                if data.get('archived_review') is None:data['archived_review']={k:data[k] for k in ('review_state','reviewed_at','review_note')}
                data.update(review_state='draft',reviewed_at=None,review_note='')
                for evidence in data['evidence']:evidence['origin']='imported_snapshot'
                versions.append((fact_id,version.version,facts.packed(data),version.created_at))
            latest=json.loads(versions[-1][2])
            conn.execute('INSERT INTO fact_entries VALUES(?,?,?,?,?,?)',(fact_id,project_id,entry.version,facts.comparison_key(latest),entry.created_at,entry.updated_at))
            conn.executemany('INSERT INTO fact_versions VALUES(?,?,?,?)',versions)
        event(conn, project_id, "snapshot_imported", "Imported a project snapshot as a new project; permissions, secrets and global preferences were not restored")
        result = dict(conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())
    warnings = [DOCUMENT_SNAPSHOT_NOTICE]
    if reset_file_scopes:
        warnings.append(FILE_SCOPE_RESET_NOTICE)
    return result | {"dropped_citations": dropped_citations, "stripped_document_metadata": stripped_document_metadata,
                     "reset_file_scopes": reset_file_scopes, "warnings": warnings}
