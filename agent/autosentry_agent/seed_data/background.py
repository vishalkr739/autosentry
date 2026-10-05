import numpy as np

from .base_entities import BaseGraph, format_ts, window_seconds
from .config import ActivityParameters


def generate_background_activity(
    rng: np.random.Generator, base: BaseGraph, activity: ActivityParameters
) -> tuple[list[dict], list[dict]]:
    """Ordinary transactions for every account: card spend at merchants,
    ACH payments, and a share of P2P transfers to other accounts, each from
    one of the owner's own devices and IPs. Returns (entities, edges)."""
    entities: list[dict] = []
    edges: list[dict] = []
    span = window_seconds(activity)

    for account_id in base.account_ids:
        devices = base.devices_by_account[account_id]
        ips = base.ips_by_account[account_id]
        earliest = max(0.0, base.opened_at[account_id])
        for n in range(int(rng.integers(activity.min_background_tx, activity.max_background_tx + 1))):
            tx_id = f"tx-{account_id.removeprefix('account-')}-{n:03d}"
            roll = float(rng.uniform())
            if roll < activity.p2p_share:
                channel = "p2p"
            elif roll < 0.7:
                channel = "card"
            else:
                channel = "ach"
            entities.append({
                "id": tx_id,
                "type": "Transaction",
                "amount": round(float(rng.lognormal(mean=3.8, sigma=1.0)), 2),
                "timestamp": format_ts(activity, float(rng.uniform(earliest, span))),
                "channel": channel,
            })
            edges.append({"from_id": account_id, "to_id": tx_id, "edge_type": "INITIATED"})
            edges.append({"from_id": tx_id, "to_id": str(rng.choice(devices)), "edge_type": "FROM_DEVICE"})
            edges.append({"from_id": tx_id, "to_id": str(rng.choice(ips)), "edge_type": "FROM_IP"})
            if channel == "card":
                edges.append({"from_id": tx_id, "to_id": str(rng.choice(base.merchant_ids)), "edge_type": "AT"})
            elif channel == "p2p":
                payee = str(rng.choice(base.account_ids))
                if payee != account_id:
                    edges.append({"from_id": tx_id, "to_id": payee, "edge_type": "PAYS_TO_ACCOUNT"})

    return entities, edges
