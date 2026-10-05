# agent/tests/integration/test_agent_live.py
# The real Fraud Investigator (a real LLM, the real tools) on the spec's
# section 15 reference questions, scored against the seed's ground truth.
# Needs the small seed 42 in AutosentrySandbox with the query library
# installed, graphrag serving the regulatory corpus, and LLM_API_KEY,
# GRAPH_DATA_MCP_URL, TG_HOST and TG_JWT_TOKEN; skipped otherwise. Read-only.
#
# An LLM is not deterministic: these check what an answer must contain to
# be right (which entities and documents it cites), not its wording.
import os

import pytest

from autosentry_agent.config import GraphDataSettings, get_secrets_provider
from autosentry_agent.mcp.transport import GraphDataMcpClient
from autosentry_agent.seed_data.generator import generate

pytestmark = [
    # langchain-openai caches one async HTTP client per process, so every test
    # here shares one event loop (as a server does) instead of one each.
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not all(os.environ.get(v) for v in ("LLM_API_KEY", "GRAPH_DATA_MCP_URL", "TG_HOST", "TG_JWT_TOKEN")),
        reason="needs LLM_API_KEY, GRAPH_DATA_MCP_URL, TG_HOST and TG_JWT_TOKEN",
    ),
]


@pytest.fixture(scope="module")
def truth():
    return generate(seed=42, scale="small").ground_truth


@pytest.fixture
def investigator():
    from autosentry_agent.main import build_investigator

    secrets = get_secrets_provider()
    built, why = build_investigator(GraphDataMcpClient(GraphDataSettings(), secrets), secrets)
    assert built is not None, why
    return built


def _accounts(result: dict) -> set[str]:
    return {e["id"] for e in result["cited_evidence"] if e["type"] == "Account"}


async def test_list_mule_accounts(investigator, truth):
    result = await investigator.ask("List mule accounts in the graph")
    cited = _accounts(result)
    mules = set(truth["mule_accounts"])
    assert result["unbacked"] == []
    assert len(cited & mules) / len(cited) >= 0.8, f"cited {sorted(cited - mules)} that are not mules"
    assert len(cited & mules) >= len(mules) * 0.8, "missed most of the mules"


async def test_investigate_a_ring_member(investigator, truth):
    chain = truth["mule_chains"][0]
    result = await investigator.ask(
        f"Investigate {chain['accounts'][0]}: is this part of a fraud ring? What type of fraud is it?"
    )
    cited = {e["id"] for e in result["cited_evidence"]}
    assert result["unbacked"] == []
    assert len(set(chain["accounts"]) & cited) >= len(chain["accounts"]) - 1, "didn't name the ring"
    assert chain["shared_devices"][0] in cited or chain["beneficiary"] in cited, "no ring evidence cited"
    assert "mule" in result["answer"].lower()


async def test_regulatory_report_question_cites_fincen(investigator):
    result = await investigator.ask("Do I need to file a regulatory report on money mule accounts?")
    documents = {d["doc_id"] for d in result["cited_documents"]}
    assert result["unbacked"] == []
    assert documents & {"fincen_sar_faqs_2025", "fincen_imposter_money_mule_advisory_2020", "fincen_sar_narrative_guidance"}
    assert "sar" in result["answer"].lower() or "suspicious activity report" in result["answer"].lower()
