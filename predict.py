"""Score a transaction with the current supervised model.

python predict.py --transaction-id T00000001 --input data/processed/transactions.csv
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from app.ml.supervised.model import load_artifact, score_one


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='Predict fraud risk for one transaction')
    p.add_argument('--transaction-id', required=True)
    p.add_argument('--input', type=Path, required=True, help='History CSV including the target row')
    p.add_argument('--model-version', default='CURRENT')
    p.add_argument('--top-k', type=int, default=5)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    raw = pd.read_csv(args.input)
    if 'transaction_id' not in raw.columns and 'id' in raw.columns:
        raw['transaction_id'] = raw['id']
    art = load_artifact(args.model_version)
    result = score_one(raw, args.transaction_id, art, top_k=args.top_k)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
