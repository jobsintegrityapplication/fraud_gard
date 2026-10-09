from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..discovery.lifecycle import InvalidPatternTransition
from ..security.auth import Principal, require_roles
from .service import (
    activate_pattern,
    deprecate_pattern,
    get_pattern,
    list_patterns,
    review_pattern,
)

router = APIRouter(prefix='/api/v1', tags=['pattern-registry'])


class ReviewBody(BaseModel):
    notes: str = ''


@router.get('/patterns')
def registry_list(_principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST', 'ML_ENGINEER', 'AUDITOR'))):
    return list_patterns()


@router.get('/patterns/{pattern_id}')
def registry_get(pattern_id: str, _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST', 'ML_ENGINEER', 'AUDITOR'))):
    row = get_pattern(pattern_id)
    if row is None:
        raise HTTPException(404, 'pattern not found')
    return row


@router.post('/patterns/{pattern_id}/review')
def registry_review(pattern_id: str, body: ReviewBody | None = None, principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST'))):
    body = body or ReviewBody()
    current = get_pattern(pattern_id)
    if current is None:
        raise HTTPException(404, 'pattern not found')
    if current.get('state') == 'UNDER_REVIEW' and 'SENIOR_ANALYST' not in principal.roles and 'ADMIN' not in principal.roles:
        raise HTTPException(403, 'confirming a pattern requires a senior analyst')
    try:
        return review_pattern(pattern_id, actor=principal.actor_id, notes=body.notes)
    except KeyError as exc:
        raise HTTPException(404, 'pattern not found') from exc
    except InvalidPatternTransition as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/patterns/{pattern_id}/activate')
def registry_activate(
    pattern_id: str,
    principal: Principal = Depends(require_roles('SENIOR_ANALYST')),
):
    try:
        return activate_pattern(pattern_id, actor=principal.actor_id)
    except KeyError as exc:
        raise HTTPException(404, 'pattern not found') from exc
    except InvalidPatternTransition as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/patterns/{pattern_id}/deprecate')
def registry_deprecate(pattern_id: str, principal: Principal = Depends(require_roles('SENIOR_ANALYST'))):
    try:
        return deprecate_pattern(pattern_id, actor=principal.actor_id)
    except KeyError as exc:
        raise HTTPException(404, 'pattern not found') from exc
    except InvalidPatternTransition as exc:
        raise HTTPException(409, str(exc)) from exc
