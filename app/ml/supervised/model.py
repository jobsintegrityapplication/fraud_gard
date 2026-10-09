from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss
from xgboost import XGBClassifier

from ... import config
from ...features.engine import compute_features
from ...features.registry import production_registry
from ...features.windows import FEATURE_SET_VERSION, FeatureWindows
from .metrics import PromotionThresholds, classification_metrics, segment_metrics
from ...observability import observe_training, record_feature_drift, record_model_evaluation
from .split import temporal_split
from .tracking import log_training_run

SEGMENT_COLS = ['handset_model', 'merchant_id', 'segment', 'channel', 'location']


def _risk_level(score: float) -> str:
    if score >= 0.85:
        return 'CRITICAL'
    if score >= 0.65:
        return 'HIGH'
    if score >= 0.40:
        return 'MEDIUM'
    return 'LOW'


def _prepare_frame(raw: pd.DataFrame, windows: FeatureWindows | None = None) -> pd.DataFrame:
    work = raw.copy()
    if 'transaction_id' not in work.columns and 'id' in work.columns:
        work['transaction_id'] = work['id']
    if 'location' not in work.columns and 'location_code' in work.columns:
        work['location'] = work['location_code']
    if 'handset_model' not in work.columns:
        work['handset_model'] = work['model'] if 'model' in work.columns else 'UNKNOWN'
    if 'segment' not in work.columns:
        work['segment'] = 'UNKNOWN'
    if 'channel' not in work.columns:
        work['channel'] = 'APP'
    feats = compute_features(work, windows=windows or FeatureWindows())
    keep = ['transaction_id', 'label', 'ts', *SEGMENT_COLS]
    extra = work[[c for c in keep if c in work.columns]].drop_duplicates('transaction_id')
    merged = feats.merge(extra, on='transaction_id', how='left')
    if 'label' not in merged.columns:
        raise ValueError('label is required to train/evaluate the supervised model')
    # Preserve unlabeled rows as feature-window context above, but never coerce
    # them into negative labels. Training/evaluation must use reviewed labels.
    merged = merged.loc[merged['label'].notna()].copy()
    if merged.empty:
        raise ValueError('at least one reviewed label is required to train/evaluate the supervised model')
    merged['label'] = merged['label'].astype(int)
    return merged


def _matrix(frame: pd.DataFrame, feature_names: list[str]):
    X = frame[feature_names].fillna(0).astype(float)
    y = frame['label'].astype(int).values
    return X, y


def _scale_pos_weight(y: np.ndarray) -> float:
    pos = max(int((y == 1).sum()), 1)
    neg = int((y == 0).sum())
    return float(neg / pos)


def _maybe_calibrate(estimator, X_val, y_val):
    if len(y_val) < 40 or len(np.unique(y_val)) < 2 or int((y_val == 1).sum()) < 8:
        return estimator, False, {}
    raw_p = estimator.predict_proba(X_val)[:, 1]
    raw_brier = float(brier_score_loss(y_val, raw_p))
    method = 'isotonic' if int((y_val == 1).sum()) >= 15 else 'sigmoid'
    try:
        cal = CalibratedClassifierCV(estimator, method=method, cv='prefit')
        cal.fit(X_val, y_val)
        cal_p = cal.predict_proba(X_val)[:, 1]
        cal_brier = float(brier_score_loss(y_val, cal_p))
    except Exception:
        return estimator, False, {'raw_brier': raw_brier, 'calibrated': False}
    if cal_brier < raw_brier - 1e-6:
        return cal, True, {'raw_brier': raw_brier, 'calibrated_brier': cal_brier, 'method': method}
    return estimator, False, {'raw_brier': raw_brier, 'calibrated_brier': cal_brier, 'method': method, 'kept_raw': True}


def _shap_top(model, X: pd.DataFrame, row: pd.DataFrame, k: int = 5) -> list[dict]:
    """Backward-compatible helper returning FeatureExplanation dicts."""
    from .explain import build_feature_explanations, compute_shap_values
    names = list(row.columns)
    sv, _, _ = compute_shap_values(model, row)
    return [f.to_dict() for f in build_feature_explanations(names, row, sv, top_k=k)]


def new_version_id() -> str:
    return 'SUP-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


class SupervisedArtifact:
    def __init__(self, model, feature_names: list[str], meta: dict, calibrator_applied: bool):
        self.model = model
        self.feature_names = feature_names
        self.meta = meta
        self.calibrator_applied = calibrator_applied

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(X[self.feature_names].fillna(0).astype(float))[:, 1]

    def explain(self, X_row: pd.DataFrame, k: int = 5, *, risk_score: float | None = None, transaction_id: str | None = None):
        from .explain import PredictionExplanation, explain_prediction
        score = float(risk_score) if risk_score is not None else float(self.predict_proba(X_row)[0])
        return explain_prediction(
            self.model,
            X_row,
            self.feature_names,
            risk_score=score,
            model_version=str(self.meta.get('model_version') or ''),
            feature_version=str(self.meta.get('feature_set_version') or FEATURE_SET_VERSION),
            top_k=k,
            transaction_id=transaction_id,
        )


def artifact_dir(version: str) -> Path:
    return Path(config.MODEL_DIR) / 'supervised' / version


def current_version_file() -> Path:
    return Path(config.MODEL_DIR) / 'supervised' / 'CURRENT'


def save_artifact(art: SupervisedArtifact, version: str, promote: bool):
    """Persist artifact. CURRENT pointer is updated only on promotion or first bootstrap."""
    config.ensure_dirs()
    dest = artifact_dir(version)
    dest.mkdir(parents=True, exist_ok=True)
    joblib.dump(art, dest / 'model.joblib')
    (dest / 'meta.json').write_text(json.dumps(art.meta, indent=2, default=str), encoding='utf-8')
    pointer = {
        'current': version,
        'promoted': bool(promote),
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }
    # Bootstrap only when no champion/CURRENT exists. Failed challengers must not replace champion.
    if promote:
        current_version_file().write_text(json.dumps(pointer), encoding='utf-8')
    elif not current_version_file().exists():
        pointer['bootstrap'] = True
        current_version_file().write_text(json.dumps(pointer), encoding='utf-8')
    return dest


def load_artifact(version: str | None = None) -> SupervisedArtifact:
    if version is None or version.upper() == 'CURRENT':
        if not current_version_file().exists():
            raise FileNotFoundError('no current supervised model')
        version = json.loads(current_version_file().read_text(encoding='utf-8'))['current']
    path = artifact_dir(version) / 'model.joblib'
    if not path.exists():
        raise FileNotFoundError(f'model version not found: {version}')
    return joblib.load(path)


@observe_training('xgboost')
def train_supervised(
    raw: pd.DataFrame,
    *,
    thresholds: PromotionThresholds | None = None,
    windows: FeatureWindows | None = None,
    n_estimators: int = 120,
    max_depth: int = 4,
    random_state: int = 42,
    decision_threshold: float = 0.5,
    dataset_version: str | None = None,
    register_lifecycle: bool = True,
    promotion_gates=None,
) -> SupervisedArtifact:
    thresholds = thresholds or PromotionThresholds()
    registry = production_registry()
    feature_names = registry.names()
    frame = _prepare_frame(raw, windows)
    splits = temporal_split(frame)
    X_train, y_train = _matrix(splits.train, feature_names)
    X_valid, y_valid = _matrix(splits.valid, feature_names)
    X_test, y_test = _matrix(splits.test, feature_names)
    spw = _scale_pos_weight(y_train)
    clf = XGBClassifier(
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=0.08,
        subsample=0.85,
        colsample_bytree=0.85,
        eval_metric='logloss',
        random_state=random_state,
        scale_pos_weight=spw,
        n_jobs=1,
        tree_method='hist',
    )
    clf.fit(X_train, y_train)
    model, calibrated, cal_info = _maybe_calibrate(clf, X_valid, y_valid)
    version = new_version_id()
    split_metrics = {}
    for name, X, y in (('train', X_train, y_train), ('valid', X_valid, y_valid), ('test', X_test, y_test)):
        proba = model.predict_proba(X)[:, 1]
        split_metrics[name] = classification_metrics(y, proba, decision_threshold)
        record_model_evaluation(version, name, split_metrics[name])
    test_scores = pd.DataFrame({
        'transaction_id': splits.test['transaction_id'].values,
        'label': y_test,
        'proba': model.predict_proba(X_test)[:, 1],
    })
    for col in SEGMENT_COLS:
        if col in splits.test.columns:
            test_scores[col] = splits.test[col].values
    segments = segment_metrics(test_scores, 'label', 'proba', [c for c in SEGMENT_COLS if c in test_scores.columns], decision_threshold)
    ds_version = dataset_version or f'ds-{len(frame)}-{random_state}'
    feature_baseline = {}
    for name in feature_names:
        if name not in frame.columns:
            continue
        values = pd.to_numeric(frame[name], errors='coerce').replace([np.inf, -np.inf], np.nan).fillna(0)
        mean = float(values.mean())
        std = float(values.std())
        feature_baseline[name] = {'mean': mean, 'std': std if np.isfinite(std) and std > 1e-6 else 1e-6}
    meta = {
        'model_version': version,
        'algorithm': 'XGBoost',
        'feature_set_version': FEATURE_SET_VERSION,
        'dataset_version': ds_version,
        'feature_names': feature_names,
        'feature_baseline': feature_baseline,
        'scale_pos_weight': spw,
        'calibrated': calibrated,
        'calibration': cal_info,
        'decision_threshold': decision_threshold,
        'split_sizes': {'train': int(len(splits.train)), 'valid': int(len(splits.valid)), 'test': int(len(splits.test))},
        'metrics': split_metrics,
        'segment_metrics': segments,
        'trained_at': datetime.now(timezone.utc).isoformat(),
        'n_estimators': n_estimators,
        'max_depth': max_depth,
        'random_state': random_state,
    }
    art = SupervisedArtifact(model, feature_names, meta, calibrated)
    # Persist challenger artifact first without promoting; lifecycle decides champion replacement.
    dest = save_artifact(art, version, promote=False)
    if register_lifecycle:
        from ..lifecycle.gates import PromotionGates
        from ..lifecycle.promote import register_challenger_from_artifact
        from ..lifecycle.registry import get_champion
        gates = promotion_gates or PromotionGates(
            precision_min=thresholds.precision,
            recall_min=thresholds.recall,
        )
        outcome = register_challenger_from_artifact(
            art,
            dataset_version=ds_version,
            artifact_uri=str(dest / 'model.joblib'),
            train_frame=frame,
            gates=gates,
            auto_promote=True,
        )
        if outcome.get('champion_replaced'):
            save_artifact(art, version, promote=True)
        else:
            # Ensure CURRENT still points at existing champion artifact if present.
            champ = get_champion()
            if champ and champ.get('version') and champ['version'] != version:
                pointer = {
                    'current': champ['version'],
                    'promoted': True,
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                    'note': 'challenger failed gates; champion retained',
                }
                current_version_file().write_text(json.dumps(pointer), encoding='utf-8')
    else:
        from .metrics import promotion_decision
        art.meta['promotion'] = promotion_decision(split_metrics['test'], thresholds)
        (dest / 'meta.json').write_text(json.dumps(art.meta, indent=2, default=str), encoding='utf-8')
    log_training_run(
        {'model_version': version, 'scale_pos_weight': spw, 'calibrated': calibrated, 'n_estimators': n_estimators},
        split_metrics,
        dest,
        tags={
            'feature_set_version': FEATURE_SET_VERSION,
            'promoted': str(bool((art.meta.get('promotion') or {}).get('promoted'))),
        },
    )
    return art


def score_frame(raw: pd.DataFrame, art: SupervisedArtifact, windows: FeatureWindows | None = None) -> pd.DataFrame:
    work = raw.copy()
    if 'transaction_id' not in work.columns and 'id' in work.columns:
        work['transaction_id'] = work['id']
    feats = compute_features(work, windows=windows or FeatureWindows())
    X = feats[art.feature_names].replace([np.inf, -np.inf], np.nan).fillna(0).astype(float)
    baseline = art.meta.get('feature_baseline') or {}
    shifts = {}
    for name in art.feature_names:
        reference = baseline.get(name)
        if not reference or name not in X.columns:
            continue
        std = max(float(reference.get('std') or 1e-6), 1e-6)
        mean = float(X[name].mean())
        if np.isfinite(mean):
            shifts[name] = abs(mean - float(reference.get('mean') or 0)) / std
    record_feature_drift(art.meta.get('model_version', 'unknown'), shifts)
    proba = art.predict_proba(X)
    out = feats[['transaction_id']].copy()
    out['risk_score'] = proba
    out['risk_level'] = [_risk_level(s) for s in proba]
    out['model_version'] = art.meta['model_version']
    return out


def score_one(history: pd.DataFrame, transaction_id: str, art: SupervisedArtifact, top_k: int = 5) -> dict:
    scored = score_frame(history, art)
    hit = scored[scored.transaction_id == transaction_id]
    if hit.empty:
        raise KeyError(transaction_id)
    feats = compute_features(history)
    row = feats[feats.transaction_id == transaction_id]
    rec = hit.iloc[0]
    risk = float(rec['risk_score'])
    explanation = art.explain(row, k=top_k, risk_score=risk, transaction_id=transaction_id)
    expl = explanation.to_dict() if hasattr(explanation, 'to_dict') else explanation
    top = expl['top_features']
    return {
        'transaction_id': transaction_id,
        'risk_score': risk,
        'risk_level': rec['risk_level'],
        'model_version': rec['model_version'],
        'feature_version': art.meta.get('feature_set_version') or FEATURE_SET_VERSION,
        'feature_set_version': art.meta.get('feature_set_version'),
        'top_features': top,
        'explanation': expl,
        'calibrated': bool(art.meta.get('calibrated')),
    }
