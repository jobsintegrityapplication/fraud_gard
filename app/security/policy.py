from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import conn
from .audit import record_event
from .auth import Principal, require_roles

router = APIRouter(prefix='/api/v1/policy', tags=['policy'])
POLICY_KEY = 'decision'
ALLOWED_KEYS = {'weights', 'thresholds', 'level_actions'}


def load_active_policy() -> dict:
    c = conn()
    try:
        row = c.execute('SELECT policy_json FROM policy_settings WHERE policy_key=?', (POLICY_KEY,)).fetchone()
        return json.loads(row['policy_json']) if row else {}
    finally:
        c.close()


@router.get('')
def read_policy(_principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST', 'ML_ENGINEER', 'AUDITOR'))):
    c = conn()
    try:
        row = c.execute('SELECT version,policy_json,updated_by,updated_at FROM policy_settings WHERE policy_key=?', (POLICY_KEY,)).fetchone()
        return {'version': row['version'], 'policy': json.loads(row['policy_json']), 'updated_by': row['updated_by'], 'updated_at': row['updated_at']} if row else {'version': 0, 'policy': {}, 'updated_by': None, 'updated_at': None}
    finally:
        c.close()


class PolicyUpdate(BaseModel):
    policy: dict
    expected_version: int = Field(ge=0)


@router.put('')
def update_policy(body: PolicyUpdate, principal: Principal = Depends(require_roles('ADMIN'))):
    unknown = set(body.policy) - ALLOWED_KEYS
    if unknown:
        raise HTTPException(400, 'unsupported policy fields')
    weights = body.policy.get('weights')
    if weights is not None:
        allowed_weights = {'xgboost_score', 'anomaly_score', 'graph_score', 'pattern_score'}
        if not isinstance(weights, dict) or set(weights) - allowed_weights or any(not isinstance(v, (int, float)) or v < 0 for v in weights.values()) or sum(weights.values()) <= 0:
            raise HTTPException(400, 'weights must be non-negative numeric values with a positive sum')
    thresholds = body.policy.get('thresholds')
    if thresholds is not None:
        allowed_thresholds = {'MEDIUM', 'HIGH', 'CRITICAL'}
        if not isinstance(thresholds, dict) or set(thresholds) - allowed_thresholds or any(not isinstance(v, (int, float)) or not 0 <= v <= 1 for v in thresholds.values()):
            raise HTTPException(400, 'thresholds must be numeric risk cutoffs between 0 and 1')
        merged = {'MEDIUM': 0.40, 'HIGH': 0.65, 'CRITICAL': 0.85} | thresholds
        ordered = [merged['MEDIUM'], merged['HIGH'], merged['CRITICAL']]
        if ordered != sorted(ordered):
            raise HTTPException(400, 'thresholds must be in ascending severity order')
    level_actions = body.policy.get('level_actions')
    if level_actions is not None:
        allowed_levels = {'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'}
        allowed_actions = {'ALLOW', 'STEP_UP', 'MANUAL_REVIEW', 'HOLD', 'BLOCK'}
        if not isinstance(level_actions, dict) or set(level_actions) - allowed_levels or any(v not in allowed_actions for v in level_actions.values()):
            raise HTTPException(400, 'level_actions contains an unsupported level or action')

    encoded = json.dumps(body.policy, sort_keys=True)
    digest = hashlib.sha256(encoded.encode()).hexdigest()[:16]
    now = datetime.now(timezone.utc).isoformat()
    c = conn()
    try:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT version FROM policy_settings WHERE policy_key=?', (POLICY_KEY,)).fetchone()
        current_version = int(row['version']) if row else 0
        if body.expected_version != current_version:
            c.rollback()
            raise HTTPException(409, 'policy version changed; reload before updating')
        version = current_version + 1
        c.execute(
            '''INSERT INTO policy_settings(policy_key,version,policy_json,updated_by,updated_at)
               VALUES(?,?,?,?,?) ON CONFLICT(policy_key) DO UPDATE SET version=excluded.version,
               policy_json=excluded.policy_json,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
            (POLICY_KEY, version, encoded, principal.actor_id, now),
        )
        c.commit()
    finally:
        c.close()
    record_event(principal.actor_id, 'POLICY_CHANGE', 'DecisionPolicy', POLICY_KEY, {'version': version, 'fields': sorted(body.policy), 'digest': digest})
    return {'version': version, 'policy': body.policy, 'updated_by': principal.actor_id, 'updated_at': now}
