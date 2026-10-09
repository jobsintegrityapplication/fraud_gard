from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from .schema import AMOUNT_MAX, IMEI_LEN, OPTIONAL_COLUMNS, REQUIRED_COLUMNS


def _is_null(value) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(value, str) and value.strip() == '':
        return True
    return False


def _imei_ok(value) -> bool:
    text = str(value).strip()
    return text.isdigit() and len(text) == IMEI_LEN


def _amount_ok(value) -> tuple[bool, str | None]:
    try:
        amt = float(value)
    except (TypeError, ValueError):
        return False, 'amount is not numeric'
    if amt < 0:
        return False, 'amount is negative'
    if amt > AMOUNT_MAX:
        return False, f'amount exceeds {AMOUNT_MAX}'
    return True, None


def _ts_ok(value) -> bool:
    if _is_null(value):
        return False
    try:
        pd.to_datetime(value, utc=True)
        return True
    except (TypeError, ValueError):
        return False


def quality_score(row_count: int, valid_rows: int, duplicate_rows: int, schema_n: int, range_n: int) -> float:
    if row_count <= 0:
        return 0.0
    completeness = valid_rows / row_count
    dup_pen = min(0.4, duplicate_rows / row_count)
    err_pen = min(0.4, (schema_n + range_n) / max(1, row_count) * 0.25)
    return round(max(0.0, min(1.0, completeness - 0.5 * dup_pen - err_pen)), 4)


def evaluate(df: pd.DataFrame, existing_ids: set[str] | None = None) -> dict:
    existing_ids = existing_ids or set()
    schema_errors: list[dict] = []
    range_errors: list[dict] = []
    quarantine: list[dict] = []
    accepted: list[dict] = []
    seen: set[str] = set()
    duplicate_rows = 0
    file_missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if file_missing:
        schema_errors.append({'row_number': None, 'field': '*', 'error': f'missing required columns: {file_missing}'})

    null_summary = {c: int(df[c].isna().sum()) if c in df.columns else int(len(df)) for c in list(REQUIRED_COLUMNS) + list(OPTIONAL_COLUMNS) if c in df.columns or c in REQUIRED_COLUMNS}

    for i, rec in enumerate(df.to_dict('records'), start=1):
        reasons: list[str] = []
        schema_hit = False
        range_hit = False
        for col in REQUIRED_COLUMNS:
            if col not in rec or _is_null(rec.get(col)):
                msg = f'missing required field {col}'
                reasons.append(msg)
                schema_errors.append({'row_number': i, 'field': col, 'error': msg})
                schema_hit = True
        if not schema_hit:
            ok, amt_err = _amount_ok(rec.get('amount'))
            if not ok:
                reasons.append(amt_err)
                range_errors.append({'row_number': i, 'field': 'amount', 'error': amt_err})
                range_hit = True
            if not _imei_ok(rec.get('imei')):
                msg = f'imei must be {IMEI_LEN} digits'
                reasons.append(msg)
                range_errors.append({'row_number': i, 'field': 'imei', 'error': msg})
                range_hit = True
            if not _ts_ok(rec.get('ts')):
                msg = 'ts is not a valid timestamp'
                reasons.append(msg)
                range_errors.append({'row_number': i, 'field': 'ts', 'error': msg})
                range_hit = True
        tid = None if _is_null(rec.get('transaction_id')) else str(rec.get('transaction_id')).strip()
        if tid:
            if tid in seen or tid in existing_ids:
                duplicate_rows += 1
                reasons.append('duplicate transaction_id')
            else:
                seen.add(tid)
        payload = {k: (None if _is_null(v) else v) for k, v in rec.items()}
        if reasons:
            quarantine.append({'row_number': i, 'payload': payload, 'reasons': reasons, 'schema': schema_hit, 'range': range_hit})
        else:
            accepted.append(payload)

    row_count = int(len(df))
    result = {
        'row_count': row_count,
        'valid_rows': len(accepted),
        'invalid_rows': len(quarantine),
        'duplicate_rows': duplicate_rows,
        'null_summary': null_summary,
        'schema_errors': schema_errors,
        'range_errors': range_errors,
        'quality_score': quality_score(row_count, len(accepted), duplicate_rows, len(schema_errors), len(range_errors)),
        'evaluated_at': datetime.now(timezone.utc).isoformat(),
    }
    return {'quality': result, 'accepted': accepted, 'quarantine': quarantine}
