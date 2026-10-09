from __future__ import annotations

from datetime import datetime, timezone
import json

from ...db import conn
from .lifecycle import validate_state


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize(row) -> dict:
    data = dict(row)
    return {
        'candidate_id': data['candidate_id'],
        'name': data['name'],
        'description': data.get('description') or '',
        'description_source': data.get('description_source') or 'rules',
        'status': data['status'],
        'origin': data.get('origin'),
        'origin_ref': data.get('origin_ref'),
        'template': data.get('template'),
        'params': json.loads(data.get('params_json') or '{}'),
        'entity_level': data.get('entity_level'),
        'validation': json.loads(data.get('validation_json') or '{}'),
        'production': bool(int(data.get('production') or 0)),
        'llm_may_edit': json.loads(data.get('llm_may_edit_json') or '["description"]'),
        'created_at': data.get('created_at'),
        'updated_at': data.get('updated_at'),
    }


def upsert_candidate(record: dict) -> dict:
    status = validate_state(record.get('status') or 'PROPOSED')
    now = _now()
    cid = record['candidate_id']
    c = conn()
    existing = c.execute('SELECT candidate_id FROM candidate_features WHERE candidate_id = ?', (cid,)).fetchone()
    if existing:
        c.execute(
            '''UPDATE candidate_features SET name=?, description=?, description_source=?, status=?,
               origin=?, origin_ref=?, template=?, params_json=?, entity_level=?, validation_json=?,
               production=?, llm_may_edit_json=?, updated_at=? WHERE candidate_id=?''',
            (
                record['name'], record.get('description') or '', record.get('description_source') or 'rules',
                status, record.get('origin') or '', record.get('origin_ref') or '', record.get('template') or '',
                json.dumps(record.get('params') or {}), record.get('entity_level') or 'TRANSACTION',
                json.dumps(record.get('validation') or {}), 1 if record.get('production') else 0,
                json.dumps(record.get('llm_may_edit') or ['description']), now, cid,
            ),
        )
    else:
        c.execute(
            '''INSERT INTO candidate_features(
                candidate_id, name, description, description_source, status, origin, origin_ref,
                template, params_json, entity_level, validation_json, production, llm_may_edit_json,
                created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                cid, record['name'], record.get('description') or '', record.get('description_source') or 'rules',
                status, record.get('origin') or '', record.get('origin_ref') or '', record.get('template') or '',
                json.dumps(record.get('params') or {}), record.get('entity_level') or 'TRANSACTION',
                json.dumps(record.get('validation') or {}), 1 if record.get('production') else 0,
                json.dumps(record.get('llm_may_edit') or ['description']), now, now,
            ),
        )
    c.commit()
    row = c.execute('SELECT * FROM candidate_features WHERE candidate_id = ?', (cid,)).fetchone()
    c.close()
    return _serialize(row)


def get_candidate(candidate_id: str) -> dict | None:
    c = conn()
    row = c.execute('SELECT * FROM candidate_features WHERE candidate_id = ?', (candidate_id,)).fetchone()
    c.close()
    return _serialize(row) if row else None


def list_candidates(status: str | None = None) -> list[dict]:
    c = conn()
    if status:
        validate_state(status)
        rows = c.execute(
            'SELECT * FROM candidate_features WHERE status = ? ORDER BY updated_at DESC', (status,),
        ).fetchall()
    else:
        rows = c.execute('SELECT * FROM candidate_features ORDER BY updated_at DESC').fetchall()
    c.close()
    return [_serialize(r) for r in rows]
