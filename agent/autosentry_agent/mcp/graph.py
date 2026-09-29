from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from tigergraph_mcp.tool_names import TigerGraphToolName

from .session_manager import McpSessionManager


class GraphState(TypedDict):
    graph_name: str
    schema: dict[str, Any]


def build_placeholder_graph(
    session_manager: McpSessionManager,
) -> CompiledStateGraph[GraphState, None, GraphState, GraphState]:
    async def call_schema_tool(state: GraphState) -> GraphState:
        data = await session_manager.call_tool(
            TigerGraphToolName.GET_GRAPH_SCHEMA.value, {"graph_name": state["graph_name"]}
        )
        # `get_graph_schema`'s envelope data is {"graph_name", "schema",
        # "vertex_type_count", "edge_type_count"}; state holds only the
        # nested TigerGraph schema ({"VertexTypes": [...], "EdgeTypes": [...]}).
        return {**state, "schema": data["schema"]}

    workflow = StateGraph(GraphState)
    workflow.add_node("call_schema_tool", call_schema_tool)
    workflow.set_entry_point("call_schema_tool")
    workflow.add_edge("call_schema_tool", END)
    return workflow.compile()
