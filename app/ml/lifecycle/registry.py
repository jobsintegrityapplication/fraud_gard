from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from ... import config
from ...db import conn

STATUSES = (
    'TRAINING',
    'VALIDATING',
    'FAILED',
    'SHADOW',
    'CANARY',
    'PRODUCTION',
    'ROLLED_BACK',
)

CHAMPION_STATUS = 'PRODUCTION'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _registry_dir() -> Path:
    dest = Path(config.MODEL_DIR) / 'lifecycle'
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def champion_pointer_path() -> Path:
    return _registry_dir() / 'CHAMPION.json'


def new_model_id() -> str:
    return 'MDL-' + uuid.uuid4().hex[:10].upper()


def serialize_record(row) -> dict:
    data = dict(row)
    data['metrics'] = json.loads(data.pop('metrics_json') or '{}')
    data['gate_results'] = json.loads(data.pop('gate_results_json') or '{}')
    return {
        'model_id': data['model_id'],
        'version': data['version'],
        'algorithm': data['algorithm'],
        'dataset_version': data.get('dataset_version'),
        'feature_version': data.get('feature_version'),
        'training_timestamp': data.get('training_timestamp'),
        'metrics': data['metrics'],
        'status': data['status'],
        'artifact_uri': data.get('artifact_uri'),
        'gate_results': data['gate_results'],
        'created_at': data.get('created_at'),
        'updated_at': data.get('updated_at'),
    }


def upsert_model_record(
    *,
    model_id: str,
    version: str,
    algorithm: str,
    dataset_version: str,
    feature_version: str,
    training_timestamp: str,
    metrics: dict,
    status: str,
    artifact_uri: str | None = None,
    gate_results: dict | None = None,
) -> dict:
    if status not in STATUSES:
        raise ValueError(f'invalid model status: {status}')
    now = _now()
    c = conn()
    existing = c.execute('SELECT model_id FROM model_registry WHERE model_id = ?', (model_id,)).fetchone()
    if existing:
        c.execute(
            '''UPDATE model_registry SET version=?, algorithm=?, dataset_version=?, feature_version=?,
               training_timestamp=?, metrics_json=?, status=?, artifact_uri=?, gate_results_json=?, updated_at=?
               WHERE model_id=?''',
            (
                version, algorithm, dataset_version, feature_version, training_timestamp,
                json.dumps(metrics or {}), status, artifact_uri, json.dumps(gate_results or {}), now, model_id,
            ),
        )
    else:
        c.execute(
            '''INSERT INTO model_registry(
                model_id, version, algorithm, dataset_version, feature_version, training_timestamp,
                metrics_json, status, artifact_uri, gate_results_json, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                model_id, version, algorithm, dataset_version, feature_version, training_timestamp,
                json.dumps(metrics or {}), status, artifact_uri, json.dumps(gate_results or {}), now, now,
            ),
        )
    c.commit()
    row = c.execute('SELECT * FROM model_registry WHERE model_id = ?', (model_id,)).fetchone()
    c.close()
    return serialize_record(row)


def get_model(model_id: str) -> dict | None:
    c = conn()
    row = c.execute('SELECT * FROM model_registry WHERE model_id = ?', (model_id,)).fetchone()
    c.close()
    return serialize_record(row) if row else None


def list_models(status: str | None = None) -> list[dict]:
    c = conn()
    if status:
        rows = c.execute('SELECT * FROM model_registry WHERE status = ? ORDER BY updated_at DESC', (status,)).fetchall()
    else:
        rows = c.execute('SELECT * FROM model_registry ORDER BY updated_at DESC').fetchall()
    c.close()
    return [serialize_record(r) for r in rows]


def get_champion() -> dict | None:
    pointer = champion_pointer_path()
    if pointer.exists():
        data = json.loads(pointer.read_text(encoding='utf-8'))
        mid = data.get('model_id')
        if mid:
            rec = get_model(mid)
            if rec and rec['status'] == CHAMPION_STATUS:
                return rec
    prod = list_models(status=CHAMPION_STATUS)
    return prod[0] if prod else None


def set_champion(model_id: str, *, replace_previous: bool = True) -> dict:
    challenger = get_model(model_id)
    if challenger is None:
        raise KeyError(model_id)
    previous = get_champion()
    if previous and previous['model_id'] != model_id and replace_previous:
        upsert_model_record(
            model_id=previous['model_id'],
            version=previous['version'],
            algorithm=previous['algorithm'],
            dataset_version=previous['dataset_version'] or '',
            feature_version=previous['feature_version'] or '',
            training_timestamp=previous['training_timestamp'] or '',
            metrics=previous['metrics'],
            status='ROLLED_BACK',
            artifact_uri=previous.get('artifact_uri'),
            gate_results=previous.get('gate_results'),
        )
    updated = upsert_model_record(
        model_id=challenger['model_id'],
        version=challenger['version'],
        algorithm=challenger['algorithm'],
        dataset_version=challenger['dataset_version'] or '',
        feature_version=challenger['feature_version'] or '',
        training_timestamp=challenger['training_timestamp'] or '',
        metrics=challenger['metrics'],
        status=CHAMPION_STATUS,
        artifact_uri=challenger.get('artifact_uri'),
        gate_results=challenger.get('gate_results'),
    )
    champion_pointer_path().write_text(
        json.dumps({'model_id': model_id, 'version': updated['version'], 'updated_at': _now()}, indent=2),
        encoding='utf-8',
    )
    try:
        from . import deploy as deploy_mod
        deploy_mod.sync_from_champion()
    except Exception:
        pass
    return updated


def keep_champion_active(reason: str) -> dict:
    """Explicit no-op path used when a challenger fails gates."""
    champ = get_champion()
    return {
        'champion_replaced': False,
        'champion': champ,
        'reason': reason,
    }
