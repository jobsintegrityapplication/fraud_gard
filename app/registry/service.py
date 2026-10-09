from __future__ import annotations

from datetime import datetime, timezone
import json

from ..db import conn
from ..discovery.lifecycle import InvalidPatternTransition, apply_transition
from ..domain.redact import sanitize_audit_detail
from .matching import score_transaction

REQUIRED_FIELDS = (
    'pattern_id', 'version', 'name', 'description', 'signature', 'supporting_cases',
    'supporting_evidence', 'threshold', 'policy_action', 'owner', 'state',
    'created_at', 'updated_at',
)

POLICY_ACTIONS = {'ALERT', 'BLOCK', 'STEP_UP', 'REVIEW'}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value, default):
    if value is None or value == '':
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def serialize_row(row) -> dict:
    data = dict(row)
    signature = _loads(data.get('signature_json'), {})
    evidence = _loads(data.get('supporting_evidence_json'), _loads(data.get('evidence_json'), []))
    cases = _loads(data.get('supporting_cases_json'), [])
    return {
        'pattern_id': data.get('id') or data.get('pattern_id'),
        'version': int(data.get('version') or 1),
        'name': data.get('name') or '',
        'description': data.get('description') or '',
        'signature': signature if isinstance(signature, dict) else {'raw': signature},
        'supporting_cases': cases if isinstance(cases, list) else [cases],
        'supporting_evidence': evidence if isinstance(evidence, list) else [evidence],
        'threshold': float(data.get('threshold') if data.get('threshold') is not None else 1.0),
        'policy_action': data.get('policy_action') or 'ALERT',
        'owner': data.get('owner') or '',
        'state': data.get('state') or 'DISCOVERED',
        'created_at': data.get('created_at'),
        'updated_at': data.get('updated_at'),
    }


def write_audit(actor: str, action: str, pattern_id: str, detail: dict | str | None = None):
    c = conn()
    c.execute(
        'INSERT INTO audit(actor,action,entity_type,entity_id,detail,ts) VALUES(?,?,?,?,?,?)',
        (
            actor or 'anonymous',
            action,
            'FraudPattern',
            pattern_id,
            sanitize_audit_detail(detail),
            _now(),
        ),
    )
    c.commit()
    c.close()


def list_patterns() -> list[dict]:
    c = conn()
    rows = c.execute('SELECT * FROM patterns ORDER BY created_at DESC').fetchall()
    c.close()
    return [serialize_row(r) for r in rows]


def get_pattern(pattern_id: str) -> dict | None:
    c = conn()
    row = c.execute('SELECT * FROM patterns WHERE id = ?', (pattern_id,)).fetchone()
    c.close()
    if row is None:
        return None
    return serialize_row(row)


def upsert_pattern(
    *,
    pattern_id: str,
    name: str,
    state: str = 'DISCOVERED',
    description: str = '',
    signature: dict | None = None,
    supporting_cases: list | None = None,
    supporting_evidence: list | None = None,
    threshold: float = 1.0,
    policy_action: str = 'ALERT',
    owner: str = '',
    version: int = 1,
    actor: str = 'system',
    audit_action: str | None = 'PATTERN_UPSERT',
) -> dict:
    action = (policy_action or 'ALERT').upper()
    if action not in POLICY_ACTIONS:
        raise ValueError(f'invalid policy_action: {policy_action}')
    now = _now()
    existing = get_pattern(pattern_id)
    created = existing['created_at'] if existing else now
    c = conn()
    c.execute(
        '''INSERT OR REPLACE INTO patterns(
            id, name, state, signature_json, evidence_json, created_at, updated_at,
            description, version, supporting_cases_json, supporting_evidence_json,
            threshold, policy_action, owner
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (
            pattern_id, name, state,
            json.dumps(signature or {}),
            json.dumps(supporting_evidence or []),
            created, now, description, int(version),
            json.dumps(supporting_cases or []),
            json.dumps(supporting_evidence or []),
            float(threshold), action, owner or '',
        ),
    )
    c.commit()
    c.close()
    if audit_action:
        write_audit(actor, audit_action, pattern_id, {'state': state, 'version': version, 'name': name})
    return get_pattern(pattern_id)


def _transition(pattern_id: str, target: str, actor: str, action: str, extra: dict | None = None) -> dict:
    current = get_pattern(pattern_id)
    if current is None:
        raise KeyError(pattern_id)
    new_state = apply_transition(current['state'], target)
    updated = upsert_pattern(
        pattern_id=current['pattern_id'],
        name=current['name'],
        state=new_state,
        description=current['description'],
        signature=current['signature'],
        supporting_cases=current['supporting_cases'],
        supporting_evidence=current['supporting_evidence'],
        threshold=current['threshold'],
        policy_action=current['policy_action'],
        owner=current['owner'],
        version=current['version'] + 1,
        actor=actor,
        audit_action=None,
    )
    write_audit(actor, action, pattern_id, {
        'from': current['state'],
        'to': new_state,
        **(extra or {}),
    })
    return updated


def review_pattern(pattern_id: str, actor: str, notes: str = '') -> dict:
    current = get_pattern(pattern_id)
    if current is None:
        raise KeyError(pattern_id)
    if current['state'] == 'DISCOVERED':
        target = 'UNDER_REVIEW'
    elif current['state'] == 'UNDER_REVIEW':
        target = 'CONFIRMED'
    else:
        raise InvalidPatternTransition(f"pattern {pattern_id} is {current['state']}; review is not applicable")
    return _transition(pattern_id, target, actor, 'PATTERN_REVIEW', {'notes': notes})


def activate_pattern(pattern_id: str, actor: str) -> dict:
    return _transition(pattern_id, 'ACTIVE', actor, 'PATTERN_ACTIVATION')


def deprecate_pattern(pattern_id: str, actor: str) -> dict:
    return _transition(pattern_id, 'DEPRECATED', actor, 'PATTERN_DEPRECATE')


def fireable_patterns() -> list[dict]:
    return [p for p in list_patterns() if p['state'] == 'ACTIVE']


def score_with_registry(tx: dict) -> tuple[float, str | None]:
    return score_transaction(tx, fireable_patterns())
