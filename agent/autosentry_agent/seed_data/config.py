from dataclasses import dataclass


@dataclass(frozen=True)
class PatternParameters:
    structuring_threshold: float = 1000.0
    structuring_target_amount: float = 994.0
    mule_chain_min_hops: int = 3
    mule_chain_max_hops: int = 6
    velocity_burst_window_seconds: int = 300
    velocity_burst_min_tx_count: int = 5


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
