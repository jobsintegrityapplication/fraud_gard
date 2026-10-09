"""Deterministic candidate feature templates.

An LLM may propose a description; only these reviewed templates may compute values.
LLMs never supply executable feature code.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from ..windows import LEAKAGE_COLUMNS

# Whitelisted implementers — only these keys may be activated.
TEMPLATE_KEYS = frozenset({
    'ratio',
    'product',
    'log1p_ratio',
    'temporal_burst_ratio',
    'entity_link_strength',
    'pattern_signature_mean',
    'hour_amount_interaction',
})


def _require_cols(frame: pd.DataFrame, cols: list[str]):
    missing = [c for c in cols if c not in frame.columns]
    if missing:
        raise KeyError(f'missing columns for template: {missing}')


def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    denom = pd.to_numeric(b, errors='coerce').fillna(0.0).astype(float)
    numer = pd.to_numeric(a, errors='coerce').fillna(0.0).astype(float)
    return numer / denom.where(denom.abs() > 1e-9, np.nan)


def impl_ratio(frame: pd.DataFrame, params: dict) -> pd.Series:
    left, right = params['left'], params['right']
    _require_cols(frame, [left, right])
    return _safe_div(frame[left], frame[right]).fillna(0.0)


def impl_product(frame: pd.DataFrame, params: dict) -> pd.Series:
    left, right = params['left'], params['right']
    _require_cols(frame, [left, right])
    a = pd.to_numeric(frame[left], errors='coerce').fillna(0.0).astype(float)
    b = pd.to_numeric(frame[right], errors='coerce').fillna(0.0).astype(float)
    return a * b


def impl_log1p_ratio(frame: pd.DataFrame, params: dict) -> pd.Series:
    return np.log1p(impl_ratio(frame, params).clip(lower=0))


def impl_temporal_burst_ratio(frame: pd.DataFrame, params: dict) -> pd.Series:
    short = params.get('short', 'purchases_last_1h')
    long = params.get('long', 'purchases_last_24h')
    return impl_ratio(frame, {'left': short, 'right': long})


def impl_entity_link_strength(frame: pd.DataFrame, params: dict) -> pd.Series:
    """Graph-ish strength from existing entity relationship features."""
    left = params.get('left', 'connected_accounts')
    right = params.get('right', 'fraud_neighbor_count')
    return impl_product(frame, {'left': left, 'right': right})


def impl_pattern_signature_mean(frame: pd.DataFrame, params: dict) -> pd.Series:
    """Mean of listed production features from a confirmed pattern signature (no pattern_id column)."""
    features = list(params.get('features') or [])
    if not features:
        return pd.Series(0.0, index=frame.index)
    leak = [f for f in features if f in LEAKAGE_COLUMNS]
    if leak:
        raise ValueError(f'pattern signature features include leakage columns: {leak}')
    _require_cols(frame, features)
    mat = frame[features].apply(pd.to_numeric, errors='coerce').fillna(0.0).astype(float)
    return mat.mean(axis=1)


def impl_hour_amount_interaction(frame: pd.DataFrame, params: dict) -> pd.Series:
    hour = params.get('hour', 'hour_of_day')
    amount = params.get('amount', 'amount')
    _require_cols(frame, [hour, amount])
    h = pd.to_numeric(frame[hour], errors='coerce').fillna(0.0).astype(float)
    a = pd.to_numeric(frame[amount], errors='coerce').fillna(0.0).astype(float)
    # Night-ish hours get a mild weight; still deterministic and PIT-safe.
    night = ((h >= 22) | (h <= 5)).astype(float)
    return a * (1.0 + night)


IMPLEMENTERS: dict[str, Callable[[pd.DataFrame, dict], pd.Series]] = {
    'ratio': impl_ratio,
    'product': impl_product,
    'log1p_ratio': impl_log1p_ratio,
    'temporal_burst_ratio': impl_temporal_burst_ratio,
    'entity_link_strength': impl_entity_link_strength,
    'pattern_signature_mean': impl_pattern_signature_mean,
    'hour_amount_interaction': impl_hour_amount_interaction,
}


def compute_candidate_column(frame: pd.DataFrame, template: str, params: dict) -> pd.Series:
    key = str(template or '').strip()
    if key not in IMPLEMENTERS:
        raise PermissionError(
            f'unknown or disallowed template {template!r}; '
            'only deterministic reviewed templates may implement candidate features'
        )
    # Refuse executable payloads from LLM / untrusted sources.
    if 'code' in (params or {}) or 'expression' in (params or {}) or 'python' in (params or {}):
        raise PermissionError('candidate params must not include executable code')
    return IMPLEMENTERS[key](frame, params or {})
