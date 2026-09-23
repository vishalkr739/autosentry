from dataclasses import dataclass, field

import numpy as np

from .base_entities import sample_base_entities
from .config import SCALE_PRESETS, PatternParameters
from .patterns import generate_mule_chains, generate_structuring_clusters


@dataclass
class GenerationResult:
    entities: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    mule_chains: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"entities": self.entities, "edges": self.edges, "mule_chains": self.mule_chains}


def generate(seed: int, scale: str = "small") -> GenerationResult:
    """Deterministic: the same seed always produces byte-identical
    output (spec section 4.3). A single top-level rng seeds every
    sub-generator via an offset, so one pattern's randomness stream never
    perturbs another's."""
    scale_config = SCALE_PRESETS[scale]
    params = PatternParameters()

    base_rng = np.random.default_rng(seed)
    entities = sample_base_entities(base_rng, scale_config)

    # Base entities are created in lockstep pairs: person-{i} and
    # account-{i} for the same index i (see sample_base_entities). Every
    # Person owns the Account created alongside it.
    owns_edges = [
        {"from_id": f"person-{i:06d}", "to_id": f"account-{i:06d}", "edge_type": "OWNS"}
        for i in range(scale_config.person_count)
    ]

    mule_rng = np.random.default_rng(seed + 1)
    mule_entities, mule_edges = generate_mule_chains(mule_rng, entities, scale_config, params)

    structuring_rng = np.random.default_rng(seed + 2)
    structuring_entities = generate_structuring_clusters(structuring_rng, entities, scale_config, params)

    all_entities = entities + mule_entities + structuring_entities
    all_edges = mule_edges + owns_edges

    entity_ids = {e["id"] for e in all_entities}
    for edge in all_edges:
        assert edge["from_id"] in entity_ids, f"dangling edge reference: {edge['from_id']}"
        assert edge["to_id"] in entity_ids, f"dangling edge reference: {edge['to_id']}"

    chains_by_prefix: dict[str, int] = {}
    for e in mule_entities:
        chain_id = "-".join(e["id"].split("-")[:3])
        chains_by_prefix[chain_id] = chains_by_prefix.get(chain_id, 0) + 1
    mule_chain_summaries = [
        {"chain_id": chain_id, "hop_count": hop_count} for chain_id, hop_count in chains_by_prefix.items()
    ]

    return GenerationResult(entities=all_entities, edges=all_edges, mule_chains=mule_chain_summaries)
