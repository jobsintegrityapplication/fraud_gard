import pandas as pd

FEATURES = [
    'amount', 'account_age_days', 'imei_account_count', 'imei_sim_count', 'ip_account_count',
    'merchant_fraud_rate', 'txn_velocity_1h', 'location_distance_km', 'device_change_rate',
    'sim_change_rate', 'hour_deviation',
]


def build_features(df: pd.DataFrame):
    x = df.copy()
    for c in FEATURES:
        if c not in x.columns:
            x[c] = 0
    return x[FEATURES].fillna(0).astype(float)
