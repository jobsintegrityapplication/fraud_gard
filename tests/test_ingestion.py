import hashlib
import io
import json

import pandas as pd

from app.ingestion.quality import evaluate
from app.ingestion.schema import STATUSES


def _valid_row(tid='T1', amount=10, imei='123456789012345'):
    return {
        'transaction_id': tid,
        'customer_id': 'C1',
        'account_id': 'A1',
        'sim_id': 'S1',
        'imei': imei,
        'merchant_id': 'M1',
        'amount': amount,
        'ts': '2026-01-01T12:00:00Z',
        'ip': '10.0.0.1',
        'location': 'BLR',
    }


def test_quality_quarantines_invalid_and_duplicates():
    rows = [
        _valid_row('T1'),
        _valid_row('T1'),
        _valid_row('T2', amount=-5),
        {**_valid_row('T3'), 'imei': 'bad'},
        {k: v for k, v in _valid_row('T4').items() if k != 'customer_id'},
    ]
    out = evaluate(pd.DataFrame(rows))
    q = out['quality']
    assert q['row_count'] == 5
    assert q['valid_rows'] == 1
    assert q['invalid_rows'] == 4
    assert q['duplicate_rows'] == 1
    assert q['schema_errors']
    assert q['range_errors']
    assert 0 <= q['quality_score'] <= 1
    assert len(out['quarantine']) == 4
    assert {item['row_number'] for item in out['quarantine']} == {2, 3, 4, 5}


def test_upload_csv_json_parquet(client):
    df = pd.DataFrame([_valid_row('TA'), _valid_row('TB', amount=20)])
    csv_bytes = df.to_csv(index=False).encode()
    r = client.post('/api/v1/ingestion/upload', files={'file': ('txns.csv', csv_bytes, 'text/csv')})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['status'] == 'COMPLETED'
    assert body['upload_id'].startswith('ING-')
    assert body['file_metadata']['checksum_sha256'] == hashlib.sha256(csv_bytes).hexdigest()
    assert body['quality']['row_count'] == 2
    assert body['quality']['valid_rows'] == 2
    assert set(body['status_history']) == set(STATUSES)
    fetched = client.get(f"/api/v1/ingestion/{body['upload_id']}")
    assert fetched.status_code == 200
    assert fetched.json()['status'] == 'COMPLETED'

    json_bytes = json.dumps({'records': [_valid_row('TC')]}).encode()
    r = client.post('/api/v1/ingestion/upload', files={'file': ('txns.json', json_bytes, 'application/json')})
    assert r.status_code == 200
    assert r.json()['quality']['valid_rows'] == 1

    buf = io.BytesIO()
    pd.DataFrame([_valid_row('TD')]).to_parquet(buf, index=False)
    r = client.post('/api/v1/ingestion/upload', files={'file': ('txns.parquet', buf.getvalue(), 'application/octet-stream')})
    assert r.status_code == 200
    assert r.json()['file_metadata']['file_format'] == 'parquet'
    assert r.json()['quality']['valid_rows'] == 1


def test_invalid_rows_are_quarantined_not_discarded(client):
    df = pd.DataFrame([_valid_row('OK1'), _valid_row('BAD', amount=-9), _valid_row('OK1')])
    r = client.post('/api/v1/ingestion/upload', files={'file': ('mix.csv', df.to_csv(index=False).encode(), 'text/csv')})
    body = r.json()
    assert body['status'] == 'COMPLETED'
    assert body['quality']['valid_rows'] == 1
    assert body['quality']['invalid_rows'] == 2
    assert body['quality']['duplicate_rows'] == 1
    q = client.get(f"/api/v1/ingestion/{body['upload_id']}/quarantine")
    assert q.status_code == 200
    records = q.json()['records']
    assert len(records) == 2
    assert all(rec['reasons'] for rec in records)
    assert body['error_report']['note']


def test_unsupported_format_fails(client):
    r = client.post('/api/v1/ingestion/upload', files={'file': ('x.bin', b'not-a-table', 'application/octet-stream')})
    assert r.status_code == 200
    assert r.json()['status'] == 'FAILED'
    assert r.json()['error_report']['errors']
