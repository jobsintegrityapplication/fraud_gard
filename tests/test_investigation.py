from datetime import datetime, timezone

from app.investigation.engine import investigate_transaction
from app.investigation.tools import (
    get_account_history,
    get_active_patterns,
    get_transaction,
)
from app.registry.service import upsert_pattern


def test_missing_transaction_is_not_invented():
    result = get_transaction('TXN-DOES-NOT-EXIST')
    assert result.found is False
    assert result.data is None
    assert result.invented is False
    assert result.evidence_ids == []
    assert result.unanswered_questions
    assert 'TXN-DOES-NOT-EXIST' in result.unanswered_questions[0]


def test_blank_account_history_is_not_invented():
    result = get_account_history(None)
    assert result.found is False
    assert result.data is None
    assert result.invented is False


def test_tools_return_structured_evidence_ids(client):
    gen = client.post('/api/v1/demo/generate?n=40')
    assert gen.status_code == 200
    tid = gen.json()['sample_ids'][0]
    txn = get_transaction(tid)
    assert txn.found is True
    assert txn.data['id'] == tid
    assert txn.evidence_ids
    assert all(e.startswith('EVI-get_transaction-') for e in txn.evidence_ids)
    hist = get_account_history(txn.data.get('account_id'))
    assert hist.found is True
    assert 'records' in hist.data
    assert hist.evidence_ids
    again = get_transaction(tid)
    assert again.evidence_ids == txn.evidence_ids


def test_active_patterns_only_returns_active():
    upsert_pattern(pattern_id='FP-DISC', name='n', state='DISCOVERED', actor='t', audit_action=None)
    upsert_pattern(pattern_id='FP-ACT', name='a', state='ACTIVE', actor='t', audit_action=None)
    result = get_active_patterns()
    ids = [p['pattern_id'] for p in result.data['patterns']]
    assert 'FP-ACT' in ids
    assert 'FP-DISC' not in ids
    assert result.evidence_ids


def test_investigation_result_shape_and_gaps(client):
    missing = investigate_transaction('NO-SUCH')
    payload = missing.to_dict()
    for key in ('findings', 'evidence_ids', 'risk_signals', 'unanswered_questions'):
        assert key in payload
    assert payload['invented'] is False
    assert payload['unanswered_questions']
    assert all(f.get('invented') is False for f in payload['findings'])

    gen = client.post('/api/v1/demo/generate?n=30')
    tid = gen.json()['sample_ids'][0]
    done = investigate_transaction(tid)
    assert done.invented is False
    tools = [f['tool'] for f in done.findings]
    assert 'get_transaction' in tools
    assert 'get_graph_subgraph' in tools
    assert 'get_active_patterns' in tools
    assert done.evidence_ids
    api = client.post('/api/v1/investigation', json={'transaction_id': tid})
    assert api.status_code == 200, api.text
    body = api.json()
    assert body['evidence_ids'] == done.evidence_ids
    assert 'findings' in body and 'risk_signals' in body and 'unanswered_questions' in body
