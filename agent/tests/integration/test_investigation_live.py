# agent/tests/integration/test_investigation_live.py
# The investigation tools against the live sandbox, scored against the
# seed's ground truth. Needs the small seed 42 loaded into AutosentrySandbox
# and the query library installed (python -m autosentry_agent.seed_data load,
# python -m autosentry_agent.query_library install); skipped without
# GRAPH_DATA_MCP_URL, TG_HOST and TG_JWT_TOKEN. Read-only.
import os
from pathlib import Path

import httpx
import pytest

from autosentry_agent.config import GraphDataSettings, get_secrets_provider
from autosentry_agent.ingestion.corpus import load_corpus
from autosentry_agent.ingestion.graphrag_current import CurrentGraphragBackend
from autosentry_agent.mcp.transport import GraphDataMcpClient
from autosentry_agent.seed_data.generator import generate
from autosentry_agent.tools import build_tools

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not all(os.environ.get(v) for v in ("GRAPH_DATA_MCP_URL", "TG_HOST", "TG_JWT_TOKEN")),
        reason="needs GRAPH_DATA_MCP_URL, TG_HOST and TG_JWT_TOKEN for a live tigergraph-mcp",
    ),
]

_CORPUS = Path(__file__).resolve().parents[2] / "autosentry_agent" / "ingestion" / "corpora" / "regulatory.toml"


@pytest.fixture(scope="module")
def truth():
    return generate(seed=42, scale="small").ground_truth


@pytest.fixture
def tools():
    client = GraphDataMcpClient(GraphDataSettings(), get_secrets_provider())
    backend = CurrentGraphragBackend(
        os.environ.get("GRAPHRAG_BASE_URL", "http://localhost:8000"),
        os.environ["TG_JWT_TOKEN"],
        http=httpx.Client(timeout=180),
    )
    return {t.name: t for t in build_tools(client, knowledge_backend=backend, corpus=load_corpus(_CORPUS))}


async def test_mule_candidates_rank_the_real_mules_first(tools, truth):
    mules = set(truth["mule_accounts"])
    result = await tools["find_mule_candidates"].invoke({"top_k": len(mules) + 5})
    assert result["ok"], result["error"]
    top = [c["account"] for c in result["data"]["candidates"]][: len(mules)]
    precision = len(set(top) & mules) / len(mules)
    assert precision >= 0.8, f"only {precision:.0%} of the top {len(mules)} are mules"


async def test_every_ring_traces_end_to_end(tools, truth):
    for chain in truth["mule_chains"]:
        result = await tools["trace_fund_transfer_chain"].invoke({"account": chain["accounts"][0]})
        assert result["ok"], result["error"]
        path = [chain["accounts"][0]]
        for hop in result["data"]["hops"]:
            if hop["from_account"] == path[-1] and hop["tx"] in chain["transactions"]:
                path.append(hop["to_account"])
        assert path == chain["accounts"], chain["chain_id"]
        assert chain["beneficiary"] in {c["beneficiary"] for c in result["data"]["cash_outs"]}


async def test_shared_infrastructure_surfaces_each_rings_device(tools, truth):
    for chain in truth["mule_chains"]:
        result = await tools["find_shared_infrastructure"].invoke({"account": chain["accounts"][-1]})
        assert result["ok"], result["error"]
        devices = {d["device"]: d for d in result["data"]["devices"]}
        ring = devices[chain["shared_devices"][0]]
        assert ring["user_count"] == len(chain["accounts"])
        assert set(ring["other_accounts"]) == set(chain["accounts"]) - {chain["accounts"][-1]}


async def test_structuring_finds_every_cluster_account(tools, truth):
    result = await tools["find_structuring_pattern"].invoke({})
    assert result["ok"], result["error"]
    found = {c["account"] for c in result["data"]["clusters"]}
    assert found == {c["account"] for c in truth["structuring_clusters"]}


async def test_guarded_query_runs_reads_and_refuses_writes(tools):
    query = tools["run_graph_query"]
    read = await query.invoke({
        "query": 'a = {Account.*}; big = SELECT t FROM a:s -(INITIATED>)- Transaction:t '
                 'WHERE t.channel == "wire" AND t.amount > 3000; PRINT big[big.amount];'
    })
    assert read["ok"], read["error"]
    assert read["evidence"] and all(e["type"] == "Transaction" for e in read["evidence"])
    write = await query.invoke({"query": 'a = {Account.*}; x = SELECT s FROM a:s POST-ACCUM s.status = "blocked";'})
    assert write["ok"] is False and "not read-only" in write["error"]


async def test_knowledge_search_cites_the_sar_guidance(tools):
    if os.environ.get("SKIP_GRAPHRAG"):
        pytest.skip("SKIP_GRAPHRAG set")
    result = await tools["search_knowledge"].invoke({"question": "What are the five essential elements a SAR narrative should cover?"})
    assert result["ok"], result["error"]
    assert "fincen_sar_narrative_guidance" in {c["doc_id"] for c in result["citations"]}
