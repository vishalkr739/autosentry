"""Runs a corpus through an IngestionBackend and verifies it is retrievable."""

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .backend import IngestionBackend
from .corpus import Corpus, CorpusDocument
from .model import DocumentState, DocumentSubmission, IngestionStatus

logger = logging.getLogger(__name__)


class DocumentFetchError(RuntimeError):
    """A corpus document could not be downloaded or failed verification."""


@dataclass(frozen=True)
class QuestionResult:
    question: str
    expected_doc_id: str
    cited_doc_ids: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.expected_doc_id in self.cited_doc_ids


@dataclass
class RunReport:
    fetch_errors: list[str] = field(default_factory=list)
    documents: list[DocumentState] = field(default_factory=list)
    questions: list[QuestionResult] = field(default_factory=list)
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return (
            not self.fetch_errors
            and not self.timed_out
            and bool(self.documents)
            and all(d.status is IngestionStatus.READY for d in self.documents)
            and all(q.passed for q in self.questions)
        )

    def render(self) -> str:
        lines = []
        for error in self.fetch_errors:
            lines.append(f"FETCH FAIL  {error}")
        for doc in self.documents:
            mark = "OK  " if doc.status is IngestionStatus.READY else "FAIL"
            detail = f"  ({doc.detail})" if doc.detail else ""
            lines.append(f"DOC {mark}    {doc.doc_id}: {doc.status}{detail}")
        if self.timed_out:
            lines.append("TIMED OUT   documents did not reach a terminal state")
        for q in self.questions:
            mark = "OK  " if q.passed else "FAIL"
            cited = ", ".join(q.cited_doc_ids) or "nothing"
            lines.append(f"QUESTION {mark} {q.question!r}: expected {q.expected_doc_id}, cited {cited}")
        lines.append("RESULT      " + ("PASS" if self.ok else "FAIL"))
        return "\n".join(lines)


def fetch_document(doc: CorpusDocument, cache_dir: Path, http: httpx.Client) -> Path:
    """Download a document into the cache (once) and verify its pinned hash."""
    path = cache_dir / f"{doc.doc_id}{doc.suffix}"
    if not path.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        response = http.get(doc.url, follow_redirects=True)
        if response.status_code != 200:
            raise DocumentFetchError(f"{doc.doc_id}: HTTP {response.status_code} from {doc.url}")
        partial = path.with_name(path.name + ".part")
        partial.write_bytes(response.content)
        partial.replace(path)

    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if not doc.sha256:
        raise DocumentFetchError(
            f"{doc.doc_id}: no sha256 pinned in the corpus; the downloaded file "
            f"({path}) hashes to {actual}. Review it, then pin that value."
        )
    if actual != doc.sha256:
        path.unlink()
        raise DocumentFetchError(
            f"{doc.doc_id}: sha256 mismatch (pinned {doc.sha256}, got {actual}); "
            f"the source changed or the download is corrupt"
        )
    return path


def run(
    corpus: Corpus,
    backend: IngestionBackend,
    *,
    cache_dir: Path,
    http: httpx.Client,
    poll_interval: float = 30.0,
    timeout: float = 7200.0,
    top_k: int = 5,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> RunReport:
    report = RunReport()

    # Every document is fetched and verified before graphrag is touched, so a
    # changed or unreachable source can't leave the graph half-ingested.
    paths: dict[str, Path] = {}
    for doc in corpus.documents:
        try:
            paths[doc.doc_id] = fetch_document(doc, cache_dir, http)
        except (DocumentFetchError, httpx.HTTPError) as err:
            report.fetch_errors.append(str(err))
    if report.fetch_errors:
        return report

    graph = corpus.graph
    backend.ensure_graph(graph)
    for doc in corpus.documents:
        backend.submit(
            graph, DocumentSubmission(doc.doc_id, paths[doc.doc_id], doc.sha256, doc.metadata)
        )
    backend.commit(graph)

    doc_ids = [doc.doc_id for doc in corpus.documents]
    deadline = clock() + timeout
    while True:
        report.documents = backend.status(graph, doc_ids)
        pending = [d for d in report.documents if not d.status.is_terminal]
        if not pending:
            break
        if clock() >= deadline:
            report.timed_out = True
            return report
        logger.info("%d of %d documents still processing (%s)", len(pending), len(doc_ids), pending[0].status)
        sleep(poll_interval)

    if all(d.status is IngestionStatus.READY for d in report.documents):
        for q in corpus.questions:
            chunks = backend.retrieve(graph, q.question, corpus.access, top_k)
            cited = tuple(sorted({c.doc_id for c in chunks}))
            report.questions.append(QuestionResult(q.question, q.expected_doc_id, cited))
    return report
