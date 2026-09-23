import numpy as np

from .config import PatternParameters, ScaleConfig


def generate_mule_chains(rng: np.random.Generator, entities: list[dict], scale: ScaleConfig, params: PatternParameters) -> tuple[list[dict], list[dict]]:
    """Layered PAYS/INITIATED chains, 3 to 6 hops (spec section 4.2)."""
    account_ids = [e["id"] for e in entities if e["type"] == "Account"]
    new_entities: list[dict] = []
    new_edges: list[dict] = []

    for c in range(scale.mule_chain_count):
        hop_count = int(rng.integers(params.mule_chain_min_hops, params.mule_chain_max_hops + 1))
        chosen_accounts = rng.choice(account_ids, size=hop_count, replace=False)
        chain_tx_ids = []
        for hop, account_id in enumerate(chosen_accounts):
            tx_id = f"mule-chain-{c:04d}-tx-{hop:02d}"
            new_entities.append({"id": tx_id, "type": "Transaction", "amount": float(rng.uniform(500, 5000))})
            new_edges.append({"from_id": account_id, "to_id": tx_id, "edge_type": "INITIATED"})
            chain_tx_ids.append(tx_id)
            if hop > 0:
                new_edges.append({"from_id": chain_tx_ids[hop - 1], "to_id": account_id, "edge_type": "PAYS_TO_ACCOUNT"})

    return new_entities, new_edges


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
