from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from ..db import TXN_COLUMNS, conn
from .quality import evaluate
from .readers import detect_format, load_table, write_original
from .schema import MAX_BYTES, STATUSES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_job(upload_id: str, meta: dict):
    c = conn()
    c.execute(
        '''INSERT INTO ingestions(
            upload_id, filename, content_type, file_format, byte_size, checksum_sha256, stored_path,
            status, row_count, valid_rows, invalid_rows, duplicate_rows, quality_json, error_report_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (
            upload_id, meta.get('filename'), meta.get('content_type'), meta.get('file_format'),
            meta.get('byte_size'), meta.get('checksum_sha256'), meta.get('stored_path'),
            'PENDING', 0, 0, 0, 0, None, None, _now(), _now(),
        ),
    )
    c.commit()
    c.close()


def _update_job(upload_id: str, **fields):
    fields['updated_at'] = _now()
    assignments = ', '.join(f'{k}=?' for k in fields)
    c = conn()
    c.execute(f'UPDATE ingestions SET {assignments} WHERE upload_id=?', [*fields.values(), upload_id])
    c.commit()
    c.close()


def _existing_ids() -> set[str]:
    c = conn()
    rows = c.execute('SELECT id FROM transactions').fetchall()
    c.close()
    return {r[0] for r in rows}


def _persist_accepted(rows: list[dict]):
    if not rows:
        return
    c = conn()
    for rec in rows:
        mapped = {col: rec.get(col) for col in TXN_COLUMNS}
        mapped['id'] = rec.get('transaction_id') or rec.get('id')
        mapped['amount'] = float(rec['amount'])
        ts = rec.get('ts')
        mapped['ts'] = ts.isoformat() if hasattr(ts, 'isoformat') else str(ts)
        if mapped.get('label') is None:
            mapped['label'] = None
        placeholders = ','.join(['?'] * len(TXN_COLUMNS))
        colsql = ','.join(TXN_COLUMNS)
        c.execute(
            f'INSERT OR IGNORE INTO transactions ({colsql}) VALUES ({placeholders})',
            [mapped.get(col) for col in TXN_COLUMNS],
        )
    c.commit()
    c.close()


def _quarantine(upload_id: str, items: list[dict]):
    c = conn()
    for item in items:
        c.execute(
            'INSERT INTO ingestion_quarantine(upload_id,row_number,payload_json,reasons_json,created_at) VALUES (?,?,?,?,?)',
            (upload_id, item['row_number'], json.dumps(item['payload'], default=str), json.dumps(item['reasons']), _now()),
        )
    c.commit()
    c.close()


def job_to_dict(row) -> dict:
    d = dict(row)
    for key in ('quality_json', 'error_report_json'):
        if d.get(key):
            d[key.replace('_json', '')] = json.loads(d[key])
        d.pop(key, None)
    d['file_metadata'] = {
        'filename': d.get('filename'),
        'content_type': d.get('content_type'),
        'file_format': d.get('file_format'),
        'byte_size': d.get('byte_size'),
        'checksum_sha256': d.get('checksum_sha256'),
        'stored_path': d.get('stored_path'),
    }
    return d


def get_ingestion(upload_id: str) -> dict | None:
    c = conn()
    row = c.execute('SELECT * FROM ingestions WHERE upload_id=?', (upload_id,)).fetchone()
    c.close()
    return job_to_dict(row) if row else None


def list_quarantine(upload_id: str) -> list[dict]:
    c = conn()
    rows = c.execute(
        'SELECT row_number, payload_json, reasons_json FROM ingestion_quarantine WHERE upload_id=? ORDER BY row_number',
        (upload_id,),
    ).fetchall()
    c.close()
    out = []
    for r in rows:
        out.append({
            'row_number': r['row_number'],
            'payload': json.loads(r['payload_json']),
            'reasons': json.loads(r['reasons_json']),
        })
    return out


def ingest_bytes(payload: bytes, filename: str, content_type: str | None, upload_id: str) -> dict:
    if len(payload) > MAX_BYTES:
        raise ValueError(f'file exceeds {MAX_BYTES} bytes')
    if not payload:
        raise ValueError('empty file')
    config.ensure_dirs()
    checksum = hashlib.sha256(payload).hexdigest()
    try:
        file_format = detect_format(filename, content_type)
    except ValueError as exc:
        _insert_job(upload_id, {
            'filename': filename, 'content_type': content_type, 'file_format': None,
            'byte_size': len(payload), 'checksum_sha256': checksum, 'stored_path': None,
        })
        _update_job(upload_id, status='FAILED', error_report_json=json.dumps({'errors': [str(exc)]}))
        raise

    dest = Path(config.INGEST_DIR) / upload_id / f'original.{file_format}'
    write_original(dest, payload)
    meta = {
        'filename': filename,
        'content_type': content_type,
        'file_format': file_format,
        'byte_size': len(payload),
        'checksum_sha256': checksum,
        'stored_path': str(dest),
    }
    _insert_job(upload_id, meta)

    try:
        _update_job(upload_id, status='VALIDATING')
        df = load_table(payload, file_format)
        _update_job(upload_id, status='PROCESSING')
        evaluated = evaluate(df, existing_ids=_existing_ids())
        quality = evaluated['quality']
        _quarantine(upload_id, evaluated['quarantine'])
        _persist_accepted(evaluated['accepted'])
        error_report = {
            'quarantined': len(evaluated['quarantine']),
            'schema_errors': quality['schema_errors'],
            'range_errors': quality['range_errors'],
            'note': 'Invalid and duplicate rows were quarantined; they were not discarded.',
        }
        _update_job(
            upload_id,
            status='COMPLETED',
            row_count=quality['row_count'],
            valid_rows=quality['valid_rows'],
            invalid_rows=quality['invalid_rows'],
            duplicate_rows=quality['duplicate_rows'],
            quality_json=json.dumps(quality),
            error_report_json=json.dumps(error_report),
        )
    except Exception as exc:
        _update_job(upload_id, status='FAILED', error_report_json=json.dumps({'errors': [str(exc)]}))
        raise

    result = get_ingestion(upload_id)
    result['quality'] = quality
    result['error_report'] = error_report
    result['status_history'] = list(STATUSES)
    return result
