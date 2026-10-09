from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import conn
from .lifecycle import InvalidPatternTransition, STATES
from .pipeline import discover_patterns
from .store import get_pattern, load_candidates, transition_pattern
from ..security.auth import Principal, require_principal, require_roles
from ..security.audit import record_event

router = APIRouter(prefix='/api/v1/discovery', tags=['discovery'])


class DiscoverRequest(BaseModel):
    contamination: float = 0.1
    top_fraction: float = 0.2
    min_cluster_size: int = 5
    min_input_rows: int = 20
    random_state: int = 42


class TransitionRequest(BaseModel):
    state: str


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


@router.post('/patterns')
def run_discovery(req: DiscoverRequest = DiscoverRequest(), _principal: Principal = Depends(require_roles('DATA_SCIENTIST', 'ML_ENGINEER'))):
    hist = _history()
    if hist.empty:
        raise HTTPException(400, 'no transactions loaded')
    result = discover_patterns(
        hist,
        contamination=req.contamination,
        top_fraction=req.top_fraction,
        min_cluster_size=req.min_cluster_size,
        min_input_rows=req.min_input_rows,
        random_state=req.random_state,
    )
    return result.to_dict()


@router.get('/patterns')
def list_discovered(_principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST', 'ML_ENGINEER', 'AUDITOR'))):
    return {'states': list(STATES), 'candidates': list(load_candidates().values())}


@router.get('/patterns/{pattern_id}')
def read_pattern(pattern_id: str, _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST', 'ML_ENGINEER', 'AUDITOR'))):
    try:
        return get_pattern(pattern_id)
    except KeyError as exc:
        raise HTTPException(404, 'pattern not found') from exc


@router.post('/patterns/{pattern_id}/transition')
def move_pattern(pattern_id: str, req: TransitionRequest, principal: Principal = Depends(require_principal)):
    target = req.state.upper()
    if target in {'ACTIVE', 'DEPRECATED', 'CONFIRMED', 'MONITORED'} and 'ADMIN' not in principal.roles and 'SENIOR_ANALYST' not in principal.roles:
        raise HTTPException(403, 'senior analyst role required')
    if target in {'UNDER_REVIEW', 'DISCOVERED'} and 'ADMIN' not in principal.roles and not principal.roles.intersection({'FRAUD_ANALYST', 'SENIOR_ANALYST'}):
        raise HTTPException(403, 'Insufficient role')
    if target not in {'UNDER_REVIEW', 'DISCOVERED', 'CONFIRMED', 'ACTIVE', 'DEPRECATED', 'MONITORED'}:
        raise HTTPException(400, 'Invalid pattern state')
    try:
        result = transition_pattern(pattern_id, req.state)
        if req.state.upper() == 'ACTIVE':
            record_event(principal.actor_id, 'PATTERN_ACTIVATION', 'FraudPattern', pattern_id, {'state': req.state.upper()})
        return result
    except KeyError as exc:
        raise HTTPException(404, 'pattern not found') from exc
    except InvalidPatternTransition as exc:
        raise HTTPException(409, str(exc)) from exc
