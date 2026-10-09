"""Train the supervised XGBoost fraud model.

python train.py --transactions 5000 --seed 42
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.data.generator import build_dataset
from app.ml.supervised.metrics import PromotionThresholds
from app.ml.supervised.model import train_supervised


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='Train supervised XGBoost handset fraud model')
    p.add_argument('--transactions', type=int, default=3000)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--input', type=Path, default=None, help='Optional labeled CSV with transaction columns + label')
    p.add_argument('--precision-threshold', type=float, default=float(os.environ.get('FRAUD_PROMOTE_PRECISION', 0.90)))
    p.add_argument('--recall-threshold', type=float, default=float(os.environ.get('FRAUD_PROMOTE_RECALL', 0.90)))
    p.add_argument('--n-estimators', type=int, default=120)
    p.add_argument('--max-depth', type=int, default=4)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.input:
        import pandas as pd
        raw = pd.read_csv(args.input)
    else:
        raw = build_dataset(args.transactions, seed=args.seed, persist=False).processed
    art = train_supervised(
        raw,
        thresholds=PromotionThresholds(precision=args.precision_threshold, recall=args.recall_threshold),
        n_estimators=args.n_estimators,
        max_depth=args.max_depth,
        random_state=args.seed,
    )
    print(json.dumps({
        'model_version': art.meta['model_version'],
        'metrics': art.meta['metrics'],
        'promotion': art.meta['promotion'],
        'calibrated': art.meta['calibrated'],
        'split_sizes': art.meta['split_sizes'],
    }, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
