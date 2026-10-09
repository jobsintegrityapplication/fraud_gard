from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from .jobs import schedule_learning_job
from .labels import list_queue
from .workflow import JOB_STAGES, get_job
from ..security.auth import Principal, require_roles

router = APIRouter(prefix='/api/v1/learning', tags=['learning'])


@router.get('/queue')
def api_queue(status: str | None = None, limit: int = Query(100, ge=1, le=1000), _principal: Principal = Depends(require_roles('SENIOR_ANALYST', 'DATA_SCIENTIST', 'ML_ENGINEER'))):
    return {'items': list_queue(status=status, limit=limit)}


@router.get('/jobs/{job_id}')
def api_job(job_id: str, _principal: Principal = Depends(require_roles('DATA_SCIENTIST', 'ML_ENGINEER'))):
    row = get_job(job_id)
    if row is None:
        raise HTTPException(404, 'job not found')
    return row


@router.post('/jobs')
def api_start_job(wait: bool = False, min_rows: int = Query(5, ge=1, le=10000), principal: Principal = Depends(require_roles('ML_ENGINEER'))):
    """Start the async learning workflow. Default is non-blocking."""
    return {
        **schedule_learning_job(wait=wait, min_rows=min_rows, actor_id=principal.actor_id),
        'stages': list(JOB_STAGES),
    }
