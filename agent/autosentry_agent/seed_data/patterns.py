import numpy as np

from .base_entities import BaseGraph, format_ts, window_seconds
from .config import ActivityParameters, PatternParameters, ScaleConfig


def generate_mule_chains(
    rng: np.random.Generator,
    base: BaseGraph,
    scale: ScaleConfig,
    params: PatternParameters,
    activity: ActivityParameters,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Mule rings: layered PAYS_TO_ACCOUNT/INITIATED chains of 3 to 6
    accounts (spec section 4.1.2) that end in a cash-out PAYS to an external
    Beneficiary. Each ring's transfers run from one shared device and one
    shared VPN IP, move within minutes to hours of each other, and shrink a
    little at every hop. The ring's accounts are made newer and lower-KYC,
    which makes them findable without making them trivially obvious.

    Returns (new_entities, new_edges, chain_summaries, chain_truth).
    chain_summaries' hop_count is derived from the actual edges just
    appended (see _hop_count_from_edges) rather than from the id-prefix
    naming scheme, so a bug in the edge-generation logic itself would
    surface there instead of being silently re-confirmed.
    """
    new_entities: list[dict] = []
    new_edges: list[dict] = []
    chain_summaries: list[dict] = []
    chain_truth: list[dict] = []
    used: set[str] = set()
    span = window_seconds(activity)

    for c in range(scale.mule_chain_count):
        chain_id = f"mule-chain-{c:04d}"
        hop_count = int(rng.integers(params.mule_chain_min_hops, params.mule_chain_max_hops + 1))
        available = [a for a in base.account_ids if a not in used]
        chosen_accounts = [str(a) for a in rng.choice(available, size=hop_count, replace=False)]
        used.update(chosen_accounts)

        ring_device = f"device-ring-{c:04d}"
        ring_ip = f"198.51.{100 + c // 250}.{c % 250 + 1}"
        new_entities.append({
            "id": ring_device,
            "type": "Device",
            "fingerprint": f"fp-{rng.integers(0, 2**48):012x}",
            "first_seen_at": format_ts(activity, float(rng.uniform(0, span * 0.3))),
        })
        new_entities.append({
            "id": ring_ip,
            "type": "IPAddress",
            "is_vpn": True,
            "country_code": str(rng.choice(["NL", "RO", "NG", "VN"])),
        })
        beneficiary_id = f"beneficiary-{c:04d}"
        new_entities.append({
            "id": beneficiary_id,
            "type": "Beneficiary",
            "name_hash": f"name-hash-{rng.integers(0, 2**32):08x}",
            "bank_identifier": f"XB{rng.integers(10**7, 10**8)}",
        })

        for account_id in chosen_accounts:
            account = base.accounts[account_id]
            # Opened shortly before the ring starts moving money.
            opened = float(rng.uniform(span * 0.3, span * 0.5))
            base.opened_at[account_id] = opened
            account["open_date"] = format_ts(activity, opened)
            account["kyc_score"] = float(rng.uniform(0.05, 0.4))

        start = float(rng.uniform(span * 0.6, span * 0.9))
        amount = float(rng.uniform(3000, 9000))
        chain_tx_ids: list[str] = []
        chain_edges_start = len(new_edges)
        moment = start
        for hop, account_id in enumerate(chosen_accounts):
            tx_id = f"{chain_id}-tx-{hop:02d}"
            last = hop == hop_count - 1
            new_entities.append({
                "id": tx_id,
                "type": "Transaction",
                "amount": round(amount, 2),
                "timestamp": format_ts(activity, moment),
                "channel": "wire" if last else "p2p",
            })
            new_edges.append({"from_id": account_id, "to_id": tx_id, "edge_type": "INITIATED"})
            new_edges.append({"from_id": tx_id, "to_id": ring_device, "edge_type": "FROM_DEVICE"})
            new_edges.append({"from_id": tx_id, "to_id": ring_ip, "edge_type": "FROM_IP"})
            if hop > 0:
                new_edges.append({"from_id": chain_tx_ids[hop - 1], "to_id": account_id, "edge_type": "PAYS_TO_ACCOUNT"})
            if last:
                new_edges.append({"from_id": tx_id, "to_id": beneficiary_id, "edge_type": "PAYS"})
            chain_tx_ids.append(tx_id)
            moment += float(rng.uniform(600, 6 * 3600))
            amount *= float(rng.uniform(0.9, 0.98))

        chain_edges = new_edges[chain_edges_start:]
        chain_summaries.append({"chain_id": chain_id, "hop_count": _hop_count_from_edges(chain_edges)})
        chain_truth.append({
            "chain_id": chain_id,
            "accounts": chosen_accounts,
            "transactions": chain_tx_ids,
            "beneficiary": beneficiary_id,
            "shared_devices": [ring_device],
            "shared_ips": [ring_ip],
        })

    return new_entities, new_edges, chain_summaries, chain_truth


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


def generate_structuring_clusters(
    rng: np.random.Generator,
    base: BaseGraph,
    mule_accounts: list[str],
    scale: ScaleConfig,
    params: PatternParameters,
    activity: ActivityParameters,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Cash deposits clustered just under the reporting threshold (spec
    section 4.1.2), e.g. repeated values near $994 against $1,000, made
    within a few days. About half the clusters sit on mule accounts, so
    the two patterns overlap the way they do in real rings.

    Returns (new_entities, new_edges, cluster_truth).
    """
    new_entities: list[dict] = []
    new_edges: list[dict] = []
    cluster_truth: list[dict] = []
    span = window_seconds(activity)
    others = [a for a in base.account_ids if a not in set(mule_accounts)]

    for c in range(scale.structuring_cluster_count):
        pool = mule_accounts if c % 2 == 0 and mule_accounts else others
        account_id = str(rng.choice(pool))
        devices = base.devices_by_account.get(account_id, [])
        start = float(rng.uniform(max(0.0, base.opened_at[account_id]), span * 0.9))
        tx_ids = []
        for k in range(int(rng.integers(3, 8))):
            tx_id = f"structuring-tx-{c:04d}-{k:02d}"
            amount = params.structuring_target_amount + float(rng.uniform(-5, 5))
            new_entities.append({
                "id": tx_id,
                "type": "Transaction",
                "amount": round(min(amount, params.structuring_threshold - 0.01), 2),
                "timestamp": format_ts(activity, start + float(rng.uniform(0, 4 * 86_400))),
                "channel": "cash_deposit",
            })
            new_edges.append({"from_id": account_id, "to_id": tx_id, "edge_type": "INITIATED"})
            if devices:
                new_edges.append({"from_id": tx_id, "to_id": devices[0], "edge_type": "FROM_DEVICE"})
            tx_ids.append(tx_id)
        cluster_truth.append({
            "cluster_id": f"structuring-{c:04d}",
            "account": account_id,
            "transactions": tx_ids,
        })

    return new_entities, new_edges, cluster_truth
