import hashlib
import json
import re
from pathlib import Path

import pytest

from autosentry_agent.seed_data.generator import generate
from autosentry_agent.seed_data.structural_assertions import assert_structural_invariants

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "reference_schema.gsql"


def _hash_result(result) -> str:
    payload = json.dumps(result.to_dict(), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_generation_is_byte_identical_across_runs():
    result_1 = generate(seed=42, scale="small")
    result_2 = generate(seed=42, scale="small")
    assert _hash_result(result_1) == _hash_result(result_2)


def test_different_seeds_produce_different_output():
    result_1 = generate(seed=42, scale="small")
    result_2 = generate(seed=43, scale="small")
    assert _hash_result(result_1) != _hash_result(result_2)


def test_referential_integrity_every_edge_endpoint_exists():
    result = generate(seed=42, scale="small")
    entity_ids = {e["id"] for e in result.entities}
    for edge in result.edges:
        assert edge["from_id"] in entity_ids
        assert edge["to_id"] in entity_ids


def test_structural_assertions_pass_on_valid_generation():
    result = generate(seed=42, scale="small")
    assert_structural_invariants(result)


def test_structural_assertions_catch_a_generator_with_no_mule_chains():
    result = generate(seed=42, scale="small")
    result.mule_chains = []
    with pytest.raises(AssertionError):
        assert_structural_invariants(result)


def _real_schema_names() -> tuple[set[str], set[str]]:
    """Parse agent/schema/reference_schema.gsql for real vertex/edge type
    names via a simple line-scan (no need for a real GSQL parser)."""
    schema_text = _SCHEMA_PATH.read_text()
    vertex_names = set(re.findall(r"CREATE VERTEX (\w+)", schema_text))
    edge_names = set(re.findall(r"CREATE (?:DIRECTED |UNDIRECTED )?EDGE (\w+)", schema_text))
    return vertex_names, edge_names


def test_generated_entities_and_edges_use_real_schema_types():
    """Regression test for Finding 3: the generator must never emit an
    entity type or edge type that doesn't actually exist in the reference
    schema. This would have caught generate_mule_chains emitting the
    non-existent "PAYS_PROXY" edge type, and the total absence of OWNS
    edges."""
    vertex_names, edge_names = _real_schema_names()
    assert vertex_names, "expected at least one CREATE VERTEX in the schema file"
    assert edge_names, "expected at least one CREATE EDGE in the schema file"

    result = generate(seed=42, scale="small")
    for entity in result.entities:
        assert entity["type"] in vertex_names, f"unknown vertex type: {entity['type']!r}"
    for edge in result.edges:
        assert edge["edge_type"] in edge_names, f"unknown edge type: {edge['edge_type']!r}"


def test_owns_edges_connect_every_person_to_its_account():
    result = generate(seed=42, scale="small")
    owns_pairs = {
        (edge["from_id"], edge["to_id"]) for edge in result.edges if edge["edge_type"] == "OWNS"
    }
    person_ids = sorted(e["id"] for e in result.entities if e["type"] == "Person")
    assert person_ids, "expected at least one Person entity"
    for person_id in person_ids:
        account_id = "account-" + person_id.split("-")[1]
        assert (person_id, account_id) in owns_pairs, f"missing OWNS edge for {person_id}"


def test_mule_chain_hop_counts_match_actual_edges():
    """Regression test for Finding 5: each mule chain's reported hop_count
    must match a count independently derived by walking the chain's real
    INITIATED/PAYS_TO_ACCOUNT edges in the generated graph, not merely
    restate a value the generator assumed while building its own id
    strings. This would catch a bug where the edges themselves were wrong
    even if the summary's own bookkeeping still "added up"."""
    result = generate(seed=42, scale="small")
    assert result.mule_chains, "expected at least one mule chain"

    for chain in result.mule_chains:
        chain_tx_ids = {
            e["id"]
            for e in result.entities
            if e["type"] == "Transaction" and e["id"].startswith(chain["chain_id"] + "-tx-")
        }
        accounts_in_chain = {
            edge["from_id"]
            for edge in result.edges
            if edge["edge_type"] == "INITIATED" and edge["to_id"] in chain_tx_ids
        }
        assert len(accounts_in_chain) == chain["hop_count"], (
            f"{chain['chain_id']}: reported hop_count {chain['hop_count']} does not match "
            f"{len(accounts_in_chain)} accounts found by walking actual edges"
        )


# --- Mule Network scenario (sub-project 1) -------------------------------------------


def _by_id(result) -> dict[str, dict]:
    return {e["id"]: e for e in result.entities}


def _targets(result, from_id: str, edge_type: str) -> set[str]:
    return {e["to_id"] for e in result.edges if e["from_id"] == from_id and e["edge_type"] == edge_type}


def test_every_entity_and_edge_attribute_is_in_the_schema():
    from autosentry_agent.seed_data.loader import plan_load
    from autosentry_agent.seed_data.schema import load_reference_schema

    vertices, edges = plan_load(generate(seed=42, scale="small"), load_reference_schema())
    assert {"Person", "Account", "Transaction", "Device", "IPAddress", "Merchant", "Beneficiary", "InvestigationCase"} <= set(vertices)
    assert {"OWNS", "INITIATED", "FROM_DEVICE", "FROM_IP", "AT", "PAYS", "PAYS_TO_ACCOUNT", "INVESTIGATED_IN"} <= set(edges)


def test_mule_accounts_are_new_and_low_kyc_but_so_are_some_ordinary_ones():
    """Each mule signal also appears on ordinary accounts, so no single
    signal identifies a mule: the scenario has to be investigated."""
    result = generate(seed=42, scale="small")
    entities = _by_id(result)
    mules = set(result.ground_truth["mule_accounts"])
    ordinary = [e for e in result.entities if e["type"] == "Account" and e["id"] not in mules]
    assert mules
    assert all(entities[m]["kyc_score"] < 0.4 for m in mules)
    assert all(entities[m]["open_date"] >= "2026-06-01 00:00:00" for m in mules)
    assert any(a["kyc_score"] < 0.4 for a in ordinary), "no ordinary low-KYC look-alikes"
    assert any(a["open_date"] >= "2026-06-01 00:00:00" for a in ordinary), "no ordinary new accounts"


def test_some_households_share_a_device_benignly():
    result = generate(seed=42, scale="small")
    ring_devices = {d for chain in result.ground_truth["mule_chains"] for d in chain["shared_devices"]}
    initiator = {e["to_id"]: e["from_id"] for e in result.edges if e["edge_type"] == "INITIATED"}
    users: dict[str, set[str]] = {}
    for edge in result.edges:
        if edge["edge_type"] == "FROM_DEVICE" and edge["to_id"] not in ring_devices:
            users.setdefault(edge["to_id"], set()).add(initiator[edge["from_id"]])
    assert any(len(accounts) == 2 for accounts in users.values())


def test_no_transaction_predates_its_accounts_open_date():
    result = generate(seed=42, scale="small")
    entities = _by_id(result)
    for edge in result.edges:
        if edge["edge_type"] == "INITIATED":
            account, tx = entities[edge["from_id"]], entities[edge["to_id"]]
            assert tx["timestamp"] >= min(account["open_date"], "2026-06-01 00:00:00"), tx["id"]


def test_each_ring_shares_its_device_and_ip_only_within_the_ring():
    result = generate(seed=42, scale="small")
    for chain in result.ground_truth["mule_chains"]:
        device = chain["shared_devices"][0]
        users = {e["from_id"] for e in result.edges if e["edge_type"] == "FROM_DEVICE" and e["to_id"] == device}
        assert users == set(chain["transactions"])


def test_benign_shared_ips_exist_so_sharing_alone_is_not_proof():
    result = generate(seed=42, scale="small")
    ring_ips = {ip for chain in result.ground_truth["mule_chains"] for ip in chain["shared_ips"]}
    initiator = {e["to_id"]: e["from_id"] for e in result.edges if e["edge_type"] == "INITIATED"}
    accounts_per_ip: dict[str, set[str]] = {}
    for edge in result.edges:
        if edge["edge_type"] == "FROM_IP" and edge["to_id"] not in ring_ips:
            accounts_per_ip.setdefault(edge["to_id"], set()).add(initiator[edge["from_id"]])
    assert max(len(accounts) for accounts in accounts_per_ip.values()) >= 5


def test_structuring_overlaps_some_mule_accounts():
    result = generate(seed=42, scale="small")
    mules = set(result.ground_truth["mule_accounts"])
    accounts = [c["account"] for c in result.ground_truth["structuring_clusters"]]
    assert any(a in mules for a in accounts)
    assert any(a not in mules for a in accounts)


def test_the_seeded_case_opens_on_a_mule_account():
    result = generate(seed=42, scale="small")
    case = result.ground_truth["case"]
    assert case["subject"] in result.ground_truth["mule_accounts"]
    assert "case-0001" in _targets(result, case["subject"], "INVESTIGATED_IN")


def test_structural_assertions_catch_a_ring_without_its_cash_out():
    result = generate(seed=42, scale="small")
    result.edges = [e for e in result.edges if e["edge_type"] != "PAYS"]
    with pytest.raises(AssertionError, match="cash out"):
        assert_structural_invariants(result)


def test_ground_truth_is_part_of_the_deterministic_output():
    a, b = generate(seed=42, scale="small"), generate(seed=42, scale="small")
    assert a.ground_truth == b.ground_truth
    assert a.ground_truth["mule_accounts"] != generate(seed=43, scale="small").ground_truth["mule_accounts"]
