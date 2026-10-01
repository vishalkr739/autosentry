import base64
import json
import re
from pathlib import Path

import httpx
import pytest

from autosentry_agent.ingestion.graphrag_current import CurrentGraphragBackend, GraphragError
from autosentry_agent.ingestion.model import (
    AccessContext,
    DocumentMetadata,
    DocumentSubmission,
    IngestionStatus,
)

TOKEN = "jwt-token"
BASIC = "Basic " + base64.b64encode(f"__graphrag_token__:{TOKEN}".encode()).decode()
ACCESS = AccessContext("global", frozenset({"analyst"}))


class FakeGraphrag:
    """Just enough of graphrag's HTTP API to drive the adapter."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.graph_exists = False
        self.eligibility = "empty"
        self.init_states = ["running", "completed"]
        self.upload_status: str | None = None  # forces a status instead of storing
        self.stored: dict[str, int] = {}  # filename -> size, as uploads/list reports it
        self.truncate_uploads = False  # store 0 bytes, like the crash-interrupted upload
        self.ingest_results: dict[str, dict] = {}
        self.rebuild_polls: list[dict] = [{"is_running": False, "status": "idle"}]
        self.coverage = {"total": 4, "missing": 0}
        self.coverage_status = 200
        self.search_result = {}

    def _upload(self, request: httpx.Request) -> httpx.Response:
        if self.upload_status is not None:
            return httpx.Response(200, json={"status": self.upload_status})
        body = request.content
        filename = re.search(rb'filename="([^"]+)"', body).group(1).decode()
        payload = body.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
        if filename in self.stored and request.url.params["overwrite"] == "false":
            return httpx.Response(200, json={"status": "conflict"})
        self.stored[filename] = 0 if self.truncate_uploads else len(payload)
        return httpx.Response(200, json={"status": "success"})

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/uploads/list"):
            files = [{"filename": f, "size": s} for f, s in self.stored.items()]
            return httpx.Response(200, json={"files": files})
        if path.endswith("/graphrag/search"):
            return httpx.Response(200, json=[{"final_retrieval": self.search_result}])
        if path.endswith("/create_graph"):
            if self.graph_exists:
                return httpx.Response(200, json={"status": "error", "message": "Graph 'KB' already exists"})
            self.graph_exists = True
            return httpx.Response(200, json={"status": "success"})
        if path.endswith("/check_init_eligibility"):
            return httpx.Response(200, json={"state": self.eligibility})
        if path.endswith("/initialize_graph"):
            return httpx.Response(202, json={"status": "submitted"})
        if path.endswith("/initialize_status"):
            return httpx.Response(200, json={"state": self.init_states.pop(0)})
        if path.endswith("/config/graphrag"):
            return httpx.Response(200, json={"status": "success"})
        if path.endswith("/uploads"):
            return self._upload(request)
        if path.endswith("/create_ingest"):
            return httpx.Response(200, json={"load_job_id": "job", "data_source_id": {"data_source": "server"}, "data_path": "in_temp_storage"})
        if path.endswith("/ingest"):
            files = [{"jsonl_file": f"{d}.jsonl", **r} for d, r in self.ingest_results.items()]
            return httpx.Response(200, json={"ingested_files": files})
        if path.endswith("/rebuild_graph"):
            return httpx.Response(200, json={"status": "submitted"})
        if path.endswith("/rebuild_status"):
            poll = self.rebuild_polls.pop(0) if len(self.rebuild_polls) > 1 else self.rebuild_polls[0]
            return httpx.Response(200, json=poll)
        if path.endswith("/migration/status"):
            if self.coverage_status != 200:
                return httpx.Response(self.coverage_status, text="Internal Server Error")
            return httpx.Response(200, json={"embeddings": {"by_type": {"DocumentChunk": self.coverage}}})
        return httpx.Response(404, text=f"unexpected {request.method} {path}")

    def paths(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]


@pytest.fixture
def server():
    return FakeGraphrag()


@pytest.fixture
def backend(server):
    return CurrentGraphragBackend(
        "http://graphrag:8000/",
        TOKEN,
        http=httpx.Client(transport=httpx.MockTransport(server.handle)),
        sleep=lambda _: None,
    )


def submission(tmp_path: Path, doc_id: str) -> DocumentSubmission:
    path = tmp_path / f"{doc_id}-download.PDF"
    path.write_bytes(b"%PDF content")
    return DocumentSubmission(doc_id, path, "hash", DocumentMetadata(doc_type="guidance", source="FATF"))


def test_ensure_graph_creates_initializes_and_disables_image_extraction(backend, server):
    backend.ensure_graph("KB")

    assert server.paths() == [
        "POST /ui/KB/create_graph",
        "GET /ui/KB/check_init_eligibility",
        "POST /ui/KB/initialize_graph",
        "GET /ui/KB/initialize_status",
        "GET /ui/KB/initialize_status",
        "POST /ui/config/graphrag",
    ]
    assert json.loads(server.requests[-1].content) == {"scope": "graph", "graphname": "KB", "extract_images": False}
    assert all(r.headers["Authorization"] == BASIC for r in server.requests)


def test_ensure_graph_is_idempotent_for_an_initialized_graph(backend, server):
    server.graph_exists = True
    server.eligibility = "structural_present"

    backend.ensure_graph("KB")

    assert "POST /ui/KB/initialize_graph" not in server.paths()


def test_ensure_graph_refuses_to_initialize_over_foreign_types(backend, server):
    server.eligibility = "user_types_present"
    with pytest.raises(GraphragError, match="pre-existing"):
        backend.ensure_graph("KB")


def test_ensure_graph_surfaces_initialization_failure(backend, server):
    server.init_states = ["error"]
    with pytest.raises(GraphragError, match="initialize_graph failed"):
        backend.ensure_graph("KB")


def test_submit_uploads_under_the_doc_id_without_overwriting(backend, server, tmp_path):
    state = backend.submit("KB", submission(tmp_path, "fatf_recs"))

    assert state.status is IngestionStatus.UPLOADED
    upload = server.requests[0]
    assert upload.url.path == "/ui/KB/uploads"
    assert upload.url.params["overwrite"] == "false"
    assert b'filename="fatf_recs.pdf"' in upload.content


def test_submit_treats_an_existing_upload_of_the_same_size_as_submitted(backend, server, tmp_path):
    doc = submission(tmp_path, "a")
    server.stored["a.pdf"] = doc.path.stat().st_size

    assert backend.submit("KB", doc).status is IngestionStatus.UPLOADED
    uploads = [r for r in server.requests if r.url.path.endswith("/uploads")]
    assert [r.url.params["overwrite"] for r in uploads] == ["false"]


def test_submit_replaces_an_existing_upload_whose_size_differs(backend, server, tmp_path):
    doc = submission(tmp_path, "a")
    server.stored["a.pdf"] = 0  # left empty by an interrupted earlier upload

    assert backend.submit("KB", doc).status is IngestionStatus.UPLOADED
    uploads = [r for r in server.requests if r.url.path.endswith("/uploads")]
    assert [r.url.params["overwrite"] for r in uploads] == ["false", "true"]
    assert server.stored["a.pdf"] == doc.path.stat().st_size


def test_submit_raises_when_the_stored_file_does_not_match(backend, server, tmp_path):
    server.truncate_uploads = True
    with pytest.raises(GraphragError, match="is 0 bytes on graphrag, expected 12"):
        backend.submit("KB", submission(tmp_path, "a"))


def test_submit_raises_when_graphrag_rejects_the_upload(backend, server, tmp_path):
    server.upload_status = "error"
    with pytest.raises(GraphragError):
        backend.submit("KB", submission(tmp_path, "a"))


def test_commit_ingests_the_upload_folder_then_triggers_one_rebuild(backend, server, tmp_path):
    backend.submit("KB", submission(tmp_path, "a"))
    server.ingest_results = {"a": {"status": "success"}}
    server.requests.clear()

    backend.commit("KB")

    assert server.paths() == [
        "POST /ui/KB/create_ingest",
        "POST /ui/KB/ingest",
        "GET /ui/KB/rebuild_status",
        "POST /ui/KB/rebuild_graph",
    ]
    assert json.loads(server.requests[0].content)["data_source_config"] == {"data_path": "uploads/KB"}
    assert json.loads(server.requests[1].content)["file_path"] == "in_temp_storage"


def test_commit_with_nothing_submitted_does_nothing(backend, server):
    backend.commit("KB")
    assert server.requests == []


def committed(backend, server, tmp_path, *doc_ids, baseline=None):
    for doc_id in doc_ids:
        backend.submit("KB", submission(tmp_path, doc_id))
    server.rebuild_polls = [baseline or {"is_running": False, "status": "idle"}]
    backend.commit("KB")


def test_status_follows_the_rebuild_to_ready(backend, server, tmp_path):
    server.ingest_results = {"a": {"status": "success"}}
    committed(backend, server, tmp_path, "a")
    server.rebuild_polls = [
        {"is_running": False, "status": "idle"},
        {"is_running": True, "status": "running", "stage": "Doc Processing"},
        {"is_running": False, "status": "completed", "completed_at": 200.0},
    ]

    seen = [backend.status("KB", ["a"])[0] for _ in range(3)]

    assert [s.status for s in seen] == [IngestionStatus.QUEUED, IngestionStatus.EMBEDDING, IngestionStatus.READY]
    assert seen[1].detail == "Doc Processing"


def test_status_ignores_a_completion_left_over_from_an_earlier_rebuild(backend, server, tmp_path):
    server.ingest_results = {"a": {"status": "success"}}
    stale = {"is_running": False, "status": "completed", "completed_at": 100.0}
    committed(backend, server, tmp_path, "a", baseline=stale)
    server.rebuild_polls = [stale]

    assert backend.status("KB", ["a"])[0].status is IngestionStatus.QUEUED


def test_status_treats_idle_after_running_as_finished(backend, server, tmp_path):
    server.ingest_results = {"a": {"status": "success"}}
    committed(backend, server, tmp_path, "a")
    server.rebuild_polls = [{"is_running": True, "status": "running"}, {"is_running": False, "status": "idle"}]

    backend.status("KB", ["a"])

    assert backend.status("KB", ["a"])[0].status is IngestionStatus.READY


def test_status_reports_rebuild_failure(backend, server, tmp_path):
    server.ingest_results = {"a": {"status": "success"}}
    committed(backend, server, tmp_path, "a")
    server.rebuild_polls = [{"is_running": False, "status": "failed", "failed_at": 5.0, "error": "extract() takes 5 positional arguments"}]

    state = backend.status("KB", ["a"])[0]

    assert state.status is IngestionStatus.FAILED
    assert "extract()" in state.detail


def test_status_fails_when_chunks_are_missing_embeddings(backend, server, tmp_path):
    server.ingest_results = {"a": {"status": "success"}}
    committed(backend, server, tmp_path, "a")
    server.rebuild_polls = [{"is_running": False, "status": "completed", "completed_at": 9.0}]
    server.coverage = {"total": 10, "missing": 3}

    state = backend.status("KB", ["a"])[0]

    assert state.status is IngestionStatus.FAILED
    assert state.detail == "3 of 10 chunks have no embedding"


def test_status_fails_when_the_rebuild_produced_no_chunks(backend, server, tmp_path):
    server.ingest_results = {"a": {"status": "success"}}
    committed(backend, server, tmp_path, "a")
    server.rebuild_polls = [{"is_running": False, "status": "completed", "completed_at": 9.0}]
    server.coverage = {"total": 0, "missing": 0}

    assert backend.status("KB", ["a"])[0].status is IngestionStatus.FAILED


def test_status_is_ready_but_unverified_when_coverage_report_fails(backend, server, tmp_path, caplog):
    server.ingest_results = {"a": {"status": "success"}}
    committed(backend, server, tmp_path, "a")
    server.rebuild_polls = [{"is_running": False, "status": "completed", "completed_at": 9.0}]
    server.coverage_status = 500

    state = backend.status("KB", ["a"])[0]

    assert (state.status, state.detail) == (
        IngestionStatus.READY,
        "rebuild completed; embedding coverage unverified",
    )
    assert "migration/status failed" in caplog.text


def test_status_does_not_mask_a_rejected_coverage_request(backend, server, tmp_path):
    server.ingest_results = {"a": {"status": "success"}}
    committed(backend, server, tmp_path, "a")
    server.rebuild_polls = [{"is_running": False, "status": "completed", "completed_at": 9.0}]
    server.coverage_status = 403

    with pytest.raises(GraphragError, match="HTTP 403"):
        backend.status("KB", ["a"])


def test_status_reports_per_document_ingest_failures(backend, server, tmp_path):
    server.ingest_results = {"a": {"status": "success"}, "b": {"status": "failed", "error": "parse error"}}
    committed(backend, server, tmp_path, "a", "b", "c")
    server.rebuild_polls = [{"is_running": True, "status": "running"}]

    a, b, c = backend.status("KB", ["a", "b", "c"])

    assert a.status is IngestionStatus.EMBEDDING
    assert (b.status, b.detail) == (IngestionStatus.FAILED, "parse error")
    assert c.status is IngestionStatus.FAILED
    assert "missing from graphrag's ingest results" in c.detail


def test_status_before_commit_is_uploaded(backend, tmp_path):
    backend.submit("KB", submission(tmp_path, "a"))
    assert backend.status("KB", ["a"])[0].status is IngestionStatus.UPLOADED


def test_retrieve_uses_vector_search_and_maps_chunks_to_documents(backend, server):
    server.search_result = {"fatf_recs_chunk_0": "text one", "fincen_mules_chunk_12": "text two"}

    chunks = backend.retrieve("KB", "money mules?", ACCESS, top_k=3)

    request = server.requests[0]
    assert request.url.path == "/KB/graphrag/search"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    body = json.loads(request.content)
    assert body["method"] == "similarity"
    assert body["method_params"] == {"index": "DocumentChunk", "top_k": 3, "withHyDE": False}
    assert [(c.chunk_id, c.doc_id, c.text) for c in chunks] == [
        ("fatf_recs_chunk_0", "fatf_recs", "text one"),
        ("fincen_mules_chunk_12", "fincen_mules", "text two"),
    ]


def test_http_errors_are_raised_with_context():
    failing = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500, text="boom")))
    backend = CurrentGraphragBackend("http://graphrag:8000", TOKEN, http=failing)
    with pytest.raises(GraphragError, match="HTTP 500: boom") as raised:
        backend.ensure_graph("KB")
    assert raised.value.status_code == 500
