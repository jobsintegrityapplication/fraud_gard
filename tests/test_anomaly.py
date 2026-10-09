import pandas as pd

from app.features.engine import compute_features
from app.ml.anomaly.model import (
    load_artifact,
    parse_contamination,
    score_feature_frame,
    score_one,
    train_anomaly,
)


def _normal_transactions(n=80, seed=7):
    rng = pd.Series(range(n))
    return pd.DataFrame({
        'transaction_id': [f'TXN-{i:06d}' for i in rng],
        'ts': pd.date_range('2026-03-01', periods=n, freq='h', tz='UTC'),
        'account_id': [f'ACC-{i % 12:03d}' for i in rng],
        'customer_id': [f'CUS-{i % 12:03d}' for i in rng],
        'imei': [f'IMEI-{i % 10}' for i in rng],
        'sim_id': [f'SIM-{i % 10}' for i in rng],
        'ip': [f'10.0.0.{i % 8}' for i in rng],
        'merchant_id': [f'M-{i % 5}' for i in rng],
        'location': ['DEL'] * n,
        'latitude': 28.6,
        'longitude': 77.2,
        'amount': 250.0 + (rng % 5) * 10.0,
        'channel': 'APP',
        'label': 0,
    })


def _inject_anomalies(df: pd.DataFrame, k=4):
    out = df.copy()
    idx = out.index[-k:]
    out.loc[idx, 'amount'] = 9_999_999.0
    out.loc[idx, 'latitude'] = -45.0
    out.loc[idx, 'longitude'] = 170.0
    out.loc[idx, 'location'] = 'ZNZ'
    out.loc[idx, 'ip'] = '203.0.113.9'
    out.loc[idx, 'imei'] = ['IMEI-X' + str(i) for i in range(k)]
    return out, list(out.loc[idx, 'transaction_id'])


def test_contamination_is_configurable():
    assert parse_contamination('0.12') == 0.12
    assert parse_contamination('auto') == 'auto'


def test_normal_transactions_have_bounded_scores():
    raw = _normal_transactions(60, seed=3)
    art = train_anomaly(raw, contamination=0.05, random_state=42, n_estimators=80)
    scored = score_feature_frame(compute_features(raw), art)
    assert art.meta['anomaly_is_not_fraud'] is True
    assert art.meta['algorithm'] == 'IsolationForest'
    assert (scored['anomaly_score'].between(0.0, 1.0)).all()
    assert set(scored['anomaly_rank']) <= set(range(1, len(scored) + 1))
    assert scored['anomaly_rank'].min() == 1
    # Tight cluster of normal traffic should not all be flagged as extreme
    assert float(scored['anomaly_score'].median()) < 0.9


def test_injected_anomalies_rank_highest():
    base = _normal_transactions(90, seed=9)
    raw, injected = _inject_anomalies(base, k=4)
    art = train_anomaly(raw, contamination=0.05, random_state=42, n_estimators=120)
    scored = score_feature_frame(compute_features(raw), art)
    top = set(scored.nsmallest(4, 'anomaly_rank')['transaction_id'])
    assert len(set(injected) & top) >= 3
    inj_scores = scored[scored.transaction_id.isin(injected)]['anomaly_score']
    rest = scored[~scored.transaction_id.isin(injected)]['anomaly_score']
    assert float(inj_scores.min()) > float(rest.median())
    one = score_one(raw, injected[0], art)
    assert one['transaction_id'] == injected[0]
    assert set(one) == {'transaction_id', 'anomaly_score', 'anomaly_rank', 'model_version'}
    assert one['anomaly_rank'] <= 4


def test_missing_features_are_filled_not_dropped():
    raw = _normal_transactions(40)
    art = train_anomaly(raw, contamination=0.1, random_state=1, n_estimators=50)
    feats = compute_features(raw)
    dropped = feats.drop(columns=art.feature_names[:5])
    scored = score_feature_frame(dropped, art)
    assert len(scored) == len(feats)
    assert scored['anomaly_score'].notna().all()
    assert scored['anomaly_rank'].notna().all()


def test_model_loading_is_deterministic():
    raw = _normal_transactions(50)
    art = train_anomaly(raw, contamination=0.08, random_state=42, n_estimators=60)
    loaded = load_artifact('CURRENT')
    assert loaded.meta['model_version'] == art.meta['model_version']
    a = score_feature_frame(compute_features(raw), art)
    b = score_feature_frame(compute_features(raw), loaded)
    pd.testing.assert_series_equal(a['anomaly_score'], b['anomaly_score'])
    pd.testing.assert_series_equal(a['anomaly_rank'], b['anomaly_rank'])
    again = train_anomaly(raw, contamination=0.08, random_state=42, n_estimators=60)
    c = score_feature_frame(compute_features(raw), again)
    pd.testing.assert_series_equal(a['anomaly_score'], c['anomaly_score'])


def test_anomaly_score_api(client):
    raw = _normal_transactions(40)
    train_anomaly(raw, contamination=0.1, random_state=2, n_estimators=40)
    gen = client.post('/api/v1/demo/generate?n=40')
    assert gen.status_code == 200
    tid = gen.json()['sample_ids'][0]
    r = client.post('/api/v1/anomaly/score', json={'transaction_id': tid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['transaction_id'] == tid
    assert 0.0 <= body['anomaly_score'] <= 1.0
    assert body['anomaly_rank'] >= 1
    assert body['model_version'].startswith('ANOM-')
    ranked = client.get('/api/v1/anomaly/rank?limit=5')
    assert ranked.status_code == 200
    assert len(ranked.json()) == 5
