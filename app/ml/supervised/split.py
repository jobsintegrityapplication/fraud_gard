from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TemporalSplit:
    train: pd.DataFrame
    valid: pd.DataFrame
    test: pd.DataFrame


def temporal_split(df: pd.DataFrame, ts_col: str = 'ts', train_frac: float = 0.70, valid_frac: float = 0.15) -> TemporalSplit:
    if ts_col not in df.columns:
        raise ValueError(f'{ts_col} is required for a temporal split')
    work = df.copy()
    work[ts_col] = pd.to_datetime(work[ts_col], utc=True)
    work = work.sort_values([ts_col, 'transaction_id'] if 'transaction_id' in work.columns else [ts_col], kind='mergesort')
    n = len(work)
    if n < 10:
        raise ValueError('need at least 10 rows for train/valid/test')
    i_train = max(1, int(n * train_frac))
    i_valid = max(i_train + 1, int(n * (train_frac + valid_frac)))
    i_valid = min(i_valid, n - 1)
    return TemporalSplit(
        train=work.iloc[:i_train].reset_index(drop=True),
        valid=work.iloc[i_train:i_valid].reset_index(drop=True),
        test=work.iloc[i_valid:].reset_index(drop=True),
    )
