"""IngestionBackend adapter for graphrag's current (pre-Track-A) API.

Today's pipeline differs from the interface in ways this adapter absorbs:
- Processing is one graph-wide batch (ingest the upload folder, then a full
  rebuild), not per document. `commit` runs that batch; per-document status
  is derived from the per-file ingest results plus the graph-wide rebuild
  status and embedding coverage.
- The Document vertex has no metadata or ACL fields yet, so metadata is
  accepted and dropped, and retrieval does not enforce the access context.
- There is no quarantine or server-side dedup; QUARANTINED is never reported.
- Upload responses aren't trusted on their own: every upload is checked
  against the server's file listing, since an "already exists" conflict
  can hide a file truncated by an earlier crash.
- migration/status (the embedding-coverage report) fails server-side for
  API-token callers; when it errors, a completed rebuild counts as READY
  with coverage unverified, and the driver's verification questions are
  the check that the content is actually retrievable.
"""

import base64
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .model import (
    AccessContext,
    DocumentState,
    DocumentSubmission,
    IngestionStatus,
    RetrievedChunk,
)

logger = logging.getLogger(__name__)

# graphrag's UI routes accept an API token as Basic auth under this username.
_TOKEN_SENTINEL = "__graphrag_token__"
_CHUNK_SEPARATOR = "_chunk_"


class GraphragError(RuntimeError):
    """graphrag rejected a request or reported a failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class _GraphProgress:
    pending: set[str] = field(default_factory=set)
    ingest_failures: dict[str, str] = field(default_factory=dict)
    ingested: set[str] = field(default_factory=set)
    # ECC's last finish timestamps from before we triggered our rebuild.
    # Compared for change rather than against our own clock, since the
    # container clock can drift from the host's.
    baseline_completed_at: Any = None
    baseline_failed_at: Any = None
    saw_running: bool = False


class CurrentGraphragBackend:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        *,
        http: httpx.Client | None = None,
        extract_images: bool = False,
        init_poll_interval: float = 10.0,
        init_timeout: float = 1800.0,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=httpx.Timeout(180.0))
        basic = base64.b64encode(f"{_TOKEN_SENTINEL}:{api_token}".encode()).decode()
        self._ui_auth = {"Authorization": f"Basic {basic}"}
        self._api_auth = {"Authorization": f"Bearer {api_token}"}
        self._extract_images = extract_images
        self._init_poll_interval = init_poll_interval
        self._init_timeout = init_timeout
        self._clock = clock
        self._sleep = sleep
        self._progress: dict[str, _GraphProgress] = {}
        self._warned_metadata = False
        self._warned_acl = False
        self._warned_coverage = False

    def ensure_graph(self, graph: str) -> None:
        created = self._ui("POST", f"/{graph}/create_graph")
        if created.get("status") != "success" and "already exists" not in str(
            created.get("message", "")
        ):
            raise GraphragError(f"create_graph failed for {graph}: {created}")

        eligibility = self._ui("GET", f"/{graph}/check_init_eligibility")
        state = eligibility.get("state")
        if state == "empty":
            self._ui("POST", f"/{graph}/initialize_graph", json={})
            self._wait_for_init(graph)
        elif state != "structural_present":
            raise GraphragError(
                f"{graph} has pre-existing non-GraphRAG types; refusing to "
                f"initialize over them: {eligibility}"
            )

        if not self._extract_images:
            self._ui(
                "POST",
                "/config/graphrag",
                json={"scope": "graph", "graphname": graph, "extract_images": False},
            )

    def submit(self, graph: str, doc: DocumentSubmission) -> DocumentState:
        if not self._warned_metadata:
            logger.warning(
                "graphrag's current Document schema has no metadata fields; "
                "doc_type/source/jurisdiction/classification/allowed_roles are "
                "not persisted until graphrag's schema migration lands."
            )
            self._warned_metadata = True

        filename = f"{doc.doc_id}{doc.path.suffix.lower()}"
        local_size = doc.path.stat().st_size
        result = self._upload(graph, filename, doc.path, overwrite=False)
        if result.get("status") == "conflict" and self._remote_size(graph, filename) != local_size:
            logger.warning(
                "%s already exists on graphrag with a different size; re-uploading", filename
            )
            result = self._upload(graph, filename, doc.path, overwrite=True)
        if result.get("status") not in ("success", "conflict"):
            raise GraphragError(f"upload of {filename} failed: {result}")
        remote_size = self._remote_size(graph, filename)
        if remote_size != local_size:
            raise GraphragError(
                f"{filename} is {remote_size} bytes on graphrag, expected {local_size}"
            )
        self._graph(graph).pending.add(doc.doc_id)
        return DocumentState(doc.doc_id, IngestionStatus.UPLOADED)

    def commit(self, graph: str) -> None:
        progress = self._graph(graph)
        if not progress.pending:
            return

        job = self._ui(
            "POST",
            f"/{graph}/create_ingest",
            json={
                "data_source": "server",
                "data_source_config": {"data_path": f"uploads/{graph}"},
                "loader_config": {},
                "file_format": "multi",
            },
        )
        result = self._ui(
            "POST",
            f"/{graph}/ingest",
            json={
                "load_job_id": job["load_job_id"],
                "data_source_id": job["data_source_id"],
                "file_path": job["data_path"],
            },
        )
        by_doc = {
            str(entry.get("jsonl_file", "")).removesuffix(".jsonl"): entry
            for entry in result.get("ingested_files", [])
        }
        for doc_id in progress.pending:
            entry = by_doc.get(doc_id)
            if entry is None:
                progress.ingest_failures[doc_id] = "missing from graphrag's ingest results"
            elif entry.get("status") != "success":
                progress.ingest_failures[doc_id] = str(entry.get("error", "ingest failed"))
            else:
                progress.ingested.add(doc_id)
        progress.pending.clear()

        baseline = self._ui("GET", f"/{graph}/rebuild_status")
        progress.baseline_completed_at = baseline.get("completed_at")
        progress.baseline_failed_at = baseline.get("failed_at")
        progress.saw_running = False
        self._ui("POST", f"/{graph}/rebuild_graph")

    def status(self, graph: str, doc_ids: Sequence[str]) -> list[DocumentState]:
        progress = self._graph(graph)
        rebuild: DocumentState | None = None
        states = []
        for doc_id in doc_ids:
            if doc_id in progress.ingest_failures:
                states.append(
                    DocumentState(doc_id, IngestionStatus.FAILED, progress.ingest_failures[doc_id])
                )
            elif doc_id not in progress.ingested:
                states.append(DocumentState(doc_id, IngestionStatus.UPLOADED))
            else:
                if rebuild is None:
                    rebuild = self._rebuild_state(graph, progress)
                states.append(DocumentState(doc_id, rebuild.status, rebuild.detail))
        return states

    def retrieve(
        self, graph: str, question: str, access: AccessContext, top_k: int
    ) -> list[RetrievedChunk]:
        if not self._warned_acl:
            logger.warning(
                "graphrag's current retrieval has no ACL; the access context "
                "is not enforced until graphrag's ACL filter lands."
            )
            self._warned_acl = True

        response = self._http.post(
            f"{self._base_url}/{graph}/graphrag/search",
            headers=self._api_auth,
            json={
                "question": question,
                "method": "similarity",
                "method_params": {"index": "DocumentChunk", "top_k": top_k, "withHyDE": False},
            },
        )
        body = self._json(response, "search")
        retrieved: dict[str, str] = body[0].get("final_retrieval", {}) if body else {}
        return [
            RetrievedChunk(chunk_id, chunk_id.rsplit(_CHUNK_SEPARATOR, 1)[0], text)
            for chunk_id, text in retrieved.items()
        ]

    def _rebuild_state(self, graph: str, progress: _GraphProgress) -> DocumentState:
        rebuild = self._ui("GET", f"/{graph}/rebuild_status")
        status = rebuild.get("status")
        if rebuild.get("is_running"):
            progress.saw_running = True
            stage = rebuild.get("stage") or "rebuild running"
            return DocumentState("", IngestionStatus.EMBEDDING, str(stage))
        if status == "failed" and rebuild.get("failed_at") != progress.baseline_failed_at:
            return DocumentState("", IngestionStatus.FAILED, str(rebuild.get("error", "rebuild failed")))
        finished = status == "completed" and rebuild.get("completed_at") != progress.baseline_completed_at
        # ECC drops a finished task's status after a few minutes; if we already
        # watched our run in progress, a later idle means it finished.
        if not finished and not progress.saw_running:
            return DocumentState("", IngestionStatus.QUEUED, "waiting for rebuild to start")

        try:
            report = self._ui("GET", f"/{graph}/migration/status")
        except GraphragError as err:
            if err.status_code is None or err.status_code < 500:
                raise
            if not self._warned_coverage:
                logger.warning(
                    "graphrag's migration/status failed (%s); embedding coverage "
                    "can't be checked, relying on the verification questions",
                    err,
                )
                self._warned_coverage = True
            return DocumentState(
                "", IngestionStatus.READY, "rebuild completed; embedding coverage unverified"
            )
        coverage = report.get("embeddings", {}).get("by_type", {}).get("DocumentChunk")
        if not coverage or not coverage.get("total"):
            return DocumentState("", IngestionStatus.FAILED, "rebuild completed but produced no chunks")
        if coverage.get("missing"):
            return DocumentState(
                "",
                IngestionStatus.FAILED,
                f"{coverage['missing']} of {coverage['total']} chunks have no embedding",
            )
        return DocumentState("", IngestionStatus.READY)

    def _wait_for_init(self, graph: str) -> None:
        deadline = self._clock() + self._init_timeout
        while True:
            state = self._ui("GET", f"/{graph}/initialize_status")
            if state.get("state") == "completed":
                return
            if state.get("state") == "error":
                raise GraphragError(f"initialize_graph failed for {graph}: {state.get('error')}")
            if self._clock() >= deadline:
                raise GraphragError(f"initialize_graph for {graph} did not finish in time")
            self._sleep(self._init_poll_interval)

    def _upload(self, graph: str, filename: str, path: Path, *, overwrite: bool) -> Any:
        with path.open("rb") as fh:
            return self._ui(
                "POST",
                f"/{graph}/uploads",
                params={"overwrite": "true" if overwrite else "false"},
                files={"files": (filename, fh)},
            )

    def _remote_size(self, graph: str, filename: str) -> int | None:
        listing = self._ui("GET", f"/{graph}/uploads/list")
        for entry in listing.get("files", []):
            if entry.get("filename") == filename:
                return int(entry.get("size", 0))
        return None

    def _graph(self, graph: str) -> _GraphProgress:
        return self._progress.setdefault(graph, _GraphProgress())

    def _ui(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(
            method, f"{self._base_url}/ui{path}", headers=self._ui_auth, **kwargs
        )
        return self._json(response, path)

    @staticmethod
    def _json(response: httpx.Response, what: str) -> Any:
        if response.status_code >= 400:
            raise GraphragError(
                f"{what} returned HTTP {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
            )
        return response.json()
