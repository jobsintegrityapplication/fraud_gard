from __future__ import annotations

from typing import Annotated, Any, NotRequired, TypedDict
import operator


class CaseState(TypedDict):
    case_id: str
    transaction_id: str
    detection_signals: dict[str, Any]
    anomaly_signals: dict[str, Any]
    graph_evidence: dict[str, Any]
    pattern_matches: list[dict[str, Any]]
    retrieved_history: dict[str, Any]
    evidence_items: Annotated[list[dict[str, Any]], operator.add]
    decision_candidate: dict[str, Any]
    analyst_decision: dict[str, Any] | None
    model_version: str
    feature_version: str
    policy_version: str
    pattern_version: str
    report_version: str
    audit_events: Annotated[list[dict[str, Any]], operator.add]
    unavailable: Annotated[list[dict[str, Any]], operator.add]
    current_agent: NotRequired[str]
    actor_id: NotRequired[str]


POLICY_VERSION = 'pol-v1.0.0'
WORKFLOW_ORDER = (
    'MasterOrchestratorAgent',
    'FraudDetectionAgent',
    'AnomalyDiscoveryAgent',
    'InvestigationAgent',
    'EvidenceGraphAgent',
    'DecisionAgent',
    'HumanReviewAgent',
    'LearningRetrainingAgent',
    'ReportAgent',
)
