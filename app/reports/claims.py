from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..agents.policy import EVIDENCE_UNAVAILABLE


@dataclass
class Claim:
    """A material report assertion. Must cite evidence or mark unavailable."""

    field: str
    label: str
    value: Any
    evidence_ids: list[str] = field(default_factory=list)
    status: str = 'OK'
    note: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def claim(
    field: str,
    label: str,
    value: Any,
    evidence_ids: list[str] | None = None,
    *,
    available: bool = True,
    reason: str | None = None,
) -> Claim:
    eids = [e for e in (evidence_ids or []) if e]
    if not available or value is None or value == EVIDENCE_UNAVAILABLE:
        return Claim(
            field=field,
            label=label,
            value=EVIDENCE_UNAVAILABLE,
            evidence_ids=eids,
            status=EVIDENCE_UNAVAILABLE,
            note=reason or 'Evidence unavailable; not fabricated',
        )
    if not eids:
        # Material claim without evidence must not pretend to be proven.
        return Claim(
            field=field,
            label=label,
            value=value,
            evidence_ids=[],
            status=EVIDENCE_UNAVAILABLE,
            note=reason or 'No evidence ID available for this claim; value shown as observed but uncited',
        )
    return Claim(field=field, label=label, value=value, evidence_ids=eids, status='OK')


def validate_claims(claims: list[Claim]) -> list[str]:
    """Return errors for fabricated / uncitable material claims."""
    errors = []
    for c in claims:
        if c.status == EVIDENCE_UNAVAILABLE:
            if c.value not in (EVIDENCE_UNAVAILABLE, None) and not c.note:
                errors.append(f'{c.field}: unavailable claim missing explicit note')
            continue
        if not c.evidence_ids:
            errors.append(f'{c.field}: OK claim missing evidence_ids')
        if c.value == EVIDENCE_UNAVAILABLE:
            errors.append(f'{c.field}: OK claim has EVIDENCE_UNAVAILABLE value')
    return errors
