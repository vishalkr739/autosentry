"""Regulatory knowledge search: FinCEN/OFAC guidance as citable passages.

Goes through the ingestion backend's `retrieve`, the interface built around
graphrag's planned pipeline (with the access context its ACL filter will
enforce), so switching to that pipeline needs no change here. Each hit
becomes a citation carrying the source document's title and URL from the
corpus file, so an answer can name what it relies on.
"""

import asyncio

from pydantic import BaseModel, ConfigDict, Field

from ..ingestion.backend import IngestionBackend
from ..ingestion.corpus import Corpus
from .base import ToolSpec
from .results import Citation, Evidence, InvestigationResult, failure


class SearchKnowledgeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3, max_length=500, description="What to look up in the guidance")
    top_k: int = Field(5, ge=1, le=10, description="How many passages to return")


def knowledge_tool(backend: IngestionBackend, corpus: Corpus) -> ToolSpec:
    documents = {doc.doc_id: doc for doc in corpus.documents}

    async def search_knowledge(params: SearchKnowledgeInput) -> InvestigationResult:
        try:
            chunks = await asyncio.to_thread(
                backend.retrieve, corpus.graph, params.question, corpus.access, params.top_k
            )
        except Exception as exc:  # the knowledge service is optional to an investigation
            return failure("knowledge search could not run", str(exc))

        citations = []
        for chunk in chunks:
            doc = documents.get(chunk.doc_id)
            citations.append(Citation(
                doc_id=chunk.doc_id,
                chunk_id=chunk.chunk_id,
                title=doc.title if doc else chunk.doc_id,
                url=doc.url if doc else "",
                text=chunk.text,
            ))
        sources = sorted({c["title"] for c in citations})
        summary = (
            f"{len(citations)} passage(s) from: {'; '.join(sources)}" if citations
            else "no passages found in the regulatory guidance"
        )
        return InvestigationResult(
            ok=True,
            summary=summary,
            data={"question": params.question},
            error=None,
            evidence=[Evidence(type="DocumentChunk", id=c["chunk_id"]) for c in citations],
            citations=citations,
        )

    return ToolSpec(
        "search_knowledge",
        "Search the regulatory guidance corpus (FinCEN advisories and SAR guidance, OFAC sanctions "
        "compliance) for passages relevant to a question, e.g. what a typology looks like or what a "
        "SAR must contain. Returns passages with their source document's title and URL to cite.",
        SearchKnowledgeInput,
        search_knowledge,
    )
