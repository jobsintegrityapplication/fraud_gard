from __future__ import annotations

STATES = (
    'DISCOVERED',
    'UNDER_REVIEW',
    'CONFIRMED',
    'ACTIVE',
    'MONITORED',
    'DEPRECATED',
)

INITIAL_STATE = 'DISCOVERED'

# Reviewers move candidates through this chain. Discovery never skips ahead.
TRANSITIONS: dict[str, frozenset[str]] = {
    'DISCOVERED': frozenset({'UNDER_REVIEW', 'DEPRECATED'}),
    'UNDER_REVIEW': frozenset({'CONFIRMED', 'DISCOVERED', 'DEPRECATED'}),
    'CONFIRMED': frozenset({'ACTIVE', 'UNDER_REVIEW', 'DEPRECATED'}),
    'ACTIVE': frozenset({'MONITORED', 'DEPRECATED'}),
    'MONITORED': frozenset({'ACTIVE', 'DEPRECATED'}),
    'DEPRECATED': frozenset(),
}


class InvalidPatternTransition(ValueError):
    pass


def validate_state(state: str) -> str:
    value = str(state).upper()
    if value not in STATES:
        raise InvalidPatternTransition(f'unknown pattern state: {state}')
    return value


def can_transition(current: str, target: str) -> bool:
    return validate_state(target) in TRANSITIONS[validate_state(current)]


def apply_transition(current: str, target: str) -> str:
    cur, nxt = validate_state(current), validate_state(target)
    if nxt not in TRANSITIONS[cur]:
        raise InvalidPatternTransition(f'cannot transition {cur} -> {nxt}')
    return nxt
