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

    _assert_network_patterns(result.ground_truth, entities, edges_from)


def _payee(edges_from, tx: str) -> str | None:
    targets = edges_from.get((tx, "PAYS_TO_ACCOUNT"), set()) | edges_from.get((tx, "PAYS"), set())
    return next(iter(targets), None)


def _assert_network_patterns(truth: dict, entities: dict, edges_from: dict) -> None:
    from datetime import datetime

    def at(tx: str) -> datetime:
        return datetime.fromisoformat(entities[tx]["timestamp"])

    for cycle in truth.get("round_trips", []):
        accounts, txs = cycle["accounts"], cycle["transactions"]
        times = [at(tx) for tx in txs]
        assert times == sorted(times), f"{cycle['cycle_id']}: hops out of time order"
        for hop, tx in enumerate(txs):
            assert tx in edges_from.get((accounts[hop], "INITIATED"), set()), f"{tx} not sent by its hop"
            assert _payee(edges_from, tx) == accounts[(hop + 1) % len(accounts)], f"{tx} pays the wrong account"
        assert (times[-1] - times[0]).days < 7, f"{cycle['cycle_id']}: loop takes a week or more"
        assert entities[txs[-1]]["amount"] >= 0.5 * entities[txs[0]]["amount"], f"{cycle['cycle_id']}: loses half"

    for funnel in truth.get("funnels", []):
        assert len(set(funnel["victims"])) == len(funnel["inbound"]) >= 5, "a funnel needs 5+ distinct senders"
        inbound_times = [at(tx) for tx in funnel["inbound"]]
        assert (max(inbound_times) - min(inbound_times)).days < 7, "funnel inflows span a week or more"
        assert _payee(edges_from, funnel["cash_out"]) == funnel["beneficiary"], "funnel cash-out pays elsewhere"
        assert at(funnel["cash_out"]) > max(inbound_times), "funnel cashes out before the money arrives"

    for stream in truth.get("controller_payments", []) + truth.get("landlords", []):
        assert len(stream["transactions"]) >= 3, f"{stream['from']}->{stream['to']}: not repeated"
        assert all(_payee(edges_from, tx) == stream["to"] for tx in stream["transactions"])

    for pair in truth.get("family_pairs", []):
        times = sorted(at(tx) for tx in pair["transactions"])
        assert all((b - a).days >= 7 for a, b in zip(times, times[1:])), "family transfers too close together"

    for business in truth.get("businesses", []):
        times = sorted(at(tx) for tx in business["transactions"])
        assert (times[-1] - times[0]).days >= 30, "business income is not spread out"
