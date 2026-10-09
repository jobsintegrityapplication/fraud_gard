from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.features.engine import compute_features
from app.features.registry import FeatureRegistry, production_registry
from app.features.specs import PRODUCTION_SPECS
from app.features.windows import FEATURE_SET_VERSION, FeatureSpec, FeatureWindows, LEAKAGE_COLUMNS


def _rows():
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    return [
        dict(transaction_id='T1', account_id='A1', customer_id='C1', sim_id='S1', imei='111111111111111',
             ip='1.1.1.1', merchant_id='M1', location='BLR', latitude=12.97, longitude=77.59,
             amount=10, ts=t0, label=0, channel='APP'),
        dict(transaction_id='T2', account_id='A2', customer_id='C2', sim_id='S2', imei='111111111111111',
             ip='1.1.1.1', merchant_id='M1', location='BLR', latitude=12.97, longitude=77.59,
             amount=12, ts=t0 + timedelta(minutes=10), label=1, channel='APP'),
        dict(transaction_id='T3', account_id='A1', customer_id='C1', sim_id='S1', imei='222222222222222',
             ip='8.8.8.8', merchant_id='M1', location='NYC', latitude=40.71, longitude=-74.0,
             amount=80, ts=t0 + timedelta(hours=2), label=0, channel='APP'),
        dict(transaction_id='T4', account_id='A1', customer_id='C1', sim_id='S3', imei='222222222222222',
             ip='8.8.8.8', merchant_id='M2', location='NYC', latitude=40.71, longitude=-74.0,
             amount=15, ts=t0 + timedelta(hours=2, minutes=20), label=0, channel='RETURN'),
        dict(transaction_id='T5', account_id='A3', customer_id='C3', sim_id='S4', imei='111111111111111',
             ip='9.9.9.9', merchant_id='M1', location='BLR', latitude=12.97, longitude=77.59,
             amount=11, ts=t0 + timedelta(hours=3), label=0, channel='APP'),
    ]


def test_llm_cannot_register_production_features():
    reg = FeatureRegistry()
    spec = FeatureSpec('llm_magic', 'TRANSACTION', 'x', 'nope', 'float', production=True)
    with pytest.raises(PermissionError):
        reg.register(spec, source='llm')
    with pytest.raises(PermissionError):
        reg.register(spec, source='prompt')


def test_production_registry_metadata_and_version():
    reg = production_registry()
    meta = reg.metadata()
    assert meta['feature_set_version'] == FEATURE_SET_VERSION
    names = {s.name for s in PRODUCTION_SPECS}
    assert names == set(reg.names())
    assert 'previous_fraud_links' in names
    assert 'community_size' in names
    by_name = {f['name']: f for f in meta['features']}
    assert by_name['historical_fraud_rate']['uses_label_history'] is True
    assert by_name['amount']['leakage'] is False


def test_no_target_leakage_in_output():
    feats = compute_features(pd.DataFrame(_rows()))
    assert set(feats.columns).isdisjoint(LEAKAGE_COLUMNS)
    assert 'label' not in feats.columns
    assert 'fraud_reason' not in feats.columns


def test_point_in_time_and_deterministic():
    df = pd.DataFrame(_rows())
    a = compute_features(df)
    b = compute_features(df.sample(frac=1.0, random_state=1).reset_index(drop=True))
    a = a.sort_values('transaction_id').reset_index(drop=True)
    b = b.sort_values('transaction_id').reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)
    t1 = a[a.transaction_id == 'T1'].iloc[0]
    t2 = a[a.transaction_id == 'T2'].iloc[0]
    t3 = a[a.transaction_id == 'T3'].iloc[0]
    assert t1['accounts_per_imei'] == 0
    assert t2['accounts_per_imei'] == 1
    assert t1['previous_fraud_links'] == 0
    assert t1['historical_fraud_rate'] == 0
    assert t1['merchant_fraud_rate'] == 0
    assert t2['previous_fraud_links'] == 0
    assert t3['previous_fraud_links'] == 0
    t5 = a[a.transaction_id == 'T5'].iloc[0]
    assert t5['previous_fraud_links'] == 1
    assert t5['accounts_per_imei'] == 2
    assert t3['accounts_per_imei'] == 0
    assert t3['device_switch_velocity'] > 0
    assert t3['distance_from_normal_location'] > 100
    assert t3['unusual_location_indicator'] == 1
    assert t3['connected_accounts'] >= 1
    assert t3['fraud_neighbor_count'] >= 1
    assert t3['community_size'] >= 2
    t4 = a[a.transaction_id == 'T4'].iloc[0]
    assert t4['sim_change_velocity'] > 0
    assert t4['return_rate'] == 0
    later = compute_features(pd.concat([df, df.iloc[[0]].assign(transaction_id='T9', ts=df['ts'].iloc[0] + pd.Timedelta(days=9), label=1)], ignore_index=True))
    t1_again = later[later.transaction_id == 'T1'].iloc[0]
    assert t1_again['previous_fraud_links'] == t1['previous_fraud_links']
    assert t1_again['historical_fraud_rate'] == t1['historical_fraud_rate']


def test_configurable_windows():
    df = pd.DataFrame(_rows())
    wide = FeatureWindows(purchases_short=pd.Timedelta(hours=3), velocity=pd.Timedelta(hours=3))
    default = compute_features(df)
    tuned = compute_features(df, windows=wide)
    t3d = default[default.transaction_id == 'T3'].iloc[0]
    t3t = tuned[tuned.transaction_id == 'T3'].iloc[0]
    assert t3t['purchases_last_1h'] >= t3d['purchases_last_1h']
    assert tuned.attrs['feature_set_version'] == FEATURE_SET_VERSION
    assert 'windows' in tuned.attrs
    assert len(tuned.columns) == 1 + len(PRODUCTION_SPECS)
