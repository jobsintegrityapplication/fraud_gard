from datetime import datetime, timezone
import time

from app.cases.service import create_case, record_decision
from app.db import conn
from app.learning.jobs import schedule_learning_job
from app.learning.labels import (
    NEVER_POSITIVE,
    LabelQualityRules,
    enqueue_from_decision,
    evaluate_label_eligibility,
    list_queue,
)
from app.learning.workflow import JOB_STAGES, run_learning_workflow


def _seed_txn(tid='T-LEARN-1'):
    c = conn()
    c.execute(
        '''INSERT OR REPLACE INTO transactions(
            id, customer_id, account_id, sim_id, imei, ip, merchant_id, location, amount, ts, label
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
        (tid, 'C1', 'A1', 'S1', 'IMEI1', '10.0.0.1', 'M1', 'DEL', 42.0, '2026-09-18T00:00:00+00:00', None),
    )
    c.commit()
    c.close()


def test_confirmed_fraud_enqueues_positive_without_sync_retrain(client):
    _seed_txn('T-CF-1')
    case = create_case(transaction_id='T-CF-1', title='cf', actor='analyst')
    before_models = list((client.app is not None and True,))  # noqa: keep client fixture active
    assert before_models
    out = record_decision(
        case['case_id'],
        decision='CONFIRMED_FRAUD',
        reason='ring confirmed with evidence',
        reviewer='lead',
        timestamp=datetime.now(timezone.utc),
        evidence_version=1,
    )
    assert out['learning']['synchronous_retrain'] is False
    assert out['learning']['queue']['label'] == 1
    assert out['learning']['job']['async'] is True
    assert out['learning']['job']['status'] == 'SCHEDULED'
    queued = list_queue()
    assert any(q['transaction_id'] == 'T-CF-1' and q['label'] == 1 for q in queued)


def test_need_more_evidence_and_uncertain_never_positive():
    assert 'NEED_MORE_EVIDENCE' in NEVER_POSITIVE
    assert 'UNCERTAIN' in NEVER_POSITIVE
    for decision in ('NEED_MORE_EVIDENCE', 'UNCERTAIN'):
        verdict = evaluate_label_eligibility(
            decision, reason='need more', reviewer='r', transaction_id='T1',
        )
        assert verdict['enqueue'] is False
        assert verdict['label'] is None
        assert enqueue_from_decision(
            case_id='C1', transaction_id='T1', decision=decision,
            reviewer='r', reason='need more evidence please', evidence_version=1,
        ) is None


def test_not_fraud_requires_label_quality_rules():
    short = evaluate_label_eligibility(
        'NOT_FRAUD', reason='no', reviewer='r', transaction_id='T1',
        rules=LabelQualityRules(require_reason_min_chars=8),
    )
    assert short['enqueue'] is False
    ok = evaluate_label_eligibility(
        'NOT_FRAUD', reason='clearly legitimate purchase', reviewer='r', transaction_id='T1',
        rules=LabelQualityRules(require_reason_min_chars=8, accept_not_fraud_as_negative=True),
    )
    assert ok['enqueue'] is True
    assert ok['label'] == 0
    disabled = evaluate_label_eligibility(
        'NOT_FRAUD', reason='clearly legitimate purchase', reviewer='r', transaction_id='T1',
        rules=LabelQualityRules(accept_not_fraud_as_negative=False),
    )
    assert disabled['enqueue'] is False


def test_async_job_pipeline_stages(client):
    _seed_txn('T-JOB-1')
    # Seed enough labeled rows so quality gate can pass when wait=True
    for i in range(6):
        tid = f'T-JOB-{i}'
        _seed_txn(tid)
        enqueue_from_decision(
            case_id=f'C-{i}', transaction_id=tid, decision='CONFIRMED_FRAUD',
            reviewer='lead', reason='confirmed fraud with investigation', evidence_version=1,
        )
    scheduled = schedule_learning_job(wait=False, min_rows=5)
    assert scheduled['async'] is True
    assert scheduled['status'] == 'SCHEDULED'
    # Completing synchronously exercises the full stage chain without blocking the case API path.
    for i in range(6):
        tid = f'T-SYNC-{i}'
        _seed_txn(tid)
        enqueue_from_decision(
            case_id=f'CS-{i}', transaction_id=tid, decision='CONFIRMED_FRAUD',
            reviewer='lead', reason='confirmed fraud with investigation', evidence_version=1,
        )
    done = run_learning_workflow(min_rows=5)
    assert set(JOB_STAGES) <= set(done['stages'])
    for stage in JOB_STAGES:
        assert done['stages'][stage]['status'] in {'OK', 'FAILED', 'HELD', 'PENDING'}
    assert done['stages']['training_queue']['status'] == 'OK'
    assert done['stages']['data_quality']['status'] in {'OK', 'FAILED'}
    if done['status'] == 'COMPLETED' and done['result'].get('trained'):
        assert done['stages']['challenger_training']['status'] == 'OK'
        assert done['stages']['evaluation']['status'] == 'OK'
        assert done['stages']['promotion_gates']['status'] in {'OK', 'HELD'}


def test_decision_api_schedules_learning(client):
    _seed_txn('T-API-1')
    created = client.post('/api/v1/cases', json={'transaction_id': 'T-API-1', 'title': 'learn'})
    case_id = created.json()['case_id']
    decided = client.post(
        f'/api/v1/cases/{case_id}/decision',
        json={
            'decision': 'CONFIRMED_FRAUD',
            'reason': 'confirmed after review',
            'reviewer': 'analyst',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'evidence_version': 1,
        },
    )
    assert decided.status_code == 200, decided.text
    body = decided.json()
    assert body['learning']['synchronous_retrain'] is False
    assert body['learning']['queue']['label'] == 1
    q = client.get('/api/v1/learning/queue')
    assert q.status_code == 200
    assert any(i['transaction_id'] == 'T-API-1' for i in q.json()['items'])
    # Give the background thread a moment; it must not be required for API success.
    time.sleep(0.05)
