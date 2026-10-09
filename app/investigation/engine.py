from __future__ import annotations

from .result import InvestigationResult, ToolResult
from .tools import (
    get_account_history,
    get_active_patterns,
    get_device_history,
    get_graph_subgraph,
    get_ip_history,
    get_location_history,
    get_merchant_history,
    get_previous_fraud_cases,
    get_sim_history,
    get_transaction,
)


def _merge_questions(*results: ToolResult) -> list[str]:
    seen: list[str] = []
    for res in results:
        for q in res.unanswered_questions:
            if q not in seen:
                seen.append(q)
    return seen


def _merge_evidence(*results: ToolResult) -> list[str]:
    seen: list[str] = []
    for res in results:
        for eid in res.evidence_ids:
            if eid not in seen:
                seen.append(eid)
    return seen


def investigate_transaction(transaction_id: str) -> InvestigationResult:
    """Deterministic tool sequence. Does not classify fraud or fill missing facts."""
    txn = get_transaction(transaction_id)
    findings = [txn.to_dict()]
    signals: list[dict] = []
    results = [txn]

    if txn.found and isinstance(txn.data, dict):
        data = txn.data
        hist_tools = [
            get_account_history(data.get('account_id')),
            get_device_history(data.get('imei')),
            get_sim_history(data.get('sim_id')),
            get_ip_history(data.get('ip')),
            get_merchant_history(data.get('merchant_id')),
            get_location_history(data.get('location')),
        ]
        for item in hist_tools:
            findings.append(item.to_dict())
            results.append(item)
        graph = get_graph_subgraph(str(data.get('id') or transaction_id))
        findings.append(graph.to_dict())
        results.append(graph)
        if graph.found and isinstance(graph.data, dict):
            gscore = graph.data.get('graph_risk_score')
            if gscore is not None:
                signals.append({
                    'source': 'graph',
                    'graph_risk_score': gscore,
                    'correlation_is_not_proof': True,
                    'classified_as_fraud': False,
                })
            shared = (graph.data.get('risk_signals') or {}).get('shared_identifiers') or []
            for sig in shared:
                signals.append({**sig, 'source': 'graph', 'correlation_is_not_proof': True})
        prior = get_previous_fraud_cases(
            transaction_id=str(data.get('id') or transaction_id),
            account_id=data.get('account_id'),
            customer_id=data.get('customer_id'),
            imei=data.get('imei'),
        )
        findings.append(prior.to_dict())
        results.append(prior)
        if prior.found and isinstance(prior.data, dict) and prior.data.get('count'):
            signals.append({
                'source': 'prior_cases',
                'confirmed_case_count': prior.data['count'],
                'correlation_is_not_proof': True,
            })
    else:
        prior = get_previous_fraud_cases(transaction_id=transaction_id)
        findings.append(prior.to_dict())
        results.append(prior)

    patterns = get_active_patterns()
    findings.append(patterns.to_dict())
    results.append(patterns)
    if patterns.found and isinstance(patterns.data, dict):
        for pat in patterns.data.get('patterns') or []:
            signals.append({
                'source': 'active_pattern',
                'pattern_id': pat.get('pattern_id'),
                'name': pat.get('name'),
                'correlation_is_not_proof': True,
            })

    questions = _merge_questions(*results)
    if not txn.found:
        questions.insert(0, f'cannot complete investigation without stored transaction {transaction_id}')

    return InvestigationResult(
        findings=findings,
        evidence_ids=_merge_evidence(*results),
        risk_signals=signals,
        unanswered_questions=questions,
        transaction_id=transaction_id,
        invented=False,
    )
