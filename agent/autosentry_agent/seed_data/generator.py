from dataclasses import dataclass, field

import numpy as np

from .background import generate_background_activity
from .base_entities import format_ts, sample_base_entities, window_seconds
from .config import SCALE_PRESETS, ActivityParameters, PatternParameters
from .patterns import generate_mule_chains, generate_structuring_clusters


@dataclass
class GenerationResult:
    entities: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    mule_chains: list[dict] = field(default_factory=list)
    # What the patterns really are: which accounts are mules, each ring's
    # path and shared infrastructure, the structuring clusters, the seeded
    # case. Never loaded into the graph; evals score answers against it.
    ground_truth: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "entities": self.entities,
            "edges": self.edges,
            "mule_chains": self.mule_chains,
            "ground_truth": self.ground_truth,
        }


def generate(seed: int, scale: str = "small") -> GenerationResult:
    """Deterministic: the same seed always produces byte-identical
    output (spec section 4.3). A single top-level rng seeds every
    sub-generator via an offset, so one pattern's randomness stream never
    perturbs another's."""
    scale_config = SCALE_PRESETS[scale]
    params = PatternParameters()
    activity = ActivityParameters()

    base = sample_base_entities(np.random.default_rng(seed), scale_config, activity)

    # Mule rings come before background activity, since they re-date their
    # accounts' open_date and background activity must not predate it.
    mule_entities, mule_edges, mule_chain_summaries, chain_truth = generate_mule_chains(
        np.random.default_rng(seed + 1), base, scale_config, params, activity
    )
    mule_accounts = sorted(a for chain in chain_truth for a in chain["accounts"])

    structuring_entities, structuring_edges, structuring_truth = generate_structuring_clusters(
        np.random.default_rng(seed + 2), base, mule_accounts, scale_config, params, activity
    )

    background_entities, background_edges = generate_background_activity(
        np.random.default_rng(seed + 3), base, activity
    )

    # One open case, opened on the first ring's starting account: where an
    # analyst's investigation begins.
    case_subject = chain_truth[0]["accounts"][0] if chain_truth else None
    case_entities: list[dict] = []
    case_edges: list[dict] = []
    if case_subject is not None:
        case_entities.append({
            "id": "case-0001",
            "type": "InvestigationCase",
            "status": "open",
            "opened_at": format_ts(activity, window_seconds(activity) - 86_400),
        })
        case_edges.append({
            "from_id": case_subject,
            "to_id": "case-0001",
            "edge_type": "INVESTIGATED_IN",
            "role": "subject",
        })

    all_entities = base.entities + mule_entities + structuring_entities + background_entities + case_entities
    all_edges = base.edges + mule_edges + structuring_edges + background_edges + case_edges

    entity_ids = {e["id"] for e in all_entities}
    assert len(entity_ids) == len(all_entities), "duplicate entity id"
    for edge in all_edges:
        assert edge["from_id"] in entity_ids, f"dangling edge reference: {edge['from_id']}"
        assert edge["to_id"] in entity_ids, f"dangling edge reference: {edge['to_id']}"

    ground_truth = {
        "mule_accounts": mule_accounts,
        "mule_chains": chain_truth,
        "structuring_clusters": structuring_truth,
        "case": {"case_id": "case-0001", "subject": case_subject} if case_subject else None,
    }
    return GenerationResult(
        entities=all_entities,
        edges=all_edges,
        mule_chains=mule_chain_summaries,
        ground_truth=ground_truth,
    )
