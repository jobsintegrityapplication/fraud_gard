from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import pandas as pd

from .. import config
from ..db import conn
from ..features.engine import compute_features
from ..features.registry import production_registry
from ..features.windows import FEATURE_SET_VERSION, LEAKAGE_COLUMNS
from ..ml.supervised.metrics import PromotionThresholds
from ..ml.supervised.model import train_supervised
from .labels import claim_queued, list_queue, update_queue_status

JOB_STAGES = (
    'confirmed_label',
    'training_queue',
    'data_quality',
    'leakage_checks',
    'feature_validation',
    'challenger_training',
    'evaluation',
    'promotion_gates',
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_dir(job_id: str) -> Path:
    dest = Path(config.MODEL_DIR) / 'learning_jobs' / job_id
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def create_job_record(job_id: str) -> dict:
    payload = {
        'job_id': job_id,
        'status': 'QUEUED',
        'stages': {s: {'status': 'PENDING'} for s in JOB_STAGES},
        'created_at': _now(),
        'updated_at': _now(),
        'result': {},
        'error': None,
    }
    c = conn()
    c.execute(
        '''INSERT INTO learning_jobs(job_id, status, stages_json, result_json, error, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?)''',
        (job_id, 'QUEUED', json.dumps(payload['stages']), '{}', None, payload['created_at'], payload['updated_at']),
    )
    c.commit()
    c.close()
    (_job_dir(job_id) / 'job.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return payload


def _save_job(payload: dict):
    payload['updated_at'] = _now()
    c = conn()
    c.execute(
        'UPDATE learning_jobs SET status=?, stages_json=?, result_json=?, error=?, updated_at=? WHERE job_id=?',
        (
            payload['status'], json.dumps(payload['stages']), json.dumps(payload.get('result') or {}),
            payload.get('error'), payload['updated_at'], payload['job_id'],
        ),
    )
    c.commit()
    c.close()
    (_job_dir(payload['job_id']) / 'job.json').write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')


def _stage(payload: dict, name: str, status: str, detail: dict | None = None):
    payload['stages'][name] = {'status': status, 'detail': detail or {}, 'ts': _now()}
    _save_job(payload)


def _load_transaction(transaction_id: str) -> dict | None:
    c = conn()
    row = c.execute('SELECT * FROM transactions WHERE id = ?', (transaction_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def _build_labeled_frame(claimed: list[dict]) -> tuple[pd.DataFrame, list[str], list[str]]:
    records = []
    used = []
    rejected = []
    for item in claimed:
        tid = item.get('transaction_id')
        if not tid:
            update_queue_status(item['queue_id'], 'QUALITY_FAILED', 'missing transaction_id')
            rejected.append(item['queue_id'])
            continue
        tx = _load_transaction(str(tid))
        if tx is None:
            update_queue_status(item['queue_id'], 'QUALITY_FAILED', 'transaction not found')
            rejected.append(item['queue_id'])
            continue
        row = dict(tx)
        row['transaction_id'] = row.get('id') or tid
        row['label'] = int(item['label'])
        records.append(row)
        used.append(item['queue_id'])
    if not records:
        return pd.DataFrame(), used, rejected
    return pd.DataFrame(records), used, rejected


def _data_quality(frame: pd.DataFrame) -> dict:
    issues = []
    if frame.empty:
        issues.append('no labeled rows')
    if 'transaction_id' not in frame.columns and 'id' not in frame.columns:
        issues.append('missing transaction id column')
    if 'label' not in frame.columns:
        issues.append('missing label')
    else:
        bad = frame[~frame['label'].isin([0, 1])]
        if len(bad):
            issues.append('labels outside {0,1}')
        if int((frame['label'] == 1).sum()) < 1:
            issues.append('no positive labels in batch')
    dupes = int(frame.duplicated(subset=['transaction_id'] if 'transaction_id' in frame.columns else ['id']).sum()) if len(frame) else 0
    if dupes:
        issues.append(f'duplicate transactions: {dupes}')
    return {'ok': not issues, 'issues': issues, 'n_rows': int(len(frame)), 'n_pos': int((frame['label'] == 1).sum()) if 'label' in frame.columns and len(frame) else 0}


def _leakage_checks(frame: pd.DataFrame) -> dict:
    cols = set(frame.columns)
    leak = sorted(cols & LEAKAGE_COLUMNS - {'label'})  # label is the target, not a feature
    # Features must not include leakage when computed
    feats = compute_features(frame)
    feat_leak = sorted(set(feats.columns) & LEAKAGE_COLUMNS)
    ok = not feat_leak
    return {'ok': ok, 'raw_leakage_columns_present': leak, 'feature_leakage': feat_leak}


def _feature_validation(frame: pd.DataFrame) -> dict:
    registry = production_registry()
    names = registry.names()
    feats = compute_features(frame)
    missing = [n for n in names if n not in feats.columns]
    null_rates = {n: float(feats[n].isna().mean()) for n in names if n in feats.columns}
    high_null = [n for n, r in null_rates.items() if r > 0.5]
    ok = not missing and not high_null and len(feats) == len(frame)
    return {
        'ok': ok,
        'feature_set_version': FEATURE_SET_VERSION,
        'missing_features': missing,
        'high_null_features': high_null,
        'n_features': len(names),
    }


def run_learning_workflow(job_id: str | None = None, *, min_rows: int = 5, actor_id: str = 'system') -> dict:
    """Async pipeline body. Safe to run in a background thread; never called inline by case APIs."""
    job_id = job_id or ('LJ-' + uuid.uuid4().hex[:10].upper())
    payload = create_job_record(job_id)
    payload['status'] = 'RUNNING'
    _save_job(payload)
    try:
        _stage(payload, 'confirmed_label', 'OK', {'note': 'labels originate from analyst decisions only'})
        claimed = claim_queued()
        _stage(payload, 'training_queue', 'OK', {'claimed': len(claimed), 'queue_ids': [c['queue_id'] for c in claimed]})
        if not claimed:
            payload['status'] = 'COMPLETED'
            payload['result'] = {'trained': False, 'reason': 'empty queue'}
            _save_job(payload)
            return payload

        frame, used_ids, rejected_ids = _build_labeled_frame(claimed)
        quality = _data_quality(frame)
        if not quality['ok'] or len(frame) < min_rows:
            for qid in used_ids:
                update_queue_status(qid, 'QUALITY_FAILED', ';'.join(quality['issues']) or f'need>={min_rows} rows')
            _stage(payload, 'data_quality', 'FAILED', quality)
            payload['status'] = 'FAILED'
            payload['result'] = {'trained': False, 'quality': quality, 'rejected': rejected_ids + used_ids}
            _save_job(payload)
            return payload
        _stage(payload, 'data_quality', 'OK', quality)

        leak = _leakage_checks(frame)
        if not leak['ok']:
            for qid in used_ids:
                update_queue_status(qid, 'QUALITY_FAILED', 'feature leakage detected')
            _stage(payload, 'leakage_checks', 'FAILED', leak)
            payload['status'] = 'FAILED'
            payload['error'] = 'leakage'
            _save_job(payload)
            return payload
        _stage(payload, 'leakage_checks', 'OK', leak)

        feats = _feature_validation(frame)
        if not feats['ok']:
            for qid in used_ids:
                update_queue_status(qid, 'QUALITY_FAILED', 'feature validation failed')
            _stage(payload, 'feature_validation', 'FAILED', feats)
            payload['status'] = 'FAILED'
            _save_job(payload)
            return payload
        _stage(payload, 'feature_validation', 'OK', feats)

        # Use all transactions only as feature context. Training labels must come
        # exclusively from analyst-reviewed queue items; synthetic/source labels
        # on other transaction rows are not verified outcomes.
        c = conn()
        hist = c.execute('SELECT * FROM transactions').fetchall()
        c.close()
        hist_df = pd.DataFrame([dict(r) for r in hist]) if hist else pd.DataFrame()
        if not hist_df.empty:
            if 'transaction_id' not in hist_df.columns and 'id' in hist_df.columns:
                hist_df['transaction_id'] = hist_df['id']
            label_map = {str(r['transaction_id']): int(r['label']) for _, r in frame.iterrows()}
            hist_df = hist_df.copy()
            def _label(row):
                tid = str(row.get('transaction_id') or row.get('id') or '')
                return label_map.get(tid)
            hist_df['label'] = hist_df.apply(_label, axis=1)
            train_df = hist_df
        else:
            train_df = frame

        art = train_supervised(train_df, n_estimators=60, max_depth=3, random_state=42)
        _stage(payload, 'challenger_training', 'OK', {'model_version': art.meta['model_version']})
        metrics = art.meta.get('metrics', {}).get('test', {})
        _stage(payload, 'evaluation', 'OK', metrics)
        promo = art.meta.get('promotion', {})
        _stage(payload, 'promotion_gates', 'OK' if promo.get('promoted') else 'HELD', promo)
        if promo.get('champion_replaced'):
            from ..security.audit import record_event
            promoted_model = promo.get('model') or {}
            record_event(actor_id, 'MODEL_PROMOTION', 'Model', promoted_model.get('model_id'), {'version': promoted_model.get('version'), 'source': 'learning_job', 'job_id': job_id})
        for qid in used_ids:
            update_queue_status(qid, 'USED_IN_TRAINING', metadata={'model_version': art.meta['model_version']})
        payload['status'] = 'COMPLETED'
        payload['result'] = {
            'trained': True,
            'model_version': art.meta['model_version'],
            'promoted': bool(promo.get('promoted')),
            'champion_replaced': bool(promo.get('champion_replaced')),
            'promotion': promo,
            'used_queue_ids': used_ids,
            'rejected_queue_ids': rejected_ids,
        }
        _save_job(payload)
        return payload
    except Exception as exc:
        payload['status'] = 'FAILED'
        # Exception text and tracebacks can embed row values or credentials.
        payload['error'] = type(exc).__name__
        _save_job(payload)
        return payload


def get_job(job_id: str) -> dict | None:
    c = conn()
    row = c.execute('SELECT * FROM learning_jobs WHERE job_id = ?', (job_id,)).fetchone()
    c.close()
    if row is None:
        return None
    d = dict(row)
    d['stages'] = json.loads(d.pop('stages_json') or '{}')
    d['result'] = json.loads(d.pop('result_json') or '{}')
    return d
