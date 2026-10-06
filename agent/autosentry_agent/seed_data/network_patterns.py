"""Network-level patterns beyond mule chains, each with an innocent look-alike.

Suspicious: round-trip cycles (money returns to where it started, quickly
and nearly whole), funnel accounts (many victims pay in, the total is wired
out within hours), and repeated payments from ring members to a controller.
Innocent look-alikes: family members paying each other back weeks apart,
a small business with many customers spread over months, and tenants
paying rent. The look-alikes make sure no single fact (a loop, many
senders, a repeated payee) is treated as proof by itself.
"""

from dataclasses import dataclass, field

import numpy as np

from .base_entities import BaseGraph, format_ts, window_seconds
from .config import ActivityParameters, NetworkParameters, ScaleConfig

DAY = 86_400


@dataclass
class NetworkResult:
    entities: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    truth: dict = field(default_factory=dict)


def add_second_accounts(
    rng: np.random.Generator, base: BaseGraph, activity: ActivityParameters, params: NetworkParameters
) -> dict[str, list[str]]:
    """Give some people a second account, which shares their devices and IPs.

    Returns person -> accounts for everyone who now owns more than one.
    """
    owners: dict[str, list[str]] = {}
    for account_id in list(base.account_ids):
        if float(rng.uniform()) >= params.second_account_share:
            continue
        index = account_id.removeprefix("account-")
        person_id = f"person-{index}"
        second = f"account-{index}-b"
        opened = -float(rng.uniform(0, 2 * 365 * DAY))
        account = {
            "id": second,
            "type": "Account",
            "kyc_score": base.accounts[account_id]["kyc_score"],
            "open_date": format_ts(activity, opened),
            "status": "active",
        }
        base.entities.append(account)
        base.accounts[second] = account
        base.account_ids.append(second)
        base.opened_at[second] = opened
        base.devices_by_account[second] = list(base.devices_by_account[account_id])
        base.ips_by_account[second] = list(base.ips_by_account[account_id])
        base.edges.append({"from_id": person_id, "to_id": second, "edge_type": "OWNS", "since": account["open_date"]})
        owners[person_id] = [account_id, second]
    return owners


class _Builder:
    """Transactions with their INITIATED, device, IP and payee edges."""

    def __init__(self, rng: np.random.Generator, base: BaseGraph, activity: ActivityParameters) -> None:
        self.rng = rng
        self.base = base
        self.activity = activity
        self.result = NetworkResult()

    def account_tx(self, tx_id: str, sender: str, payee: str, amount: float, at: float, channel: str = "p2p") -> str:
        self._tx(tx_id, sender, amount, at, channel)
        self.result.edges.append({"from_id": tx_id, "to_id": payee, "edge_type": "PAYS_TO_ACCOUNT"})
        return tx_id

    def beneficiary_tx(self, tx_id: str, sender: str, beneficiary: str, amount: float, at: float) -> str:
        self._tx(tx_id, sender, amount, at, "wire")
        self.result.edges.append({"from_id": tx_id, "to_id": beneficiary, "edge_type": "PAYS"})
        return tx_id

    def beneficiary(self, beneficiary_id: str) -> str:
        self.result.entities.append({
            "id": beneficiary_id,
            "type": "Beneficiary",
            "name_hash": f"name-hash-{self.rng.integers(0, 2**32):08x}",
            "bank_identifier": f"XB{self.rng.integers(10**7, 10**8)}",
        })
        return beneficiary_id

    def _tx(self, tx_id: str, sender: str, amount: float, at: float, channel: str) -> None:
        self.result.entities.append({
            "id": tx_id,
            "type": "Transaction",
            "amount": round(amount, 2),
            "timestamp": format_ts(self.activity, at),
            "channel": channel,
        })
        self.result.edges.append({"from_id": sender, "to_id": tx_id, "edge_type": "INITIATED"})
        self.result.edges.append(
            {"from_id": tx_id, "to_id": self.base.devices_by_account[sender][0], "edge_type": "FROM_DEVICE"}
        )
        self.result.edges.append({"from_id": tx_id, "to_id": self.base.ips_by_account[sender][0], "edge_type": "FROM_IP"})


def _pick(rng: np.random.Generator, base: BaseGraph, used: set[str], count: int, opened_before: float) -> list[str]:
    """Distinct unused accounts opened before `opened_before` (seconds into the window)."""
    pool = [a for a in base.account_ids if a not in used and base.opened_at[a] < opened_before]
    chosen = [str(a) for a in rng.choice(pool, size=count, replace=False)]
    used.update(chosen)
    return chosen


def generate_network_patterns(
    rng: np.random.Generator,
    base: BaseGraph,
    scale: ScaleConfig,
    activity: ActivityParameters,
    params: NetworkParameters,
    used: set[str],
    ring_chains: list[dict],
) -> NetworkResult:
    """All network patterns and look-alikes. `used` holds accounts already
    given a role (the mule rings); every account chosen here joins it, so
    each ground-truth entry is about an account doing one thing."""
    span = window_seconds(activity)
    b = _Builder(rng, base, activity)
    truth: dict[str, list] = {
        "round_trips": [], "funnels": [], "controller_payments": [],
        "family_pairs": [], "businesses": [], "landlords": [],
    }

    # Round trips: money leaves an account, passes through 2 to 4 others
    # within hours, and comes back to it nearly whole.
    for c in range(scale.round_trip_count):
        size = int(rng.integers(params.round_trip_min_accounts, params.round_trip_max_accounts + 1))
        start = float(rng.uniform(span * 0.3, span * 0.85))
        accounts = _pick(rng, base, used, size, start)
        amount = float(rng.uniform(2000, 8000))
        at = start
        txs = []
        for hop in range(size):
            payee = accounts[(hop + 1) % size]
            txs.append(b.account_tx(f"round-trip-{c:04d}-tx-{hop:02d}", accounts[hop], payee, amount, at))
            at += float(rng.uniform(3600, 12 * 3600))
            amount *= float(rng.uniform(0.95, 0.99))
        truth["round_trips"].append({"cycle_id": f"round-trip-{c:04d}", "accounts": accounts, "transactions": txs})

    # Funnels: 8 to 15 victims each pay a small amount into one account
    # within 3 days; most of the total is wired out within hours.
    for c in range(scale.funnel_count):
        start = float(rng.uniform(span * 0.3, span * 0.85))
        funnel = _pick(rng, base, used, 1, start)[0]
        victims = _pick(rng, base, used, int(rng.integers(params.funnel_min_victims, params.funnel_max_victims + 1)), start)
        inbound, total, last = [], 0.0, start
        for k, victim in enumerate(victims):
            at = start + float(rng.uniform(0, 3 * DAY))
            amount = float(rng.uniform(200, 900))
            inbound.append(b.account_tx(f"funnel-{c:04d}-in-{k:02d}", victim, funnel, amount, at))
            total += amount
            last = max(last, at)
        beneficiary = b.beneficiary(f"beneficiary-funnel-{c:04d}")
        out = b.beneficiary_tx(
            f"funnel-{c:04d}-out", funnel, beneficiary, total * float(rng.uniform(0.85, 0.95)),
            last + float(rng.uniform(3600, 12 * 3600)),
        )
        truth["funnels"].append({
            "account": funnel, "victims": victims, "inbound": inbound,
            "cash_out": out, "beneficiary": beneficiary,
        })

    # Controllers: two ring members pay the same account every week.
    for c in range(min(scale.controller_count, len(ring_chains))):
        chain = ring_chains[c]
        controller = _pick(rng, base, used, 1, span * 0.3)[0]
        for m, member in enumerate(chain["accounts"][1:3]):
            at = float(rng.uniform(span * 0.5, span * 0.55))
            txs = []
            for k in range(int(rng.integers(params.controller_min_payments, params.controller_max_payments + 1))):
                if at >= span:
                    break
                txs.append(b.account_tx(
                    f"controller-{c:04d}-{m}-tx-{k:02d}", member, controller, float(rng.uniform(300, 1500)), at
                ))
                at += 7 * DAY + float(rng.uniform(-DAY, DAY))
            truth["controller_payments"].append(
                {"from": member, "to": controller, "transactions": txs, "chain_id": chain["chain_id"]}
            )

    # Look-alike: family members paying each other back, weeks apart.
    for c in range(scale.family_pair_count):
        first, second = _pick(rng, base, used, 2, 0.0)
        at = float(rng.uniform(0, span * 0.2))
        txs = []
        for k in range(4):
            sender, payee = (first, second) if k % 2 == 0 else (second, first)
            txs.append(b.account_tx(f"family-{c:04d}-tx-{k:02d}", sender, payee, float(rng.uniform(100, 500)), at))
            at += float(rng.uniform(10, 20)) * DAY
        truth["family_pairs"].append({"accounts": [first, second], "transactions": txs})

    # Look-alike: a small business paid by many customers, spread over months.
    for c in range(scale.business_count):
        business = _pick(rng, base, used, 1, 0.0)[0]
        customers = _pick(rng, base, used, 12, 0.0)
        txs = [
            b.account_tx(
                f"business-{c:04d}-in-{k:02d}", customer, business, float(rng.uniform(40, 300)),
                k * (span / len(customers)) + float(rng.uniform(0, DAY)),
            )
            for k, customer in enumerate(customers)
        ]
        truth["businesses"].append({"account": business, "customers": customers, "transactions": txs})

    # Look-alike: tenants paying the same landlord monthly, a steady amount.
    for c in range(scale.landlord_count):
        landlord = _pick(rng, base, used, 1, 0.0)[0]
        tenants = _pick(rng, base, used, params.landlord_tenants, 0.0)
        for t, tenant in enumerate(tenants):
            rent = float(rng.uniform(900, 2000))
            txs = [
                b.account_tx(f"rent-{c:04d}-{t}-tx-{k:02d}", tenant, landlord, rent, k * 30 * DAY + float(rng.uniform(0, 2 * DAY)))
                for k in range(3)
            ]
            truth["landlords"].append({"from": tenant, "to": landlord, "transactions": txs})

    b.result.truth = truth
    return b.result
