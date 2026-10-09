from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

Impact = Literal['increases_risk', 'decreases_risk', 'neutral']

NEUTRAL_EPS = 1e-9


@dataclass(frozen=True)
class FeatureExplanation:
    """Single-feature SHAP contribution for a prediction."""

    feature: str
    value: float
    impact: Impact
    contribution: float

    def to_dict(self) -> dict[str, Any]:
        return {
            'feature': self.feature,
            'value': _json_num(self.value),
            'impact': self.impact,
            'contribution': float(self.contribution),
        }


@dataclass
class PredictionExplanation:
    """Full explanation object for one supervised prediction."""

    model_version: str
    feature_version: str
    risk_score: float
    top_features: list[FeatureExplanation] = field(default_factory=list)
    method: str = 'shap_tree'
    base_value: float | None = None
    consistency: dict[str, Any] = field(default_factory=dict)
    transaction_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'transaction_id': self.transaction_id,
            'model_version': self.model_version,
            'feature_version': self.feature_version,
            'risk_score': float(self.risk_score),
            'method': self.method,
            'base_value': None if self.base_value is None else float(self.base_value),
            'top_features': [f.to_dict() for f in self.top_features],
            'consistency': self.consistency,
        }


def _json_num(x: float) -> float | int:
    v = float(x)
    if abs(v - round(v)) < 1e-12 and abs(v) < 1e15:
        return int(round(v))
    return float(v)


def impact_from_contribution(contribution: float, *, eps: float = NEUTRAL_EPS) -> Impact:
    if contribution > eps:
        return 'increases_risk'
    if contribution < -eps:
        return 'decreases_risk'
    return 'neutral'


def _unwrap_booster(model):
    booster = model
    if hasattr(model, 'estimator'):
        booster = model.estimator
    if hasattr(model, 'calibrated_classifiers_'):
        inner = model.calibrated_classifiers_[0]
        booster = getattr(inner, 'estimator', getattr(inner, 'base_estimator', booster))
    return booster


def compute_shap_values(model, row: pd.DataFrame) -> tuple[np.ndarray, float | None, str]:
    """
    Return per-feature SHAP values for the positive (fraud) class.
    Positive values increase the model's fraud score → increases_risk.
    """
    booster = _unwrap_booster(model)
    try:
        import shap
        explainer = shap.TreeExplainer(booster)
        values = explainer.shap_values(row)
        base = explainer.expected_value
        if isinstance(values, list):
            # Binary: index 1 is positive/fraud class when present.
            values = values[1] if len(values) > 1 else values[0]
            if isinstance(base, (list, np.ndarray)):
                base = base[1] if len(base) > 1 else base[0]
        sv = np.asarray(values, dtype=float).reshape(-1)
        base_f = float(np.asarray(base).reshape(-1)[0]) if base is not None else None
        return sv, base_f, 'shap_tree'
    except Exception:
        importances = getattr(booster, 'feature_importances_', None)
        if importances is None or len(importances) != row.shape[1]:
            importances = np.ones(row.shape[1], dtype=float)
        raw = row.to_numpy(dtype=float).reshape(-1)
        # Signed proxy: centered feature * importance (not true SHAP; marked as fallback).
        centered = raw - float(np.mean(raw)) if len(raw) else raw
        sv = centered * np.asarray(importances, dtype=float)
        return sv, None, 'importance_fallback'


def assert_explanations_consistent(
    contributions: list[FeatureExplanation],
    *,
    shap_vector: np.ndarray,
    feature_names: list[str],
    row: pd.DataFrame,
    risk_score: float,
    base_value: float | None = None,
) -> dict[str, Any]:
    """
    Guardrails so explanations never contradict the model computation.
    - impact must match contribution sign
    - contribution must equal the SHAP value for that feature
    - feature value must match the scored row
    - optional: base + sum(shap) sanity vs risk (informational when calibrated)
    """
    errors: list[str] = []
    name_to_idx = {n: i for i, n in enumerate(feature_names)}

    for item in contributions:
        expected_impact = impact_from_contribution(item.contribution)
        if item.impact != expected_impact:
            errors.append(
                f"{item.feature}: impact {item.impact!r} contradicts contribution {item.contribution}"
            )
        idx = name_to_idx.get(item.feature)
        if idx is None:
            errors.append(f'{item.feature}: not in model feature set')
            continue
        shap_val = float(shap_vector[idx])
        if abs(shap_val - item.contribution) > 1e-6:
            errors.append(
                f"{item.feature}: contribution {item.contribution} != shap {shap_val}"
            )
        actual_val = float(row.iloc[0, idx])
        if abs(actual_val - float(item.value)) > 1e-6:
            errors.append(
                f"{item.feature}: value {item.value} != row value {actual_val}"
            )

    # Ranking must follow |contribution|
    abs_order = [c.feature for c in contributions]
    recomputed = sorted(contributions, key=lambda c: abs(c.contribution), reverse=True)
    if [c.feature for c in recomputed] != abs_order:
        errors.append('top_features are not ordered by absolute contribution')

    additive = None
    if base_value is not None and len(shap_vector):
        additive = float(base_value) + float(np.sum(shap_vector))

    ok = not errors
    if not ok:
        raise ValueError('explanation contradicts model output: ' + '; '.join(errors))
    return {
        'ok': True,
        'risk_score': float(risk_score),
        'shap_sum': float(np.sum(shap_vector)) if len(shap_vector) else 0.0,
        'base_plus_shap_sum': additive,
        'note': (
            'impacts derived from SHAP signs for the fraud class; '
            'calibrated risk_score may differ from raw additive SHAP margin'
        ),
    }


def build_feature_explanations(
    feature_names: list[str],
    row: pd.DataFrame,
    shap_vector: np.ndarray,
    *,
    top_k: int = 5,
) -> list[FeatureExplanation]:
    sv = np.asarray(shap_vector, dtype=float).reshape(-1)
    if len(sv) != len(feature_names):
        raise ValueError('shap vector length does not match feature names')
    order = np.argsort(np.abs(sv))[::-1][: max(1, top_k)]
    out: list[FeatureExplanation] = []
    for i in order:
        contrib = float(sv[i])
        out.append(FeatureExplanation(
            feature=feature_names[i],
            value=float(row.iloc[0, i]),
            impact=impact_from_contribution(contrib),
            contribution=contrib,
        ))
    return out


def explain_prediction(
    model,
    X_row: pd.DataFrame,
    feature_names: list[str],
    *,
    risk_score: float,
    model_version: str,
    feature_version: str,
    top_k: int = 5,
    transaction_id: str | None = None,
) -> PredictionExplanation:
    """Build a PredictionExplanation from the actual model + SHAP values for this row."""
    row = X_row[feature_names].fillna(0).astype(float)
    if len(row) != 1:
        raise ValueError('explain_prediction expects a single-row frame')
    shap_vector, base_value, method = compute_shap_values(model, row)
    top = build_feature_explanations(feature_names, row, shap_vector, top_k=top_k)
    consistency = assert_explanations_consistent(
        top,
        shap_vector=shap_vector,
        feature_names=feature_names,
        row=row,
        risk_score=risk_score,
        base_value=base_value,
    )
    return PredictionExplanation(
        model_version=model_version,
        feature_version=feature_version,
        risk_score=float(risk_score),
        top_features=top,
        method=method,
        base_value=base_value,
        consistency=consistency,
        transaction_id=transaction_id,
    )
