import hashlib
import json

import pytest

from autosentry_agent.seed_data.generator import generate
from autosentry_agent.seed_data.structural_assertions import assert_structural_invariants


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
