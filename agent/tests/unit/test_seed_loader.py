"""load_seed against an in-memory stand-in for tigergraph-mcp's tools.

The MCP wire protocol is covered by test_graph_data_client.py; here the
client is faked at `call_data` (what load_seed calls), with a graph that
really stores what add_nodes/add_edges send, so the count check is tested
against real bookkeeping rather than canned numbers.
"""

from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any

import pytest

from autosentry_agent.mcp.transport import GraphDataToolError
from autosentry_agent.seed_data.generator import GenerationResult, generate
from autosentry_agent.seed_data.loader import SeedLoadError, load_seed, plan_load
from autosentry_agent.seed_data.schema import load_reference_schema

from .tigergraph_mcp_fixtures import get_graph_schema_data, tigergraph_schema


class FakeGraphData:
    def __init__(self, graph_name: str = "AutosentrySandbox", graph_exists: bool = True) -> None:
        self.graph_name = graph_name
        self.graphs = [graph_name] if graph_exists else []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.vertices: dict[str, set[str]] = defaultdict(set)
        self.edges: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self.sessions = 0
        self.drop_edges_of: str | None = None  # simulate a load that silently lost edges
        self.stale_count_reads = 0  # counts read low this many times, like fresh REST++ stats

    @asynccontextmanager
    async def connected(self):
        self.sessions += 1
        yield

    async def call_data(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool, arguments))
        name = tool.removeprefix("tigergraph__")
        if name == "list_graphs":
            return {"graphs": list(self.graphs), "count": len(self.graphs)}
        if name == "gsql":
            self.graphs.append(self.graph_name)
            return {"result": "ok"}
        if name == "get_graph_schema":
            return get_graph_schema_data(tigergraph_schema(["Account"], ["OWNS"]), self.graph_name)
        if name == "clear_graph_data":
            assert arguments["confirm"] is True
            self.vertices.clear()
            self.edges.clear()
            return {}
        if name == "add_nodes":
            self.vertices[arguments["vertex_type"]].update(v["id"] for v in arguments["vertices"])
            return {}
        if name == "add_edges":
            if arguments["edge_type"] != self.drop_edges_of:
                self.edges[arguments["edge_type"]].update(
                    (e["source_id"], e["target_id"]) for e in arguments["edges"]
                )
            return {}
        if name == "get_vertex_count":
            count = len(self.vertices[arguments["vertex_type"]])
            if self.stale_count_reads > 0:
                self.stale_count_reads -= 1
                count //= 2
            return {"count": count}
        if name == "get_edge_count":
            return {"count": len(self.edges[arguments["edge_type"]])}
        raise GraphDataToolError(f"unexpected tool {tool}")

    def tools_called(self) -> list[str]:
        return [tool.removeprefix("tigergraph__") for tool, _ in self.calls]


@pytest.fixture(scope="module")
def seed() -> GenerationResult:
    return generate(seed=42, scale="small")


@pytest.mark.asyncio
async def test_loads_into_an_existing_graph_and_verifies_counts(seed):
    fake = FakeGraphData()
    report = await load_seed(fake, seed)

    assert report.deployed_schema is False
    assert report.vertex_counts["Account"] == 200
    assert report.vertex_counts["Transaction"] == sum(1 for e in seed.entities if e["type"] == "Transaction")
    assert report.edge_counts["PAYS"] == 5
    assert len(report.schema_hash) == 64 and len(report.data_hash) == 64
    assert "gsql" not in fake.tools_called() and "clear_graph_data" not in fake.tools_called()
    assert fake.sessions == 1  # the whole load runs in one MCP session


@pytest.mark.asyncio
async def test_deploys_the_reference_schema_when_the_graph_is_missing(seed):
    fake = FakeGraphData(graph_exists=False)
    report = await load_seed(fake, seed)
    assert report.deployed_schema is True
    assert fake.tools_called()[:2] == ["list_graphs", "gsql"]
    assert "CREATE GRAPH AutosentrySandbox" in fake.calls[1][1]["command"]


@pytest.mark.asyncio
async def test_reset_clears_the_graph_only_when_asked(seed):
    fake = FakeGraphData()
    fake.vertices["Account"].add("account-from-an-old-seed")

    with pytest.raises(SeedLoadError, match="Account: expected 200, graph has 201"):
        await load_seed(fake, seed, verify_timeout=0)

    report = await load_seed(fake, seed, reset=True)
    assert report.reset is True and report.vertex_counts["Account"] == 200
    assert fake.tools_called().count("clear_graph_data") == 1


@pytest.mark.asyncio
async def test_reloading_the_same_seed_is_idempotent(seed):
    fake = FakeGraphData()
    first = await load_seed(fake, seed)
    second = await load_seed(fake, seed)
    assert first.vertex_counts == second.vertex_counts and first.data_hash == second.data_hash


@pytest.mark.asyncio
async def test_a_load_that_loses_edges_fails_the_check(seed):
    fake = FakeGraphData()
    fake.drop_edges_of = "FROM_IP"
    with pytest.raises(SeedLoadError, match="FROM_IP: expected"):
        await load_seed(fake, seed, verify_timeout=0)


@pytest.mark.asyncio
async def test_batches_and_payload_shapes(seed):
    fake = FakeGraphData()
    await load_seed(fake, seed, batch_size=100)

    node_calls = [args for tool, args in fake.calls if tool.endswith("add_nodes")]
    edge_calls = [args for tool, args in fake.calls if tool.endswith("add_edges")]
    assert all(len(args["vertices"]) <= 100 for args in node_calls)
    assert sum(len(a["vertices"]) for a in node_calls if a["vertex_type"] == "Account") == 200
    account = next(a for a in node_calls if a["vertex_type"] == "Account")["vertices"][0]
    assert set(account) == {"id", "kyc_score", "open_date", "status"}
    assert all(args["graph_name"] == "AutosentrySandbox" for args in node_calls + edge_calls)

    owns = next(a for a in edge_calls if a["edge_type"] == "OWNS")["edges"][0]
    assert owns["source_type"] == "Person" and owns["target_type"] == "Account"
    assert "since" in owns and "edge_type" not in owns


@pytest.mark.asyncio
async def test_refuses_a_client_aimed_at_a_different_graph(seed):
    with pytest.raises(SeedLoadError, match="creates AutosentrySandbox"):
        await load_seed(FakeGraphData(graph_name="AutosentryRegulatoryKB"), seed)


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.mark.asyncio
async def test_waits_for_lagging_counts_to_settle(seed):
    fake = FakeGraphData()
    # 8 vertex types are read per pass, so 10 stale reads span two passes.
    fake.stale_count_reads = 10
    time = FakeTime()
    report = await load_seed(
        fake, seed, verify_timeout=60, verify_interval=3, clock=time.clock, sleep=time.sleep
    )
    assert report.vertex_counts["Account"] == 200
    assert time.slept == [3, 3]


@pytest.mark.asyncio
async def test_gives_up_when_counts_never_settle(seed):
    fake = FakeGraphData()
    fake.drop_edges_of = "PAYS"
    time = FakeTime()
    with pytest.raises(SeedLoadError, match="PAYS: expected 5, graph has 0"):
        await load_seed(fake, seed, verify_timeout=9, verify_interval=3, clock=time.clock, sleep=time.sleep)
    assert time.now >= 9


def test_plan_load_refuses_attributes_the_schema_does_not_declare():
    broken = GenerationResult(
        entities=[{"id": "account-x", "type": "Account", "kyc_score": 0.5, "nickname": "x"}],
        edges=[],
    )
    with pytest.raises(SeedLoadError, match=r"\['nickname'\] not attributes of Account"):
        plan_load(broken, load_reference_schema())


def test_plan_load_refuses_unknown_types():
    broken = GenerationResult(
        entities=[{"id": "p", "type": "Pet"}],
        edges=[{"from_id": "a", "to_id": "b", "edge_type": "LIKES"}],
    )
    with pytest.raises(SeedLoadError, match="2 schema problem"):
        plan_load(broken, load_reference_schema())
