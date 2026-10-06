from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PatternParameters:
    structuring_threshold: float = 1000.0
    structuring_target_amount: float = 994.0
    mule_chain_min_hops: int = 3
    mule_chain_max_hops: int = 6
    velocity_burst_window_seconds: int = 300
    velocity_burst_min_tx_count: int = 5


@dataclass(frozen=True)
class ActivityParameters:
    """Background ("normal") activity every account has, so the fraud
    patterns are something to find rather than the whole graph."""

    # Every timestamp falls in [window_start, window_start + window_days).
    window_start: datetime = datetime(2026, 6, 1)
    window_days: int = 90
    min_background_tx: int = 5
    max_background_tx: int = 30
    merchant_count: int = 40
    # Share of background transactions that are P2P transfers to another
    # account, so PAYS_TO_ACCOUNT alone doesn't single out mule chains.
    p2p_share: float = 0.1
    # A few benign IPs (public Wi-Fi, carrier NAT) used by many unrelated
    # accounts, so shared infrastructure alone doesn't single out a ring.
    public_ip_count: int = 3
    public_ip_users: int = 12
    # Benign look-alikes of mule signals, so no single signal decides:
    # ordinary accounts with a low KYC score, ones opened recently, and
    # two-person households that share a device.
    low_kyc_share: float = 0.08
    new_account_share: float = 0.06
    household_share: float = 0.05


@dataclass(frozen=True)
class NetworkParameters:
    """The network patterns beyond mule chains, and their innocent look-alikes."""

    # Share of people who also own a second (savings) account.
    second_account_share: float = 0.15
    round_trip_min_accounts: int = 3
    round_trip_max_accounts: int = 5
    funnel_min_victims: int = 8
    funnel_max_victims: int = 15
    controller_min_payments: int = 4
    controller_max_payments: int = 6
    landlord_tenants: int = 3


@dataclass(frozen=True)
class ScaleConfig:
    person_count: int
    mule_chain_count: int
    structuring_cluster_count: int
    ato_timeline_count: int
    round_trip_count: int = 0
    funnel_count: int = 0
    controller_count: int = 0
    family_pair_count: int = 0
    business_count: int = 0
    landlord_count: int = 0


SCALE_PRESETS = {
    "small": ScaleConfig(
        person_count=200, mule_chain_count=5, structuring_cluster_count=5, ato_timeline_count=3,
        round_trip_count=3, funnel_count=2, controller_count=2,
        family_pair_count=3, business_count=1, landlord_count=1,
    ),
    "large": ScaleConfig(
        person_count=20_000, mule_chain_count=500, structuring_cluster_count=500, ato_timeline_count=300,
        round_trip_count=300, funnel_count=200, controller_count=200,
        family_pair_count=300, business_count=100, landlord_count=100,
    ),
}
