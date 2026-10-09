from __future__ import annotations

from fastapi import APIRouter, Depends

from ..db import conn
from ..ml.lifecycle.deploy import get_deployment
from ..ml.lifecycle.registry import get_champion, list_models
from ..security.auth import Principal, require_roles

router = APIRouter(prefix='/api/v1', tags=['dashboard'])


@router.get('/dashboard')
def dashboard_stats(_principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST', 'ML_ENGINEER', 'AUDITOR'))):
    """Aggregate live platform metrics from SQLite — no hardcoded values."""
    c = conn()
    total_alerts = c.execute('SELECT COUNT(*) FROM alerts').fetchone()[0]
    high_risk = c.execute(
        "SELECT COUNT(*) FROM alerts WHERE UPPER(risk) IN ('HIGH','CRITICAL')"
    ).fetchone()[0]
    confirmed = c.execute(
        "SELECT COUNT(*) FROM cases WHERE decision='CONFIRMED_FRAUD' OR status='CONFIRMED_FRAUD'"
    ).fetchone()[0]
    false_pos = c.execute(
        "SELECT COUNT(*) FROM cases WHERE decision='NOT_FRAUD' OR status='NOT_FRAUD'"
    ).fetchone()[0]
    anomalies = c.execute(
        'SELECT COUNT(*) FROM alerts WHERE anomaly_score IS NOT NULL AND anomaly_score >= 0.7'
    ).fetchone()[0]
    # Fallback if anomaly threshold yields nothing but alerts exist with scores
    if anomalies == 0:
        anomalies = c.execute(
            'SELECT COUNT(*) FROM alerts WHERE anomaly_score IS NOT NULL AND CAST(anomaly_score AS REAL) > 0'
        ).fetchone()[0]
    new_patterns = c.execute(
        "SELECT COUNT(*) FROM patterns WHERE UPPER(COALESCE(state,'')) IN ('DISCOVERED','UNDER_REVIEW')"
    ).fetchone()[0]
    # Investigation time proxy: avg minutes between case created_at and latest decision
    inv_row = c.execute(
        '''SELECT AVG(
            (julianday(d.timestamp) - julianday(c.created_at)) * 24 * 60
        ) FROM cases c
        JOIN analyst_decisions d ON d.case_id = c.id'''
    ).fetchone()
    investigation_minutes = float(inv_row[0]) if inv_row and inv_row[0] is not None else None
    c.close()

    champ = get_champion()
    deploy = get_deployment()
    challengers = [m for m in list_models() if m.get('status') in {'SHADOW', 'CANARY', 'VALIDATING', 'TRAINING', 'FAILED'}]
    canary = next((m for m in list_models() if m.get('status') == 'CANARY' or m.get('model_id') == deploy.canary_model_id), None)
    challenger = canary or (challengers[0] if challengers else None)

    return {
        'total_alerts': int(total_alerts),
        'high_risk_alerts': int(high_risk),
        'confirmed_fraud': int(confirmed),
        'false_positives': int(false_pos),
        'anomalies': int(anomalies),
        'new_patterns': int(new_patterns),
        'active_model': {
            'model_id': champ.get('model_id') if champ else None,
            'version': champ.get('version') if champ else None,
            'status': champ.get('status') if champ else None,
        },
        'challenger_model': {
            'model_id': challenger.get('model_id') if challenger else None,
            'version': challenger.get('version') if challenger else None,
            'status': challenger.get('status') if challenger else None,
        },
        'investigation_time_minutes': investigation_minutes,
        'deployment': deploy.to_dict(),
    }


@router.get('/audit')
def list_audit(limit: int = 200, _principal: Principal = Depends(require_roles('AUDITOR'))):
    limit = max(1, min(int(limit), 500))
    c = conn()
    rows = c.execute(
        'SELECT id, actor, action, entity_type, entity_id, detail, ts FROM audit ORDER BY id DESC LIMIT ?',
        (limit,),
    ).fetchall()
    c.close()
    return {'events': [dict(r) for r in rows]}
