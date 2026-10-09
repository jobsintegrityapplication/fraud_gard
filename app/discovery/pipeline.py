from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from math import ceil

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from ..features.engine import compute_features
from ..features.registry import production_registry
from ..features.windows import FeatureWindows
from ..ml.anomaly.model import align_features
from .candidates import DiscoveryResult, FraudPatternCandidate
from .lifecycle import INITIAL_STATE
from .store import upsert_candidates

DISCOVERY_METHOD = 'isolation_forest_hdbscan'


def _txid(df: pd.DataFrame) -> pd.Series:
    if 'transaction_id' in df.columns:
        return df['transaction_id'].astype(str)
    if 'id' in df.columns:
        return df['id'].astype(str)
    return pd.Series([f'ROW-{i}' for i in range(len(df))], index=df.index)


def dedupe_transactions(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    work = df.copy()
    work['transaction_id'] = _txid(work)
    before = len(work)
    work = work.drop_duplicates(subset=['transaction_id'], keep='first')
    return work.reset_index(drop=True), int(before - len(work))


def pattern_id_for(cluster_id: int, sample_ids: list[str], random_state: int) -> str:
    payload = f'{random_state}:{int(cluster_id)}:' + ','.join(sorted(sample_ids))
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12].upper()
    return f'FPD-{digest}'


def _nunique(series: pd.Series) -> int:
    return int(series.dropna().astype(str).nunique())


def entity_summary(group: pd.DataFrame) -> dict:
    def col(*names):
        for n in names:
            if n in group.columns:
                return group[n]
        return pd.Series(dtype=object)

    merchants = col('merchant_id')
    locations = col('location', 'location_code')
    summary = {
        'n_accounts': _nunique(col('account_id')),
        'n_customers': _nunique(col('customer_id')),
        'n_imeis': _nunique(col('imei')),
        'n_sims': _nunique(col('sim_id')),
        'n_ips': _nunique(col('ip')),
        'n_merchants': _nunique(merchants),
        'n_locations': _nunique(locations),
    }
    if len(merchants):
        summary['top_merchants'] = merchants.astype(str).value_counts().head(5).to_dict()
    if len(locations):
        summary['top_locations'] = locations.astype(str).value_counts().head(5).to_dict()
    return summary


def feature_signature(cluster_X: np.ndarray, selected_X: np.ndarray, names: list[str], top_k: int = 8) -> dict:
    means = cluster_X.mean(axis=0)
    global_mean = selected_X.mean(axis=0)
    global_std = selected_X.std(axis=0)
    global_std = np.where(global_std < 1e-9, 1.0, global_std)
    z = (means - global_mean) / global_std
    order = np.argsort(np.abs(z))[::-1][:top_k]
    distinctive = [
        {
            'feature': names[i],
            'cluster_mean': float(means[i]),
            'selected_mean': float(global_mean[i]),
            'z_vs_selected': float(z[i]),
        }
        for i in order
    ]
    return {
        'distinctive_features': distinctive,
        'means': {names[i]: float(means[i]) for i in range(len(names))},
        'size': int(len(cluster_X)),
    }


def normalize_features(X: np.ndarray) -> tuple[np.ndarray, StandardScaler]:
    scaler = StandardScaler()
    if len(X) == 0:
        return X, scaler
    scaled = scaler.fit_transform(X)
    scaled = np.nan_to_num(scaled, nan=0.0, posinf=0.0, neginf=0.0)
    return scaled, scaler


def select_top_anomalies(X: np.ndarray, *, contamination: float, top_fraction: float, random_state: int) -> np.ndarray:
    n = len(X)
    if n == 0:
        return np.array([], dtype=int)
    forest = IsolationForest(
        n_estimators=200,
        contamination=contamination if 0 < contamination <= 0.5 else 0.1,
        random_state=int(random_state),
        n_jobs=1,
        bootstrap=False,
    )
    forest.fit(X)
    strength = -np.asarray(forest.decision_function(X), dtype=float)
    k = max(1, int(ceil(top_fraction * n)))
    k = min(k, n)
    return np.argsort(strength)[::-1][:k]


def _fit_hdbscan(X: np.ndarray, min_cluster_size: int, min_samples: int | None):
    import hdbscan
    size = max(2, int(min_cluster_size))
    samples = int(min_samples) if min_samples is not None else size
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=size,
        min_samples=max(1, samples),
        metric='euclidean',
        cluster_selection_method='eom',
        core_dist_n_jobs=1,
        allow_single_cluster=True,
    )
    labels = clusterer.fit_predict(X)
    return np.asarray(labels)


def cluster_normalized(
    X_scaled: np.ndarray,
    transaction_ids: list[str],
    *,
    source_rows: pd.DataFrame | None = None,
    feature_names: list[str] | None = None,
    min_cluster_size: int = 5,
    min_samples: int | None = None,
    random_state: int = 42,
    sample_limit: int = 20,
    discovery_timestamp: str | None = None,
) -> DiscoveryResult:
    ts = discovery_timestamp or datetime.now(timezone.utc).isoformat()
    names = feature_names or [f'f{i}' for i in range(X_scaled.shape[1] if len(X_scaled) else 0)]
    if len(X_scaled) < max(2, min_cluster_size):
        return DiscoveryResult(
            n_anomalies_selected=len(X_scaled),
            reason='insufficient_samples',
            discovery_method=DISCOVERY_METHOD,
        )
    labels = _fit_hdbscan(X_scaled, min_cluster_size, min_samples)
    ids = [str(x) for x in transaction_ids]
    n_noise = int((labels < 0).sum())
    n_discarded_small = 0
    candidates: list[FraudPatternCandidate] = []
    src = source_rows.reset_index(drop=True) if source_rows is not None else pd.DataFrame({'transaction_id': ids})
    if 'transaction_id' not in src.columns:
        src = src.copy()
        src['transaction_id'] = ids

    for cluster_id in sorted(set(labels.tolist())):
        if cluster_id < 0:
            continue
        mask = labels == cluster_id
        size = int(mask.sum())
        if size < min_cluster_size:
            n_discarded_small += 1
            continue
        cluster_ids = [ids[i] for i, flag in enumerate(mask) if flag]
        sample_ids = sorted(cluster_ids)[:sample_limit]
        group = src[src['transaction_id'].astype(str).isin(cluster_ids)]
        sig = feature_signature(X_scaled[mask], X_scaled, names)
        candidates.append(FraudPatternCandidate(
            pattern_id=pattern_id_for(cluster_id, sample_ids, random_state),
            cluster_id=int(cluster_id),
            feature_signature=sig,
            sample_transaction_ids=sample_ids,
            entity_summary=entity_summary(group),
            cluster_size=size,
            discovery_timestamp=ts,
            discovery_method=DISCOVERY_METHOD,
            state=INITIAL_STATE,
        ))
    return DiscoveryResult(
        candidates=candidates,
        n_anomalies_selected=len(X_scaled),
        n_noise=n_noise,
        n_discarded_small=n_discarded_small,
        discovery_method=DISCOVERY_METHOD,
        auto_classified_as_fraud=False,
    )


def discover_patterns(
    raw: pd.DataFrame,
    *,
    contamination: float = 0.1,
    top_fraction: float = 0.2,
    min_cluster_size: int = 5,
    min_samples: int | None = 5,
    min_input_rows: int = 20,
    random_state: int = 42,
    persist: bool = True,
    windows: FeatureWindows | None = None,
) -> DiscoveryResult:
    if raw is None or len(raw) == 0:
        return DiscoveryResult(reason='insufficient_samples')
    work, n_dup = dedupe_transactions(raw)
    result_meta = {'n_input': int(len(raw)), 'n_duplicates_removed': n_dup}
    if len(work) < min_input_rows:
        return DiscoveryResult(reason='insufficient_samples', **result_meta)

    registry = production_registry()
    feature_names = registry.names()
    feats = compute_features(work, windows=windows or FeatureWindows())
    medians = {name: 0.0 for name in feature_names}
    for name in feature_names:
        if name in feats.columns:
            val = float(pd.to_numeric(feats[name], errors='coerce').median())
            medians[name] = val if np.isfinite(val) else 0.0
    X = align_features(feats, feature_names, medians).to_numpy(dtype=float)
    ids = feats['transaction_id'].astype(str).tolist()
    selected_idx = select_top_anomalies(
        X, contamination=contamination, top_fraction=top_fraction, random_state=random_state,
    )
    if len(selected_idx) < min_cluster_size:
        return DiscoveryResult(
            reason='insufficient_samples',
            n_anomalies_selected=int(len(selected_idx)),
            **result_meta,
        )
    selected_X = X[selected_idx]
    selected_ids = [ids[i] for i in selected_idx]
    scaled, _ = normalize_features(selected_X)
    src_rows = work.set_index(work['transaction_id'].astype(str), drop=False).reindex(selected_ids).reset_index(drop=True)
    clustered = cluster_normalized(
        scaled,
        selected_ids,
        source_rows=src_rows,
        feature_names=feature_names,
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        random_state=random_state,
    )
    clustered.n_input = result_meta['n_input']
    clustered.n_duplicates_removed = n_dup
    clustered.n_anomalies_selected = int(len(selected_idx))
    if persist and clustered.candidates:
        upsert_candidates(clustered.candidates)
    return clustered
