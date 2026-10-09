from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import (
    anomaly_discovery_agent,
    decision_agent,
    evidence_graph_agent,
    fraud_detection_agent,
    human_review_agent,
    investigation_agent,
    learning_retraining_agent,
    master_orchestrator,
    report_agent,
)
from .state import CaseState, WORKFLOW_ORDER

NODE_MAP = {
    'MasterOrchestratorAgent': master_orchestrator,
    'FraudDetectionAgent': fraud_detection_agent,
    'AnomalyDiscoveryAgent': anomaly_discovery_agent,
    'InvestigationAgent': investigation_agent,
    'EvidenceGraphAgent': evidence_graph_agent,
    'DecisionAgent': decision_agent,
    'HumanReviewAgent': human_review_agent,
    'LearningRetrainingAgent': learning_retraining_agent,
    'ReportAgent': report_agent,
}


def build_workflow():
    graph = StateGraph(CaseState)
    for name, fn in NODE_MAP.items():
        graph.add_node(name, fn)
    graph.add_edge(START, WORKFLOW_ORDER[0])
    for left, right in zip(WORKFLOW_ORDER, WORKFLOW_ORDER[1:]):
        graph.add_edge(left, right)
    graph.add_edge(WORKFLOW_ORDER[-1], END)
    return graph.compile()


_APP = None


def get_app():
    global _APP
    if _APP is None:
        _APP = build_workflow()
    return _APP


def run_case_workflow(transaction_id: str, case_id: str = '', analyst_decision: dict | None = None, actor_id: str = 'system') -> CaseState:
    app = get_app()
    initial: CaseState = {
        'case_id': case_id or '',
        'actor_id': actor_id,
        'transaction_id': transaction_id,
        'detection_signals': {},
        'anomaly_signals': {},
        'graph_evidence': {},
        'pattern_matches': [],
        'retrieved_history': {},
        'evidence_items': [],
        'decision_candidate': {},
        'analyst_decision': analyst_decision,
        'model_version': '',
        'feature_version': '',
        'policy_version': '',
        'pattern_version': '',
        'report_version': '',
        'audit_events': [],
        'unavailable': [],
    }
    return app.invoke(initial)
