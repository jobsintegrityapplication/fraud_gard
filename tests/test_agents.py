from app.agents.graph import run_case_workflow
from app.agents.policy import EVIDENCE_UNAVAILABLE, FORBIDDEN_TOOLS, PolicyViolation, call_deterministic_tool, preserve_human_decision
from app.agents.state import WORKFLOW_ORDER
from app.ml.supervised.model import train_supervised
from app.data.generator import build_dataset


def test_forbidden_tools_are_blocked():
    for name in ('activate_pattern', 'promote_model', 'override_analyst_decision', 'invent_evidence'):
        assert name in FORBIDDEN_TOOLS
        try:
            call_deterministic_tool(name, lambda: 'nope')
            raised = False
        except PolicyViolation:
            raised = True
        assert raised


def test_human_decision_not_overridden():
    human = {'source': 'analyst', 'decision': 'NOT_FRAUD', 'reviewer': 'a'}
    assert preserve_human_decision(human, {'source': 'llm', 'decision': 'CONFIRMED_FRAUD'})['decision'] == 'NOT_FRAUD'


def test_missing_transaction_is_evidence_unavailable(client):
    state = run_case_workflow('TXN-MISSING')
    assert state['transaction_id'] == 'TXN-MISSING'
    assert state['detection_signals'].get('status') == EVIDENCE_UNAVAILABLE
    kinds = {u.get('kind') for u in state['unavailable']}
    assert 'transaction' in kinds or 'supervised_model' in kinds or 'supervised_score' in kinds
    agents = [e['agent'] for e in state['audit_events']]
    assert agents[:9] == list(WORKFLOW_ORDER)
    assert state['analyst_decision']['status'] == 'PENDING_REVIEW'
    assert state['decision_candidate'].get('is_final') is False
    assert state['report_version'].startswith('RPT-')


def test_workflow_uses_real_scores_when_trained(client):
    raw = build_dataset(180, seed=5, persist=False).processed
    art = train_supervised(raw, n_estimators=30, max_depth=3, random_state=5)
    gen = client.post('/api/v1/demo/generate?n=60')
    tid = gen.json()['sample_ids'][0]
    human = {'source': 'analyst', 'decision': 'NEED_MORE_EVIDENCE', 'reviewer': 'lead'}
    state = run_case_workflow(tid, analyst_decision=human)
    if state['detection_signals'].get('status') == 'OK':
        assert state['detection_signals']['model_version'] == art.meta['model_version']
        assert 0 <= state['detection_signals']['risk_score'] <= 1
    assert state['analyst_decision']['decision'] == 'NEED_MORE_EVIDENCE'
    assert 'LearningRetrainingAgent' in [e['agent'] for e in state['audit_events']]
    api = client.post('/api/v1/agents/workflow', json={'transaction_id': tid})
    assert api.status_code == 200
    body = api.json()
    for key in (
        'case_id', 'transaction_id', 'detection_signals', 'anomaly_signals', 'graph_evidence',
        'pattern_matches', 'retrieved_history', 'evidence_items', 'decision_candidate',
        'analyst_decision', 'model_version', 'feature_version', 'policy_version',
        'pattern_version', 'report_version', 'audit_events',
    ):
        assert key in body
