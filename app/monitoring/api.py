from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..security.audit import record_event
from ..security.auth import Principal, require_roles
from .service import run_drift_monitoring

router = APIRouter(prefix='/api/v1/monitoring', tags=['monitoring'])


@router.post('/drift/run')
def run_drift(principal: Principal = Depends(require_roles('ADMIN', 'DATA_SCIENTIST', 'ML_ENGINEER'))):
    """Compare historical and recent transaction feature distributions.

    Drift may create a pending retraining recommendation. This endpoint never
    schedules or executes a training job.
    """
    try:
        result = run_drift_monitoring()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_event(
        principal.actor_id,
        'MODEL_DATA_DRIFT_RUN',
        'DriftMonitorRun',
        result['run_id'],
        {
            'overall_status': result['overall_status'],
            'feature_count': len(result['results']),
            'recommendation_id': (result.get('retraining_recommendation') or {}).get('recommendation_id'),
        },
    )
    return result
