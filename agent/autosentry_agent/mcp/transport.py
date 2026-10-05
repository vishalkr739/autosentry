"""tigergraph-mcp over streamable HTTP, authenticated per session by headers.

Mirrors savanna-agent's `tools/mcp_transport.py` and
`tools/graph_data_mcp_client.py` so the two agents can converge: tools are
discovered from the server, never hardcoded; each call opens its own MCP
session unless a turn holds one open with `connected()`; every result comes
back as `{ok, summary, data, error}`; and the TigerGraph identity travels in
`X-TG-*` headers that tigergraph-mcp's HTTP middleware reads per session, so
a rotated token takes effect on the next session.
"""

import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, TypedDict

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent
from tigergraph_mcp.tool_names import TigerGraphToolName

from ..config import GraphDataSettings
from ..secrets.base import SecretsProvider

logger = logging.getLogger(__name__)

# tigergraph-mcp's response formatter renders every result as one text block
# that opens with the structured envelope in a fenced JSON block, followed by
# markdown meant for an LLM. Only the fenced block is machine-readable.
_ENVELOPE_OPEN = "```json\n"
_ENVELOPE_CLOSE = "\n```"
_CLOUD_HOST_SUFFIXES = (".tgcloud.io", ".tgcloud.com", ".tgcloud-dev.com")


class GraphDataToolError(RuntimeError):
    """tigergraph-mcp could not be reached or authenticated to, a result
    could not be read, or (from `call_data`) a tool reported failure."""


class ToolResult(TypedDict):
    ok: bool
    summary: str
    data: Any
    error: str | None


def parse_result(tool: str, result: CallToolResult) -> ToolResult:
    """Turn a tigergraph-mcp `CallToolResult` into `{ok, summary, data, error}`.

    tigergraph-mcp never sets `isError`; its envelope's `success` is the
    tool-level failure signal. The SDK's own failures (for example input
    validation) do set `isError`, with plain text and no envelope.
    """
    first = result.content[0] if result.content else None
    text = first.text if isinstance(first, TextContent) else None

    if result.isError:
        message = text or "unknown error"
        return ToolResult(ok=False, summary=message, data=None, error=message)
    if text is None or not text.startswith(_ENVELOPE_OPEN):
        raise GraphDataToolError(f"{tool} returned a result with no JSON envelope: {text!r}")

    try:
        envelope = json.loads(text[len(_ENVELOPE_OPEN):].split(_ENVELOPE_CLOSE, 1)[0])
    except json.JSONDecodeError as exc:
        raise GraphDataToolError(f"{tool} returned a malformed JSON envelope: {exc}") from exc
    if not isinstance(envelope, dict):
        raise GraphDataToolError(f"{tool} returned an envelope that is not an object")

    ok = envelope.get("success") is True
    summary = str(envelope.get("summary") or "")
    error = None if ok else str(envelope.get("error") or summary or "unknown error")
    return ToolResult(ok=ok, summary=summary, data=envelope.get("data"), error=error)


class GraphDataMcpClient:
    """tigergraph-mcp, reached under Autosentry's TigerGraph credential."""

    def __init__(
        self,
        settings: GraphDataSettings,
        secrets: SecretsProvider,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._secrets = secrets
        self._http_transport = http_transport
        self._session: ClientSession | None = None
        self._clock: Callable[[], float] = time.monotonic
        self._ready_at: float | None = None
        self._ready_value = False

    @property
    def graph_name(self) -> str:
        return self._settings.tg_graphname

    @property
    def _url(self) -> str:
        return f"{self._settings.graph_data_mcp_url.rstrip('/')}/mcp/"

    def _headers(self) -> dict[str, str]:
        settings = self._settings
        headers = {"X-TG-Host": settings.tg_host}
        try:
            headers["X-TG-Jwt-Token"] = self._secrets.get_secret("TG_JWT_TOKEN")
        except KeyError:
            if settings.autosentry_env == "prod" or not settings.tg_username:
                raise GraphDataToolError(
                    "no TigerGraph credential: set TG_JWT_TOKEN"
                    + ("" if settings.autosentry_env == "prod" else " (or TG_USERNAME and TG_PASSWORD)")
                ) from None
            try:
                password = self._secrets.get_secret("TG_PASSWORD")
            except KeyError:
                raise GraphDataToolError(
                    "no TigerGraph credential: set TG_JWT_TOKEN, or TG_PASSWORD with TG_USERNAME"
                ) from None
            headers["X-TG-Username"] = settings.tg_username
            headers["X-TG-Password"] = password

        host = httpx.URL(settings.tg_host).host
        if host.endswith(_CLOUD_HOST_SUFFIXES):
            headers["X-TG-Tgcloud"] = "true"
        optional = {
            "X-TG-Version": settings.tg_version,
            "X-TG-Gsql-Prefix": settings.tg_gsql_prefix,
            "X-TG-Restpp-Prefix": settings.tg_restpp_prefix,
        }
        headers.update({name: value for name, value in optional.items() if value})
        return headers

    def _http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self._headers(),
            timeout=self._settings.graph_data_mcp_timeout_seconds,
            follow_redirects=True,
            transport=self._http_transport,
        )

    @asynccontextmanager
    async def connected(self) -> AsyncIterator[None]:
        """Hold one session for every call in the block (one turn); when it
        cannot open, calls inside fall back to a session each."""
        if self._session is not None:
            yield
            return
        stack = AsyncExitStack()
        try:
            http_client = await stack.enter_async_context(self._http_client())
            read, write, _ = await stack.enter_async_context(
                streamable_http_client(self._url, http_client=http_client)
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except Exception as exc:
            logger.warning("tigergraph-mcp: no shared session, calling one by one: %s", exc)
            await stack.aclose()
            yield
            return
        self._session = session
        try:
            yield
        finally:
            self._session = None
            await stack.aclose()

    async def _in_session(self, action: Callable[[ClientSession], Awaitable[Any]]) -> Any:
        if self._session is not None:
            return await action(self._session)
        async with (
            self._http_client() as http_client,
            streamable_http_client(self._url, http_client=http_client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return await action(session)

    async def _reach(self, action: Callable[[ClientSession], Awaitable[Any]]) -> Any:
        try:
            return await self._in_session(action)
        except GraphDataToolError:
            raise
        except Exception as exc:
            raise GraphDataToolError(f"could not reach tigergraph-mcp at {self._url}: {exc}") from exc

    async def list_tools(self) -> list[dict[str, Any]]:
        """What the server exposes. Discovered, never hardcoded."""
        listed = await self._reach(lambda session: session.list_tools())
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": dict(tool.inputSchema or {}),
            }
            for tool in listed.tools
        ]

    async def call(self, tool: str, arguments: dict[str, Any]) -> ToolResult:
        """Invoke one tool and return its `{ok, summary, data, error}`."""
        result = await self._reach(lambda session: session.call_tool(tool, arguments))
        return parse_result(tool, result)

    async def call_data(self, tool: str, arguments: dict[str, Any]) -> Any:
        """Invoke one tool and return its data, raising if it failed."""
        result = await self.call(tool, arguments)
        if not result["ok"]:
            raise GraphDataToolError(f"{tool} failed: {result['error']}")
        return result["data"]

    async def is_ready(self) -> bool:
        """Whether the configured graph's schema can be read through
        tigergraph-mcp. Cached briefly so `/ready` probes stay cheap."""
        now = self._clock()
        cache_for = self._settings.graph_data_ready_cache_seconds
        if self._ready_at is not None and now - self._ready_at < cache_for:
            return self._ready_value
        try:
            await self.call_data(
                TigerGraphToolName.GET_GRAPH_SCHEMA.value, {"graph_name": self.graph_name}
            )
            ready = True
        except GraphDataToolError as exc:
            logger.warning("tigergraph-mcp not ready: %s", exc)
            ready = False
        self._ready_at, self._ready_value = now, ready
        return ready
