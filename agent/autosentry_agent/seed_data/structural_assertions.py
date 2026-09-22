def assert_structural_invariants(result) -> None:
    """A generator can be deterministic and still wrong (spec section
    4.4): these assertions catch a silently-broken pattern generator."""
    assert len(result.mule_chains) >= 1, "expected at least one mule chain, found zero"
    for chain in result.mule_chains:
        assert 3 <= chain["hop_count"] <= 6, f"mule chain hop_count out of range: {chain['hop_count']}"
