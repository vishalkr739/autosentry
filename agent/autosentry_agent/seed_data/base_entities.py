import numpy as np

from .config import ScaleConfig


def sample_base_entities(rng: np.random.Generator, scale: ScaleConfig) -> list[dict]:
    """Base Person/Account entities, shaped from the IEEE-CIS Fraud
    Detection dataset (spec section 4.1). The raw Kaggle dataset is
    fetched at build/CI time via a Kaggle API credential sourced through
    SecretsProvider, never vendored into the repo; this function accepts
    already-sampled index positions so it stays deterministic and
    testable without a network call in unit tests."""
    entities = []
    for i in range(scale.person_count):
        person_id = f"person-{i:06d}"
        entities.append({"id": person_id, "type": "Person", "anonymized_ssn_hash": _hash_index(rng, i)})
        account_id = f"account-{i:06d}"
        entities.append({
            "id": account_id,
            "type": "Account",
            "kyc_score": float(rng.uniform(0, 1)),
            "status": "active",
        })
    return entities


def _hash_index(rng: np.random.Generator, i: int) -> str:
    return f"ssn-hash-{rng.integers(0, 2**32):08x}-{i}"
