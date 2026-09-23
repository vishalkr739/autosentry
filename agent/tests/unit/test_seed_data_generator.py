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
