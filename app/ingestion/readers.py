from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pandas as pd

from .schema import COLUMN_ALIASES, SUPPORTED_FORMATS


def detect_format(filename: str | None, content_type: str | None) -> str:
    name = (filename or '').lower()
    ctype = (content_type or '').lower()
    if name.endswith('.csv') or 'csv' in ctype:
        return 'csv'
    if name.endswith('.parquet') or 'parquet' in ctype:
        return 'parquet'
    if name.endswith('.json') or 'json' in ctype:
        return 'json'
    raise ValueError('unsupported format; use csv, json, or parquet')


def load_table(payload: bytes, file_format: str) -> pd.DataFrame:
    if file_format not in SUPPORTED_FORMATS:
        raise ValueError(f'unsupported format: {file_format}')
    if file_format == 'csv':
        df = pd.read_csv(BytesIO(payload))
    elif file_format == 'parquet':
        df = pd.read_parquet(BytesIO(payload))
    else:
        raw = json.loads(payload.decode('utf-8'))
        if isinstance(raw, dict):
            for key in ('records', 'transactions', 'data', 'rows'):
                if key in raw and isinstance(raw[key], list):
                    raw = raw[key]
                    break
            else:
                raw = [raw]
        if not isinstance(raw, list):
            raise ValueError('JSON must be a list of objects or an object with records[]')
        df = pd.DataFrame(raw)
    df = df.rename(columns={c: COLUMN_ALIASES.get(str(c), str(c)) for c in df.columns})
    return df


def write_original(path: Path, payload: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
