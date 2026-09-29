import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from mcp.types import CallToolResult, TextContent
from tigergraph_mcp.tool_names import TigerGraphToolName

from autosentry_agent.mcp.session_manager import (
    McpNotReadyError,
    McpSessionManager,
    McpToolError,
)
from autosentry_agent.secrets.base import SecretsProvider

from .tigergraph_mcp_fixtures import (
    get_graph_schema_data,
    get_graph_schema_result,
    gsql_success_result,
    sdk_error_result,
    tigergraph_schema,
    tool_error_result,
)

pytestmark = pytest.mark.asyncio


class FakeSecretsProvider(SecretsProvider):
    """A controllable SecretsProvider test double. Lets tests change the
    stored secret/version between calls to simulate rotation."""

    def __init__(self, secret: str = "hunter2", version: str | None = "v1"):
        self.secret = secret
        self.version = version
        self.get_secret_calls: list[str] = []
        self.get_secret_version_calls: list[str] = []

    def get_secret(self, name: str) -> str:
        self.get_secret_calls.append(name)
        return self.secret

    def get_secret_version(self, name: str) -> str | None:
        self.get_secret_version_calls.append(name)
        return self.version


class FakeSessionContextManager:
    """Stands in for the object returned by
    `MultiServerMCPClient.session(...)` (an `@asynccontextmanager` result).
    Records __aenter__/__aexit__ calls so tests can assert on them."""

    def __init__(self, session: MagicMock):
        self._session = session
        self.aenter_calls = 0
        self.aexit_calls: list[tuple] = []

    async def __aenter__(self):
        self.aenter_calls += 1
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        self.aexit_calls.append((exc_type, exc, tb))
        return None


def make_session_mock() -> MagicMock:
    session = MagicMock()
    # `ClientSession.call_tool()` returns a `CallToolResult`. The default
    # here is exactly what the real tigergraph-mcp 1.0.3 server returns for
    # a successful `tigergraph__get_graph_schema` call (built with the real
    # response formatter; see tigergraph_mcp_fixtures).
    session.call_tool = AsyncMock(return_value=get_graph_schema_result())
    return session


def make_client_mock(session_cm: FakeSessionContextManager) -> MagicMock:
    client = MagicMock()
    client.session = MagicMock(return_value=session_cm)
    return client


@pytest.fixture
def secrets_provider():
    return FakeSecretsProvider()


@pytest.fixture
def session_mock():
    return make_session_mock()


@pytest.fixture
def session_cm(session_mock):
    return FakeSessionContextManager(session_mock)


@pytest.fixture
def client_mock(session_cm):
    return make_client_mock(session_cm)


@pytest.fixture
def patched_client(client_mock):
    with patch(
        "autosentry_agent.mcp.session_manager.MultiServerMCPClient",
        return_value=client_mock,
    ) as client_class:
        yield client_class


@pytest.fixture
def patched_load_tools():
    with patch(
        "autosentry_agent.mcp.session_manager.load_mcp_tools",
        new_callable=AsyncMock,
        return_value=[],
    ) as load_tools:
        yield load_tools


class TestIsReady:
    async def test_not_ready_before_start(self, secrets_provider):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        assert manager.is_ready() is False


class TestStart:
    async def test_start_marks_ready(
        self, secrets_provider, patched_client, patched_load_tools
    ):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()
        assert manager.is_ready() is True

    async def test_start_builds_stdio_config_with_graph_and_password(
        self, secrets_provider, patched_client, patched_load_tools
    ):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()

        assert patched_client.call_count == 1
        (connections,), kwargs = patched_client.call_args
        assert kwargs == {}
        config = connections["tigergraph-mcp-server"]
        assert config["transport"] == "stdio"
        assert config["command"] == "tigergraph-mcp"
        assert config["args"] == []
        assert config["env"]["TG_PASSWORD"] == "hunter2"
        assert config["env"]["TG_GRAPHNAME"] == "MyGraph"

    async def test_start_does_not_mutate_process_os_environ(
        self, secrets_provider, patched_client, patched_load_tools, monkeypatch
    ):
        # Start from a known-clean state so a developer's own TG_* variables
        # don't make this test fail spuriously.
        monkeypatch.delenv("TG_PASSWORD", raising=False)
        monkeypatch.delenv("TG_GRAPHNAME", raising=False)
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()
        assert "TG_PASSWORD" not in os.environ
        assert "TG_GRAPHNAME" not in os.environ

    async def test_start_does_not_leak_parent_environment_into_subprocess(
        self, secrets_provider, patched_client, patched_load_tools, monkeypatch
    ):
        """The subprocess env is an allowlist: parent secrets (the Control
        Store's DATABASE_URL, AWS credentials) and stray TG_* variables
        that would re-point tigergraph-mcp's connection must not reach it."""
        monkeypatch.setenv("SOME_UNRELATED_VAR", "do-not-forward")
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@db/control")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
        monkeypatch.setenv("TG_PROFILE", "staging")
        monkeypatch.setenv("TG_API_TOKEN", "stray-token")
        monkeypatch.setenv("STAGING_TG_HOST", "https://elsewhere")
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()

        (connections,), _ = patched_client.call_args
        env = connections["tigergraph-mcp-server"]["env"]
        for leaked in (
            "SOME_UNRELATED_VAR",
            "DATABASE_URL",
            "AWS_SECRET_ACCESS_KEY",
            "TG_PROFILE",
            "TG_API_TOKEN",
            "STAGING_TG_HOST",
        ):
            assert leaked not in env
        assert set(env) <= {
            "PATH",
            "SYSTEMROOT",
            "HOME",
            "TG_HOST",
            "TG_USERNAME",
            "TG_GRAPHNAME",
            "TG_PASSWORD",
        }

    async def test_start_forwards_path(
        self, secrets_provider, patched_client, patched_load_tools, monkeypatch
    ):
        monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()

        (connections,), _ = patched_client.call_args
        assert connections["tigergraph-mcp-server"]["env"]["PATH"] == "/usr/local/bin:/usr/bin"

    async def test_start_forwards_configured_host_and_username(
        self, secrets_provider, patched_client, patched_load_tools
    ):
        manager = McpSessionManager(
            "tenant-a",
            "MyGraph",
            secrets_provider,
            tg_host="https://tg.example.internal",
            tg_username="autosentry_svc",
        )
        await manager.start()

        (connections,), _ = patched_client.call_args
        env = connections["tigergraph-mcp-server"]["env"]
        assert env["TG_HOST"] == "https://tg.example.internal"
        assert env["TG_USERNAME"] == "autosentry_svc"

    async def test_start_ignores_parent_tg_host_and_username(
        self, secrets_provider, patched_client, patched_load_tools, monkeypatch
    ):
        """Host/username come only from the constructor, never implicitly
        from this process's TG_* environment."""
        monkeypatch.setenv("TG_HOST", "https://from-parent-env")
        monkeypatch.setenv("TG_USERNAME", "from-parent-env")
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()

        (connections,), _ = patched_client.call_args
        env = connections["tigergraph-mcp-server"]["env"]
        assert "TG_HOST" not in env
        assert "TG_USERNAME" not in env

    async def test_start_drops_empty_env_entries(
        self, secrets_provider, patched_client, patched_load_tools, monkeypatch
    ):
        monkeypatch.delenv("SYSTEMROOT", raising=False)
        monkeypatch.delenv("HOME", raising=False)
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider, tg_host="")
        await manager.start()

        (connections,), _ = patched_client.call_args
        env = connections["tigergraph-mcp-server"]["env"]
        assert all(value != "" for value in env.values())
        assert "SYSTEMROOT" not in env
        assert "HOME" not in env
        assert "TG_HOST" not in env

    async def test_start_records_secret_version(
        self, secrets_provider, patched_client, patched_load_tools
    ):
        secrets_provider.version = "v42"
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()
        assert manager._current_secret_version == "v42"

    async def test_start_enters_session_context_manager(
        self, secrets_provider, patched_client, patched_load_tools, session_cm
    ):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()
        assert session_cm.aenter_calls == 1

    async def test_start_loads_mcp_tools_with_the_session(
        self, secrets_provider, patched_client, patched_load_tools, session_mock
    ):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()
        patched_load_tools.assert_awaited_once_with(session_mock)

    async def test_start_calls_get_graph_schema_with_graph_name(
        self, secrets_provider, patched_client, patched_load_tools, session_mock
    ):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()
        session_mock.call_tool.assert_any_await(
            "tigergraph__get_graph_schema", {"graph_name": "MyGraph"}
        )

    async def test_start_uses_the_real_servers_registered_tool_name(
        self, secrets_provider, patched_client, patched_load_tools, session_mock
    ):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()
        (name, _), _ = session_mock.call_tool.await_args
        assert name == TigerGraphToolName.GET_GRAPH_SCHEMA.value
        assert type(name) is str

    async def test_start_calls_get_graph_schema_before_marking_ready(
        self, secrets_provider, patched_client, patched_load_tools, session_mock
    ):
        # If get_graph_schema fails, `_ready` must not have been set yet.
        session_mock.call_tool.side_effect = RuntimeError("schema fetch failed")
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        with pytest.raises(RuntimeError):
            await manager.start()
        assert manager.is_ready() is False

    async def test_start_fails_closed_when_server_reports_schema_failure(
        self, secrets_provider, patched_client, patched_load_tools, session_mock
    ):
        """The real server reports an unreachable TigerGraph as an ordinary
        CallToolResult with a `success: false` envelope (isError stays
        False). start() must treat that as failure, not as ready."""
        session_mock.call_tool.return_value = tool_error_result(
            "get_graph_schema",
            Exception("Connection refused: unreachable host"),
            context={"graph_name": "MyGraph"},
        )
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        with pytest.raises(McpToolError):
            await manager.start()
        assert manager.is_ready() is False


class TestCallTool:
    """`call_tool()` is mocked only at the `ClientSession.call_tool`
    boundary, returning `CallToolResult`s built by the real
    `tigergraph_mcp.response_formatter` (see tigergraph_mcp_fixtures)."""

    @pytest_asyncio.fixture
    async def started(self, secrets_provider, patched_client, patched_load_tools, session_mock):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()
        session_mock.call_tool.reset_mock()
        return manager

    async def test_returns_envelope_data_for_get_graph_schema(self, started, session_mock):
        schema = tigergraph_schema(["Person", "Account", "Transaction"], ["OWNS", "INITIATED"])
        session_mock.call_tool.return_value = get_graph_schema_result(schema)

        result = await started.call_tool("tigergraph__get_graph_schema", {"graph_name": "MyGraph"})

        session_mock.call_tool.assert_awaited_once_with(
            "tigergraph__get_graph_schema", {"graph_name": "MyGraph"}
        )
        assert result == get_graph_schema_data(schema)
        assert result["schema"]["VertexTypes"][2]["Name"] == "Transaction"

    async def test_returns_envelope_data_for_gsql(self, started, session_mock):
        session_mock.call_tool.return_value = gsql_success_result("The graph MyGraph is created.")

        result = await started.call_tool("tigergraph__gsql", {"command": "CREATE GRAPH MyGraph()"})

        assert result == {"result": "The graph MyGraph is created."}

    async def test_ignores_markdown_after_the_envelope(self, started, session_mock):
        """format_response appends a human-readable section that repeats
        `data` in its own ```json block; only the first block is parsed,
        and fences/newlines inside string values do not confuse it."""
        tricky = 'line1\n```\nnot a fence\n```json\n{"success": false}\n```'
        session_mock.call_tool.return_value = gsql_success_result(tricky)
        text = session_mock.call_tool.return_value.content[0].text
        assert text.count("```json") >= 2  # sanity: the real format really has two

        result = await started.call_tool("tigergraph__gsql", {"command": "LS"})

        assert result == {"result": tricky}

    async def test_success_envelope_without_data_returns_empty_dict(self, started, session_mock):
        from tigergraph_mcp.response_formatter import format_success

        session_mock.call_tool.return_value = CallToolResult(
            content=list(format_success(operation="drop_query", summary="Dropped"))
        )

        assert await started.call_tool("tigergraph__drop_query", {}) == {}

    async def test_success_false_envelope_raises_even_though_is_error_is_false(
        self, started, session_mock
    ):
        """The real server never sets isError; the envelope's `success`
        field is the only tool-level failure signal."""
        failure = tool_error_result(
            "gsql",
            Exception('GSQL command returned an error:\nEncountered "FOO" at line 1'),
            context={"graph_name": "MyGraph"},
        )
        assert failure.isError is False
        session_mock.call_tool.return_value = failure

        with pytest.raises(McpToolError, match=r"(?s)tigergraph__gsql failed: .*Encountered"):
            await started.call_tool("tigergraph__gsql", {"command": "FOO"})

    async def test_unknown_tool_error_from_server_raises(self, started, session_mock):
        """What the real server returns for an unrecognized (e.g. unprefixed)
        tool name: `raise ValueError("Unknown tool: ...")` caught and
        formatted as a `success: false` envelope."""
        session_mock.call_tool.return_value = tool_error_result(
            "get_graph_schema",
            ValueError("Unknown tool: get_graph_schema"),
            context={"arguments": {"graph_name": "MyGraph"}},
        )

        with pytest.raises(McpToolError, match="Unknown tool: get_graph_schema"):
            await started.call_tool("get_graph_schema", {"graph_name": "MyGraph"})

    async def test_sdk_level_is_error_result_raises(self, started, session_mock):
        """The mcp SDK's own failures (e.g. input-schema validation) come
        back with isError=True and a plain-text body, no envelope."""
        session_mock.call_tool.return_value = sdk_error_result(
            "Input validation error: 'command' is a required property"
        )

        with pytest.raises(McpToolError, match="'command' is a required property"):
            await started.call_tool("tigergraph__gsql", {})

    async def test_is_error_raises_even_if_envelope_claims_success(self, started, session_mock):
        result = get_graph_schema_result()
        session_mock.call_tool.return_value = CallToolResult(content=result.content, isError=True)

        with pytest.raises(McpToolError):
            await started.call_tool("tigergraph__get_graph_schema", {"graph_name": "MyGraph"})

    async def test_response_without_envelope_raises(self, started, session_mock):
        session_mock.call_tool.return_value = CallToolResult(
            content=[TextContent(type="text", text="**Something happened**")]
        )

        with pytest.raises(McpToolError, match="no JSON envelope"):
            await started.call_tool("tigergraph__gsql", {"command": "LS"})

    async def test_malformed_envelope_raises(self, started, session_mock):
        session_mock.call_tool.return_value = CallToolResult(
            content=[TextContent(type="text", text="```json\n{not json\n```\n\n**x**")]
        )

        with pytest.raises(McpToolError, match="malformed JSON envelope"):
            await started.call_tool("tigergraph__gsql", {"command": "LS"})

    async def test_empty_content_raises(self, started, session_mock):
        session_mock.call_tool.return_value = CallToolResult(content=[])

        with pytest.raises(McpToolError):
            await started.call_tool("tigergraph__gsql", {"command": "LS"})

    async def test_call_tool_before_start_raises_not_ready(self, secrets_provider):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        with pytest.raises(McpNotReadyError):
            await manager.call_tool("tigergraph__get_graph_schema", {"graph_name": "MyGraph"})

    async def test_call_tool_after_stop_raises_not_ready(self, started):
        await started.stop()
        with pytest.raises(McpNotReadyError):
            await started.call_tool("tigergraph__get_graph_schema", {"graph_name": "MyGraph"})

    async def test_call_tool_after_failed_start_raises_not_ready(
        self, secrets_provider, patched_load_tools
    ):
        failing_cm = MagicMock()
        failing_cm.__aenter__ = AsyncMock(side_effect=OSError("tigergraph-mcp not found"))
        client = MagicMock()
        client.session = MagicMock(return_value=failing_cm)
        with patch(
            "autosentry_agent.mcp.session_manager.MultiServerMCPClient", return_value=client
        ):
            manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
            with pytest.raises(OSError):
                await manager.start()

        with pytest.raises(McpNotReadyError):
            await manager.call_tool("tigergraph__get_graph_schema", {"graph_name": "MyGraph"})


class TestStop:
    async def test_stop_exits_session_context_manager(
        self, secrets_provider, patched_client, patched_load_tools, session_cm
    ):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()
        await manager.stop()
        assert session_cm.aexit_calls == [(None, None, None)]

    async def test_stop_marks_not_ready(
        self, secrets_provider, patched_client, patched_load_tools
    ):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()
        await manager.stop()
        assert manager.is_ready() is False

    async def test_stop_clears_session_state(
        self, secrets_provider, patched_client, patched_load_tools
    ):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()
        await manager.stop()
        assert manager._session is None
        assert manager._session_cm is None

    async def test_stop_before_start_is_a_noop(self, secrets_provider):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.stop()  # must not raise
        assert manager.is_ready() is False

    async def test_stop_called_twice_is_a_noop_the_second_time(
        self, secrets_provider, patched_client, patched_load_tools, session_cm
    ):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()
        await manager.stop()
        await manager.stop()  # must not raise, must not re-exit the old cm
        assert len(session_cm.aexit_calls) == 1


class TestRotateIfNeeded:
    async def test_no_versioning_backend_is_a_noop(
        self, secrets_provider, patched_client, patched_load_tools, session_cm
    ):
        secrets_provider.version = None
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()
        aenter_before = session_cm.aenter_calls

        await manager.rotate_if_needed()

        assert session_cm.aenter_calls == aenter_before
        assert session_cm.aexit_calls == []
        assert manager.is_ready() is True

    async def test_unchanged_version_is_a_noop(
        self, secrets_provider, patched_client, patched_load_tools, session_cm
    ):
        secrets_provider.version = "v1"
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()

        await manager.rotate_if_needed()

        assert session_cm.aexit_calls == []
        assert manager.is_ready() is True

    async def test_changed_version_restarts_the_session(
        self, secrets_provider, patched_client, patched_load_tools, session_cm
    ):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()
        assert manager._current_secret_version == "v1"

        secrets_provider.secret = "new-password"
        secrets_provider.version = "v2"
        await manager.rotate_if_needed()

        # Old session was torn down...
        assert session_cm.aexit_calls == [(None, None, None)]
        # ...and a fresh one was started with the new credential.
        (connections,), _ = patched_client.call_args
        assert connections["tigergraph-mcp-server"]["env"]["TG_PASSWORD"] == "new-password"
        assert manager._current_secret_version == "v2"
        assert manager.is_ready() is True

    async def test_rotate_before_start_treats_missing_version_as_change_when_prior_is_none(
        self, secrets_provider, patched_client, patched_load_tools
    ):
        # rotate_if_needed is only meaningful after start(), but calling it
        # beforehand should not crash: stop() on an unstarted manager is a
        # no-op, and start() runs normally.
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.rotate_if_needed()
        assert manager.is_ready() is True


class TestRecoverFromCrash:
    async def test_recover_stops_then_starts(
        self, secrets_provider, patched_client, patched_load_tools, session_cm
    ):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.start()

        await manager.recover_from_crash()

        assert session_cm.aexit_calls == [(None, None, None)]
        assert manager.is_ready() is True

    async def test_recover_from_crash_without_prior_start_still_starts(
        self, secrets_provider, patched_client, patched_load_tools
    ):
        manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
        await manager.recover_from_crash()
        assert manager.is_ready() is True


class TestDoubleStart:
    async def test_start_called_twice_creates_a_second_client_without_closing_the_first(
        self, secrets_provider, patched_load_tools
    ):
        """Documents current behavior per the task-6 design: `start()` has
        no re-entrancy guard, so calling it twice replaces `_client` and
        `_session_cm` without exiting the previous session. This is a known
        gap in the design as specified, not something this test suite
        silently papers over."""
        first_session = make_session_mock()
        first_cm = FakeSessionContextManager(first_session)
        second_session = make_session_mock()
        second_cm = FakeSessionContextManager(second_session)

        clients = [make_client_mock(first_cm), make_client_mock(second_cm)]
        with patch(
            "autosentry_agent.mcp.session_manager.MultiServerMCPClient",
            side_effect=clients,
        ):
            manager = McpSessionManager("tenant-a", "MyGraph", secrets_provider)
            await manager.start()
            await manager.start()

        assert first_cm.aenter_calls == 1
        assert second_cm.aenter_calls == 1
        # The first session's __aexit__ was never called: start() does not
        # guard against re-entrancy or clean up a prior session.
        assert first_cm.aexit_calls == []
        assert manager._session is second_session
        assert manager.is_ready() is True
