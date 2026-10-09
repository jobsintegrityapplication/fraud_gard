"""CLI: python -m app.data.generate --transactions 10000 --seed 42"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .catalog import SIZE_PRESETS
from .generator import build_dataset


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='Generate a synthetic handset fraud dataset with ground-truth metadata.')
    p.add_argument('--transactions', type=int, required=True, help='Exact transaction count (presets: 10000, 50000, 100000, 1000000)')
    p.add_argument('--seed', type=int, default=42, help='RNG seed for reproducibility')
    p.add_argument('--output-root', type=Path, default=None, help='Root folder containing raw/, generated/, processed/')
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.transactions < 1:
        print('error: --transactions must be >= 1', file=sys.stderr)
        return 2
    bundle = build_dataset(args.transactions, seed=args.seed, persist=True, output_root=args.output_root)
    print(json.dumps({
        'ok': True,
        'transactions': bundle.manifest['n_transactions'],
        'seed': args.seed,
        'fraud_rate': bundle.manifest['fraud_rate'],
        'fraud_cases': bundle.manifest['n_fraud_cases'],
        'presets': list(SIZE_PRESETS),
        'output_root': str(args.output_root) if args.output_root else 'data/',
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
