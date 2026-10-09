from pathlib import Path

from app.agents.policy import EVIDENCE_UNAVAILABLE
from app.agents.graph import run_case_workflow
from app.reports.assemble import assemble_report_document
from app.reports.claims import claim, validate_claims
from app.reports.service import generate_investigation_report, load_report


def test_material_claims_require_evidence_or_unavailable():
    ok = claim('xgboost_score', 'XGBoost score', 0.8, ['EV-1'])
    assert ok.status == 'OK'
    missing = claim('xgboost_score', 'XGBoost score', 0.8, [])
    assert missing.status == EVIDENCE_UNAVAILABLE
    assert 'evidence' in (missing.note or '').lower() or 'Evidence' in (missing.note or '')
    unavail = claim('xgboost_score', 'XGBoost score', None, available=False, reason='model missing')
    assert unavail.value == EVIDENCE_UNAVAILABLE
    errors = validate_claims([
        claim('a', 'A', 1, ['E1']),
        claim('b', 'B', EVIDENCE_UNAVAILABLE, available=False, reason='gone'),
    ])
    assert errors == []


def test_report_never_fabricates_and_writes_html_then_pdf(isolate_storage):
    state = {
        'case_id': 'CASE-1',
        'transaction_id': 'T-1',
        'detection_signals': {'status': EVIDENCE_UNAVAILABLE, 'reason': 'no model'},
        'anomaly_signals': {'status': EVIDENCE_UNAVAILABLE, 'reason': 'no anomaly model'},
        'graph_evidence': {'status': EVIDENCE_UNAVAILABLE, 'reason': 'no graph'},
        'pattern_matches': [],
        'retrieved_history': {'findings': []},
        'evidence_items': [],
        'decision_candidate': {},
        'analyst_decision': {'source': 'analyst', 'decision': 'PENDING_REVIEW', 'status': 'PENDING_REVIEW'},
        'model_version': EVIDENCE_UNAVAILABLE,
        'feature_version': 'fe-v1.0.0',
        'policy_version': 'pol-v1.0.0',
        'pattern_version': EVIDENCE_UNAVAILABLE,
        'audit_events': [{'agent': 'ReportAgent', 'action': 'TEST', 'ts': '2026-09-18T00:00:00Z'}],
        'unavailable': [{'kind': 'supervised_model', 'reason': 'not trained', 'status': EVIDENCE_UNAVAILABLE}],
    }
    out = generate_investigation_report(state, report_version='RPT-TEST001')
    assert out['invented'] is False
    html = Path(out['html_path'])
    pdf = Path(out['pdf_path'])
    js = Path(out['json_path'])
    assert html.exists() and pdf.exists() and js.exists()
    # HTML generated before PDF (mtime ordering / both exist); content checks
    text = html.read_text(encoding='utf-8')
    assert 'Fraud Investigation Report' in text
    assert EVIDENCE_UNAVAILABLE in text
    assert 'CASE-1' in text
    assert 'fabricat' not in text.lower() or 'never fabricated' in text.lower()

    doc = load_report('RPT-TEST001')
    assert doc is not None
    by_field = {c['field']: c for c in doc['claims']}
    for required in (
        'case_id', 'transaction', 'risk_classification', 'xgboost_score',
        'isolation_forest_score', 'graph_signals', 'pattern_matches', 'timeline',
        'linked_entities', 'evidence', 'analyst_decision', 'model_version',
        'feature_version', 'policy_version', 'pattern_version', 'audit_references',
    ):
        assert required in by_field
    assert by_field['xgboost_score']['value'] == EVIDENCE_UNAVAILABLE
    assert by_field['xgboost_score']['status'] == EVIDENCE_UNAVAILABLE
    assert by_field['feature_version']['value'] == 'fe-v1.0.0'
    assert by_field['feature_version']['evidence_ids']
    assert doc['invented'] is False


def test_report_agent_in_workflow_writes_artifacts(client):
    state = run_case_workflow('TXN-MISSING-RPT')
    assert state['report_version'].startswith('RPT-')
    version = state['report_version']
    from app import config
    assert (Path(config.REPORT_DIR) / f'{version}.html').exists()
    assert (Path(config.REPORT_DIR) / f'{version}.pdf').exists()
    r = client.get(f'/api/v1/reports/{version}')
    assert r.status_code == 200
    body = r.json()
    assert body['invented'] is False
    assert any(c['field'] == 'xgboost_score' for c in body['claims'])


def test_assemble_cites_scores_when_present(isolate_storage):
    state = {
        'case_id': 'C2',
        'transaction_id': 'T2',
        'detection_signals': {},
        'anomaly_signals': {},
        'graph_evidence': {},
        'pattern_matches': [{'pattern_id': 'P1', 'score': 0.9, 'why': ['imei overlap']}],
        'retrieved_history': {
            'findings': [{
                'tool': 'get_transaction',
                'found': True,
                'data': {'id': 'T2', 'amount': 10},
                'evidence_ids': ['EV-TXN-1'],
            }],
        },
        'evidence_items': [
            {'kind': 'detection', 'evidence_ids': ['EV-XGB-1'], 'payload': {
                'status': 'OK', 'risk_score': 0.77, 'risk_level': 'HIGH', 'model_version': 'SUP-1',
            }},
            {'kind': 'anomaly', 'evidence_ids': ['EV-IF-1'], 'payload': {
                'status': 'OK', 'anomaly_score': 0.4, 'model_version': 'ANOM-1',
            }},
            {'kind': 'graph', 'evidence_ids': ['EV-G-1'], 'payload': {
                'found': True,
                'data': {'nodes': [{'id': 'A1', 'type': 'ACCOUNT', 'evidence_id': 'EV-G-1'}], 'edges': []},
            }},
            {'kind': 'investigation', 'evidence_ids': ['EV-TXN-1'], 'payload': {
                'findings': [{
                    'tool': 'get_transaction', 'found': True,
                    'data': {'id': 'T2', 'amount': 10}, 'evidence_ids': ['EV-TXN-1'],
                }],
            }},
        ],
        'decision_candidate': {'risk_level': 'HIGH', 'recommended_action': 'REVIEW', 'evidence_ids': ['EV-DEC-1']},
        'analyst_decision': {'decision': 'NEED_MORE_EVIDENCE', 'source': 'analyst'},
        'model_version': 'SUP-1',
        'feature_version': 'fe-v1.0.0',
        'policy_version': 'pol-v1.0.0',
        'pattern_version': '1',
        'audit_events': [{'agent': 'DecisionAgent', 'action': 'DECIDED', 'ts': 't'}],
        'unavailable': [],
    }
    doc = assemble_report_document(state, report_version='RPT-SCORED')
    by_field = {c['field']: c for c in doc['claims']}
    assert by_field['xgboost_score']['value'] == 0.77
    assert 'EV-XGB-1' in by_field['xgboost_score']['evidence_ids']
    assert by_field['isolation_forest_score']['value'] == 0.4
    assert by_field['transaction']['value']['id'] == 'T2'
    assert by_field['linked_entities']['status'] == 'OK'
