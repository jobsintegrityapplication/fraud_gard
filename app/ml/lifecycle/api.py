from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .deploy import (
    DEPLOYMENT_STATES,
    clear_canary,
    clear_shadow,
    deploy_canary,
    deploy_production,
    deploy_shadow,
    deployment_status,
    get_deployment,
    rollback_to_last_known_good,
)
from .gates import PromotionGates, evaluate_promotion_gates
from .registry import STATUSES, get_champion, get_model, list_models
from ...security.audit import record_event
from ...security.auth import Principal, require_roles

router = APIRouter(prefix='/api/v1/models', tags=['models'])


class CanaryRequest(BaseModel):
    model_id: str
    percent: float | None = Field(default=None, ge=0, le=100)


class DeployRequest(BaseModel):
    model_id: str


@router.get('')
def api_list_models(status: str | None = None, _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST', 'ML_ENGINEER', 'AUDITOR'))):
    if status and status not in STATUSES and status not in DEPLOYMENT_STATES:
        raise HTTPException(400, f'invalid status; expected one of {STATUSES}')
    return {'statuses': list(STATUSES), 'deployment_states': list(DEPLOYMENT_STATES), 'models': list_models(status=status)}


@router.get('/champion')
def api_champion(_principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST', 'ML_ENGINEER', 'AUDITOR'))):
    champ = get_champion()
    if champ is None:
        raise HTTPException(404, 'no production champion')
    return champ


@router.get('/deployment')
def api_deployment_status(_principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST', 'ML_ENGINEER', 'AUDITOR'))):
    return deployment_status()


@router.post('/deployment/shadow')
def api_deploy_shadow(req: DeployRequest, _principal: Principal = Depends(require_roles('ML_ENGINEER'))):
    try:
        return deploy_shadow(req.model_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/deployment/canary')
def api_deploy_canary(req: CanaryRequest, _principal: Principal = Depends(require_roles('ML_ENGINEER'))):
    try:
        return deploy_canary(req.model_id, percent=req.percent)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/deployment/production')
def api_deploy_production(req: DeployRequest, principal: Principal = Depends(require_roles('ML_ENGINEER'))):
    try:
        result = deploy_production(req.model_id, as_champion=True)
        record_event(principal.actor_id, 'MODEL_PROMOTION', 'Model', req.model_id, {'version': result.get('model', {}).get('version') if isinstance(result.get('model'), dict) else None})
        return result
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/deployment/rollback')
def api_rollback(principal: Principal = Depends(require_roles('ML_ENGINEER'))):
    try:
        result = rollback_to_last_known_good()
        record_event(principal.actor_id, 'MODEL_ROLLBACK', 'Model', result.get('rolled_back_from'), {'restored_model_id': result.get('production_model_id')})
        return result
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete('/deployment/shadow')
def api_clear_shadow(_principal: Principal = Depends(require_roles('ML_ENGINEER'))):
    return clear_shadow()


@router.delete('/deployment/canary')
def api_clear_canary(_principal: Principal = Depends(require_roles('ML_ENGINEER'))):
    return clear_canary()


@router.get('/{model_id}')
def api_get_model(model_id: str, _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST', 'ML_ENGINEER', 'AUDITOR'))):
    row = get_model(model_id)
    if row is None:
        raise HTTPException(404, 'model not found')
    return row


@router.post('/gates/evaluate')
def api_evaluate_gates(payload: dict, _principal: Principal = Depends(require_roles('DATA_SCIENTIST', 'ML_ENGINEER'))):
    metrics = payload.get('metrics') or {}
    decision = evaluate_promotion_gates(
        metrics=metrics,
        split_metrics=payload.get('split_metrics'),
        segment_metrics=payload.get('segment_metrics'),
        split_sizes=payload.get('split_sizes'),
        record=payload.get('record'),
        drift_report=payload.get('drift_report'),
        data_quality=payload.get('data_quality'),
        gates=PromotionGates(**(payload.get('gates') or {})),
    )
    return decision
