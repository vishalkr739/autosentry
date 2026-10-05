"""GraphDataMcpClient against a real MCP server over streamable HTTP.

The server is the mcp SDK's own FastMCP, served in-process through
httpx's ASGI transport, so the full protocol runs (initialize, list_tools,
call_tool over HTTP). Its tools answer with envelopes built by
tigergraph-mcp's real response formatter, so the parsing can't drift from
what the real server sends.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import TextContent
from tigergraph_mcp.response_formatter import format_error, format_success

from autosentry_agent.config import GraphDataSettings
from autosentry_agent.mcp.transport import GraphDataMcpClient, GraphDataToolError
from autosentry_agent.secrets.base import SecretsProvider

from .tigergraph_mcp_fixtures import get_graph_schema_data

pytestmark = pytest.mark.asyncio

HOST = "https://tg-workspace.i.tgcloud.io"


class FakeSecrets(SecretsProvider):
    def __init__(self, **secrets: str) -> None:
        self.secrets = secrets

    def get_secret(self, name: str) -> str:
        return self.secrets[name]

    def get_secret_version(self, name: str) -> str | None:
        return None


class FakeTigergraphMcp:
    """Just the tools these tests call, registered under tigergraph-mcp's names."""

    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []
        self.sessions = 0
        self.server = FastMCP("fake-tigergraph-mcp", json_response=True)

        @self.server.tool(name="tigergraph__get_graph_schema")
        def get_graph_schema(graph_name: str, ctx: Context) -> list[TextContent]:
            self._record(ctx)
            return list(
                format_success(
                    operation="get_graph_schema",
                    summary=f"Schema retrieved for graph '{graph_name}'",
                    data=get_graph_schema_data(graph_name=graph_name),
                )
            )

        @self.server.tool(name="tigergraph__gsql")
        def gsql(command: str, ctx: Context) -> list[TextContent]:
            self._record(ctx)
            if command == "FAIL":
                return list(format_error(operation="gsql", error=Exception('Encountered "FAIL"')))
            return list(format_success(operation="gsql", summary="ok", data={"result": command}))

        @self.server.tool(name="tigergraph__plain_text")
        def plain_text(ctx: Context) -> str:
            self._record(ctx)
            return "**no envelope here**"

    def _record(self, ctx: Context) -> None:
        request = ctx.request_context.request
        assert request is not None
        self.headers.append({k.lower(): v for k, v in request.headers.items()})


@pytest_asyncio.fixture
async def fake() -> AsyncIterator[FakeTigergraphMcp]:
    fake = FakeTigergraphMcp()
    fake.app = fake.server.streamable_http_app()  # type: ignore[attr-defined]
    # The session manager's task group must be entered and exited by one
    # task, and pytest-asyncio may tear a fixture down in another, so it
    # runs in a task of its own.
    started, stop = asyncio.Event(), asyncio.Event()

    async def serve() -> None:
        async with fake.server.session_manager.run():
            started.set()
            await stop.wait()

    server_task = asyncio.create_task(serve())
    await started.wait()
    yield fake
    stop.set()
    await server_task


def make_client(fake: FakeTigergraphMcp, secrets: SecretsProvider, **settings: Any) -> GraphDataMcpClient:
    config = GraphDataSettings(
        graph_data_mcp_url="http://127.0.0.1:8010",
        tg_host=HOST,
        tg_graphname="AutosentrySandbox",
        **settings,
    )
    return GraphDataMcpClient(
        config, secrets, http_transport=httpx.ASGITransport(app=fake.app)  # type: ignore[attr-defined]
    )


@pytest.fixture
def token_secrets() -> FakeSecrets:
    return FakeSecrets(TG_JWT_TOKEN="jwt-1")


async def test_list_tools_discovers_the_servers_tools(fake, token_secrets):
    tools = await make_client(fake, token_secrets).list_tools()
    assert {t["name"] for t in tools} >= {"tigergraph__get_graph_schema", "tigergraph__gsql"}
    assert all("input_schema" in t for t in tools)


async def test_call_returns_the_envelope_as_ok_summary_data(fake, token_secrets):
    result = await make_client(fake, token_secrets).call(
        "tigergraph__get_graph_schema", {"graph_name": "AutosentrySandbox"}
    )
    assert result["ok"] is True
    assert result["error"] is None
    assert "Schema retrieved" in result["summary"]
    assert result["data"] == get_graph_schema_data(graph_name="AutosentrySandbox")


async def test_tool_failure_comes_back_as_not_ok(fake, token_secrets):
    result = await make_client(fake, token_secrets).call("tigergraph__gsql", {"command": "FAIL"})
    assert result["ok"] is False
    assert 'Encountered "FAIL"' in result["error"]


async def test_call_data_returns_data_and_raises_on_failure(fake, token_secrets):
    client = make_client(fake, token_secrets)
    assert await client.call_data("tigergraph__gsql", {"command": "LS"}) == {"result": "LS"}
    with pytest.raises(GraphDataToolError, match=r'tigergraph__gsql failed: .*Encountered "FAIL"'):
        await client.call_data("tigergraph__gsql", {"command": "FAIL"})


async def test_sdk_level_error_comes_back_as_not_ok(fake, token_secrets):
    # Missing required argument: the SDK rejects it with isError and plain text.
    result = await make_client(fake, token_secrets).call("tigergraph__gsql", {})
    assert result["ok"] is False
    assert "command" in result["error"]


async def test_a_result_with_no_envelope_raises(fake, token_secrets):
    with pytest.raises(GraphDataToolError, match="no JSON envelope"):
        await make_client(fake, token_secrets).call("tigergraph__plain_text", {})


async def test_token_auth_headers(fake, token_secrets):
    await make_client(fake, token_secrets, tg_version="4.2.1").call(
        "tigergraph__gsql", {"command": "LS"}
    )
    headers = fake.headers[-1]
    assert headers["x-tg-host"] == HOST
    assert headers["x-tg-jwt-token"] == "jwt-1"
    assert headers["x-tg-tgcloud"] == "true"
    assert headers["x-tg-version"] == "4.2.1"
    assert "x-tg-password" not in headers
    assert "x-tg-gsql-prefix" not in headers


async def test_proxy_prefixes_are_sent_when_configured(fake, token_secrets):
    await make_client(
        fake, token_secrets, tg_gsql_prefix="/api/gsql-server", tg_restpp_prefix="/api/restpp"
    ).call("tigergraph__gsql", {"command": "LS"})
    assert fake.headers[-1]["x-tg-gsql-prefix"] == "/api/gsql-server"
    assert fake.headers[-1]["x-tg-restpp-prefix"] == "/api/restpp"


async def test_non_cloud_host_is_not_marked_tgcloud(fake, token_secrets):
    client = make_client(fake, token_secrets)
    client._settings.tg_host = "http://localhost:14240"
    await client.call("tigergraph__gsql", {"command": "LS"})
    assert "x-tg-tgcloud" not in fake.headers[-1]


async def test_token_is_read_fresh_for_each_session(fake, token_secrets):
    client = make_client(fake, token_secrets)
    await client.call("tigergraph__gsql", {"command": "LS"})
    token_secrets.secrets["TG_JWT_TOKEN"] = "jwt-2"  # rotated
    await client.call("tigergraph__gsql", {"command": "LS"})
    assert [h["x-tg-jwt-token"] for h in fake.headers] == ["jwt-1", "jwt-2"]


async def test_password_fallback_outside_prod(fake):
    secrets = FakeSecrets(TG_PASSWORD="pw")
    await make_client(fake, secrets, tg_username="tigergraph").call(
        "tigergraph__gsql", {"command": "LS"}
    )
    assert fake.headers[-1]["x-tg-username"] == "tigergraph"
    assert fake.headers[-1]["x-tg-password"] == "pw"
    assert "x-tg-jwt-token" not in fake.headers[-1]


async def test_password_fallback_is_refused_in_prod(fake):
    client = make_client(fake, FakeSecrets(TG_PASSWORD="pw"), tg_username="tigergraph", autosentry_env="prod")
    with pytest.raises(GraphDataToolError, match="TG_JWT_TOKEN"):
        await client.call("tigergraph__gsql", {"command": "LS"})
    assert fake.headers == []


async def test_no_credential_at_all_is_refused(fake):
    with pytest.raises(GraphDataToolError, match="TG_JWT_TOKEN"):
        await make_client(fake, FakeSecrets()).call("tigergraph__gsql", {"command": "LS"})


async def test_connected_reuses_one_session_for_the_block(fake, token_secrets):
    client = make_client(fake, token_secrets)
    async with client.connected():
        await client.call("tigergraph__gsql", {"command": "a"})
        await client.call("tigergraph__gsql", {"command": "b"})
    session_ids = {h.get("mcp-session-id") for h in fake.headers}
    assert len(fake.headers) == 2
    assert len(session_ids) == 1 and None not in session_ids


async def test_unreachable_server_raises_a_typed_error(token_secrets):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    config = GraphDataSettings(graph_data_mcp_url="http://127.0.0.1:8010", tg_host=HOST)
    client = GraphDataMcpClient(config, token_secrets, http_transport=httpx.MockTransport(refuse))
    with pytest.raises(GraphDataToolError, match="could not reach tigergraph-mcp"):
        await client.call("tigergraph__gsql", {"command": "LS"})


async def test_is_ready_checks_the_configured_graphs_schema(fake, token_secrets):
    client = make_client(fake, token_secrets)
    assert await client.is_ready() is True


async def test_is_ready_is_false_when_unreachable(token_secrets):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    config = GraphDataSettings(graph_data_mcp_url="http://127.0.0.1:8010", tg_host=HOST)
    client = GraphDataMcpClient(config, token_secrets, http_transport=httpx.MockTransport(refuse))
    assert await client.is_ready() is False


async def test_is_ready_result_is_cached_briefly(fake, token_secrets):
    now = [1000.0]
    client = make_client(fake, token_secrets)
    client._clock = lambda: now[0]
    assert await client.is_ready() is True
    assert await client.is_ready() is True
    assert len(fake.headers) == 1  # second answer came from the cache
    now[0] += 60
    assert await client.is_ready() is True
    assert len(fake.headers) == 2
