from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from autosentry_agent.ingestion.corpus import load_corpus
from autosentry_agent.ingestion.model import RetrievedChunk
from autosentry_agent.query_library import (
    QueryDefinition,
    QueryInstallError,
    install_queries,
    installed_queries,
    load_library,
)
from autosentry_agent.tools.knowledge import knowledge_tool

CORPUS = load_corpus(
    Path(__file__).resolve().parents[2] / "autosentry_agent" / "ingestion" / "corpora" / "regulatory.toml"
)


class FakeBackend:
    def __init__(self, chunks: list[RetrievedChunk] | None = None, error: Exception | None = None) -> None:
        self.chunks = chunks or []
        self.error = error
        self.calls: list[tuple] = []

    def retrieve(self, graph, question, access, top_k):
        self.calls.append((graph, question, access, top_k))
        if self.error:
            raise self.error
        return self.chunks


@pytest.mark.asyncio
async def test_knowledge_hits_become_citations_with_title_and_url():
    backend = FakeBackend([
        RetrievedChunk("fincen_sar_narrative_guidance_chunk_3", "fincen_sar_narrative_guidance", "Who? What? When?"),
        RetrievedChunk("unknown_doc_chunk_0", "unknown_doc", "orphan passage"),
    ])
    result = await knowledge_tool(backend, CORPUS).invoke({"question": "SAR narrative elements", "top_k": 2})

    assert result["ok"] is True
    sar = result["citations"][0]
    assert sar["title"] == "Guidance on Preparing a Complete & Sufficient SAR Narrative"
    assert sar["url"].startswith("https://www.fincen.gov/")
    assert result["citations"][1]["title"] == "unknown_doc" and result["citations"][1]["url"] == ""
    assert result["evidence"][0] == {"type": "DocumentChunk", "id": "fincen_sar_narrative_guidance_chunk_3"}
    assert backend.calls == [(CORPUS.graph, "SAR narrative elements", CORPUS.access, 2)]


@pytest.mark.asyncio
async def test_knowledge_search_failure_is_not_ok_rather_than_raised():
    result = await knowledge_tool(FakeBackend(error=RuntimeError("graphrag down")), CORPUS).invoke({"question": "anything"})
    assert result["ok"] is False and "graphrag down" in result["error"]


def test_the_library_holds_the_four_investigation_queries_for_the_sandbox():
    queries = load_library()
    assert {q.name for q in queries} == {
        "find_mule_candidates", "trace_fund_transfer_chain", "find_shared_infrastructure", "find_structuring_pattern",
    }
    assert {q.graph for q in queries} == {"AutosentrySandbox"}


def test_installed_queries_reads_gsql_ls():
    listing = (
        "Queries:\n"
        "  - find_mule_candidates(int top_k, int min_score) (installed v2)\n"
        "  - trace_fund_transfer_chain(vertex<Account> account) (draft)\n"
        "  - find_structuring_pattern(double threshold) (installed v2)\n"
    )
    assert installed_queries(listing) == {"find_mule_candidates", "find_structuring_pattern"}


class FakeGsql:
    graph_name = "AutosentrySandbox"

    def __init__(self, ls: str) -> None:
        self.ls = ls
        self.commands: list[str] = []

    @asynccontextmanager
    async def connected(self):
        yield

    async def call_data(self, tool: str, arguments: dict[str, Any]) -> Any:
        assert tool == "tigergraph__gsql"
        self.commands.append(arguments["command"])
        if arguments["command"].endswith("\nLS"):
            return {"result": self.ls}
        return {"result": "ok"}


QUERIES = [
    QueryDefinition("q_one", "AutosentrySandbox", "CREATE OR REPLACE QUERY q_one() FOR GRAPH AutosentrySandbox {}"),
    QueryDefinition("q_two", "AutosentrySandbox", "CREATE OR REPLACE QUERY q_two() FOR GRAPH AutosentrySandbox {}"),
]


@pytest.mark.asyncio
async def test_install_creates_each_then_installs_once_then_checks_ls():
    fake = FakeGsql("  - q_one() (installed v2)\n  - q_two() (installed v2)\n")
    assert await install_queries(fake, QUERIES) == ["q_one", "q_two"]  # type: ignore[arg-type]
    assert fake.commands[0].startswith("USE GRAPH AutosentrySandbox\nCREATE OR REPLACE QUERY q_one")
    assert fake.commands[2] == "USE GRAPH AutosentrySandbox\nINSTALL QUERY q_one, q_two"
    assert fake.commands[3] == "USE GRAPH AutosentrySandbox\nLS"


@pytest.mark.asyncio
async def test_install_fails_when_ls_does_not_show_a_query_installed():
    fake = FakeGsql("  - q_one() (installed v2)\n  - q_two() (draft)\n")
    with pytest.raises(QueryInstallError, match=r"not installed after INSTALL QUERY: \['q_two'\]"):
        await install_queries(fake, QUERIES)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_install_refuses_queries_written_for_another_graph():
    other = [QueryDefinition("q", "OtherGraph", "CREATE OR REPLACE QUERY q() FOR GRAPH OtherGraph {}")]
    with pytest.raises(QueryInstallError, match="FOR GRAPH other than AutosentrySandbox"):
        await install_queries(FakeGsql(""), other)  # type: ignore[arg-type]
