from __future__ import annotations

from dataclasses import dataclass, field
import pandas as pd

FEATURE_SET_VERSION = 'fe-v1.0.0'
LEAKAGE_COLUMNS = frozenset({
    'label', 'fraud_reason', 'pattern_code', 'pattern_id', 'pattern_name',
    'case_id', 'injection_id', 'is_injected', 'ground_truth',
})
LLM_SOURCES = frozenset({'llm', 'prompt', 'chat', 'generated', 'copilot', 'agent'})


@dataclass(frozen=True)
class FeatureWindows:
    purchases_short: pd.Timedelta = pd.Timedelta(hours=1)
    purchases_long: pd.Timedelta = pd.Timedelta(hours=24)
    velocity: pd.Timedelta = pd.Timedelta(hours=24)
    unusual_distance_km: float = 250.0
    unusual_hour_min_history: int = 5
    unusual_hour_delta: int = 6


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    entity_level: str
    version: str
    description: str
    dtype: str
    windows: tuple[str, ...] = field(default_factory=tuple)
    uses_label_history: bool = False
    production: bool = True
    owner: str = 'feature-platform'
