import json
from pathlib import Path

import pandas as pd

from app.data.catalog import FRAUD_PATTERNS, LEAKAGE_COLUMNS, SIZE_PRESETS
from app.data.generate import main as generate_main
from app.data.generator import build_dataset
from app.data.ids import luhn_check_digit
from app.ml.features import FEATURES, build_features


def test_reproducibility():
    a = build_dataset(180, seed=42)
    b = build_dataset(180, seed=42)
    c = build_dataset(180, seed=43)
    assert list(a.processed['transaction_id']) == list(b.processed['transaction_id'])
    assert list(a.processed['label']) == list(b.processed['label'])
    assert list(a.processed['amount']) == list(b.processed['amount'])
    assert list(a.processed['transaction_id']) != list(c.processed['transaction_id'])


def test_configurable_dataset_size():
    for n in (50, 120, 300):
        bundle = build_dataset(n, seed=7)
        assert len(bundle.processed) == n
        assert len(bundle.transactions) == n


def test_size_presets_are_declared():
    assert SIZE_PRESETS == (10_000, 50_000, 100_000, 1_000_000)


def test_entity_relationships():
    bundle = build_dataset(220, seed=11)
    tx = bundle.processed
    assert set(tx['customer_id']).issubset(set(bundle.customers['customer_id']))
    assert set(tx['account_id']).issubset(set(bundle.accounts['account_id']))
    assert set(tx['sim_id']).issubset(set(bundle.sims['sim_id']))
    assert set(tx['imei']).issubset(set(bundle.handsets['imei']))
    assert set(tx['merchant_id']).issubset(set(bundle.merchants['merchant_id']))
    assert set(tx['ip']).issubset(set(bundle.networks['ip']))
    acct = bundle.accounts.set_index('account_id')
    for row in tx.itertuples(index=False):
        assert acct.loc[row.account_id, 'customer_id'] == row.customer_id
    assert bundle.sims['msisdn'].str.startswith('91').all()
    for imei in bundle.handsets['imei']:
        assert len(str(imei)) == 15
        assert str(imei)[-1] == luhn_check_digit(str(imei)[:-1])


def test_fraud_injection_all_patterns():
    bundle = build_dataset(400, seed=21)
    codes = set(bundle.ground_truth['pattern_code'])
    expected = {code for _, code, _ in FRAUD_PATTERNS}
    assert expected <= codes
    assert len(bundle.fraud_patterns) == 10
    assert len(bundle.fraud_cases) >= 10
    assert bundle.processed['label'].sum() == len(bundle.ground_truth)
    assert 0 < float(bundle.processed['label'].mean()) < 0.5


def test_no_label_leakage():
    bundle = build_dataset(200, seed=5)
    processed_cols = set(bundle.processed.columns)
    assert processed_cols.isdisjoint(LEAKAGE_COLUMNS)
    assert 'fraud_reason' in bundle.ground_truth.columns
    assert 'fraud_reason' not in bundle.transactions.columns
    assert 'label' not in bundle.transactions.columns
    assert 'label' in bundle.processed.columns
    X = build_features(bundle.processed)
    assert list(X.columns) == FEATURES
    assert 'label' not in X.columns
    for col in LEAKAGE_COLUMNS:
        assert col not in X.columns
    # merchant_fraud_rate is a prior risk score, not mean(label) by merchant
    merged = bundle.processed.merge(bundle.merchants[['merchant_id', 'prior_risk']], on='merchant_id')
    assert (merged['merchant_fraud_rate'] - merged['prior_risk']).abs().max() < 1e-9


def test_cli_persist(tmp_path):
    rc = generate_main(['--transactions', '90', '--seed', '42', '--output-root', str(tmp_path)])
    assert rc == 0
    raw = tmp_path / 'raw'
    gen = tmp_path / 'generated'
    proc = tmp_path / 'processed'
    for name in ('customers.csv', 'accounts.csv', 'sims.csv', 'handsets.csv', 'merchants.csv', 'networks.csv', 'locations.csv'):
        assert (raw / name).exists()
    assert (gen / 'transactions.csv').exists()
    assert (gen / 'ground_truth.csv').exists()
    assert (gen / 'fraud_cases.csv').exists()
    assert (gen / 'fraud_patterns.csv').exists()
    proc_tx = pd.read_csv(proc / 'transactions.csv')
    assert len(proc_tx) == 90
    assert set(proc_tx.columns).isdisjoint(LEAKAGE_COLUMNS)
    policy = json.loads((proc / 'label_policy.json').read_text(encoding='utf-8'))
    assert 'label' in policy['target_columns']
    assert 'fraud_reason' in policy['leakage_columns_excluded']
