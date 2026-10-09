from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .lifecycle import INITIAL_STATE, validate_state


@dataclass
class FraudPatternCandidate:
    """Reviewer-facing candidate. Not a fraud classification."""

    pattern_id: str
    cluster_id: int
    feature_signature: dict[str, Any]
    sample_transaction_ids: list[str]
    entity_summary: dict[str, Any]
    cluster_size: int
    discovery_timestamp: str
    discovery_method: str
    state: str = INITIAL_STATE

    def __post_init__(self):
        self.state = validate_state(self.state)
        self.sample_transaction_ids = list(dict.fromkeys(str(x) for x in self.sample_transaction_ids))
        self.cluster_size = int(self.cluster_size)
        self.cluster_id = int(self.cluster_id)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_legacy(self) -> dict:
        return {
            'id': self.pattern_id,
            'name': f'Candidate cluster {self.cluster_id}',
            'state': self.state,
            'signature': self.feature_signature,
            'evidence': [{'transaction_id': tid} for tid in self.sample_transaction_ids],
            'created_at': self.discovery_timestamp,
            'cluster_id': self.cluster_id,
            'cluster_size': self.cluster_size,
            'discovery_method': self.discovery_method,
        }


@dataclass
class DiscoveryResult:
    candidates: list[FraudPatternCandidate] = field(default_factory=list)
    n_input: int = 0
    n_duplicates_removed: int = 0
    n_anomalies_selected: int = 0
    n_noise: int = 0
    n_discarded_small: int = 0
    reason: str | None = None
    discovery_method: str = 'isolation_forest_hdbscan'
    anomaly_is_not_fraud: bool = True
    auto_classified_as_fraud: bool = False

    def to_dict(self) -> dict:
        return {
            'candidates': [c.to_dict() for c in self.candidates],
            'n_input': self.n_input,
            'n_duplicates_removed': self.n_duplicates_removed,
            'n_anomalies_selected': self.n_anomalies_selected,
            'n_noise': self.n_noise,
            'n_discarded_small': self.n_discarded_small,
            'reason': self.reason,
            'discovery_method': self.discovery_method,
            'anomaly_is_not_fraud': self.anomaly_is_not_fraud,
            'auto_classified_as_fraud': self.auto_classified_as_fraud,
        }
