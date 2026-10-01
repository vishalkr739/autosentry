from collections.abc import Sequence
from typing import Protocol

from .model import AccessContext, DocumentState, DocumentSubmission, RetrievedChunk


class IngestionBackend(Protocol):
    """Document ingestion and retrieval against a knowledge graph.

    The shape follows graphrag's planned per-document pipeline (submit with
    metadata, poll per-document status, ACL-scoped retrieval). Adapters for
    today's graphrag API map its coarser behavior onto this interface.
    """

    def ensure_graph(self, graph: str) -> None:
        """Create the graph and its knowledge-graph schema if absent."""

    def submit(self, graph: str, doc: DocumentSubmission) -> DocumentState:
        """Hand one document to the pipeline."""

    def commit(self, graph: str) -> None:
        """Mark the end of a submission batch.

        A queue-driven pipeline processes each submission on its own and
        treats this as a no-op; a batch pipeline starts processing here.
        """

    def status(self, graph: str, doc_ids: Sequence[str]) -> list[DocumentState]:
        """Current pipeline state for each document, in the given order."""

    def retrieve(
        self, graph: str, question: str, access: AccessContext, top_k: int
    ) -> list[RetrievedChunk]:
        """Chunks relevant to the question that the access context may see."""
