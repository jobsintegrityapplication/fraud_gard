from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .graph import run_case_workflow
from .state import WORKFLOW_ORDER
from ..security.auth import Principal, require_roles
from ..cases.service import get_case

router = APIRouter(prefix='/api/v1', tags=['agents'])


class WorkflowBody(BaseModel):
    transaction_id: str
    case_id: str | None = None
    analyst_decision: dict | None = None


@router.get('/agents')
def list_agents(_principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST'))):
    return {'workflow': list(WORKFLOW_ORDER), 'llm_cannot': [
        'invent evidence', 'invent transactions', 'invent customer history',
        'change ML scores', 'activate patterns', 'promote models', 'override human decisions',
    ]}


@router.post('/agents/workflow')
def run_workflow(body: WorkflowBody, principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST'))):
    if body.case_id and get_case(body.case_id) is None:
        raise HTTPException(404, 'Case not found')
    state = run_case_workflow(body.transaction_id, case_id=body.case_id or '', analyst_decision=body.analyst_decision, actor_id=principal.actor_id)
    return state
