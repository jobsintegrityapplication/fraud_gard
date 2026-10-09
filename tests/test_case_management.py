from datetime import datetime, timezone

from app.cases.service import create_case, get_case, record_decision
from app.cases.states import InvalidCaseTransition


def _decision(decision='CONFIRMED_FRAUD', evidence_version=1, reviewer='rev-1', reason='documented review'):
    return {
        'decision': decision,
        'reason': reason,
        'reviewer': reviewer,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'evidence_version': evidence_version,
    }


def test_create_get_list_and_required_decision_fields(client):
    created = client.post('/api/v1/cases', json={
        'transaction_id': 'T-100',
        'title': 'Shared IMEI review',
        'evidence': [{'evidence_type': 'GRAPH', 'source': 'networkx', 'payload': {'accounts': 4}}],
    })
    assert created.status_code == 200, created.text
    body = created.json()
    case_id = body['case_id']
    assert body['id'] == case_id
    assert body['status'] == 'OPEN'
    assert body['evidence_version'] == 1
    assert body['audit_history']
    assert body['audit_history'][0]['action'] == 'CASE_CREATED'
    assert any(e['evidence_type'] == 'GRAPH' for e in body['evidence'])

    listed = client.get('/api/v1/cases')
    assert listed.status_code == 200
    assert any(c['id'] == case_id for c in listed.json())

    one = client.get(f'/api/v1/cases/{case_id}')
    assert one.status_code == 200
    assert one.json()['case_id'] == case_id
    assert isinstance(one.json()['audit_history'], list)

    missing = client.post(f'/api/v1/cases/{case_id}/decision', json={'decision': 'CONFIRMED_FRAUD'})
    assert missing.status_code == 422

    ok = client.post(f'/api/v1/cases/{case_id}/decision', json=_decision())
    assert ok.status_code == 200, ok.text
    assert ok.json()['status'] == 'CONFIRMED_FRAUD'
    assert ok.json()['decisions'][0]['reviewer'] == 'rev-1'
    assert ok.json()['decisions'][0]['evidence_version'] == 1
    actions = [a['action'] for a in ok.json()['audit_history']]
    assert 'CASE_CREATED' in actions
    assert 'CASE_DECISION' in actions


def test_decision_records_complete_audit_history():
    case = create_case(transaction_id='T-9', title='audit', actor='creator')
    cid = case['case_id']
    record_decision(cid, **_decision(decision='INVESTIGATING'))
    record_decision(cid, **_decision(decision='PENDING_REVIEW'))
    record_decision(cid, **_decision(decision='NEED_MORE_EVIDENCE'))
    full = get_case(cid)
    assert len(full['decisions']) == 3
    assert full['audit_history'][0]['action'] == 'CASE_CREATED'
    assert full['audit_history'][-1]['to_status'] == 'NEED_MORE_EVIDENCE'
    assert full['status'] == 'NEED_MORE_EVIDENCE'


def test_stale_evidence_version_rejected():
    case = create_case(transaction_id='T-8', actor='s')
    try:
        record_decision(case['case_id'], **_decision(evidence_version=99))
        raised = False
    except InvalidCaseTransition:
        raised = True
    assert raised
