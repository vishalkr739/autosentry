import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from autosentry_agent.mcp.session_manager import McpToolError
from autosentry_agent.schema_tool.deploy import compute_schema_hash, deploy_reference_schema

from .tigergraph_mcp_fixtures import get_graph_schema_data, gsql_data, tigergraph_schema

pytestmark = pytest.mark.asyncio

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

GSQL_TEXT = (
    "CREATE VERTEX Person (PRIMARY_ID person_id STRING)\n"
    "CREATE VERTEX Account (PRIMARY_ID account_id STRING)\n"
    'CREATE DIRECTED EDGE OWNS (FROM Person, TO Account) WITH REVERSE_EDGE="reverse_OWNS"\n'
)

# These tests mock at `McpSessionManager.call_tool`, which returns the parsed
# envelope's `data`: {"result": ...} for gsql, and {"graph_name", "schema":
# {"VertexTypes": [...], "EdgeTypes": [...]}, ...} for get_graph_schema.


def make_session_manager(graph_name: str = "AutosentrySandbox") -> MagicMock:
    manager = MagicMock()
    manager.graph_name = graph_name
    manager.call_tool = AsyncMock()
    return manager


def schema_data(vertex_names: list[str], edge_names: list[str]) -> dict:
    return get_graph_schema_data(tigergraph_schema(vertex_names, edge_names))


class TestDeployReferenceSchema:
    async def test_calls_gsql_tool_with_files_exact_contents(self, tmp_path):
        schema_file = tmp_path / "reference_schema.gsql"
        schema_file.write_text(GSQL_TEXT, encoding="utf-8")

        manager = make_session_manager()
        manager.call_tool.side_effect = [gsql_data(), schema_data([], [])]

        await deploy_reference_schema(manager, str(schema_file))

        gsql_call = manager.call_tool.await_args_list[0]
        assert gsql_call.args == ("tigergraph__gsql", {"command": GSQL_TEXT})

    async def test_returns_a_64_char_hex_sha256_hash(self, tmp_path):
        schema_file = tmp_path / "reference_schema.gsql"
        schema_file.write_text(GSQL_TEXT, encoding="utf-8")

        manager = make_session_manager()
        manager.call_tool.side_effect = [
            gsql_data(),
            schema_data(["Person", "Account"], ["OWNS"]),
        ]

        result = await deploy_reference_schema(manager, str(schema_file))

        assert isinstance(result, str)
        assert HEX64_RE.match(result)

    async def test_calls_get_graph_schema_with_session_managers_graph_name(self, tmp_path):
        schema_file = tmp_path / "reference_schema.gsql"
        schema_file.write_text(GSQL_TEXT, encoding="utf-8")

        manager = make_session_manager(graph_name="MyGraph")
        manager.call_tool.side_effect = [gsql_data(), schema_data([], [])]

        await deploy_reference_schema(manager, str(schema_file))

        schema_call = manager.call_tool.await_args_list[1]
        assert schema_call.args == ("tigergraph__get_graph_schema", {"graph_name": "MyGraph"})

    async def test_gsql_failure_propagates_and_skips_hashing(self, tmp_path):
        """A GSQL-level error (the server's gsql_has_error check) arrives as a
        success:false envelope, which call_tool raises as McpToolError.
        deploy must let it propagate and never go on to hash the schema."""
        schema_file = tmp_path / "reference_schema.gsql"
        schema_file.write_text(GSQL_TEXT, encoding="utf-8")

        manager = make_session_manager()
        manager.call_tool.side_effect = McpToolError(
            'tigergraph__gsql failed: GSQL command returned an error:\nEncountered "CREATE"'
        )

        with pytest.raises(McpToolError):
            await deploy_reference_schema(manager, str(schema_file))

        assert manager.call_tool.await_count == 1


class TestComputeSchemaHash:
    async def test_returns_a_64_char_hex_sha256_hash(self):
        manager = make_session_manager()
        manager.call_tool.return_value = schema_data(["Person", "Account"], ["OWNS"])

        result = await compute_schema_hash(manager)

        assert isinstance(result, str)
        assert HEX64_RE.match(result)

    async def test_hashes_the_type_names_from_the_nested_schema(self):
        """Pins the canonical form: sorted "Name"s from data["schema"]."""
        import hashlib

        manager = make_session_manager()
        manager.call_tool.return_value = schema_data(["Person", "Account"], ["OWNS"])

        result = await compute_schema_hash(manager)

        expected = hashlib.sha256(b"Account|Person::OWNS").hexdigest()
        assert result == expected

    async def test_is_order_independent_across_vertex_and_edge_ordering(self):
        """The whole point of sorting before hashing: two schema payloads that
        report the same vertex/edge types in a different order must hash
        identically. This actually runs both orderings through the real
        hashing logic and compares the digests, rather than asserting that
        `sorted()` was invoked."""
        manager_a = make_session_manager()
        manager_a.call_tool.return_value = schema_data(
            ["Person", "Account", "Transaction"], ["OWNS", "INITIATED"]
        )

        manager_b = make_session_manager()
        manager_b.call_tool.return_value = schema_data(
            ["Transaction", "Person", "Account"], ["INITIATED", "OWNS"]
        )

        hash_a = await compute_schema_hash(manager_a)
        hash_b = await compute_schema_hash(manager_b)

        assert hash_a == hash_b

    async def test_different_schemas_hash_differently(self):
        """Sanity check on the other side of the order-independence test:
        genuinely different schemas must not collide."""
        manager_a = make_session_manager()
        manager_a.call_tool.return_value = schema_data(["Person"], [])

        manager_b = make_session_manager()
        manager_b.call_tool.return_value = schema_data(["Person", "Account"], [])

        hash_a = await compute_schema_hash(manager_a)
        hash_b = await compute_schema_hash(manager_b)

        assert hash_a != hash_b

    async def test_handles_empty_vertex_and_edge_lists(self):
        manager = make_session_manager()
        manager.call_tool.return_value = schema_data([], [])

        result = await compute_schema_hash(manager)

        assert HEX64_RE.match(result)

    async def test_handles_missing_vertex_and_edge_keys_inside_schema(self):
        """A schema payload without VertexTypes/EdgeTypes keys must not crash
        the hash function."""
        manager = make_session_manager()
        manager.call_tool.return_value = get_graph_schema_data({"GraphName": "MyGraph"})

        result = await compute_schema_hash(manager)

        assert HEX64_RE.match(result)

    async def test_data_without_nested_schema_fails_loudly(self):
        """If call_tool's data lacks the "schema" key, the payload is not a
        get_graph_schema result; hashing it as an empty schema would make a
        drift check silently compare against nothing."""
        manager = make_session_manager()
        manager.call_tool.return_value = {"VertexTypes": [], "EdgeTypes": []}

        with pytest.raises(KeyError):
            await compute_schema_hash(manager)
