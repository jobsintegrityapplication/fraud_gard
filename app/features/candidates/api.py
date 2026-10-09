from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import pandas as pd

from ...db import conn
from .lifecycle import STATES, InvalidFeatureTransition
from .service import (
    CandidateFeatureError,
    activate_candidate,
    propose_candidates,
    refuse_llm_production_code_write,
    reject_candidate,
    run_validation,
    suggest_description,
    transition,
)
from .store import get_candidate, list_candidates
from ...security.auth import Principal, require_roles

router = APIRouter(prefix='/api/v1/features/candidates', tags=['candidate-features'])


class SuggestDescriptionRequest(BaseModel):
    description: str = Field(min_length=1)
    source: str = 'llm'


class TransitionRequest(BaseModel):
    target: str
    actor: str = 'analyst'


class RejectRequest(BaseModel):
    reason: str = ''


def _history() -> pd.DataFrame:
    c = conn()
    rows = c.execute('SELECT * FROM transactions').fetchall()
    c.close()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    if 'id' in df.columns and 'transaction_id' not in df.columns:
        df['transaction_id'] = df['id']
    return df


@router.get('')
def api_list(status: str | None = None, _principal: Principal = Depends(require_roles('DATA_SCIENTIST', 'ML_ENGINEER', 'SENIOR_ANALYST', 'AUDITOR'))):
    if status and status.upper() not in STATES:
        raise HTTPException(400, f'invalid status; expected one of {STATES}')
    return {'states': list(STATES), 'candidates': list_candidates(status=status.upper() if status else None)}


@router.post('/propose')
def api_propose(payload: dict | None = None, _principal: Principal = Depends(require_roles('DATA_SCIENTIST', 'ML_ENGINEER'))):
    payload = payload or {}
    try:
        rows = propose_candidates(
            include_patterns=payload.get('include_patterns', True),
            include_entity=payload.get('include_entity', True),
            include_combinations=payload.get('include_combinations', True),
            include_temporal=payload.get('include_temporal', True),
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'proposed': len(rows), 'candidates': rows}


@router.get('/{candidate_id}')
def api_get(candidate_id: str, _principal: Principal = Depends(require_roles('DATA_SCIENTIST', 'ML_ENGINEER', 'SENIOR_ANALYST', 'AUDITOR'))):
    row = get_candidate(candidate_id)
    if row is None:
        raise HTTPException(404, 'candidate not found')
    return row


@router.post('/{candidate_id}/describe')
def api_describe(candidate_id: str, req: SuggestDescriptionRequest, _principal: Principal = Depends(require_roles('DATA_SCIENTIST', 'ML_ENGINEER'))):
    try:
        return suggest_description(candidate_id, req.description, source=req.source)
    except KeyError:
        raise HTTPException(404, 'candidate not found') from None
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except CandidateFeatureError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/{candidate_id}/validate')
def api_validate(candidate_id: str, _principal: Principal = Depends(require_roles('DATA_SCIENTIST', 'ML_ENGINEER'))):
    hist = _history()
    if hist.empty:
        raise HTTPException(400, 'no transactions loaded for validation')
    try:
        return run_validation(candidate_id, hist)
    except KeyError:
        raise HTTPException(404, 'candidate not found') from None
    except InvalidFeatureTransition as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/{candidate_id}/transition')
def api_transition(candidate_id: str, req: TransitionRequest, principal: Principal = Depends(require_roles('DATA_SCIENTIST', 'ML_ENGINEER'))):
    try:
        return transition(candidate_id, req.target, actor=principal.actor_id)
    except KeyError:
        raise HTTPException(404, 'candidate not found') from None
    except InvalidFeatureTransition as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/{candidate_id}/reject')
def api_reject(candidate_id: str, req: RejectRequest, _principal: Principal = Depends(require_roles('DATA_SCIENTIST', 'ML_ENGINEER'))):
    try:
        return reject_candidate(candidate_id, reason=req.reason)
    except KeyError:
        raise HTTPException(404, 'candidate not found') from None
    except InvalidFeatureTransition as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/{candidate_id}/activate')
def api_activate(candidate_id: str, source: str = 'reviewed_code', _principal: Principal = Depends(require_roles('ML_ENGINEER'))):
    try:
        return activate_candidate(candidate_id, source=source)
    except KeyError:
        raise HTTPException(404, 'candidate not found') from None
    except (InvalidFeatureTransition, PermissionError) as exc:
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post('/guards/refuse-llm-production-write')
def api_refuse_llm_write(_principal: Principal = Depends(require_roles('ML_ENGINEER'))):
    try:
        refuse_llm_production_code_write()
    except PermissionError as exc:
        return {'allowed': False, 'detail': str(exc)}
    return {'allowed': True}
