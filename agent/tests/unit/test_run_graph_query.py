"""The guarded ad hoc query tool: what it refuses, what it lets through, and how."""

import asyncio
from typing import Any

import pytest

from autosentry_agent.mcp.transport import ToolResult
from autosentry_agent.tools.adhoc import _Schema, check_query, graph_query_tool

from .tigergraph_mcp_fixtures import get_graph_schema_data

SCHEMA = {
    "VertexTypes": [
        {"Name": "Account", "Attributes": [{"AttributeName": "kyc_score"}, {"AttributeName": "status"}]},
        {"Name": "Transaction", "Attributes": [{"AttributeName": "amount"}, {"AttributeName": "channel"}]},
    ],
    "EdgeTypes": [
        {"Name": "INITIATED", "Attributes": [], "Config": {"REVERSE_EDGE": "reverse_INITIATED"}},
        {"Name": "OWNS", "Attributes": [{"AttributeName": "since"}], "Config": {}},
    ],
}
schema = _Schema(SCHEMA)


@pytest.mark.parametrize(
    "query",
    [
        'a = {Account.*}; big = SELECT t FROM a:s -(INITIATED>)- Transaction:t WHERE t.amount > 5000; PRINT big;',
        "SumAccum<INT> @n; a = {Account.*}; a = SELECT s FROM a:s -(INITIATED>:e)- Transaction:t ACCUM s.@n += 1; PRINT a;",
        'x = SELECT t FROM Transaction:t -(reverse_INITIATED>)- Account:a WHERE a.kyc_score <= 0.4 AND t.channel == "wire"; PRINT x;',
        '/* a comment saying DELETE */ a = {Account.*}; PRINT a WHERE a.status == "INSERT me";',
        "a = {Account.*}; x = SELECT s FROM a:s WHERE s.type == \"Account\"; PRINT x.size();",
    ],
)
def test_read_only_schema_valid_queries_pass(query):
    assert check_query(query, schema) == []


@pytest.mark.parametrize(
    ("query", "problem"),
    [
        ("a = {Account.*}; DELETE s FROM a:s;", "not read-only: uses DELETE"),
        ("INSERT INTO Account VALUES (\"x\", 0.1, \"active\");", "not read-only: uses INSERT"),
        ("a = {Account.*}; x = SELECT s FROM a:s POST-ACCUM s.status = \"blocked\";", "assigns attributes (s.status ="),
        ("a = {Account.*}; x = SELECT s FROM a:s ACCUM s.kyc_score += 1;", "assigns attributes"),
        ("DROP GRAPH AutosentrySandbox", "uses DROP"),
        ("a = {Account.*}; PRINT a TO_CSV \"/tmp/out.csv\";", "uses TO_CSV"),
        ("INTERPRET QUERY () FOR GRAPH Other { PRINT 1; }", "uses INTERPRET"),
        ("a = {Customer.*}; PRINT a;", "unknown vertex or edge type(s) ['Customer']"),
        ("x = SELECT t FROM Account:a -(SENT>)- Transaction:t; PRINT x;", "['SENT']"),
        ("x = SELECT a FROM Account:a WHERE a.balance > 10; PRINT x;", "unknown attribute(s) ['a.balance']"),
        ("a = {Account.*}; PRINT a;}", "unbalanced {}"),
    ],
)
def test_writes_and_schema_mistakes_are_refused(query, problem):
    problems = "; ".join(check_query(query, schema))
    assert problem in problems


class FakeClient:
    graph_name = "AutosentrySandbox"

    def __init__(self, result: ToolResult | None = None, delay: float = 0.0) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.result = result or ToolResult(
            ok=True, summary="ok",
            data={"result": [{"big": [{"v_id": "tx-1", "v_type": "Transaction", "attributes": {"amount": 9000}}]}]},
            error=None,
        )
        self.delay = delay

    async def call_data(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool, arguments))
        return get_graph_schema_data(SCHEMA, self.graph_name)

    async def call(self, tool: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((tool, arguments))
        await asyncio.sleep(self.delay)
        return self.result


QUERY = "a = {Account.*}; big = SELECT t FROM a:s -(INITIATED>)- Transaction:t WHERE t.amount > 5000; PRINT big;"


@pytest.mark.asyncio
async def test_a_passing_query_runs_wrapped_for_the_configured_graph():
    client = FakeClient()
    result = await graph_query_tool(client).invoke({"query": QUERY})  # type: ignore[arg-type]

    assert result["ok"] is True
    assert result["evidence"] == [{"type": "Transaction", "id": "tx-1"}]
    tool, args = client.calls[-1]
    assert tool == "tigergraph__run_query"
    assert args["query_text"] == f"INTERPRET QUERY () FOR GRAPH AutosentrySandbox {{\n{QUERY}\n}}"


@pytest.mark.asyncio
async def test_a_refused_query_never_reaches_tigergraph():
    client = FakeClient()
    result = await graph_query_tool(client).invoke({"query": "a = {Account.*}; DELETE s FROM a:s;"})  # type: ignore[arg-type]
    assert result["ok"] is False and "rejected before running" in result["summary"]
    assert [tool for tool, _ in client.calls] == ["tigergraph__get_graph_schema"]


@pytest.mark.asyncio
async def test_a_tigergraph_error_comes_back_for_the_agent_to_fix():
    client = FakeClient(ToolResult(ok=False, summary="", data=None, error="Type Check Error in query (TYP-8017)"))
    result = await graph_query_tool(client).invoke({"query": QUERY})  # type: ignore[arg-type]
    assert result["ok"] is False and "TYP-8017" in result["error"]


@pytest.mark.asyncio
async def test_a_slow_query_times_out():
    client = FakeClient(delay=1.0)
    result = await graph_query_tool(client, timeout_seconds=0.05).invoke({"query": QUERY})  # type: ignore[arg-type]
    assert result["ok"] is False and "timed out" in result["summary"]


@pytest.mark.asyncio
async def test_a_large_result_is_truncated_but_keeps_its_evidence():
    rows = [{"v_id": f"tx-{i}", "v_type": "Transaction", "attributes": {"amount": i}} for i in range(500)]
    client = FakeClient(ToolResult(ok=True, summary="ok", data={"result": [{"big": rows}]}, error=None))
    result = await graph_query_tool(client, max_result_chars=1000).invoke({"query": QUERY})  # type: ignore[arg-type]
    assert result["data"]["truncated"] is True and len(result["data"]["preview"]) == 1000
    assert len(result["evidence"]) == 200


@pytest.mark.asyncio
async def test_the_schema_is_read_once_per_tool():
    client = FakeClient()
    tool = graph_query_tool(client)  # type: ignore[arg-type]
    await tool.invoke({"query": QUERY})
    await tool.invoke({"query": QUERY})
    assert [t for t, _ in client.calls].count("tigergraph__get_graph_schema") == 1
