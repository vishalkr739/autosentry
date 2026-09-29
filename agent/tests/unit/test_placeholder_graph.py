from unittest.mock import AsyncMock, MagicMock

import pytest

from autosentry_agent.mcp.graph import GraphState, build_placeholder_graph
from autosentry_agent.mcp.session_manager import McpToolError

from .tigergraph_mcp_fixtures import get_graph_schema_data, tigergraph_schema

# These tests mock one level above the wire, at `McpSessionManager.call_tool`,
# which returns the parsed envelope's `data` dict. For `get_graph_schema` that
# is {"graph_name", "schema", "vertex_type_count", "edge_type_count"}, with
# the TigerGraph schema nested under "schema".


def make_session_manager(data: dict | None = None) -> MagicMock:
    session_manager = MagicMock()
    session_manager.call_tool = AsyncMock(
        return_value=get_graph_schema_data() if data is None else data
    )
    return session_manager


class TestGraphState:
    def test_graph_state_has_required_fields(self):
        """GraphState should have graph_name (str) and schema (dict) fields."""
        state: GraphState = {"graph_name": "MyGraph", "schema": {"key": "value"}}
        assert state["graph_name"] == "MyGraph"
        assert state["schema"] == {"key": "value"}


class TestBuildPlaceholderGraph:
    def test_graph_builds_without_error(self):
        """Building the placeholder graph should not raise."""
        graph = build_placeholder_graph(MagicMock())
        assert graph is not None

    @pytest.mark.asyncio
    async def test_graph_node_calls_session_manager_with_real_tool_name(self):
        """The node must call the tool under the name the real server
        registers (`tigergraph__get_graph_schema`), not the bare name."""
        session_manager = make_session_manager()

        graph = build_placeholder_graph(session_manager)
        await graph.ainvoke({"graph_name": "TestGraph", "schema": {}})

        session_manager.call_tool.assert_awaited_once_with(
            "tigergraph__get_graph_schema", {"graph_name": "TestGraph"}
        )

    @pytest.mark.asyncio
    async def test_graph_state_schema_is_the_nested_tigergraph_schema(self):
        """State gets data["schema"] (VertexTypes/EdgeTypes), not the whole
        envelope data with its graph_name/count bookkeeping fields."""
        schema = tigergraph_schema(["Account"], ["hasTransaction"])
        session_manager = make_session_manager(get_graph_schema_data(schema))

        graph = build_placeholder_graph(session_manager)
        output = await graph.ainvoke({"graph_name": "MyGraph", "schema": {}})

        assert output["schema"] == schema
        assert [v["Name"] for v in output["schema"]["VertexTypes"]] == ["Account"]
        assert [e["Name"] for e in output["schema"]["EdgeTypes"]] == ["hasTransaction"]
        assert "vertex_type_count" not in output["schema"]

    @pytest.mark.asyncio
    async def test_graph_preserves_existing_state_fields(self):
        """The graph should preserve graph_name and only update schema."""
        session_manager = make_session_manager()

        graph = build_placeholder_graph(session_manager)
        output = await graph.ainvoke({"graph_name": "PreservedGraph", "schema": {}})

        assert output["graph_name"] == "PreservedGraph"

    @pytest.mark.asyncio
    async def test_graph_call_tool_result_replaces_schema(self):
        """The graph should replace the entire schema field with the fetched schema."""
        schema = tigergraph_schema(["Person"], [])
        session_manager = make_session_manager(get_graph_schema_data(schema))

        graph = build_placeholder_graph(session_manager)
        output = await graph.ainvoke({"graph_name": "ReplaceGraph", "schema": {"old": "data"}})

        assert output["schema"] == schema

    @pytest.mark.asyncio
    async def test_graph_node_uses_graph_name_from_state(self):
        """The graph should pass the graph_name from the input state to call_tool."""
        session_manager = make_session_manager()

        graph = build_placeholder_graph(session_manager)
        await graph.ainvoke({"graph_name": "CustomGraphName", "schema": {}})

        session_manager.call_tool.assert_awaited_once()
        call_args = session_manager.call_tool.call_args
        assert call_args[0][1]["graph_name"] == "CustomGraphName"

    @pytest.mark.asyncio
    async def test_graph_propagates_tool_failure(self):
        """A tool failure (McpToolError from call_tool) must surface, not be
        swallowed into an empty schema."""
        session_manager = MagicMock()
        session_manager.call_tool = AsyncMock(
            side_effect=McpToolError("tigergraph__get_graph_schema failed: connection refused")
        )

        graph = build_placeholder_graph(session_manager)
        with pytest.raises(McpToolError):
            await graph.ainvoke({"graph_name": "MyGraph", "schema": {}})

    @pytest.mark.asyncio
    async def test_graph_returns_compiled_graph(self):
        """build_placeholder_graph should return a compiled LangGraph graph."""
        graph = build_placeholder_graph(make_session_manager())

        assert hasattr(graph, "ainvoke")
        result = await graph.ainvoke({"graph_name": "Test", "schema": {}})
        assert result is not None
