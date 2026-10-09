"""Train the unknown-anomaly IsolationForest.

python -m app.ml.anomaly --transactions 3000 --seed 42 --contamination 0.05
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.data.generator import build_dataset
from app.ml.anomaly.model import train_anomaly


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='Train IsolationForest unknown-anomaly model (not a fraud classifier)')
    p.add_argument('--transactions', type=int, default=3000)
    p.add_argument('--seed', type=int, default=int(os.environ.get('ANOMALY_RANDOM_STATE', 42)))
    p.add_argument('--contamination', default=os.environ.get('ANOMALY_CONTAMINATION', '0.05'))
    p.add_argument('--n-estimators', type=int, default=200)
    p.add_argument('--input', type=Path, default=None)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.input:
        import pandas as pd
        raw = pd.read_csv(args.input)
    else:
        raw = build_dataset(args.transactions, seed=args.seed, persist=False).processed
    art = train_anomaly(
        raw,
        contamination=args.contamination,
        random_state=args.seed,
        n_estimators=args.n_estimators,
    )
    print(json.dumps({
        'model_version': art.meta['model_version'],
        'task': art.meta['task'],
        'anomaly_is_not_fraud': art.meta['anomaly_is_not_fraud'],
        'contamination': art.meta['contamination'],
        'metrics': art.meta['metrics'],
    }, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
