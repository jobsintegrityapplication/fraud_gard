from __future__ import annotations

CASE_STATES = (
    'OPEN',
    'INVESTIGATING',
    'PENDING_REVIEW',
    'CONFIRMED_FRAUD',
    'NOT_FRAUD',
    'NEED_MORE_EVIDENCE',
    'CLOSED',
)

DECISIONS = (
    'CONFIRMED_FRAUD',
    'NOT_FRAUD',
    'NEED_MORE_EVIDENCE',
    'INVESTIGATING',
    'PENDING_REVIEW',
    'CLOSED',
)

TRANSITIONS: dict[str, frozenset[str]] = {
    'OPEN': frozenset({'INVESTIGATING', 'PENDING_REVIEW', 'CONFIRMED_FRAUD', 'NOT_FRAUD', 'NEED_MORE_EVIDENCE', 'CLOSED'}),
    'INVESTIGATING': frozenset({'PENDING_REVIEW', 'NEED_MORE_EVIDENCE', 'CONFIRMED_FRAUD', 'NOT_FRAUD', 'OPEN'}),
    'PENDING_REVIEW': frozenset({'CONFIRMED_FRAUD', 'NOT_FRAUD', 'NEED_MORE_EVIDENCE', 'INVESTIGATING'}),
    'NEED_MORE_EVIDENCE': frozenset({'INVESTIGATING', 'PENDING_REVIEW'}),
    'CONFIRMED_FRAUD': frozenset({'CLOSED'}),
    'NOT_FRAUD': frozenset({'CLOSED'}),
    'CLOSED': frozenset(),
}


class InvalidCaseTransition(ValueError):
    pass


def apply_decision_status(current: str, decision: str) -> str:
    target = str(decision).upper()
    if target not in DECISIONS:
        raise InvalidCaseTransition(f'invalid decision: {decision}')
    cur = str(current or 'OPEN').upper()
    if cur not in CASE_STATES:
        cur = 'OPEN'
    if target not in TRANSITIONS.get(cur, frozenset()):
        raise InvalidCaseTransition(f'cannot apply {target} from {cur}')
    return target
