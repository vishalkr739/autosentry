"""Corpus definition: what to ingest, where it comes from, and how to check it."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from .model import AccessContext, DocumentMetadata

# graphrag lowercases ids and rewrites spaces, slashes and parentheses when it
# derives chunk ids; restricting ids to this charset keeps doc ids and chunk
# ids ({doc_id}_chunk_{n}) mapping back to each other exactly.
_DOC_ID = re.compile(r"^[a-z0-9_]+$")
_SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md", ".html", ".htm", ".docx"}


class CorpusError(ValueError):
    """The corpus file is malformed."""


@dataclass(frozen=True)
class CorpusDocument:
    doc_id: str
    title: str
    url: str
    sha256: str
    suffix: str
    metadata: DocumentMetadata


@dataclass(frozen=True)
class VerificationQuestion:
    question: str
    expected_doc_id: str


@dataclass(frozen=True)
class Corpus:
    graph: str
    access: AccessContext
    documents: tuple[CorpusDocument, ...]
    questions: tuple[VerificationQuestion, ...]


def load_corpus(path: Path) -> Corpus:
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    try:
        access = AccessContext(
            tenant_id=raw["access"]["tenant_id"],
            roles=frozenset(raw["access"].get("roles", [])),
        )
        documents = tuple(_document(entry) for entry in raw["documents"])
        questions = tuple(
            VerificationQuestion(q["question"], q["expected_doc_id"])
            for q in raw.get("questions", [])
        )
        corpus = Corpus(raw["graph"], access, documents, questions)
    except KeyError as missing:
        raise CorpusError(f"{path}: missing required key {missing}") from None

    ids = [d.doc_id for d in corpus.documents]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise CorpusError(f"{path}: duplicate doc_id {duplicates}")
    unknown = sorted({q.expected_doc_id for q in corpus.questions} - set(ids))
    if unknown:
        raise CorpusError(f"{path}: questions reference unknown doc_id {unknown}")
    return corpus


def _document(entry: dict) -> CorpusDocument:
    doc_id = entry["doc_id"]
    if not _DOC_ID.match(doc_id):
        raise CorpusError(f"doc_id {doc_id!r} must match {_DOC_ID.pattern}")
    url = entry["url"]
    file_type = entry.get("file_type")
    suffix = f".{file_type.lower().lstrip('.')}" if file_type else PurePosixPath(urlparse(url).path).suffix.lower()
    doc = CorpusDocument(
        doc_id=doc_id,
        title=entry["title"],
        url=url,
        sha256=entry.get("sha256", "").lower(),
        suffix=suffix,
        metadata=DocumentMetadata(
            doc_type=entry["doc_type"],
            source=entry["source"],
            jurisdiction=entry.get("jurisdiction", ""),
            language=entry.get("language", "en"),
            classification=entry.get("classification", "RESTRICTED"),
            allowed_roles=frozenset(entry.get("allowed_roles", [])),
        ),
    )
    if doc.suffix not in _SUPPORTED_SUFFIXES:
        raise CorpusError(
            f"{doc_id}: file type must be one of {sorted(_SUPPORTED_SUFFIXES)} "
            f"(from the URL or an explicit file_type), got {doc.suffix or 'none'}"
        )
    return doc
