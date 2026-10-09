import uuid
from datetime import datetime, timezone

from sklearn.preprocessing import StandardScaler
import hdbscan

from ..discovery.pipeline import discover_patterns

DISCOVER_COLS = [
    'amount', 'imei_account_count', 'imei_sim_count', 'ip_account_count',
    'txn_velocity_1h', 'location_distance_km', 'device_change_rate', 'sim_change_rate',
]


def discover(df):
    """Candidate unknown-pattern discovery. Clusters are never labeled fraud."""
    if df is None or len(df) == 0:
        return []
    result = discover_patterns(
        df,
        min_input_rows=5,
        min_cluster_size=5,
        min_samples=5,
        top_fraction=0.35,
        contamination=0.12,
        random_state=42,
        persist=False,
    )
    if result.candidates:
        return [c.to_legacy() for c in result.candidates]
    return _legacy_discover(df)


def _legacy_discover(df):
    if df is None or len(df) < 5:
        return []
    work = df.copy()
    for c in DISCOVER_COLS:
        if c not in work.columns:
            work[c] = 0
    X = StandardScaler().fit_transform(work[DISCOVER_COLS].fillna(0))
    min_cluster_size = max(5, min(50, len(work) // 100 or 5))
    min_cluster_size = min(min_cluster_size, max(5, len(work) // 2))
    labels = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size, prediction_data=False).fit_predict(X)
    out = []
    for cl in sorted(set(labels)):
        if cl < 0:
            continue
        g = work[labels == cl]
        if len(g) < 5:
            continue
        evidence_cols = [c for c in ['id', 'account_id', 'imei', 'sim_id', 'ip', 'amount', 'label'] if c in g.columns]
        out.append({
            'id': 'FP-' + uuid.uuid4().hex[:8].upper(),
            'name': f'Candidate pattern {cl}',
            'state': 'DISCOVERED',
            'signature': {
                'cluster_size': int(len(g)),
                'mean_amount': float(g['amount'].mean()),
                'mean_imei_accounts': float(g['imei_account_count'].mean()),
                'mean_ip_accounts': float(g['ip_account_count'].mean()),
                'mean_velocity': float(g['txn_velocity_1h'].mean()),
            },
            'evidence': g[evidence_cols].head(20).to_dict('records'),
            'created_at': datetime.now(timezone.utc).isoformat(),
        })
    return out
