import json
import os
from contextlib import AbstractAsyncContextManager
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp.types import CallToolResult, TextContent
from tigergraph_mcp.tool_names import TigerGraphToolName

from ..secrets.base import SecretsProvider

# `tigergraph_mcp.response_formatter.format_response` renders every tool
# result (success or failure) as one TextContent block whose text begins with
# the structured envelope in a fenced JSON block, followed by human-readable
# markdown meant for an LLM. Only the fenced block is machine-readable.
_ENVELOPE_OPEN = "```json\n"
_ENVELOPE_CLOSE = "\n```"


class McpNotReadyError(RuntimeError):
    """Raised when a tool is called before `McpSessionManager.start()` has
    opened an MCP session (never called, failed, or already stopped)."""


class McpToolError(RuntimeError):
    """Raised when a `tigergraph-mcp` tool call fails.

    The server reports tool failures as an ordinary (non-error)
    `CallToolResult` whose envelope has `"success": false`; the MCP SDK
    itself reports protocol-level failures (e.g. input-schema validation)
    as `isError=True` with a plain-text body. Both surface as this error,
    as does a response that cannot be parsed as an envelope at all.
    """


def _parse_envelope(name: str, result: CallToolResult) -> dict[str, Any]:
    """Extract the envelope's `data` dict from a real `tigergraph-mcp`
    `CallToolResult`, raising `McpToolError` on any failure signal."""
    first = result.content[0] if result.content else None
    text = first.text if isinstance(first, TextContent) else None

    if result.isError:
        # SDK-generated errors (`Server._make_error_result`) carry a plain
        # message, not a fenced envelope, so report the text as-is.
        raise McpToolError(f"{name} failed: {text or 'unknown error'}")

    if text is None or not text.startswith(_ENVELOPE_OPEN):
        raise McpToolError(f"{name} returned a response with no JSON envelope: {text!r}")

    json_str = text[len(_ENVELOPE_OPEN):].split(_ENVELOPE_CLOSE, 1)[0]
    try:
        envelope = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise McpToolError(f"{name} returned a malformed JSON envelope: {e}") from e

    if not isinstance(envelope, dict) or envelope.get("success") is not True:
        detail = (
            (envelope.get("error") or envelope.get("summary")) if isinstance(envelope, dict) else None
        )
        raise McpToolError(f"{name} failed: {detail or 'unknown error'}")

    data = envelope.get("data")
    return data if isinstance(data, dict) else {}


class McpSessionManager:
    """Manages one `tigergraph-mcp` MCP server subprocess connection (over
    stdio transport) per tenant/graph, using `langchain_mcp_adapters`."""

    def __init__(
        self,
        tenant_id: str,
        graph_name: str,
        secrets_provider: SecretsProvider,
        *,
        tg_host: str | None = None,
        tg_username: str | None = None,
    ):
        self.tenant_id = tenant_id
        self.graph_name = graph_name
        self._secrets = secrets_provider
        self._tg_host = tg_host
        self._tg_username = tg_username
        self._client: MultiServerMCPClient | None = None
        self._session: ClientSession | None = None
        self._session_cm: AbstractAsyncContextManager[ClientSession] | None = None
        self._ready = False
        self._current_secret_version: str | None = None

    def is_ready(self) -> bool:
        return self._ready

    def _build_subprocess_env(self, credential: str) -> dict[str, str]:
        """An explicit allowlist, never a copy of `os.environ`: the parent
        process holds secrets (`DATABASE_URL`, `AWS_*`) the subprocess must
        not see, and any stray `TG_*` variable would otherwise silently
        change which TigerGraph identity/profile `tigergraph-mcp` uses."""
        env = {
            "PATH": os.environ.get("PATH", ""),
            # Windows subprocess launching needs SYSTEMROOT; POSIX tools may
            # need HOME for config discovery. Empty entries are dropped below.
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "HOME": os.environ.get("HOME", ""),
            "TG_HOST": self._tg_host or "",
            "TG_USERNAME": self._tg_username or "",
            "TG_GRAPHNAME": self.graph_name,
            # TODO: swap for CredentialFile-based file injection once
            # tigergraph-mcp supports TG_PASSWORD_FILE (reading the password
            # from a file path instead of a plain environment variable).
            "TG_PASSWORD": credential,
        }
        return {k: v for k, v in env.items() if v}

    async def start(self) -> None:
        credential = self._secrets.get_secret("TG_PASSWORD")
        self._current_secret_version = self._secrets.get_secret_version("TG_PASSWORD")

        env = self._build_subprocess_env(credential)

        self._client = MultiServerMCPClient(
            {"tigergraph-mcp-server": {"transport": "stdio", "command": "tigergraph-mcp", "args": [], "env": env}}
        )
        self._session_cm = self._client.session("tigergraph-mcp-server")
        self._session = await self._session_cm.__aenter__()
        await load_mcp_tools(self._session)

        await self.call_tool(TigerGraphToolName.GET_GRAPH_SCHEMA.value, {"graph_name": self.graph_name})
        self._ready = True

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a `tigergraph-mcp` tool and return its envelope's `data`
        dict. Raises `McpNotReadyError` if no session is open, and
        `McpToolError` if the tool reports failure in any form."""
        if self._session is None:
            raise McpNotReadyError(
                f"MCP session for tenant {self.tenant_id!r} is not open; "
                "call start() successfully before calling tools."
            )
        result = await self._session.call_tool(name, arguments)
        return _parse_envelope(name, result)

    async def stop(self) -> None:
        if self._session_cm is not None:
            await self._session_cm.__aexit__(None, None, None)
            self._session_cm = None
            self._session = None
        self._ready = False

    async def rotate_if_needed(self) -> None:
        latest_version = self._secrets.get_secret_version("TG_PASSWORD")
        if latest_version is None or latest_version == self._current_secret_version:
            return
        await self.stop()
        await self.start()

    async def recover_from_crash(self) -> None:
        await self.stop()
        await self.start()
