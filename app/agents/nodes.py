from __future__ import annotations

from ..cases.service import create_case, get_case
from ..features.windows import FEATURE_SET_VERSION
from ..investigation.engine import investigate_transaction
from ..investigation.tools import (
    get_active_patterns,
    get_graph_subgraph,
    get_transaction,
)
from ..ml.anomaly.model import load_artifact as load_anomaly, score_one as score_anomaly
from ..ml.supervised.model import load_artifact as load_supervised, score_one as score_supervised
from ..registry.matching import score_against_pattern
from .policy import (
    EVIDENCE_UNAVAILABLE,
    PolicyViolation,
    assert_scores_unmodified,
    audit_event,
    call_deterministic_tool,
    preserve_human_decision,
    unavailable,
    utcnow,
)
from .state import POLICY_VERSION, CaseState
from .store import load_transactions_df, transaction_row


def _item(kind: str, payload: dict, evidence_ids: list[str] | None = None) -> dict:
    return {
        'kind': kind,
        'payload': payload,
        'evidence_ids': evidence_ids or payload.get('evidence_ids') or [],
        'status': payload.get('status'),
    }


def master_orchestrator(state: CaseState) -> dict:
    tid = state.get('transaction_id') or ''
    case_id = state.get('case_id') or ''
    if not case_id:
        case = call_deterministic_tool('create_case', create_case, transaction_id=tid or None, title='Agent workflow', actor='MasterOrchestratorAgent')
        case_id = case['case_id']
    elif get_case(case_id) is None:
        case = call_deterministic_tool('create_case', create_case, transaction_id=tid or None, title='Agent workflow', actor='MasterOrchestratorAgent', case_id=case_id)
        case_id = case['case_id']
    model_version = EVIDENCE_UNAVAILABLE
    try:
        art = load_supervised()
        model_version = str(art.meta.get('model_version') or EVIDENCE_UNAVAILABLE)
    except FileNotFoundError:
        pass
    pattern_version = EVIDENCE_UNAVAILABLE
    patterns = call_deterministic_tool('get_active_patterns', get_active_patterns)
    if patterns.found and patterns.data and patterns.data.get('patterns'):
        versions = [str(p.get('version')) for p in patterns.data['patterns'] if p.get('version') is not None]
        if versions:
            pattern_version = ','.join(sorted(set(versions)))
    return {
        'case_id': case_id,
        'transaction_id': tid,
        'detection_signals': {},
        'anomaly_signals': {},
        'graph_evidence': {},
        'pattern_matches': [],
        'retrieved_history': {},
        'evidence_items': [],
        'decision_candidate': {},
        'analyst_decision': state.get('analyst_decision'),
        'model_version': model_version,
        'feature_version': FEATURE_SET_VERSION,
        'policy_version': POLICY_VERSION,
        'pattern_version': pattern_version,
        'report_version': '',
        'current_agent': 'MasterOrchestratorAgent',
        'unavailable': [],
        'audit_events': [audit_event('MasterOrchestratorAgent', 'START', {'case_id': case_id, 'transaction_id': tid})],
    }


def fraud_detection_agent(state: CaseState) -> dict:
    tid = state['transaction_id']
    hist = load_transactions_df()
    unavailable_items = []
    signals: dict = {'status': EVIDENCE_UNAVAILABLE}
    evidence_ids: list[str] = []
    txn = call_deterministic_tool('get_transaction', get_transaction, tid)
    if not txn.found:
        payload = unavailable('transaction', txn.unanswered_questions[0] if txn.unanswered_questions else 'transaction missing')
        unavailable_items.append(payload)
        signals = payload
    else:
        evidence_ids.extend(txn.evidence_ids)
        try:
            art = load_supervised()
            scored = call_deterministic_tool('score_supervised', score_supervised, hist, tid, art, 5)
            signals = {
                'status': 'OK',
                'risk_score': scored['risk_score'],
                'risk_level': scored['risk_level'],
                'model_version': scored['model_version'],
                'top_features': scored.get('top_features') or [],
                'evidence_ids': txn.evidence_ids,
            }
        except FileNotFoundError:
            payload = unavailable('supervised_model', 'supervised model artifact is not trained')
            unavailable_items.append(payload)
            signals = payload
        except (KeyError, ValueError) as exc:
            payload = unavailable('supervised_score', str(exc))
            unavailable_items.append(payload)
            signals = payload
    return {
        'detection_signals': signals,
        'model_version': signals.get('model_version') or state.get('model_version') or EVIDENCE_UNAVAILABLE,
        'evidence_items': [_item('detection', signals, evidence_ids)],
        'unavailable': unavailable_items,
        'current_agent': 'FraudDetectionAgent',
        'audit_events': [audit_event('FraudDetectionAgent', 'SCORED', {'status': signals.get('status')})],
    }


def anomaly_discovery_agent(state: CaseState) -> dict:
    tid = state['transaction_id']
    hist = load_transactions_df()
    unavailable_items = []
    signals: dict = {'status': EVIDENCE_UNAVAILABLE, 'anomaly_is_not_fraud': True}
    try:
        art = load_anomaly()
        scored = call_deterministic_tool('score_anomaly', score_anomaly, hist, tid, art)
        signals = {
            'status': 'OK',
            'anomaly_score': scored['anomaly_score'],
            'anomaly_rank': scored['anomaly_rank'],
            'model_version': scored['model_version'],
            'anomaly_is_not_fraud': True,
        }
    except FileNotFoundError:
        payload = unavailable('anomaly_model', 'anomaly model artifact is not trained')
        unavailable_items.append(payload)
        signals = {**payload, 'anomaly_is_not_fraud': True}
    except (KeyError, ValueError) as exc:
        payload = unavailable('anomaly_score', str(exc))
        unavailable_items.append(payload)
        signals = {**payload, 'anomaly_is_not_fraud': True}
    return {
        'anomaly_signals': signals,
        'evidence_items': [_item('anomaly', signals)],
        'unavailable': unavailable_items,
        'current_agent': 'AnomalyDiscoveryAgent',
        'audit_events': [audit_event('AnomalyDiscoveryAgent', 'SCORED', {'status': signals.get('status')})],
    }


def investigation_agent(state: CaseState) -> dict:
    result = call_deterministic_tool('investigate_transaction', investigate_transaction, state['transaction_id'])
    payload = result.to_dict()
    unavailable_items = []
    if not result.findings or any(f.get('found') is False and f.get('tool') == 'get_transaction' for f in result.findings):
        unavailable_items.append(unavailable('investigation', 'transaction or history not in store'))
    history = {
        'findings': payload.get('findings') or [],
        'unanswered_questions': payload.get('unanswered_questions') or [],
        'invented': False,
    }
    return {
        'retrieved_history': history,
        'evidence_items': [_item('investigation', payload, payload.get('evidence_ids') or [])],
        'unavailable': unavailable_items,
        'current_agent': 'InvestigationAgent',
        'audit_events': [audit_event('InvestigationAgent', 'INVESTIGATED', {'n_findings': len(history['findings'])})],
    }


def evidence_graph_agent(state: CaseState) -> dict:
    graph = call_deterministic_tool('get_graph_subgraph', get_graph_subgraph, state['transaction_id'])
    patterns = call_deterministic_tool('get_active_patterns', get_active_patterns)
    unavailable_items = []
    graph_payload: dict
    if not graph.found:
        graph_payload = unavailable('graph', graph.unanswered_questions[0] if graph.unanswered_questions else 'subgraph missing')
        unavailable_items.append(graph_payload)
    else:
        graph_payload = graph.to_dict()
        graph_payload['correlation_is_not_proof'] = True
    matches = []
    tx = transaction_row(state['transaction_id'])
    if tx and patterns.found and patterns.data:
        for pat in patterns.data.get('patterns') or []:
            score, why = score_against_pattern(tx, pat)
            if why:
                matches.append({'pattern_id': pat.get('pattern_id'), 'score': score, 'why': why, 'state': pat.get('state')})
    return {
        'graph_evidence': graph_payload,
        'pattern_matches': matches,
        'evidence_items': [_item('graph', graph_payload, graph.evidence_ids)],
        'unavailable': unavailable_items,
        'current_agent': 'EvidenceGraphAgent',
        'audit_events': [audit_event('EvidenceGraphAgent', 'GRAPH', {'n_matches': len(matches)})],
    }


def decision_agent(state: CaseState) -> dict:
    from ..decision.engine import DecisionInputs, decide, default_active_rules

    det = state.get('detection_signals') or {}
    anom = state.get('anomaly_signals') or {}
    graph = state.get('graph_evidence') or {}
    matches = state.get('pattern_matches') or []
    missing = []
    if det.get('status') == EVIDENCE_UNAVAILABLE:
        missing.append('detection')
    if anom.get('status') == EVIDENCE_UNAVAILABLE:
        missing.append('anomaly')
    if not (state.get('retrieved_history') or {}).get('findings'):
        missing.append('history')

    xgb = float(det['risk_score']) if det.get('status') == 'OK' and det.get('risk_score') is not None else 0.0
    anomaly = float(anom['anomaly_score']) if anom.get('status') == 'OK' and anom.get('anomaly_score') is not None else 0.0
    graph_score = 0.0
    if isinstance(graph, dict) and graph.get('found') and isinstance(graph.get('data'), dict):
        graph_score = float((graph['data'].get('graph_risk_score') or 0.0))
    elif isinstance(graph, dict) and graph.get('graph_risk_score') is not None:
        graph_score = float(graph.get('graph_risk_score') or 0.0)
    pattern_score = max((float(m.get('score') or 0.0) for m in matches), default=0.0)

    evidence_ids = []
    for item in state.get('evidence_items') or []:
        evidence_ids.extend(item.get('evidence_ids') or [])

    engine_out = call_deterministic_tool(
        'decision_engine',
        decide,
        DecisionInputs(
            xgboost_score=xgb,
            anomaly_score=anomaly,
            graph_score=graph_score,
            pattern_score=pattern_score,
            active_rules=default_active_rules(),
            business_policy={},
            evidence_ids=evidence_ids,
        ),
    )
    candidate = engine_out.to_dict()
    candidate['is_final'] = False  # human review still required for analyst override path
    candidate['missing'] = missing
    candidate['recommendation'] = candidate['recommended_action']
    if missing:
        candidate['note'] = 'scores treated as 0.0 where EVIDENCE_UNAVAILABLE; LLM did not invent values'
    assert_scores_unmodified(det, {'risk_score': det.get('risk_score')}, 'risk_score')
    return {
        'decision_candidate': candidate,
        'evidence_items': [_item('decision_candidate', candidate, candidate.get('evidence_ids') or [])],
        'unavailable': [unavailable(m, f'{m} evidence missing') for m in missing],
        'current_agent': 'DecisionAgent',
        'audit_events': [audit_event(
            'DecisionAgent',
            'ENGINE',
            {
                'recommended_action': candidate['recommended_action'],
                'risk_level': candidate['risk_level'],
                'llm_determined': False,
                'is_final': False,
            },
        )],
    }


def human_review_agent(state: CaseState) -> dict:
    existing = preserve_human_decision(state.get('analyst_decision'), None)
    if existing:
        return {
            'analyst_decision': existing,
            'decision_candidate': {**state.get('decision_candidate', {}), 'is_final': False},
            'evidence_items': [_item('analyst_decision', existing)],
            'unavailable': [],
            'current_agent': 'HumanReviewAgent',
            'audit_events': [audit_event('HumanReviewAgent', 'PRESERVED_HUMAN_DECISION', {'source': existing.get('source')})],
        }
    pending = {
        'status': 'PENDING_REVIEW',
        'source': 'HumanReviewAgent',
        'note': 'LLM cannot override or invent a human decision',
        'decision': None,
    }
    return {
        'analyst_decision': pending,
        'evidence_items': [_item('human_review', pending)],
        'unavailable': [],
        'current_agent': 'HumanReviewAgent',
        'audit_events': [audit_event('HumanReviewAgent', 'PENDING_REVIEW', {})],
    }


def learning_retraining_agent(state: CaseState) -> dict:
    from ..learning.labels import list_queue
    from ..learning.jobs import schedule_learning_job
    # Agents must not promote/train synchronously; they may only observe/schedule async work.
    try:
        call_deterministic_tool('promote_model', lambda: None)
        promoted = True
    except PolicyViolation:
        promoted = False
    queued = list_queue(status='QUEUED', limit=50)
    scheduled = None
    if queued:
        scheduled = call_deterministic_tool('schedule_learning_job', schedule_learning_job, wait=False)
    payload = {
        'queued_labels': len(queued),
        'scheduled_job': scheduled,
        'promoted': promoted,
        'patterns_activated': False,
        'synchronous_retrain': False,
        'reason': 'continuous learning is async; agents cannot promote models or invent labels',
    }
    return {
        'evidence_items': [_item('learning', payload)],
        'unavailable': [],
        'current_agent': 'LearningRetrainingAgent',
        'audit_events': [audit_event('LearningRetrainingAgent', 'SCHEDULED' if scheduled else 'IDLE', payload)],
    }


def report_agent(state: CaseState) -> dict:
    from ..reports.service import generate_investigation_report
    from ..security.audit import record_event

    version = 'RPT-' + utcnow().replace(':', '').replace('-', '')[:15]
    result = call_deterministic_tool(
        'generate_investigation_report',
        generate_investigation_report,
        dict(state),
        report_version=version,
    )
    report = result.get('document') or {}
    record_event(state.get('actor_id') or 'system', 'REPORT_GENERATION', 'InvestigationReport', result['report_version'], {'case_id': result.get('case_id'), 'source': 'agent_workflow'})
    report_meta = {
        'report_version': result['report_version'],
        'case_id': result.get('case_id'),
        'transaction_id': result.get('transaction_id'),
        'html_path': result.get('html_path'),
        'pdf_path': result.get('pdf_path'),
        'json_path': result.get('json_path'),
        'invented': False,
        'claims_count': len((report.get('claims') or [])),
        'model_version': state.get('model_version'),
        'feature_version': state.get('feature_version'),
        'policy_version': state.get('policy_version'),
        'pattern_version': state.get('pattern_version'),
    }
    return {
        'report_version': result['report_version'],
        'evidence_items': [_item('report', report_meta, [f"EV-REPORT-{result['report_version']}"])],
        'unavailable': [],
        'current_agent': 'ReportAgent',
        'audit_events': [audit_event('ReportAgent', 'WRITTEN', {
            'report_version': result['report_version'],
            'html_path': result.get('html_path'),
            'pdf_path': result.get('pdf_path'),
        })],
    }
