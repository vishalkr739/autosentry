import numpy as np

from .config import PatternParameters, ScaleConfig


def generate_mule_chains(
    rng: np.random.Generator, entities: list[dict], scale: ScaleConfig, params: PatternParameters
) -> tuple[list[dict], list[dict], list[dict]]:
    """Layered PAYS_TO_ACCOUNT/INITIATED chains, 3 to 6 hops (spec section
    4.2). Returns (new_entities, new_edges, chain_summaries), where
    chain_summaries' hop_count is derived from the actual edges just
    appended (see _hop_count_from_edges) rather than from the id-prefix
    naming scheme, so a bug in the edge-generation logic itself would
    surface here instead of being silently re-confirmed."""
    account_ids = [e["id"] for e in entities if e["type"] == "Account"]
    new_entities: list[dict] = []
    new_edges: list[dict] = []
    chain_summaries: list[dict] = []

    for c in range(scale.mule_chain_count):
        hop_count = int(rng.integers(params.mule_chain_min_hops, params.mule_chain_max_hops + 1))
        chosen_accounts = rng.choice(account_ids, size=hop_count, replace=False)
        chain_id = f"mule-chain-{c:04d}"
        chain_tx_ids = []
        chain_edges_start = len(new_edges)
        for hop, account_id in enumerate(chosen_accounts):
            tx_id = f"{chain_id}-tx-{hop:02d}"
            new_entities.append({"id": tx_id, "type": "Transaction", "amount": float(rng.uniform(500, 5000))})
            new_edges.append({"from_id": account_id, "to_id": tx_id, "edge_type": "INITIATED"})
            chain_tx_ids.append(tx_id)
            if hop > 0:
                new_edges.append({"from_id": chain_tx_ids[hop - 1], "to_id": account_id, "edge_type": "PAYS_TO_ACCOUNT"})

        chain_edges = new_edges[chain_edges_start:]
        chain_summaries.append({"chain_id": chain_id, "hop_count": _hop_count_from_edges(chain_edges)})

    return new_entities, new_edges, chain_summaries


def _hop_count_from_edges(chain_edges: list[dict]) -> int:
    """Derive a mule chain's hop_count (the number of distinct accounts in
    the layered chain) by walking its actual INITIATED/PAYS_TO_ACCOUNT
    edges forward, starting from the one account with no incoming
    PAYS_TO_ACCOUNT edge within this chain's edges."""
    initiated = {e["from_id"]: e["to_id"] for e in chain_edges if e["edge_type"] == "INITIATED"}
    pays_to_account = {e["from_id"]: e["to_id"] for e in chain_edges if e["edge_type"] == "PAYS_TO_ACCOUNT"}
    accounts_with_incoming_payment = set(pays_to_account.values())
    start_accounts = [account for account in initiated if account not in accounts_with_incoming_payment]
    assert len(start_accounts) == 1, (
        f"expected exactly one chain start account, found {len(start_accounts)}"
    )

    visited: list[str] = []
    account = start_accounts[0]
    while account in initiated:
        visited.append(account)
        tx_id = initiated[account]
        next_account = pays_to_account.get(tx_id)
        if next_account is None:
            break
        account = next_account
    return len(visited)


def generate_structuring_clusters(rng: np.random.Generator, entities: list[dict], scale: ScaleConfig, params: PatternParameters) -> list[dict]:
    """Transaction amounts clustered just under the reporting threshold
    (spec section 4.2), e.g. repeated values near $994 against $1,000."""
    account_ids = [e["id"] for e in entities if e["type"] == "Account"]
    new_entities = []
    for c in range(scale.structuring_cluster_count):
        account_id = rng.choice(account_ids)
        for k in range(int(rng.integers(3, 8))):
            amount = params.structuring_target_amount + float(rng.uniform(-5, 5))
            tx_id = f"structuring-tx-{c:04d}-{k:02d}"
            new_entities.append({"id": tx_id, "type": "Transaction", "amount": amount, "_account_id": account_id})
    return new_entities
