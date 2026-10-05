# agent/tests/integration/test_graph_data_live.py
# Requires a running tigergraph-mcp (streamable HTTP) at $GRAPH_DATA_MCP_URL
# and a reachable TigerGraph at $TG_HOST with $TG_JWT_TOKEN; skipped otherwise.
# $TG_GRAPHNAME must name a graph that exists there.
import os

import pytest
from tigergraph_mcp.tool_names import TigerGraphToolName

from autosentry_agent.config import GraphDataSettings, get_secrets_provider
from autosentry_agent.mcp.transport import GraphDataMcpClient

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not all(os.environ.get(v) for v in ("GRAPH_DATA_MCP_URL", "TG_HOST", "TG_JWT_TOKEN")),
        reason="needs GRAPH_DATA_MCP_URL, TG_HOST and TG_JWT_TOKEN for a live tigergraph-mcp",
    ),
]


@pytest.fixture
def client() -> GraphDataMcpClient:
    return GraphDataMcpClient(GraphDataSettings(), get_secrets_provider())


async def test_discovers_tigergraph_tools(client):
    names = {tool["name"] for tool in await client.list_tools()}
    assert TigerGraphToolName.GET_GRAPH_SCHEMA.value in names


async def test_reads_the_configured_graphs_schema_in_one_turn(client):
    async with client.connected():
        data = await client.call_data(
            TigerGraphToolName.GET_GRAPH_SCHEMA.value, {"graph_name": client.graph_name}
        )
        count = await client.call(
            TigerGraphToolName.GET_VERTEX_COUNT.value, {"graph_name": client.graph_name}
        )
    assert data["graph_name"] == client.graph_name
    assert data["schema"]["VertexTypes"]
    assert count["ok"] is True


async def test_is_ready(client):
    assert await client.is_ready() is True
