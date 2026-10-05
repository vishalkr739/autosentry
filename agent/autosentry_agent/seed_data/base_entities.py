from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np

from .config import ActivityParameters, ScaleConfig

_MCC_CODES = ["5411", "5812", "5541", "5999", "4121", "5732", "5311", "7011", "5651", "4900"]


@dataclass
class BaseGraph:
    entities: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    account_ids: list[str] = field(default_factory=list)
    # account_id -> the devices/IPs its owner normally transacts from.
    devices_by_account: dict[str, list[str]] = field(default_factory=dict)
    ips_by_account: dict[str, list[str]] = field(default_factory=dict)
    merchant_ids: list[str] = field(default_factory=list)
    accounts: dict[str, dict] = field(default_factory=dict)
    # account_id -> open time in seconds from the window start (negative =
    # before it); activity is never generated before an account was opened.
    opened_at: dict[str, float] = field(default_factory=dict)


def format_ts(activity: ActivityParameters, seconds: float) -> str:
    """A TigerGraph DATETIME string `seconds` after the window start."""
    return (activity.window_start + timedelta(seconds=int(seconds))).strftime("%Y-%m-%d %H:%M:%S")


def window_seconds(activity: ActivityParameters) -> int:
    return activity.window_days * 86_400


def sample_base_entities(
    rng: np.random.Generator, scale: ScaleConfig, activity: ActivityParameters
) -> BaseGraph:
    """Base Person/Account entities, shaped from the IEEE-CIS Fraud
    Detection dataset (spec section 4.1), plus the devices, IPs and
    merchants their normal activity runs through. The raw Kaggle dataset is
    fetched at build/CI time via a Kaggle API credential sourced through
    SecretsProvider, never vendored into the repo; this function accepts
    already-sampled index positions so it stays deterministic and
    testable without a network call in unit tests."""
    base = BaseGraph()
    for i in range(scale.person_count):
        person_id = f"person-{i:06d}"
        account_id = f"account-{i:06d}"
        # Most accounts predate the window; open_date is up to ~3 years back.
        opened = -float(rng.uniform(0, 3 * 365 * 86_400))
        base.entities.append({
            "id": person_id,
            "type": "Person",
            "anonymized_ssn_hash": _hash_index(rng, i),
            "kyc_verified_at": format_ts(activity, opened),
        })
        account = {
            "id": account_id,
            "type": "Account",
            "kyc_score": float(rng.uniform(0.4, 1)),
            "open_date": format_ts(activity, opened),
            "status": "active",
        }
        base.entities.append(account)
        base.accounts[account_id] = account
        base.account_ids.append(account_id)
        base.opened_at[account_id] = opened
        base.edges.append({
            "from_id": person_id,
            "to_id": account_id,
            "edge_type": "OWNS",
            "since": account["open_date"],
        })

        devices = []
        for d in range(int(rng.integers(1, 3))):
            device_id = f"device-{i:06d}-{d}"
            base.entities.append({
                "id": device_id,
                "type": "Device",
                "fingerprint": f"fp-{rng.integers(0, 2**48):012x}",
                "first_seen_at": format_ts(activity, opened),
            })
            devices.append(device_id)
        base.devices_by_account[account_id] = devices

        ips = []
        for k in range(int(rng.integers(1, 3))):
            ip = f"10.{i // 250 % 250}.{i % 250}.{10 + k}"
            base.entities.append({"id": ip, "type": "IPAddress", "is_vpn": False, "country_code": "US"})
            ips.append(ip)
        base.ips_by_account[account_id] = ips

    for p in range(activity.public_ip_count):
        ip = f"203.0.113.{p + 1}"
        base.entities.append({"id": ip, "type": "IPAddress", "is_vpn": False, "country_code": "US"})
        users = rng.choice(base.account_ids, size=min(activity.public_ip_users, len(base.account_ids)), replace=False)
        for account_id in users:
            base.ips_by_account[str(account_id)].append(ip)

    for m in range(activity.merchant_count):
        merchant_id = f"merchant-{m:04d}"
        base.entities.append({"id": merchant_id, "type": "Merchant", "mcc": str(rng.choice(_MCC_CODES))})
        base.merchant_ids.append(merchant_id)

    return base


def _hash_index(rng: np.random.Generator, i: int) -> str:
    return f"ssn-hash-{rng.integers(0, 2**32):08x}-{i}"
