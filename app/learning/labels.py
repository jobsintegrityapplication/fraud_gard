from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import uuid

from ..db import conn

QUEUE_STATUSES = (
    'QUEUED',
    'CLAIMED',
    'QUALITY_FAILED',
    'READY',
    'USED_IN_TRAINING',
    'REJECTED',
)

# Decisions that must never become positive training labels.
NEVER_POSITIVE = frozenset({'NEED_MORE_EVIDENCE', 'UNCERTAIN'})


@dataclass(frozen=True)
class LabelQualityRules:
    """Controls when NOT_FRAUD may become a negative training label."""

    accept_not_fraud_as_negative: bool = True
    require_reason_min_chars: int = 8
    require_reviewer: bool = True
    require_transaction_id: bool = True
    reject_uncertain: bool = True
    reject_need_more_evidence: bool = True

    @classmethod
    def from_env(cls) -> 'LabelQualityRules':
        return cls(
            accept_not_fraud_as_negative=os.environ.get('LEARNING_ACCEPT_NOT_FRAUD', '1').lower() not in {'0', 'false', 'no'},
            require_reason_min_chars=int(os.environ.get('LEARNING_NOT_FRAUD_REASON_MIN', '8')),
            require_reviewer=os.environ.get('LEARNING_REQUIRE_REVIEWER', '1').lower() not in {'0', 'false', 'no'},
            require_transaction_id=True,
            reject_uncertain=True,
            reject_need_more_evidence=True,
        )


@dataclass
class TrainingLabelQueue:
    queue_id: str
    case_id: str
    transaction_id: str | None
    decision: str
    label: int | None
    reviewer: str
    reason: str
    evidence_version: int
    status: str
    rejection_reason: str | None = None
    created_at: str = ''
    updated_at: str = ''
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return 'TLQ-' + uuid.uuid4().hex[:12].upper()


def evaluate_label_eligibility(
    decision: str,
    *,
    reason: str,
    reviewer: str,
    transaction_id: str | None,
    rules: LabelQualityRules | None = None,
) -> dict:
    """Return whether a decision may enter the training queue and with which label."""
    rules = rules or LabelQualityRules.from_env()
    d = str(decision or '').upper()
    if d in NEVER_POSITIVE or (d == 'UNCERTAIN' and rules.reject_uncertain) or (d == 'NEED_MORE_EVIDENCE' and rules.reject_need_more_evidence):
        return {
            'enqueue': False,
            'label': None,
            'reason': f'{d} must not automatically become a positive training label',
        }
    if d == 'CONFIRMED_FRAUD':
        if rules.require_transaction_id and not transaction_id:
            return {'enqueue': False, 'label': None, 'reason': 'CONFIRMED_FRAUD requires transaction_id'}
        return {'enqueue': True, 'label': 1, 'reason': 'confirmed fraud → positive training label'}
    if d == 'NOT_FRAUD':
        if not rules.accept_not_fraud_as_negative:
            return {'enqueue': False, 'label': None, 'reason': 'NOT_FRAUD negatives disabled by label-quality rules'}
        if rules.require_reviewer and not str(reviewer or '').strip():
            return {'enqueue': False, 'label': None, 'reason': 'NOT_FRAUD requires reviewer'}
        if len(str(reason or '').strip()) < rules.require_reason_min_chars:
            return {
                'enqueue': False,
                'label': None,
                'reason': f'NOT_FRAUD reason must be at least {rules.require_reason_min_chars} characters',
            }
        if rules.require_transaction_id and not transaction_id:
            return {'enqueue': False, 'label': None, 'reason': 'NOT_FRAUD requires transaction_id'}
        return {'enqueue': True, 'label': 0, 'reason': 'NOT_FRAUD accepted as negative under label-quality rules'}
    return {'enqueue': False, 'label': None, 'reason': f'decision {d} is not a training label source'}


def enqueue_from_decision(
    *,
    case_id: str,
    transaction_id: str | None,
    decision: str,
    reviewer: str,
    reason: str,
    evidence_version: int,
    decision_id: str | None = None,
    rules: LabelQualityRules | None = None,
) -> TrainingLabelQueue | None:
    verdict = evaluate_label_eligibility(
        decision, reason=reason, reviewer=reviewer, transaction_id=transaction_id, rules=rules,
    )
    if not verdict['enqueue']:
        return None
    now = _now()
    row = TrainingLabelQueue(
        queue_id=_new_id(),
        case_id=case_id,
        transaction_id=transaction_id,
        decision=str(decision).upper(),
        label=int(verdict['label']),
        reviewer=reviewer,
        reason=reason,
        evidence_version=int(evidence_version),
        status='QUEUED',
        created_at=now,
        updated_at=now,
        metadata={'decision_id': decision_id, 'eligibility': verdict['reason']},
    )
    c = conn()
    c.execute(
        '''INSERT INTO training_label_queue(
            queue_id, case_id, transaction_id, decision, label, reviewer, reason,
            evidence_version, status, rejection_reason, created_at, updated_at, metadata_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (
            row.queue_id, row.case_id, row.transaction_id, row.decision, row.label, row.reviewer,
            row.reason, row.evidence_version, row.status, None, row.created_at, row.updated_at,
            json.dumps(row.metadata),
        ),
    )
    c.commit()
    c.close()
    return row


def list_queue(status: str | None = None, limit: int = 200) -> list[dict]:
    c = conn()
    if status:
        rows = c.execute(
            'SELECT * FROM training_label_queue WHERE status = ? ORDER BY created_at ASC LIMIT ?',
            (status, int(limit)),
        ).fetchall()
    else:
        rows = c.execute(
            'SELECT * FROM training_label_queue ORDER BY created_at DESC LIMIT ?',
            (int(limit),),
        ).fetchall()
    c.close()
    out = []
    for r in rows:
        d = dict(r)
        d['metadata'] = json.loads(d.pop('metadata_json') or '{}')
        out.append(d)
    return out


def update_queue_status(queue_id: str, status: str, rejection_reason: str | None = None, metadata: dict | None = None):
    c = conn()
    row = c.execute('SELECT metadata_json FROM training_label_queue WHERE queue_id = ?', (queue_id,)).fetchone()
    if row is None:
        c.close()
        raise KeyError(queue_id)
    meta = json.loads(row['metadata_json'] or '{}')
    if metadata:
        meta.update(metadata)
    c.execute(
        'UPDATE training_label_queue SET status=?, rejection_reason=?, updated_at=?, metadata_json=? WHERE queue_id=?',
        (status, rejection_reason, _now(), json.dumps(meta), queue_id),
    )
    c.commit()
    c.close()


def claim_queued(limit: int = 500) -> list[dict]:
    # Claim under one write lock so parallel API workers cannot train on the
    # same queued labels. A conditional status update is the final ownership
    # check even if another process selected the same rows first.
    c = conn()
    c.execute('BEGIN IMMEDIATE')
    rows = c.execute(
        "SELECT * FROM training_label_queue WHERE status='QUEUED' ORDER BY created_at ASC LIMIT ?",
        (max(0, int(limit)),),
    ).fetchall()
    claimed = []
    now = _now()
    for row in rows:
        cursor = c.execute(
            "UPDATE training_label_queue SET status='CLAIMED', updated_at=? WHERE queue_id=? AND status='QUEUED'",
            (now, row['queue_id']),
        )
        if cursor.rowcount == 1:
            item = dict(row)
            item['status'] = 'CLAIMED'
            item['updated_at'] = now
            item['metadata'] = json.loads(item.pop('metadata_json') or '{}')
            claimed.append(item)
    c.commit()
    c.close()
    return claimed
