from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid

from ..db import conn
from ..domain.redact import sanitize_audit_detail
from .states import CASE_STATES, DECISIONS, InvalidCaseTransition, apply_decision_status


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


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:10].upper()


def write_case_audit(
    case_id: str,
    actor: str,
    action: str,
    *,
    from_status: str | None = None,
    to_status: str | None = None,
    evidence_version: int | None = None,
    decision_id: str | None = None,
    detail: dict | str | None = None,
):
    ts = _now()
    text = sanitize_audit_detail(detail)
    c = conn()
    # Serialize decisions and compare the evidence version inside the write
    # transaction. A read/check followed by an unconditional UPDATE allowed two
    # reviewers to commit decisions against the same case version.
    c.execute('BEGIN IMMEDIATE')
    locked = c.execute('SELECT status, evidence_version FROM cases WHERE id = ?', (case_id,)).fetchone()
    if locked is None:
        c.rollback()
        c.close()
        raise KeyError(case_id)
    if int(locked['evidence_version'] or 1) != int(evidence_version):
        c.rollback()
        c.close()
        raise InvalidCaseTransition('case evidence changed; refresh before deciding')
    try:
        new_status = apply_decision_status(locked['status'], decision)
    except Exception:
        c.rollback()
        c.close()
        raise
    c.execute(
        '''INSERT INTO case_audit_events(
            case_id, actor, action, from_status, to_status, evidence_version, decision_id, detail, ts
        ) VALUES(?,?,?,?,?,?,?,?,?)''',
        (case_id, actor or 'anonymous', action, from_status, to_status, evidence_version, decision_id, text, ts),
    )
    c.execute(
        'INSERT INTO audit(actor,action,entity_type,entity_id,detail,ts) VALUES(?,?,?,?,?,?)',
        (actor or 'anonymous', action, 'FraudCase', case_id, text, ts),
    )
    c.commit()
    c.close()


def _evidence_rows(case_id: str) -> list[dict]:
    c = conn()
    rows = c.execute(
        'SELECT * FROM case_evidence WHERE case_id = ? ORDER BY created_at',
        (case_id,),
    ).fetchall()
    c.close()
    out = []
    for row in rows:
        data = dict(row)
        data['payload'] = _loads(data.get('payload_json'), {})
        out.append({
            'evidence_id': data['evidence_id'],
            'case_id': data['case_id'],
            'evidence_type': data['evidence_type'],
            'source': data['source'],
            'payload': data['payload'],
            'evidence_version': int(data.get('evidence_version') or 1),
            'created_at': data.get('created_at'),
        })
    return out


def _decision_rows(case_id: str) -> list[dict]:
    c = conn()
    rows = c.execute(
        'SELECT * FROM analyst_decisions WHERE case_id = ? ORDER BY timestamp',
        (case_id,),
    ).fetchall()
    c.close()
    return [
        {
            'decision_id': r['decision_id'],
            'case_id': r['case_id'],
            'decision': r['decision'],
            'reason': r['reason'],
            'reviewer': r['reviewer'],
            'timestamp': r['timestamp'],
            'evidence_version': int(r['evidence_version'] or 1),
        }
        for r in rows
    ]


def _audit_rows(case_id: str) -> list[dict]:
    c = conn()
    rows = c.execute(
        'SELECT * FROM case_audit_events WHERE case_id = ? ORDER BY id',
        (case_id,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def serialize_case(row, *, include_history: bool = True) -> dict:
    data = dict(row)
    case_id = data.get('id') or data.get('case_id')
    payload = {
        'id': case_id,
        'case_id': case_id,
        'transaction_id': data.get('transaction_id'),
        'pattern_id': data.get('pattern_id'),
        'customer_id': data.get('customer_id'),
        'account_id': data.get('account_id'),
        'title': data.get('title') or '',
        'status': data.get('status') or 'OPEN',
        'decision': data.get('decision'),
        'reason': data.get('reason'),
        'evidence_version': int(data.get('evidence_version') or 1),
        'created_at': data.get('created_at'),
        'updated_at': data.get('updated_at'),
    }
    if include_history:
        payload['evidence'] = _evidence_rows(case_id)
        payload['decisions'] = _decision_rows(case_id)
        payload['audit_history'] = _audit_rows(case_id)
    return payload


def list_cases() -> list[dict]:
    c = conn()
    rows = c.execute('SELECT * FROM cases ORDER BY updated_at DESC').fetchall()
    c.close()
    return [serialize_case(r, include_history=False) for r in rows]


def get_case(case_id: str) -> dict | None:
    c = conn()
    row = c.execute('SELECT * FROM cases WHERE id = ?', (case_id,)).fetchone()
    c.close()
    if row is None:
        return None
    return serialize_case(row, include_history=True)


def add_evidence(case_id: str, items: list[dict], actor: str, *, bump: bool = True) -> int:
    current = get_case(case_id)
    if current is None:
        raise KeyError(case_id)
    version = current['evidence_version']
    if bump and items:
        version = version + 1
    now = _now()
    c = conn()
    for item in items:
        eid = item.get('evidence_id') or _new_id('EV-')
        etype = str(item.get('evidence_type') or item.get('type') or 'RULE')
        source = str(item.get('source') or 'analyst')
        payload = item.get('payload') if isinstance(item.get('payload'), dict) else {k: v for k, v in item.items() if k not in {'evidence_id', 'evidence_type', 'type', 'source'}}
        c.execute(
            '''INSERT INTO case_evidence(
                evidence_id, case_id, evidence_type, source, payload_json, evidence_version, created_at
            ) VALUES(?,?,?,?,?,?,?)''',
            (eid, case_id, etype, source, json.dumps(payload), version, now),
        )
    if bump and items:
        c.execute('UPDATE cases SET evidence_version=?, updated_at=? WHERE id=?', (version, now, case_id))
    c.commit()
    c.close()
    if items:
        write_case_audit(
            case_id, actor, 'CASE_EVIDENCE_ADDED',
            to_status=current['status'], evidence_version=version,
            detail={'n': len(items)},
        )
    return version


def create_case(
    *,
    transaction_id: str | None = None,
    pattern_id: str | None = None,
    customer_id: str | None = None,
    account_id: str | None = None,
    title: str | None = None,
    evidence: list[dict] | None = None,
    actor: str = 'system',
    case_id: str | None = None,
    status: str = 'OPEN',
) -> dict:
    state = (status or 'OPEN').upper()
    if state not in CASE_STATES:
        raise InvalidCaseTransition(f'invalid case status: {status}')
    cid = case_id or _new_id('FR-')
    now = _now()
    c = conn()
    c.execute(
        '''INSERT INTO cases(
            id, transaction_id, status, decision, reason, evidence_json, created_at, updated_at,
            title, pattern_id, customer_id, account_id, evidence_version
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (
            cid, transaction_id, state, None, None, json.dumps(evidence or []),
            now, now, title or '', pattern_id, customer_id, account_id, 1,
        ),
    )
    c.commit()
    c.close()
    write_case_audit(cid, actor, 'CASE_CREATED', to_status=state, evidence_version=1, detail={'transaction_id': transaction_id})
    if evidence:
        add_evidence(cid, evidence, actor, bump=False)
    return get_case(cid)


def record_decision(
    case_id: str,
    *,
    decision: str,
    reason: str,
    reviewer: str,
    timestamp: str | datetime,
    evidence_version: int,
) -> dict:
    if not str(reason or '').strip():
        raise ValueError('reason is required')
    if not str(reviewer or '').strip():
        raise ValueError('reviewer is required')
    if timestamp in (None, ''):
        raise ValueError('timestamp is required')
    if evidence_version is None:
        raise ValueError('evidence_version is required')
    current = get_case(case_id)
    if current is None:
        raise KeyError(case_id)
    ts = timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp)
    did = _new_id('AD-')
    now = _now()
    c = conn()
    c.execute('BEGIN IMMEDIATE')
    locked = c.execute('SELECT status, evidence_version FROM cases WHERE id = ?', (case_id,)).fetchone()
    if locked is None:
        c.rollback()
        c.close()
        raise KeyError(case_id)
    if int(evidence_version) != int(locked['evidence_version'] or 1):
        c.rollback()
        c.close()
        raise InvalidCaseTransition('case evidence changed; refresh before deciding')
    try:
        new_status = apply_decision_status(locked['status'], decision)
    except Exception:
        c.rollback()
        c.close()
        raise
    c.execute(
        '''INSERT INTO analyst_decisions(
            decision_id, case_id, decision, reason, reviewer, timestamp, evidence_version
        ) VALUES(?,?,?,?,?,?,?)''',
        (did, case_id, decision.upper(), reason, reviewer, ts, int(evidence_version)),
    )
    c.execute(
        'UPDATE cases SET decision=?, reason=?, status=?, updated_at=? WHERE id=? AND evidence_version=? AND status=?',
        (decision.upper(), reason, new_status, now, case_id, int(evidence_version), locked['status']),
    )
    if c.execute('SELECT changes()').fetchone()[0] != 1:
        c.rollback()
        c.close()
        raise InvalidCaseTransition('case changed while applying decision; refresh before deciding')
    c.commit()
    c.close()
    write_case_audit(
        case_id, reviewer, 'CASE_DECISION',
        from_status=current['status'], to_status=new_status,
        evidence_version=int(evidence_version), decision_id=did,
        detail={'decision': decision.upper(), 'reason': reason},
    )
    # Continuous learning: enqueue labels only; never retrain synchronously here.
    learning_meta = None
    try:
        from ..learning.labels import enqueue_from_decision
        from ..learning.jobs import schedule_learning_job
        queued = enqueue_from_decision(
            case_id=case_id,
            transaction_id=current.get('transaction_id'),
            decision=decision.upper(),
            reviewer=reviewer,
            reason=reason,
            evidence_version=int(evidence_version),
            decision_id=did,
        )
        if queued is not None:
            job = schedule_learning_job(wait=False, actor_id=reviewer)
            learning_meta = {'queue': queued.to_dict(), 'job': job, 'synchronous_retrain': False}
            write_case_audit(
                case_id, reviewer, 'LEARNING_QUEUED',
                to_status=new_status, evidence_version=int(evidence_version), decision_id=did,
                detail={'queue_id': queued.queue_id, 'label': queued.label, 'job_id': job.get('job_id')},
            )
    except Exception:
        # Do not return implementation details or sensitive values in API data.
        learning_meta = {'error': 'learning enqueue failed', 'synchronous_retrain': False}
    case = get_case(case_id)
    if learning_meta is not None and case is not None:
        case['learning'] = learning_meta
    return case
