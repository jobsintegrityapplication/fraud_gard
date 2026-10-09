from __future__ import annotations

from pathlib import Path

import pandas as pd

from ...features.engine import compute_features
from ...features.registry import production_registry
from ...features.windows import FEATURE_SET_VERSION
from .gates import PromotionGates, evaluate_promotion_gates
from .registry import (
    CHAMPION_STATUS,
    get_champion,
    keep_champion_active,
    new_model_id,
    set_champion,
    upsert_model_record,
)
from ...observability import record_feature_drift


def compute_drift(challenger_frame: pd.DataFrame, champion_frame: pd.DataFrame | None) -> dict:
    if champion_frame is None or champion_frame.empty or challenger_frame is None or challenger_frame.empty:
        return {'ok': True, 'max_mean_shift': 0.0, 'note': 'no champion baseline for drift'}
    names = production_registry().names()
    c_feats = compute_features(challenger_frame)
    p_feats = compute_features(champion_frame)
    shifts = []
    feature_shifts = {}
    for name in names:
        if name not in c_feats.columns or name not in p_feats.columns:
            continue
        a = pd.to_numeric(c_feats[name], errors='coerce').fillna(0).astype(float)
        b = pd.to_numeric(p_feats[name], errors='coerce').fillna(0).astype(float)
        std = float(b.std()) if len(b) > 1 else 1.0
        std = max(std, 1e-6)
        shift = abs(float(a.mean()) - float(b.mean())) / std
        shifts.append(shift)
        feature_shifts[name] = shift
    max_shift = float(max(shifts) if shifts else 0.0)
    return {'ok': True, 'max_mean_shift': max_shift, 'n_features_compared': len(shifts), 'feature_mean_shift': feature_shifts}


def register_challenger_from_artifact(
    art,
    *,
    dataset_version: str,
    artifact_uri: str | None = None,
    champion_baseline: pd.DataFrame | None = None,
    train_frame: pd.DataFrame | None = None,
    gates: PromotionGates | None = None,
    auto_promote: bool = True,
) -> dict:
    """
    Register a newly trained model as challenger, run promotion gates.
    If gates fail, champion stays PRODUCTION and is not replaced.
    """
    model_id = new_model_id()
    version = art.meta.get('model_version')
    metrics = art.meta.get('metrics', {}).get('test', {}) or {}
    record = upsert_model_record(
        model_id=model_id,
        version=version,
        algorithm=art.meta.get('algorithm') or 'XGBoost',
        dataset_version=dataset_version,
        feature_version=art.meta.get('feature_set_version') or FEATURE_SET_VERSION,
        training_timestamp=art.meta.get('trained_at') or '',
        metrics=metrics,
        status='TRAINING',
        artifact_uri=artifact_uri,
    )
    upsert_model_record(
        model_id=model_id,
        version=version,
        algorithm=record['algorithm'],
        dataset_version=dataset_version,
        feature_version=record['feature_version'],
        training_timestamp=record['training_timestamp'],
        metrics=metrics,
        status='VALIDATING',
        artifact_uri=artifact_uri,
    )
    drift = compute_drift(train_frame if train_frame is not None else pd.DataFrame(), champion_baseline)
    record_feature_drift(str(version or model_id), drift.get('feature_mean_shift') or {})
    gate_input_record = {
        'model_id': model_id,
        'version': version,
        'algorithm': record['algorithm'],
        'dataset_version': dataset_version,
        'feature_version': record['feature_version'],
        'training_timestamp': record['training_timestamp'],
        'metrics': metrics,
    }
    decision = evaluate_promotion_gates(
        metrics=metrics,
        split_metrics=art.meta.get('metrics'),
        segment_metrics=art.meta.get('segment_metrics'),
        split_sizes=art.meta.get('split_sizes'),
        record=gate_input_record,
        drift_report=drift,
        data_quality={
            'n_test': metrics.get('n') or (art.meta.get('split_sizes') or {}).get('test'),
            'n_positive': metrics.get('n_positive'),
        },
        gates=gates,
    )
    champion_before = get_champion()
    if decision['promoted'] and auto_promote:
        upsert_model_record(
            model_id=model_id,
            version=version,
            algorithm=record['algorithm'],
            dataset_version=dataset_version,
            feature_version=record['feature_version'],
            training_timestamp=record['training_timestamp'],
            metrics=metrics,
            status='CANARY',
            artifact_uri=artifact_uri,
            gate_results=decision,
        )
        promoted = set_champion(model_id)
        outcome = {
            'model': promoted,
            'gates': decision,
            'champion_replaced': True,
            'previous_champion': champion_before,
        }
    else:
        status = 'FAILED' if decision['failed_gates'] else 'SHADOW'
        failed = upsert_model_record(
            model_id=model_id,
            version=version,
            algorithm=record['algorithm'],
            dataset_version=dataset_version,
            feature_version=record['feature_version'],
            training_timestamp=record['training_timestamp'],
            metrics=metrics,
            status=status,
            artifact_uri=artifact_uri,
            gate_results=decision,
        )
        keep = keep_champion_active(decision['reason'])
        champion_after = get_champion()
        if champion_before and champion_after and champion_before['model_id'] != champion_after['model_id']:
            raise RuntimeError('champion was replaced despite failed promotion gates')
        if champion_after and champion_after['status'] != CHAMPION_STATUS:
            raise RuntimeError('champion is not in PRODUCTION after failed challenger')
        outcome = {
            'model': failed,
            'gates': decision,
            'champion_replaced': False,
            'previous_champion': champion_before,
            'champion': keep['champion'],
            'reason': keep['reason'],
        }
    art.meta['lifecycle'] = outcome
    art.meta['promotion'] = {
        'promoted': bool(decision['promoted'] and outcome['champion_replaced']),
        'gates': decision,
        'champion_replaced': outcome['champion_replaced'],
        'reason': decision['reason'] if decision['promoted'] else f"challenger not promoted; champion kept active. {decision['reason']}",
    }
    if artifact_uri:
        meta_path = Path(artifact_uri).parent / 'meta.json'
        if meta_path.exists():
            import json
            meta_path.write_text(json.dumps(art.meta, indent=2, default=str), encoding='utf-8')
    return outcome
