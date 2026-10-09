"""Evaluate a stored supervised model.

python evaluate.py --model-version CURRENT
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

from app.data.generator import build_dataset
from app.ml.supervised.metrics import classification_metrics, segment_metrics
from app.ml.supervised.model import SEGMENT_COLS, _prepare_frame, load_artifact
from app.ml.supervised.split import temporal_split


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='Evaluate supervised fraud model on a temporal holdout')
    p.add_argument('--model-version', default='CURRENT')
    p.add_argument('--input', type=Path, default=None)
    p.add_argument('--transactions', type=int, default=0, help='Generate N rows if --input is omitted')
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--threshold', type=float, default=None)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    art = load_artifact(args.model_version)
    threshold = args.threshold if args.threshold is not None else float(art.meta.get('decision_threshold', 0.5))
    if args.input:
        raw = pd.read_csv(args.input)
    elif args.transactions:
        raw = build_dataset(args.transactions, seed=args.seed, persist=False).processed
    else:
        print(json.dumps({'model_version': art.meta['model_version'], 'stored_metrics': art.meta['metrics'],
                          'segment_metrics': art.meta.get('segment_metrics'), 'promotion': art.meta.get('promotion')}, indent=2, default=str))
        return 0
    frame = _prepare_frame(raw)
    test = temporal_split(frame).test
    proba = art.predict_proba(test)
    metrics = classification_metrics(test['label'], proba, threshold)
    scored = test.copy()
    scored['proba'] = proba
    segments = segment_metrics(scored, 'label', 'proba', [c for c in SEGMENT_COLS if c in scored.columns], threshold)
    print(json.dumps({
        'model_version': art.meta['model_version'],
        'metrics': metrics,
        'segment_metrics': segments,
        'promotion_recorded': art.meta.get('promotion'),
    }, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
