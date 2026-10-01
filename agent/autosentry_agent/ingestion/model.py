from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class IngestionStatus(StrEnum):
    """Per-document pipeline state.

    Modeled on graphrag's planned Track A state machine, not on what its
    pipeline exposes today, so callers don't change when that pipeline lands.
    """

    UPLOADED = "UPLOADED"
    SCANNING = "SCANNING"
    QUEUED = "QUEUED"
    PARSING = "PARSING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    INDEXING = "INDEXING"
    READY = "READY"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"
    DELETED = "DELETED"

    @property
    def is_terminal(self) -> bool:
        return self in (
            IngestionStatus.READY,
            IngestionStatus.QUARANTINED,
            IngestionStatus.FAILED,
            IngestionStatus.DELETED,
        )


@dataclass(frozen=True)
class DocumentMetadata:
    doc_type: str
    source: str
    jurisdiction: str = ""
    language: str = "en"
    # Planned graphrag default is fail-closed: RESTRICTED with no roles.
    classification: str = "RESTRICTED"
    allowed_roles: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DocumentSubmission:
    doc_id: str
    path: Path
    sha256: str
    metadata: DocumentMetadata


@dataclass(frozen=True)
class DocumentState:
    doc_id: str
    status: IngestionStatus
    detail: str = ""


@dataclass(frozen=True)
class AccessContext:
    tenant_id: str
    roles: frozenset[str]


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    doc_id: str
    text: str
