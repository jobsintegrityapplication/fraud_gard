import numpy as np
import pandas as pd
import pytest

from app.discovery.lifecycle import InvalidPatternTransition, apply_transition
from app.discovery.pipeline import cluster_normalized, discover_patterns, normalize_features


def _blob_ids(n, prefix):
    return [f'{prefix}-{i:03d}' for i in range(n)]


def test_noise_points_are_not_patterns():
    rng = np.random.RandomState(0)
    blob_a = rng.normal(0.0, 0.04, size=(40, 2))
    blob_b = rng.normal(6.0, 0.04, size=(40, 2))
    noise = np.array([
        [25.0, 0.0], [0.0, 25.0], [-25.0, 0.0], [0.0, -25.0],
        [25.0, 25.0], [-25.0, 25.0], [25.0, -25.0], [-25.0, -25.0],
        [18.0, -18.0], [-18.0, 18.0],
    ])
    X = np.vstack([blob_a, blob_b, noise])
    scaled, _ = normalize_features(X)
    ids = _blob_ids(40, 'A') + _blob_ids(40, 'B') + _blob_ids(10, 'N')
    result = cluster_normalized(scaled, ids, min_cluster_size=8, min_samples=5, random_state=0)
    assert result.n_noise >= 8
    assigned = {tid for c in result.candidates for tid in c.sample_transaction_ids}
    assert all(not tid.startswith('N-') for tid in assigned)
    assert all(c.state == 'DISCOVERED' for c in result.candidates)
    assert result.auto_classified_as_fraud is False


def test_small_clusters_are_rejected():
    rng = np.random.RandomState(1)
    big = rng.normal(0.0, 0.03, size=(40, 2))
    tiny = rng.normal(12.0, 0.02, size=(3, 2))
    X = np.vstack([big, tiny])
    scaled, _ = normalize_features(X)
    ids = _blob_ids(40, 'BIG') + _blob_ids(3, 'TINY')
    result = cluster_normalized(scaled, ids, min_cluster_size=10, min_samples=5, random_state=1)
    tiny_ids = set(_blob_ids(3, 'TINY'))
    for cand in result.candidates:
        assert cand.cluster_size >= 10
        assert tiny_ids.isdisjoint(cand.sample_transaction_ids)
    assert result.n_noise >= 3 or result.n_discarded_small >= 1


def test_duplicate_records_are_removed():
    n = 40
    base = pd.DataFrame({
        'transaction_id': [f'TXN-{i:04d}' for i in range(n)],
        'ts': pd.date_range('2026-04-01', periods=n, freq='h', tz='UTC'),
        'account_id': [f'ACC-{i % 6}' for i in range(n)],
        'customer_id': [f'CUS-{i % 6}' for i in range(n)],
        'imei': [f'IMEI-{i % 5}' for i in range(n)],
        'sim_id': [f'SIM-{i % 5}' for i in range(n)],
        'ip': [f'10.1.0.{i % 4}' for i in range(n)],
        'merchant_id': [f'M-{i % 3}' for i in range(n)],
        'location': 'DEL',
        'latitude': 28.6,
        'longitude': 77.2,
        'amount': 200.0,
        'channel': 'APP',
    })
    doubled = pd.concat([base, base], ignore_index=True)
    result = discover_patterns(doubled, min_input_rows=10, persist=False, random_state=42)
    assert result.n_duplicates_removed == n
    for cand in result.candidates:
        assert len(cand.sample_transaction_ids) == len(set(cand.sample_transaction_ids))


def test_insufficient_samples_yield_no_candidates():
    df = pd.DataFrame({
        'transaction_id': ['T1', 'T2', 'T3'],
        'ts': pd.date_range('2026-04-01', periods=3, freq='h', tz='UTC'),
        'account_id': ['A', 'A', 'A'],
        'customer_id': ['C', 'C', 'C'],
        'imei': ['I', 'I', 'I'],
        'sim_id': ['S', 'S', 'S'],
        'ip': ['1.1.1.1'] * 3,
        'merchant_id': ['M', 'M', 'M'],
        'location': 'DEL',
        'amount': [10.0, 11.0, 12.0],
        'channel': 'APP',
    })
    result = discover_patterns(df, min_input_rows=20, persist=False)
    assert result.candidates == []
    assert result.reason == 'insufficient_samples'


def test_discovery_is_reproducible():
    rng = np.random.RandomState(42)
    a = rng.normal(0.0, 0.04, size=(30, 4))
    b = rng.normal(6.0, 0.04, size=(30, 4))
    X = np.vstack([a, b])
    ids = _blob_ids(30, 'A') + _blob_ids(30, 'B')
    scaled, _ = normalize_features(X)
    r1 = cluster_normalized(scaled, ids, min_cluster_size=8, min_samples=4, random_state=42)
    r2 = cluster_normalized(scaled, ids, min_cluster_size=8, min_samples=4, random_state=42)
    assert [c.pattern_id for c in r1.candidates] == [c.pattern_id for c in r2.candidates]
    assert [c.cluster_id for c in r1.candidates] == [c.cluster_id for c in r2.candidates]
    assert [c.cluster_size for c in r1.candidates] == [c.cluster_size for c in r2.candidates]


def test_lifecycle_does_not_auto_confirm():
    with pytest.raises(InvalidPatternTransition):
        apply_transition('DISCOVERED', 'CONFIRMED')
    assert apply_transition('DISCOVERED', 'UNDER_REVIEW') == 'UNDER_REVIEW'
    assert apply_transition('UNDER_REVIEW', 'CONFIRMED') == 'CONFIRMED'
    assert apply_transition('CONFIRMED', 'ACTIVE') == 'ACTIVE'
    assert apply_transition('ACTIVE', 'MONITORED') == 'MONITORED'
    assert apply_transition('MONITORED', 'DEPRECATED') == 'DEPRECATED'


def test_discovery_api_never_flags_fraud(client):
    gen = client.post('/api/v1/demo/generate?n=80')
    assert gen.status_code == 200
    r = client.post('/api/v1/discovery/patterns', json={'min_input_rows': 20, 'random_state': 42})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['auto_classified_as_fraud'] is False
    assert body['anomaly_is_not_fraud'] is True
    for cand in body['candidates']:
        assert cand['state'] == 'DISCOVERED'
        for key in (
            'pattern_id', 'cluster_id', 'feature_signature', 'sample_transaction_ids',
            'entity_summary', 'cluster_size', 'discovery_timestamp', 'discovery_method', 'state',
        ):
            assert key in cand
