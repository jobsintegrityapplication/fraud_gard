from __future__ import annotations

STATES = (
    'PROPOSED',
    'VALIDATING',
    'ACCEPTED',
    'ACTIVE',
    'REJECTED',
)

INITIAL_STATE = 'PROPOSED'

TRANSITIONS: dict[str, frozenset[str]] = {
    'PROPOSED': frozenset({'VALIDATING', 'REJECTED'}),
    'VALIDATING': frozenset({'ACCEPTED', 'REJECTED', 'PROPOSED'}),
    'ACCEPTED': frozenset({'ACTIVE', 'REJECTED'}),
    'ACTIVE': frozenset({'REJECTED'}),
    'REJECTED': frozenset({'PROPOSED'}),
}


class InvalidFeatureTransition(ValueError):
    pass


def validate_state(state: str) -> str:
    value = str(state).upper()
    if value not in STATES:
        raise InvalidFeatureTransition(f'unknown candidate feature state: {state}')
    return value


def can_transition(current: str, target: str) -> bool:
    return validate_state(target) in TRANSITIONS[validate_state(current)]


def apply_transition(current: str, target: str) -> str:
    cur, nxt = validate_state(current), validate_state(target)
    if nxt not in TRANSITIONS[cur]:
        raise InvalidFeatureTransition(f'cannot transition {cur} -> {nxt}')
    return nxt
