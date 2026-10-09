from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.metrics import (
    average_precision_score, confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score,
)

import pandas as pd


@dataclass(frozen=True)
class PromotionThresholds:
    precision: float = 0.90
    recall: float = 0.90


def classification_metrics(y_true, proba, threshold: float = 0.5) -> dict:
    y = np.asarray(y_true).astype(int)
    p = np.asarray(proba, dtype=float)
    pred = (p >= threshold).astype(int)
    cm = confusion_matrix(y, pred, labels=[0, 1])
    tn, fp, fn, tp = cm.ravel()
    pos = tp + fn
    neg = tn + fp
    out = {
        'precision': float(precision_score(y, pred, zero_division=0)),
        'recall': float(recall_score(y, pred, zero_division=0)),
        'f1': float(f1_score(y, pred, zero_division=0)),
        'pr_auc': float(average_precision_score(y, p)) if len(np.unique(y)) > 1 else 0.0,
        'roc_auc': float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else 0.0,
        'false_positive_rate': float(fp / neg) if neg else 0.0,
        'false_negative_rate': float(fn / pos) if pos else 0.0,
        'confusion_matrix': {'tn': int(tn), 'fp': int(fp), 'fn': int(fn), 'tp': int(tp)},
        'threshold': float(threshold),
        'n': int(len(y)),
        'n_positive': int(y.sum()),
    }
    return out


def promotion_decision(metrics: dict, thresholds: PromotionThresholds) -> dict:
    ok = metrics['precision'] >= thresholds.precision and metrics['recall'] >= thresholds.recall
    return {
        'promoted': bool(ok),
        'thresholds': {'precision': thresholds.precision, 'recall': thresholds.recall},
        'observed': {'precision': metrics['precision'], 'recall': metrics['recall']},
        'reason': 'meets promotion thresholds' if ok else 'below promotion thresholds; artifact stored as challenger only',
    }


def segment_metrics(frame: pd.DataFrame, y_col: str, p_col: str, segment_cols: list[str], threshold: float = 0.5) -> dict:
    out = {}
    for col in segment_cols:
        if col not in frame.columns:
            continue
        groups = {}
        for key, part in frame.groupby(frame[col].fillna('UNKNOWN').astype(str), dropna=False):
            if len(part) < 3:
                continue
            groups[str(key)] = classification_metrics(part[y_col], part[p_col], threshold)
            groups[str(key)]['n'] = int(len(part))
        out[col] = groups
    return out
