from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .engine import investigate_transaction
from .tools import TOOL_REGISTRY, invoke
from ..security.auth import Principal, require_roles

router = APIRouter(prefix='/api/v1', tags=['investigation'])


class InvestigateBody(BaseModel):
    transaction_id: str


class ToolBody(BaseModel):
    transaction_id: str | None = None
    account_id: str | None = None
    imei: str | None = None
    sim_id: str | None = None
    ip: str | None = None
    merchant_id: str | None = None
    location: str | None = None
    entity_id: str | None = None
    customer_id: str | None = None
    depth: int | None = None
    limit: int | None = None


@router.get('/investigation/tools')
def list_tools(_principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST'))):
    return {'tools': sorted(TOOL_REGISTRY.keys())}


@router.post('/investigation/tools/{tool_name}')
def run_tool(tool_name: str, body: ToolBody | None = None, _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST'))):
    if tool_name not in TOOL_REGISTRY:
        raise HTTPException(404, 'unknown tool')
    body = body or ToolBody()
    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    allowed = TOOL_REGISTRY[tool_name].__code__.co_varnames
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    result = invoke(tool_name, **filtered)
    return result.to_dict()


@router.post('/investigation')
def run_investigation(body: InvestigateBody, _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST'))):
    return investigate_transaction(body.transaction_id).to_dict()
