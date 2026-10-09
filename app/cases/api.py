from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import conn
from .service import create_case, get_case, list_cases, record_decision
from .states import DECISIONS, InvalidCaseTransition
from ..security.audit import record_event
from ..security.auth import Principal, require_roles
from ..observability import CONFIRMED_FRAUD

router = APIRouter(prefix='/api/v1', tags=['cases'])


class CreateCaseBody(BaseModel):
    transaction_id: str | None = None
    pattern_id: str | None = None
    customer_id: str | None = None
    account_id: str | None = None
    title: str | None = None
    evidence: list[dict] | None = None
    status: str = 'OPEN'


class DecisionBody(BaseModel):
    decision: str
    reason: str
    reviewer: str
    timestamp: datetime
    evidence_version: int = Field(..., ge=1)


@router.get('/cases')
def api_list_cases(principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'AUDITOR'))):
    rows = list_cases()
    record_event(principal.actor_id, 'CASE_LIST_ACCESS', 'FraudCase', detail={'count': len(rows)})
    return rows


@router.get('/cases/{case_id}')
def api_get_case(case_id: str, principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'AUDITOR'))):
    row = get_case(case_id)
    if row is None:
        raise HTTPException(404, 'Case not found')
    record_event(principal.actor_id, 'CASE_ACCESS', 'FraudCase', case_id)
    return row


@router.post('/cases')
def api_create_case(body: CreateCaseBody, principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST'))):
    try:
        return create_case(
            transaction_id=body.transaction_id,
            pattern_id=body.pattern_id,
            customer_id=body.customer_id,
            account_id=body.account_id,
            title=body.title,
            evidence=body.evidence,
            status=body.status,
            actor=principal.actor_id,
        )
    except InvalidCaseTransition as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/cases/{case_id}/decision')
def api_decision(case_id: str, body: DecisionBody, principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST'))):
    if body.decision.upper() not in DECISIONS:
        raise HTTPException(400, 'Invalid decision')
    try:
        result = record_decision(
            case_id,
            decision=body.decision,
            reason=body.reason,
            reviewer=principal.actor_id,
            timestamp=body.timestamp,
            evidence_version=body.evidence_version,
        )
    except KeyError as exc:
        raise HTTPException(404, 'Case not found') from exc
    except InvalidCaseTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    record_event(principal.actor_id, 'CASE_DECISION', 'FraudCase', case_id, {'decision': body.decision.upper(), 'evidence_version': body.evidence_version})
    if body.decision.upper() == 'CONFIRMED_FRAUD':
        CONFIRMED_FRAUD.inc()
    return result


@router.post('/cases/from-alert/{alert_id}')
def api_from_alert(alert_id: int, principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST'))):
    c = conn()
    a = c.execute('SELECT * FROM alerts WHERE id=?', (alert_id,)).fetchone()
    c.close()
    if not a:
        raise HTTPException(404, 'Alert not found')
    alert = dict(a)
    return create_case(
        transaction_id=alert.get('transaction_id'),
        title=f'Alert {alert_id}',
        evidence=[{
            'evidence_type': 'ALERT',
            'source': 'scoring',
            'payload': {
                'alert_id': alert.get('id'),
                'risk': alert.get('risk'),
            },
        }],
        actor=principal.actor_id,
    )
