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


@dataclass(frozen=True)
class ScaleConfig:
    person_count: int
    mule_chain_count: int
    structuring_cluster_count: int
    ato_timeline_count: int


SCALE_PRESETS = {
    "small": ScaleConfig(person_count=200, mule_chain_count=5, structuring_cluster_count=5, ato_timeline_count=3),
    "large": ScaleConfig(person_count=20_000, mule_chain_count=500, structuring_cluster_count=500, ato_timeline_count=300),
}
