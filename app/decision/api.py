from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .engine import ACTIONS, RISK_LEVELS, DecisionEngineError, DecisionInputs, decide, default_active_rules
from ..security.auth import Principal, require_roles
from ..security.policy import load_active_policy

router = APIRouter(prefix='/api/v1/decision', tags=['decision'])


class DecideBody(BaseModel):
    xgboost_score: float
    anomaly_score: float
    graph_score: float
    pattern_score: float
    active_rules: list[dict] | None = None
    business_policy: dict = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


@router.get('/actions')
def list_actions(_principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST', 'ML_ENGINEER', 'AUDITOR'))):
    return {'risk_levels': list(RISK_LEVELS), 'actions': list(ACTIONS), 'llm_determined': False}


@router.post('/evaluate')
def evaluate(body: DecideBody, _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST'))):
    try:
        out = decide(DecisionInputs(
            xgboost_score=body.xgboost_score,
            anomaly_score=body.anomaly_score,
            graph_score=body.graph_score,
            pattern_score=body.pattern_score,
            active_rules=body.active_rules if body.active_rules is not None else default_active_rules(),
            business_policy=load_active_policy(),
            evidence_ids=body.evidence_ids,
        ))
    except DecisionEngineError as exc:
        raise HTTPException(400, str(exc)) from exc
    return out.to_dict()
