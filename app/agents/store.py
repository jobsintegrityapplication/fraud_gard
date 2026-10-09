from __future__ import annotations

import pandas as pd

from ..db import conn


def load_transactions_df() -> pd.DataFrame:
    c = conn()
    rows = c.execute('SELECT * FROM transactions').fetchall()
    c.close()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    if 'id' in df.columns and 'transaction_id' not in df.columns:
        df['transaction_id'] = df['id']
    return df


def transaction_row(transaction_id: str) -> dict | None:
    df = load_transactions_df()
    if df.empty:
        return None
    hit = df[df['transaction_id'].astype(str) == str(transaction_id)]
    if hit.empty and 'id' in df.columns:
        hit = df[df['id'].astype(str) == str(transaction_id)]
    if hit.empty:
        return None
    return hit.iloc[0].to_dict()
