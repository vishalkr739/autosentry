def assert_structural_invariants(result) -> None:
    """A generator can be deterministic and still wrong (spec section
    4.4): these assertions catch a silently-broken pattern generator by
    checking each ground-truth claim against the edges actually emitted."""
    assert len(result.mule_chains) >= 1, "expected at least one mule chain, found zero"
    for chain in result.mule_chains:
        assert 3 <= chain["hop_count"] <= 6, f"mule chain hop_count out of range: {chain['hop_count']}"

    entities = {e["id"]: e for e in result.entities}
    edges_from: dict[tuple[str, str], set[str]] = {}
    for edge in result.edges:
        edges_from.setdefault((edge["from_id"], edge["edge_type"]), set()).add(edge["to_id"])

    for chain in result.ground_truth["mule_chains"]:
        txs = chain["transactions"]
        assert len(txs) == len(chain["accounts"]), f"{chain['chain_id']}: one transaction per account"
        times = [entities[tx]["timestamp"] for tx in txs]
        assert times == sorted(times), f"{chain['chain_id']}: transfers out of time order"
        for account, tx in zip(chain["accounts"], txs):
            assert tx in edges_from.get((account, "INITIATED"), set()), f"{tx} not initiated by {account}"
            for device in chain["shared_devices"]:
                assert device in edges_from.get((tx, "FROM_DEVICE"), set()), f"{tx} not from ring device"
            for ip in chain["shared_ips"]:
                assert ip in edges_from.get((tx, "FROM_IP"), set()), f"{tx} not from ring IP"
        assert chain["beneficiary"] in edges_from.get((txs[-1], "PAYS"), set()), (
            f"{chain['chain_id']}: last transfer does not cash out to its beneficiary"
        )

    for cluster in result.ground_truth["structuring_clusters"]:
        for tx in cluster["transactions"]:
            assert tx in edges_from.get((cluster["account"], "INITIATED"), set()), f"{tx} has no account"
            assert entities[tx]["amount"] < 1000.0, f"{tx} is not under the reporting threshold"
