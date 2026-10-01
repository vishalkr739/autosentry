import hashlib
from collections.abc import Sequence

import httpx
import pytest

from autosentry_agent.ingestion.corpus import Corpus, CorpusDocument, VerificationQuestion
from autosentry_agent.ingestion.driver import DocumentFetchError, fetch_document, run
from autosentry_agent.ingestion.model import (
    AccessContext,
    DocumentMetadata,
    DocumentState,
    DocumentSubmission,
    IngestionStatus,
    RetrievedChunk,
)

CONTENT = {"https://example.org/a.pdf": b"document a", "https://example.org/b.pdf": b"document b"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def document(doc_id: str, url: str, sha256: str | None = None) -> CorpusDocument:
    return CorpusDocument(
        doc_id=doc_id,
        title=doc_id,
        url=url,
        sha256=sha(CONTENT[url]) if sha256 is None else sha256,
        suffix=".pdf",
        metadata=DocumentMetadata(doc_type="guidance", source="FATF", classification="PUBLIC"),
    )


def corpus(*docs: CorpusDocument, questions=()) -> Corpus:
    return Corpus(
        graph="KB",
        access=AccessContext("global", frozenset({"analyst"})),
        documents=tuple(docs),
        questions=tuple(questions),
    )


@pytest.fixture
def http():
    downloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        downloads.append(url)
        if url in CONTENT:
            return httpx.Response(200, content=CONTENT[url])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.downloads = downloads  # type: ignore[attr-defined]
    return client


class FakeBackend:
    """Records calls and plays back a scripted sequence of status polls."""

    def __init__(self, status_polls: Sequence[IngestionStatus] = (IngestionStatus.READY,), cites=None):
        self.calls: list[str] = []
        self.submitted: list[DocumentSubmission] = []
        self.status_polls = list(status_polls)
        self.cites = cites or {}
        self.access_seen: list[AccessContext] = []

    def ensure_graph(self, graph):
        self.calls.append(f"ensure_graph:{graph}")

    def submit(self, graph, doc):
        self.calls.append(f"submit:{doc.doc_id}")
        self.submitted.append(doc)
        return DocumentState(doc.doc_id, IngestionStatus.UPLOADED)

    def commit(self, graph):
        self.calls.append("commit")

    def status(self, graph, doc_ids):
        current = self.status_polls.pop(0) if len(self.status_polls) > 1 else self.status_polls[0]
        return [DocumentState(d, current) for d in doc_ids]

    def retrieve(self, graph, question, access, top_k):
        self.access_seen.append(access)
        return [RetrievedChunk(f"{d}_chunk_0", d, "text") for d in self.cites.get(question, [])]


def run_with(backend, corpus_, tmp_path, http, **kwargs):
    ticks = iter(range(0, 10_000, 10))
    return run(
        corpus_,
        backend,
        cache_dir=tmp_path / "cache",
        http=http,
        poll_interval=10,
        timeout=kwargs.pop("timeout", 1000),
        clock=lambda: next(ticks),
        sleep=lambda _: None,
        **kwargs,
    )


def test_fetch_downloads_once_and_verifies_hash(tmp_path, http):
    doc = document("a", "https://example.org/a.pdf")

    path = fetch_document(doc, tmp_path, http)
    fetch_document(doc, tmp_path, http)

    assert path.name == "a.pdf"
    assert path.read_bytes() == b"document a"
    assert http.downloads == ["https://example.org/a.pdf"]


def test_fetch_without_pinned_hash_reports_the_hash_to_pin(tmp_path, http):
    doc = document("a", "https://example.org/a.pdf", sha256="")
    with pytest.raises(DocumentFetchError, match=sha(b"document a")):
        fetch_document(doc, tmp_path, http)


def test_fetch_hash_mismatch_is_rejected_and_discarded(tmp_path, http):
    doc = document("a", "https://example.org/a.pdf", sha256="0" * 64)
    with pytest.raises(DocumentFetchError, match="mismatch"):
        fetch_document(doc, tmp_path, http)
    assert not (tmp_path / "a.pdf").exists()


def test_fetch_error_status_is_reported(tmp_path, http):
    doc = document("missing", "https://example.org/a.pdf")
    doc = CorpusDocument(**{**doc.__dict__, "url": "https://example.org/gone.pdf"})
    with pytest.raises(DocumentFetchError, match="HTTP 404"):
        fetch_document(doc, tmp_path, http)


def test_run_happy_path_submits_every_document_then_verifies(tmp_path, http):
    q = VerificationQuestion("what does a say?", "a")
    backend = FakeBackend(
        status_polls=[IngestionStatus.QUEUED, IngestionStatus.EMBEDDING, IngestionStatus.READY],
        cites={q.question: ["a", "b"]},
    )
    c = corpus(document("a", "https://example.org/a.pdf"), document("b", "https://example.org/b.pdf"), questions=[q])

    report = run_with(backend, c, tmp_path, http)

    assert report.ok, report.render()
    assert backend.calls == ["ensure_graph:KB", "submit:a", "submit:b", "commit"]
    assert backend.submitted[0].metadata.classification == "PUBLIC"
    assert backend.access_seen == [c.access]
    assert report.questions[0].cited_doc_ids == ("a", "b")


def test_run_touches_nothing_when_any_document_fails_verification(tmp_path, http):
    backend = FakeBackend()
    c = corpus(document("a", "https://example.org/a.pdf"), document("b", "https://example.org/b.pdf", sha256="0" * 64))

    report = run_with(backend, c, tmp_path, http)

    assert not report.ok
    assert backend.calls == []
    assert len(report.fetch_errors) == 1 and "b:" in report.fetch_errors[0]


def test_run_fails_when_a_document_fails_and_skips_questions(tmp_path, http):
    backend = FakeBackend(status_polls=[IngestionStatus.FAILED])
    c = corpus(document("a", "https://example.org/a.pdf"), questions=[VerificationQuestion("q", "a")])

    report = run_with(backend, c, tmp_path, http)

    assert not report.ok
    assert report.questions == []


def test_run_fails_when_retrieval_does_not_cite_the_expected_document(tmp_path, http):
    q = VerificationQuestion("q", "a")
    backend = FakeBackend(cites={"q": ["b"]})
    c = corpus(document("a", "https://example.org/a.pdf"), document("b", "https://example.org/b.pdf"), questions=[q])

    report = run_with(backend, c, tmp_path, http)

    assert not report.ok
    assert not report.questions[0].passed
    assert "expected a, cited b" in report.render()


def test_run_times_out_when_documents_never_finish(tmp_path, http):
    backend = FakeBackend(status_polls=[IngestionStatus.EMBEDDING])
    c = corpus(document("a", "https://example.org/a.pdf"))

    report = run_with(backend, c, tmp_path, http, timeout=50)

    assert report.timed_out
    assert not report.ok


def test_empty_corpus_is_not_ok(tmp_path, http):
    assert not run_with(FakeBackend(), corpus(), tmp_path, http).ok
