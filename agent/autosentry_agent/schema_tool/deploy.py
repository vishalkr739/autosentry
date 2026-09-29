import hashlib

from tigergraph_mcp.tool_names import TigerGraphToolName

from ..mcp.session_manager import McpSessionManager


async def deploy_reference_schema(session_manager: McpSessionManager, schema_path: str) -> str:
    """Apply schema_path's GSQL via the MCP session's `gsql` tool, then
    return a stable hash of the resulting schema.

    A GSQL-level failure needs no handling here: `tigergraph-mcp`'s `gsql`
    tool detects it (`gsql_has_error`) and returns a `success: false`
    envelope, which `McpSessionManager.call_tool` raises as `McpToolError`.
    """
    with open(schema_path, encoding="utf-8") as f:
        gsql_text = f.read()
    await session_manager.call_tool(TigerGraphToolName.GSQL.value, {"command": gsql_text})
    return await compute_schema_hash(session_manager)


async def compute_schema_hash(session_manager: McpSessionManager) -> str:
    """Sha256 of the graph's sorted vertex/edge type names, order-independent
    so an equivalent schema always hashes the same regardless of GSQL's
    reported ordering."""
    data = await session_manager.call_tool(
        TigerGraphToolName.GET_GRAPH_SCHEMA.value, {"graph_name": session_manager.graph_name}
    )
    # `data` is get_graph_schema's envelope data; the TigerGraph schema is
    # nested under "schema", and its type entries are keyed "Name" (as
    # returned by TigerGraph's schema endpoint via pyTigerGraph.getSchema).
    schema = data["schema"]
    vertex_names = sorted(v["Name"] for v in schema.get("VertexTypes", []))
    edge_names = sorted(e["Name"] for e in schema.get("EdgeTypes", []))
    canonical = "|".join(vertex_names) + "::" + "|".join(edge_names)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
