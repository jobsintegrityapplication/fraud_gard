from ..data.generator import build_dataset
from ..ml.features import FEATURES

REQUIRED_COLUMNS = list(dict.fromkeys([
    'id', 'customer_id', 'account_id', 'sim_id', 'imei', 'ip', 'merchant_id', 'location',
    'amount', 'ts', 'label', *FEATURES,
]))


def generate(n=10000, seed=42):
    n = int(n)
    if n < 1:
        raise ValueError('n must be >= 1')
    bundle = build_dataset(n_transactions=n, seed=seed, persist=False)
    df = bundle.mvp_frame()
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise RuntimeError(f'generator missing columns: {missing}')
    return df[REQUIRED_COLUMNS]
