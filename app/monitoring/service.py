from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import uuid

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

from ..db import conn
from ..observability import DATA_DRIFT_PSI

NUMERICAL_FEATURES = (
    'amount', 'account_age_days', 'imei_account_count', 'imei_sim_count',
    'ip_account_count', 'merchant_fraud_rate', 'txn_velocity_1h',
    'location_distance_km', 'device_change_rate', 'sim_change_rate', 'hour_deviation',
)
CATEGORICAL_FEATURES = ('merchant_id', 'location')
REFERENCE_SHARE = 0.7
MIN_WINDOW_ROWS = 10


def _policy() -> dict:
    warning = float(os.getenv('DRIFT_PSI_WARNING', '0.10'))
    critical = float(os.getenv('DRIFT_PSI_CRITICAL', '0.25'))
    recommendation = os.getenv('DRIFT_RETRAINING_RECOMMENDATION_POLICY', 'warning').strip().lower()
    if not 0 <= warning < critical:
        raise ValueError('DRIFT_PSI_WARNING must be non-negative and below DRIFT_PSI_CRITICAL')
    if recommendation not in {'disabled', 'critical', 'warning'}:
        raise ValueError('DRIFT_RETRAINING_RECOMMENDATION_POLICY must be disabled, critical, or warning')
    return {
        'metric': 'PSI',
        'warning_threshold': warning,
        'critical_threshold': critical,
        'recommendation_trigger': recommendation,
        'minimum_rows_per_window': MIN_WINDOW_ROWS,
        'reference_share': REFERENCE_SHARE,
    }


def _psi(reference: np.ndarray, current: np.ndarray, *, categorical: bool = False) -> float:
    if categorical:
        ref = pd.Series(reference).fillna('__MISSING__').astype(str)
        cur = pd.Series(current).fillna('__MISSING__').astype(str)
        top = set(ref.value_counts().head(50).index) | set(cur.value_counts().head(50).index)
        ref = ref.where(ref.isin(top), '__OTHER__')
        cur = cur.where(cur.isin(top), '__OTHER__')
        bins = sorted(set(ref.unique()) | set(cur.unique()))
        ref_counts = ref.value_counts().reindex(bins, fill_value=0).to_numpy(dtype=float)
        cur_counts = cur.value_counts().reindex(bins, fill_value=0).to_numpy(dtype=float)
    else:
        ref = pd.to_numeric(pd.Series(reference), errors='coerce').replace([np.inf, -np.inf], np.nan).dropna().to_numpy(dtype=float)
        cur = pd.to_numeric(pd.Series(current), errors='coerce').replace([np.inf, -np.inf], np.nan).dropna().to_numpy(dtype=float)
        quantiles = np.unique(np.quantile(ref, np.linspace(0, 1, 11)))
        if len(quantiles) < 3:
            center = float(np.median(ref))
            delta = max(abs(center) * 0.01, 1e-3)
            edges = np.array([-np.inf, center - delta, center + delta, np.inf])
        else:
            edges = np.concatenate(([-np.inf], quantiles[1:-1], [np.inf]))
        ref_counts, _ = np.histogram(ref, bins=edges)
        cur_counts, _ = np.histogram(cur, bins=edges)
    epsilon = 1e-6
    ref_share = (ref_counts + epsilon) / (ref_counts.sum() + epsilon * len(ref_counts))
    cur_share = (cur_counts + epsilon) / (cur_counts.sum() + epsilon * len(cur_counts))
    return float(np.sum((cur_share - ref_share) * np.log(cur_share / ref_share)))


def _status(value: float, policy: dict) -> str:
    if value >= policy['critical_threshold']:
        return 'CRITICAL'
    if value >= policy['warning_threshold']:
        return 'WARNING'
    return 'NORMAL'


def run_drift_monitoring() -> dict:
    policy = _policy()
    columns = ['ts', *NUMERICAL_FEATURES, *CATEGORICAL_FEATURES]
    c = conn()
    try:
        rows = c.execute(f"SELECT {','.join(columns)} FROM transactions WHERE ts IS NOT NULL ORDER BY ts ASC").fetchall()
    finally:
        c.close()
    frame = pd.DataFrame([dict(row) for row in rows])
    if frame.empty:
        raise ValueError('no timestamped transaction data is available for drift monitoring')
    frame['ts'] = pd.to_datetime(frame.ts, errors='coerce', utc=True)
    frame = frame.dropna(subset=['ts']).sort_values('ts').reset_index(drop=True)
    split_at = int(len(frame) * REFERENCE_SHARE)
    reference = frame.iloc[:split_at]
    current = frame.iloc[split_at:]
    if len(reference) < MIN_WINDOW_ROWS or len(current) < MIN_WINDOW_ROWS:
        raise ValueError(f'at least {MIN_WINDOW_ROWS} rows are required in each drift window')

    results = []
    for feature in NUMERICAL_FEATURES:
        ref = pd.to_numeric(reference[feature], errors='coerce').replace([np.inf, -np.inf], np.nan).dropna().to_numpy(dtype=float)
        cur = pd.to_numeric(current[feature], errors='coerce').replace([np.inf, -np.inf], np.nan).dropna().to_numpy(dtype=float)
        if len(ref) < MIN_WINDOW_ROWS or len(cur) < MIN_WINDOW_ROWS:
            continue
        metric = _psi(ref, cur)
        status = _status(metric, policy)
        ks = float(ks_2samp(ref, cur, method='auto').statistic)
        results.append({
            'feature': feature,
            'drift_metric': metric,
            'metric_name': 'PSI',
            'ks_statistic': ks,
            'threshold': policy['warning_threshold'],
            'critical_threshold': policy['critical_threshold'],
            'status': status,
        })
    for feature in CATEGORICAL_FEATURES:
        ref = reference[feature].fillna('__MISSING__').astype(str).to_numpy()
        cur = current[feature].fillna('__MISSING__').astype(str).to_numpy()
        metric = _psi(ref, cur, categorical=True)
        status = _status(metric, policy)
        results.append({
            'feature': feature,
            'drift_metric': metric,
            'metric_name': 'PSI',
            'threshold': policy['warning_threshold'],
            'critical_threshold': policy['critical_threshold'],
            'status': status,
        })
    if not results:
        raise ValueError('no monitored features have enough valid data')

    run_id = 'DR-' + uuid.uuid4().hex[:12].upper()
    now = datetime.now(timezone.utc).isoformat()
    triggered = [r for r in results if r['status'] != 'NORMAL']
    max_status = 'CRITICAL' if any(r['status'] == 'CRITICAL' for r in triggered) else ('WARNING' if triggered else 'NORMAL')
    trigger_allowed = policy['recommendation_trigger'] == 'warning' and bool(triggered)
    trigger_allowed = trigger_allowed or (policy['recommendation_trigger'] == 'critical' and any(r['status'] == 'CRITICAL' for r in triggered))

    recommendation = None
    c = conn()
    try:
        c.execute(
            'INSERT INTO drift_monitor_runs(run_id,overall_status,policy_json,results_json,reference_rows,current_rows,created_at) VALUES(?,?,?,?,?,?,?)',
            (run_id, max_status, json.dumps(policy), json.dumps(results), len(reference), len(current), now),
        )
        if trigger_allowed:
            recommendation_id = 'RR-' + uuid.uuid4().hex[:12].upper()
            triggered_features = [
                {'feature': r['feature'], 'status': r['status'], 'drift_metric': r['drift_metric'], 'metric_name': r['metric_name']}
                for r in triggered
            ]
            c.execute(
                'INSERT INTO retraining_recommendations(recommendation_id,run_id,status,policy_json,features_json,created_at) VALUES(?,?,?,?,?,?)',
                (recommendation_id, run_id, 'PENDING_REVIEW', json.dumps(policy), json.dumps(triggered_features), now),
            )
            recommendation = {
                'recommendation_id': recommendation_id,
                'status': 'PENDING_REVIEW',
                'requires_manual_approval': True,
                'triggered_features': triggered_features,
            }
        c.commit()
    finally:
        c.close()

    for row in results:
        DATA_DRIFT_PSI.labels(row['feature']).set(row['drift_metric'])
    return {
        'run_id': run_id,
        'overall_status': max_status,
        'policy': policy,
        'reference_window_rows': len(reference),
        'current_window_rows': len(current),
        'results': results,
        'retraining_recommendation': recommendation,
        'automatic_retraining': False,
    }
