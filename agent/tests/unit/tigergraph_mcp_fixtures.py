"""Real-shaped `tigergraph-mcp` 1.0.3 wire fixtures for unit tests.

Every shape here is taken from the installed package source, not invented:

- `tigergraph_mcp.tools.schema_tools.get_graph_schema` returns
  `format_success(operation="get_graph_schema", data={"graph_name",
  "schema", "vertex_type_count", "edge_type_count"}, suggestions=[...])`,
  where `schema` is `pyTigerGraph`'s `getSchema()` result (TigerGraph's own
  schema JSON: type entries keyed "Name", see `getVertexTypes`).
- `tigergraph_mcp.tools.gsql_tools.gsql` returns
  `format_success(operation="gsql", data={"result": ...},
  metadata={"graph_name": ...})` on success, or `format_error(...)` when
  `gsql_has_error` matches the result text.
- `tigergraph_mcp.server.MCPServer` catches every exception and returns
  `format_error(...)`; the mcp 1.30 SDK's `Server.call_tool` decorator wraps
  that content list as `CallToolResult(content=..., structuredContent=None,
  isError=False)`.
- The SDK's own failures (`Server._make_error_result`, e.g. input-schema
  validation) are `CallToolResult(content=[TextContent(text=<plain
  message>)], isError=True)`, with no JSON envelope.

The envelope text itself is produced by calling the real
`tigergraph_mcp.response_formatter` functions, so fixtures cannot drift from
the installed formatter.
"""

from typing import Any

from mcp.types import CallToolResult, TextContent
from tigergraph_mcp.response_formatter import format_error, format_success


def tigergraph_schema(
    vertex_names: list[str] | None = None,
    edge_names: list[str] | None = None,
    graph_name: str = "MyGraph",
) -> dict[str, Any]:
    """A TigerGraph schema dict shaped like `pyTigerGraph.getSchema()`."""
    vertex_names = ["Person", "Account"] if vertex_names is None else vertex_names
    edge_names = ["OWNS"] if edge_names is None else edge_names
    return {
        "GraphName": graph_name,
        "VertexTypes": [
            {
                "Name": name,
                "PrimaryId": {
                    "AttributeName": "id",
                    "AttributeType": {"Name": "STRING"},
                },
                "Attributes": [],
                "Config": {"STATS": "OUTDEGREE_BY_EDGETYPE"},
                "IsLocal": True,
            }
            for name in vertex_names
        ],
        "EdgeTypes": [
            {
                "Name": name,
                "FromVertexTypeName": "Person",
                "ToVertexTypeName": "Account",
                "IsDirected": True,
                "Config": {"REVERSE_EDGE": f"reverse_{name}"},
                "Attributes": [],
            }
            for name in edge_names
        ],
    }


def get_graph_schema_data(
    schema: dict[str, Any] | None = None, graph_name: str = "MyGraph"
) -> dict[str, Any]:
    """The `data` field of a successful `get_graph_schema` envelope, i.e.
    what `McpSessionManager.call_tool` returns for that tool."""
    schema = tigergraph_schema(graph_name=graph_name) if schema is None else schema
    return {
        "graph_name": graph_name,
        "schema": schema,
        "vertex_type_count": len(schema.get("VertexTypes", [])),
        "edge_type_count": len(schema.get("EdgeTypes", [])),
    }


def gsql_data(result: str = "Successfully created vertex types: [Person, Account].") -> dict[str, Any]:
    """The `data` field of a successful `gsql` envelope."""
    return {"result": result}


def get_graph_schema_result(
    schema: dict[str, Any] | None = None, graph_name: str = "MyGraph"
) -> CallToolResult:
    """A `CallToolResult` exactly as the real server returns for a
    successful `get_graph_schema` call."""
    data = get_graph_schema_data(schema, graph_name)
    content = format_success(
        operation="get_graph_schema",
        summary=f"Success: Schema retrieved for graph '{graph_name}'",
        data=data,
        suggestions=[
            f"Full listing (schema + queries + jobs): show_graph_details(graph_name='{graph_name}')",
            "Start working with data: add_node(...) or add_edge(...)",
        ],
    )
    return CallToolResult(content=list(content))


def gsql_success_result(result: str, graph_name: str = "MyGraph") -> CallToolResult:
    content = format_success(
        operation="gsql",
        summary="GSQL command executed successfully",
        data={"result": result},
        metadata={"graph_name": graph_name},
    )
    return CallToolResult(content=list(content))


def tool_error_result(operation: str, error: Exception, context: dict[str, Any] | None = None) -> CallToolResult:
    """A `CallToolResult` as the real server returns for a tool-level
    failure: `success: false` envelope, `isError` left False."""
    content = format_error(operation=operation, error=error, context=context)
    return CallToolResult(content=list(content))


def sdk_error_result(message: str) -> CallToolResult:
    """A `CallToolResult` as the mcp SDK's `Server._make_error_result`
    builds it: plain text, `isError=True`, no envelope."""
    return CallToolResult(content=[TextContent(type="text", text=message)], isError=True)
