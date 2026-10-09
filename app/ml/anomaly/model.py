from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from ... import config
from ...features.engine import compute_features
from ...features.registry import production_registry
from ...features.windows import FEATURE_SET_VERSION, FeatureWindows
from ...observability import observe_training
from ..supervised.tracking import log_training_run

# Anomaly scores are not fraud probabilities. IsolationForest finds outliers
# in feature space; a high score does not classify a transaction as fraud.
TASK_NAME = 'unknown_anomaly_detection'


def parse_contamination(value=None):
    if value is None:
        raw = os.environ.get('ANOMALY_CONTAMINATION', '0.05')
    else:
        raw = value
    if isinstance(raw, str) and raw.strip().lower() == 'auto':
        return 'auto'
    v = float(raw)
    if not 0.0 < v <= 0.5:
        raise ValueError('contamination must be in (0, 0.5] or "auto"')
    return v


def new_version_id() -> str:
    return 'ANOM-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def artifact_dir(version: str) -> Path:
    return Path(config.MODEL_DIR) / 'anomaly' / version


def current_version_file() -> Path:
    return Path(config.MODEL_DIR) / 'anomaly' / 'CURRENT'


def _prepare_ids(raw: pd.DataFrame) -> pd.DataFrame:
    work = raw.copy()
    if 'transaction_id' not in work.columns and 'id' in work.columns:
        work['transaction_id'] = work['id']
    return work


def _feature_frame(raw: pd.DataFrame, windows: FeatureWindows | None = None) -> pd.DataFrame:
    return compute_features(_prepare_ids(raw), windows=windows or FeatureWindows())


def align_features(frame: pd.DataFrame, feature_names: list[str], medians: dict[str, float]) -> pd.DataFrame:
    """Keep every row. Missing columns/values are filled with training medians."""
    aligned = pd.DataFrame(index=frame.index)
    for name in feature_names:
        fill = float(medians.get(name, 0.0))
        if name in frame.columns:
            col = pd.to_numeric(frame[name], errors='coerce')
            aligned[name] = col.fillna(fill)
        else:
            aligned[name] = fill
    return aligned.astype(float)


def normalize_anomaly_raw(raw: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Map IsolationForest anomaly strength to [0, 1]; 1 = most anomalous."""
    raw = np.asarray(raw, dtype=float)
    if not np.isfinite(hi - lo) or abs(hi - lo) < 1e-12:
        return 1.0 / (1.0 + np.exp(-raw))
    return np.clip((raw - lo) / (hi - lo), 0.0, 1.0)


def _raw_anomaly_strength(model: IsolationForest, X: pd.DataFrame) -> np.ndarray:
    # decision_function: higher = more inlier. Invert so higher = more anomalous.
    return -np.asarray(model.decision_function(X), dtype=float)


def _add_ranks(scores: pd.Series) -> pd.Series:
    return scores.rank(ascending=False, method='min').astype(int)


class AnomalyArtifact:
    def __init__(self, model: IsolationForest, feature_names: list[str], medians: dict[str, float], meta: dict):
        self.model = model
        self.feature_names = feature_names
        self.medians = {k: float(v) for k, v in medians.items()}
        self.meta = meta

    def raw_scores(self, X: pd.DataFrame) -> np.ndarray:
        aligned = align_features(X, self.feature_names, self.medians)
        return _raw_anomaly_strength(self.model, aligned)

    def normalized_scores(self, X: pd.DataFrame) -> np.ndarray:
        raw = self.raw_scores(X)
        return normalize_anomaly_raw(raw, float(self.meta['score_low']), float(self.meta['score_high']))


def save_artifact(art: AnomalyArtifact, version: str) -> Path:
    config.ensure_dirs()
    dest = artifact_dir(version)
    dest.mkdir(parents=True, exist_ok=True)
    joblib.dump(art, dest / 'model.joblib')
    (dest / 'meta.json').write_text(json.dumps(art.meta, indent=2, default=str), encoding='utf-8')
    pointer = {
        'current': version,
        'task': TASK_NAME,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }
    current_version_file().write_text(json.dumps(pointer), encoding='utf-8')
    return dest


def load_artifact(version: str | None = None) -> AnomalyArtifact:
    if version is None or version.upper() == 'CURRENT':
        if not current_version_file().exists():
            raise FileNotFoundError('no current anomaly model')
        version = json.loads(current_version_file().read_text(encoding='utf-8'))['current']
    path = artifact_dir(version) / 'model.joblib'
    if not path.exists():
        raise FileNotFoundError(f'anomaly model version not found: {version}')
    return joblib.load(path)


@observe_training('isolation_forest')
def train_anomaly(
    raw: pd.DataFrame,
    *,
    contamination=None,
    random_state: int | None = None,
    n_estimators: int = 200,
    windows: FeatureWindows | None = None,
) -> AnomalyArtifact:
    contamination = parse_contamination(contamination)
    if random_state is None:
        random_state = int(os.environ.get('ANOMALY_RANDOM_STATE', '42'))
    registry = production_registry()
    feature_names = registry.names()
    feats = _feature_frame(raw, windows)
    if feats.empty:
        raise ValueError('cannot train anomaly model on an empty frame')
    medians = {name: float(pd.to_numeric(feats[name], errors='coerce').median()) if name in feats.columns else 0.0 for name in feature_names}
    for name, val in list(medians.items()):
        if not np.isfinite(val):
            medians[name] = 0.0
    X = align_features(feats, feature_names, medians)
    model = IsolationForest(
        n_estimators=int(n_estimators),
        contamination=contamination,
        random_state=int(random_state),
        n_jobs=1,
        bootstrap=False,
    )
    model.fit(X)
    raw_strength = _raw_anomaly_strength(model, X)
    lo = float(np.percentile(raw_strength, 1))
    hi = float(np.percentile(raw_strength, 99))
    if hi <= lo:
        lo, hi = float(np.min(raw_strength)), float(np.max(raw_strength) + 1e-6)
    norm = normalize_anomaly_raw(raw_strength, lo, hi)
    flags = model.predict(X)
    version = new_version_id()
    metrics = {
        'train': {
            'n_rows': int(len(X)),
            'n_flagged': int((flags == -1).sum()),
            'score_mean': float(np.mean(norm)),
            'score_p50': float(np.median(norm)),
            'score_p95': float(np.percentile(norm, 95)),
            'score_max': float(np.max(norm)),
        }
    }
    meta = {
        'model_version': version,
        'algorithm': 'IsolationForest',
        'task': TASK_NAME,
        'anomaly_is_not_fraud': True,
        'feature_set_version': FEATURE_SET_VERSION,
        'feature_names': feature_names,
        'feature_medians': medians,
        'contamination': contamination,
        'random_state': int(random_state),
        'n_estimators': int(n_estimators),
        'score_low': lo,
        'score_high': hi,
        'normalization': 'inverted_decision_function_p1_p99_minmax',
        'metrics': metrics,
        'trained_at': datetime.now(timezone.utc).isoformat(),
    }
    art = AnomalyArtifact(model, feature_names, medians, meta)
    dest = save_artifact(art, version)
    log_training_run(
        {
            'model_version': version,
            'algorithm': 'IsolationForest',
            'contamination': contamination,
            'random_state': random_state,
            'n_estimators': n_estimators,
            'anomaly_is_not_fraud': True,
        },
        metrics,
        dest,
        tags={
            'feature_set_version': FEATURE_SET_VERSION,
            'task': TASK_NAME,
            'anomaly_is_not_fraud': 'true',
        },
        experiment=os.environ.get('MLFLOW_ANOMALY_EXPERIMENT', 'handset-unknown-anomaly'),
    )
    return art


def score_feature_frame(feats: pd.DataFrame, art: AnomalyArtifact) -> pd.DataFrame:
    if feats.empty:
        return pd.DataFrame(columns=['transaction_id', 'anomaly_score', 'anomaly_rank', 'model_version'])
    out = pd.DataFrame({'transaction_id': feats['transaction_id'].astype(str).values})
    out['anomaly_score'] = art.normalized_scores(feats)
    out['anomaly_rank'] = _add_ranks(out['anomaly_score'])
    out['model_version'] = art.meta['model_version']
    return out.sort_values(['anomaly_rank', 'transaction_id']).reset_index(drop=True)


def score_frame(raw: pd.DataFrame, art: AnomalyArtifact, windows: FeatureWindows | None = None) -> pd.DataFrame:
    feats = _feature_frame(raw, windows)
    return score_feature_frame(feats, art)


def score_one(history: pd.DataFrame, transaction_id: str, art: AnomalyArtifact) -> dict:
    scored = score_frame(history, art)
    hit = scored[scored.transaction_id.astype(str) == str(transaction_id)]
    if hit.empty:
        raise KeyError(transaction_id)
    rec = hit.iloc[0]
    return {
        'transaction_id': str(transaction_id),
        'anomaly_score': float(rec['anomaly_score']),
        'anomaly_rank': int(rec['anomaly_rank']),
        'model_version': rec['model_version'],
    }


def top_anomalies(raw: pd.DataFrame, art: AnomalyArtifact, limit: int = 20) -> list[dict]:
    scored = score_frame(raw, art)
    return scored.head(max(1, int(limit))).to_dict(orient='records')
