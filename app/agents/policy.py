from __future__ import annotations

from datetime import datetime, timezone

EVIDENCE_UNAVAILABLE = 'EVIDENCE_UNAVAILABLE'

FORBIDDEN_TOOLS = frozenset({
    'activate_pattern',
    'deprecate_pattern',
    'promote_model',
    'train_supervised',
    'train_anomaly',
    'set_ml_score',
    'override_analyst_decision',
    'invent_transaction',
    'invent_history',
    'invent_evidence',
    'llm_final_decision',
})

LLM_CANNOT = (
    'invent evidence',
    'invent transactions',
    'invent customer history',
    'change ML scores',
    'activate patterns',
    'promote models',
    'override human decisions',
)


class PolicyViolation(PermissionError):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def unavailable(kind: str, reason: str) -> dict:
    return {
        'status': EVIDENCE_UNAVAILABLE,
        'kind': kind,
        'reason': reason,
        'invented': False,
    }


def audit_event(agent: str, action: str, detail: dict | None = None) -> dict:
    return {
        'agent': agent,
        'action': action,
        'ts': utcnow(),
        'detail': detail or {},
    }


def call_deterministic_tool(name: str, fn, *args, **kwargs):
    if name in FORBIDDEN_TOOLS:
        raise PolicyViolation(f'LLM/agents cannot call forbidden tool: {name}')
    return fn(*args, **kwargs)


def assert_scores_unmodified(original: dict | None, candidate: dict | None, key: str):
    if not original or original.get('status') == EVIDENCE_UNAVAILABLE:
        return
    if candidate is None:
        return
    if key in original and key in candidate and original[key] != candidate[key]:
        raise PolicyViolation(f'LLM cannot change ML scores ({key})')


def preserve_human_decision(existing: dict | None, proposed: dict | None) -> dict | None:
    if existing:
        return existing
    if proposed and proposed.get('source') == 'analyst':
        return proposed
    return None
