from __future__ import annotations

from dataclasses import asdict, dataclass
import os

import numpy as np
import pandas as pd

from ..engine import compute_features
from ..windows import LEAKAGE_COLUMNS
from .templates import compute_candidate_column

try:
    from sklearn.metrics import average_precision_score
except ImportError:  # pragma: no cover
    average_precision_score = None


@dataclass(frozen=True)
class CandidateValidationGates:
    max_missing_rate: float = float(os.environ.get('CAND_FEAT_MAX_MISSING', '0.40'))
    max_mean_shift: float = float(os.environ.get('CAND_FEAT_MAX_SHIFT', '3.0'))
    max_abs_correlation: float = float(os.environ.get('CAND_FEAT_MAX_CORR', '0.98'))
    min_pr_auc_lift: float = float(os.environ.get('CAND_FEAT_MIN_LIFT', '0.0'))
    min_rows: int = int(os.environ.get('CAND_FEAT_MIN_ROWS', '8'))
    segment_col: str = 'merchant_id'
    segment_min_n: int = 4


def _gate(name: str, passed: bool, detail: dict | None = None) -> dict:
    return {'gate': name, 'passed': bool(passed), 'detail': detail or {}}


def validate_candidate_feature(
    *,
    candidate: dict,
    raw: pd.DataFrame,
    gates: CandidateValidationGates | None = None,
) -> dict:
    """Deterministic validation. LLM output is never trusted for pass/fail."""
    gates = gates or CandidateValidationGates()
    results = []
    name = candidate.get('name') or ''
    template = candidate.get('template') or ''
    params = candidate.get('params') or {}

    # --- leakage ---
    leak_name = name in LEAKAGE_COLUMNS or any(
        tok in name.lower() for tok in ('label', 'ground_truth', 'fraud_reason', 'pattern_id')
    )
    leak_params = any(str(v) in LEAKAGE_COLUMNS for v in (params.values() if isinstance(params, dict) else []))
    if isinstance(params.get('features'), list):
        leak_params = leak_params or any(f in LEAKAGE_COLUMNS for f in params['features'])
    results.append(_gate('leakage', not leak_name and not leak_params, {
        'name_leakage': leak_name, 'param_leakage': leak_params,
    }))

    prod = compute_features(raw)
    try:
        series = compute_candidate_column(prod, template, params)
    except Exception as exc:
        results.append(_gate('point_in_time_availability', False, {'error': str(exc)}))
        failed = [r['gate'] for r in results if not r['passed']]
        return {
            'passed': False,
            'gates': results + [
                _gate('missingness', False, {'skipped': True}),
                _gate('stability', False, {'skipped': True}),
                _gate('correlation_redundancy', False, {'skipped': True}),
                _gate('model_improvement', False, {'skipped': True}),
                _gate('segment_impact', False, {'skipped': True}),
            ],
            'failed_gates': failed + [
                'point_in_time_availability', 'missingness', 'stability',
                'correlation_redundancy', 'model_improvement', 'segment_impact',
            ],
            'thresholds': asdict(gates),
            'reason': f'implementation failed: {exc}',
        }

    values = pd.to_numeric(series, errors='coerce')
    n = int(len(values))
    missing_rate = float(values.isna().mean()) if n else 1.0
    results.append(_gate('missingness', n >= gates.min_rows and missing_rate <= gates.max_missing_rate, {
        'n': n, 'missing_rate': missing_rate, 'threshold': gates.max_missing_rate,
    }))

    # PIT: production features are PIT; candidate built only from them + reviewed templates.
    # Extra check: candidate must not equal label when label present.
    pit_ok = True
    pit_detail = {'built_from': 'production_point_in_time_features', 'template': template}
    if 'label' in raw.columns and n:
        y = pd.to_numeric(raw['label'], errors='coerce')
        # Align by position after compute_features sort — use transaction_id join when possible.
        aligned = prod[['transaction_id']].copy()
        aligned['_cand'] = values.values
        merged = aligned.merge(
            raw.assign(transaction_id=raw.get('transaction_id', raw.get('id'))).assign(
                transaction_id=raw['transaction_id'] if 'transaction_id' in raw.columns else raw['id'],
                label=y,
            )[['transaction_id', 'label']],
            on='transaction_id',
            how='left',
        )
        if merged['label'].notna().any():
            # Exact equality with label is leakage.
            same = (merged['_cand'].fillna(-999) == merged['label'].fillna(-998)).mean()
            pit_ok = float(same) < 0.95
            pit_detail['label_equality_rate'] = float(same)
    results.append(_gate('point_in_time_availability', pit_ok, pit_detail))

    # stability: first half vs second half mean shift in std units
    filled = values.fillna(float(values.median()) if values.notna().any() else 0.0)
    mid = max(1, n // 2)
    a, b = filled.iloc[:mid], filled.iloc[mid:]
    std = float(a.std()) if len(a) > 1 else 1.0
    std = max(std, 1e-6)
    shift = abs(float(a.mean()) - float(b.mean())) / std if n >= gates.min_rows else 0.0
    results.append(_gate('stability', shift <= gates.max_mean_shift, {
        'mean_shift_std': shift, 'threshold': gates.max_mean_shift,
    }))

    # correlation / redundancy vs production features
    max_corr = 0.0
    max_corr_feat = None
    numeric_prod = prod.drop(columns=['transaction_id'], errors='ignore').apply(pd.to_numeric, errors='coerce')
    cand_v = filled.reset_index(drop=True)
    for col in numeric_prod.columns:
        other = numeric_prod[col].reset_index(drop=True).fillna(0.0)
        if other.std() < 1e-12 or cand_v.std() < 1e-12:
            continue
        corr = float(np.corrcoef(cand_v, other)[0, 1])
        if abs(corr) > abs(max_corr):
            max_corr = corr
            max_corr_feat = col
    results.append(_gate('correlation_redundancy', abs(max_corr) < gates.max_abs_correlation, {
        'max_abs_correlation': abs(max_corr),
        'with_feature': max_corr_feat,
        'threshold': gates.max_abs_correlation,
    }))

    # model improvement: single-feature PR-AUC vs amount baseline (or 0)
    improve_ok = True
    improve_detail: dict = {'note': 'insufficient labels or sklearn missing'}
    if 'label' in raw.columns and average_precision_score is not None and n >= gates.min_rows:
        aligned = prod[['transaction_id']].copy()
        aligned['_cand'] = cand_v.values
        y_frame = raw.copy()
        if 'transaction_id' not in y_frame.columns and 'id' in y_frame.columns:
            y_frame['transaction_id'] = y_frame['id']
        merged = aligned.merge(y_frame[['transaction_id', 'label']], on='transaction_id', how='left')
        y = pd.to_numeric(merged['label'], errors='coerce')
        mask = y.notna()
        if int(mask.sum()) >= gates.min_rows and y[mask].nunique() > 1:
            yv = y[mask].astype(int).to_numpy()
            score = merged.loc[mask, '_cand'].astype(float).to_numpy()
            pr = float(average_precision_score(yv, score))
            baseline = 0.0
            if 'amount' in prod.columns:
                base_m = aligned.merge(
                    prod[['transaction_id', 'amount']], on='transaction_id', how='left',
                )
                baseline = float(average_precision_score(yv, base_m.loc[mask, 'amount'].fillna(0).to_numpy()))
            lift = pr - baseline
            improve_ok = lift >= gates.min_pr_auc_lift
            improve_detail = {'pr_auc': pr, 'baseline_pr_auc': baseline, 'lift': lift, 'threshold': gates.min_pr_auc_lift}
    results.append(_gate('model_improvement', improve_ok, improve_detail))

    # segment impact: candidate variance across segments should not be all-null / zero-width only
    seg_ok = True
    seg_detail: dict = {}
    seg_col = gates.segment_col
    if seg_col in raw.columns and n >= gates.min_rows:
        work = prod[['transaction_id']].copy()
        work['_cand'] = cand_v.values
        if 'transaction_id' not in raw.columns and 'id' in raw.columns:
            seg_src = raw.assign(transaction_id=raw['id'])
        else:
            seg_src = raw
        work = work.merge(seg_src[['transaction_id', seg_col]], on='transaction_id', how='left')
        failures = []
        for key, grp in work.groupby(seg_col):
            if len(grp) < gates.segment_min_n:
                continue
            if float(grp['_cand'].isna().mean()) > gates.max_missing_rate:
                failures.append({'segment': str(key), 'missing_rate': float(grp['_cand'].isna().mean())})
        seg_ok = not failures
        seg_detail = {'failures': failures, 'segment_col': seg_col}
    results.append(_gate('segment_impact', seg_ok, seg_detail))

    passed = all(r['passed'] for r in results)
    failed = [r['gate'] for r in results if not r['passed']]
    return {
        'passed': passed,
        'gates': results,
        'failed_gates': failed,
        'thresholds': asdict(gates),
        'reason': 'all candidate feature gates passed' if passed else f"failed gates: {', '.join(failed)}",
    }
