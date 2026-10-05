# agent/tests/integration/test_seed_live.py
# Loads the small seed into $TG_GRAPHNAME (must be AutosentrySandbox) through
# the tigergraph-mcp at $GRAPH_DATA_MCP_URL and checks it in TigerGraph
# itself; skipped without GRAPH_DATA_MCP_URL, TG_HOST and TG_JWT_TOKEN.
# Writes to that graph (an idempotent upsert of the same seed).
import os

import pytest
from tigergraph_mcp.tool_names import TigerGraphToolName

from autosentry_agent.config import GraphDataSettings, get_secrets_provider
from autosentry_agent.mcp.transport import GraphDataMcpClient
from autosentry_agent.seed_data.generator import generate
from autosentry_agent.seed_data.loader import load_seed

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not all(os.environ.get(v) for v in ("GRAPH_DATA_MCP_URL", "TG_HOST", "TG_JWT_TOKEN")),
        reason="needs GRAPH_DATA_MCP_URL, TG_HOST and TG_JWT_TOKEN for a live tigergraph-mcp",
    ),
]


@pytest.fixture(scope="module")
def seed():
    return generate(seed=42, scale="small")


@pytest.fixture
def client() -> GraphDataMcpClient:
    return GraphDataMcpClient(GraphDataSettings(), get_secrets_provider())


async def test_seed_loads_and_verifies(client, seed):
    report = await load_seed(client, seed)
    assert report.vertex_counts["Account"] == 200
    assert report.edge_counts["PAYS"] == len(seed.ground_truth["mule_chains"])


async def test_a_mule_ring_walks_end_to_end_in_the_graph(client, seed):
    """Every hop of the first ring, as the ground truth describes it, is a
    real edge in TigerGraph: initiated by its account, sent from the ring's
    shared device and IP, paid into the next account, cashed out at the end."""
    chain = seed.ground_truth["mule_chains"][0]
    accounts, txs = chain["accounts"], chain["transactions"]

    hops = []
    for k, (account, tx) in enumerate(zip(accounts, txs)):
        hops.append(("Account", account, "INITIATED", "Transaction", tx))
        hops.append(("Transaction", tx, "FROM_DEVICE", "Device", chain["shared_devices"][0]))
        hops.append(("Transaction", tx, "FROM_IP", "IPAddress", chain["shared_ips"][0]))
        if k > 0:
            hops.append(("Transaction", txs[k - 1], "PAYS_TO_ACCOUNT", "Account", account))
    hops.append(("Transaction", txs[-1], "PAYS", "Beneficiary", chain["beneficiary"]))

    async with client.connected():
        for source_type, source_id, edge_type, target_type, target_id in hops:
            data = await client.call_data(
                TigerGraphToolName.HAS_EDGE.value,
                {
                    "graph_name": client.graph_name,
                    "source_vertex_type": source_type,
                    "source_vertex_id": source_id,
                    "edge_type": edge_type,
                    "target_vertex_type": target_type,
                    "target_vertex_id": target_id,
                },
            )
            assert data["exists"] is True, f"missing {source_id} -{edge_type}-> {target_id}"
