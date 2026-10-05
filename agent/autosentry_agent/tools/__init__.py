"""The investigation agent's tools: installed queries, guarded ad hoc GSQL, knowledge search."""

from ..ingestion.backend import IngestionBackend
from ..ingestion.corpus import Corpus
from ..mcp.transport import GraphDataMcpClient
from .adhoc import graph_query_tool
from .base import ToolSpec
from .investigation import investigation_tools
from .knowledge import knowledge_tool


def build_tools(
    client: GraphDataMcpClient,
    *,
    knowledge_backend: IngestionBackend | None = None,
    corpus: Corpus | None = None,
) -> list[ToolSpec]:
    """Every read-only investigation tool; knowledge search only when its backend is given."""
    tools = [*investigation_tools(client), graph_query_tool(client)]
    if knowledge_backend is not None and corpus is not None:
        tools.append(knowledge_tool(knowledge_backend, corpus))
    return tools


__all__ = ["ToolSpec", "build_tools"]
