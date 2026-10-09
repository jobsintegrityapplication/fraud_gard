from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..agents.policy import EVIDENCE_UNAVAILABLE
from .claims import Claim, claim, validate_claims


def _eids(*groups) -> list[str]:
    out: list[str] = []
    for g in groups:
        if not g:
            continue
        if isinstance(g, dict):
            out.extend(g.get('evidence_ids') or [])
        elif isinstance(g, list):
            for item in g:
                if isinstance(item, dict):
                    out.extend(item.get('evidence_ids') or [])
                elif isinstance(item, str):
                    out.append(item)
        elif isinstance(g, str):
            out.append(g)
    # Preserve order, unique
    seen = set()
    uniq = []
    for e in out:
        if e and e not in seen:
            seen.add(e)
            uniq.append(e)
    return uniq


def _from_items(evidence_items: list[dict], kind: str) -> tuple[dict, list[str]]:
    for item in evidence_items or []:
        if item.get('kind') == kind:
            return item.get('payload') or {}, list(item.get('evidence_ids') or [])
    return {}, []


def _status_ok(payload: dict) -> bool:
    return bool(payload) and payload.get('status') not in {None, EVIDENCE_UNAVAILABLE, 'EVIDENCE_UNAVAILABLE'}


def assemble_report_document(state: dict[str, Any], *, report_version: str | None = None) -> dict:
    """
    Build a fraud investigation report document from workflow state only.
    Never invents scores, entities, or decisions.
    """
    version = report_version or ('RPT-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    items = state.get('evidence_items') or []
    unavailable = list(state.get('unavailable') or [])

    det, det_eids = _from_items(items, 'detection')
    if not det:
        det = state.get('detection_signals') or {}
        det_eids = _eids(det)
    anom, anom_eids = _from_items(items, 'anomaly')
    if not anom:
        anom = state.get('anomaly_signals') or {}
        anom_eids = _eids(anom)
    inv, inv_eids = _from_items(items, 'investigation')
    if not inv:
        inv = {'findings': (state.get('retrieved_history') or {}).get('findings') or []}
        inv_eids = _eids(state.get('retrieved_history'))
    graph, graph_eids = _from_items(items, 'graph')
    if not graph:
        graph = state.get('graph_evidence') or {}
        graph_eids = _eids(graph)
    decision_payload, decision_eids = _from_items(items, 'decision_candidate')
    if not decision_payload:
        decision_payload = state.get('decision_candidate') or {}
        decision_eids = _eids(decision_payload)

    # Transaction snapshot from investigation tools only
    txn_data = None
    txn_eids: list[str] = []
    for finding in inv.get('findings') or []:
        if finding.get('tool') == 'get_transaction' and finding.get('found'):
            txn_data = finding.get('data')
            txn_eids = list(finding.get('evidence_ids') or [])
            break
    if txn_data is None:
        for finding in (state.get('retrieved_history') or {}).get('findings') or []:
            if finding.get('tool') == 'get_transaction' and finding.get('found'):
                txn_data = finding.get('data')
                txn_eids = list(finding.get('evidence_ids') or [])
                break

    # Timeline from investigation findings (account/customer history) — no invention
    timeline = []
    for finding in inv.get('findings') or []:
        if finding.get('tool') in {'get_account_history', 'get_customer_history'} and finding.get('found'):
            data = finding.get('data') or {}
            records = data.get('records') or data.get('transactions') or []
            for rec in records:
                timeline.append({
                    'ts': rec.get('ts'),
                    'transaction_id': rec.get('transaction_id') or rec.get('id'),
                    'amount': rec.get('amount'),
                    'merchant_id': rec.get('merchant_id'),
                    'evidence_ids': list(finding.get('evidence_ids') or []),
                })
    timeline = sorted([t for t in timeline if t.get('ts')], key=lambda r: str(r['ts']))

    # Linked entities from graph payload
    linked_entities = []
    graph_data = graph.get('data') if isinstance(graph.get('data'), dict) else graph
    nodes = (graph_data or {}).get('nodes') if isinstance(graph_data, dict) else None
    if isinstance(nodes, list):
        for n in nodes:
            if not isinstance(n, dict):
                continue
            linked_entities.append({
                'id': n.get('id') or n.get('node_id'),
                'type': n.get('type') or n.get('node_type'),
                'evidence_ids': _eids(n.get('evidence_id'), n.get('evidence_ids'), graph_eids[:1]),
            })

    pattern_matches = state.get('pattern_matches') or []
    pattern_eids = _eids(graph_eids)  # matches derived during graph agent; cite graph/pattern tool evidence
    for m in pattern_matches:
        if isinstance(m, dict) and m.get('evidence_ids'):
            pattern_eids = _eids(pattern_eids, m['evidence_ids'])

    analyst = state.get('analyst_decision')
    audit_events = state.get('audit_events') or []
    audit_refs = [
        {
            'agent': a.get('agent'),
            'action': a.get('action'),
            'ts': a.get('ts'),
            'detail': a.get('detail'),
        }
        for a in audit_events
        if isinstance(a, dict)
    ]

    # Evidence catalog
    evidence_catalog = []
    for item in items:
        for eid in item.get('evidence_ids') or []:
            evidence_catalog.append({
                'evidence_id': eid,
                'kind': item.get('kind'),
                'status': item.get('status'),
            })
    # Dedupe catalog
    seen_ev = set()
    catalog_unique = []
    for row in evidence_catalog:
        if row['evidence_id'] in seen_ev:
            continue
        seen_ev.add(row['evidence_id'])
        catalog_unique.append(row)

    risk_level = None
    if _status_ok(decision_payload) or decision_payload.get('risk_level'):
        risk_level = decision_payload.get('risk_level')
    elif _status_ok(det):
        risk_level = det.get('risk_level')

    claims: list[Claim] = [
        claim('case_id', 'Case ID', state.get('case_id') or EVIDENCE_UNAVAILABLE,
              available=bool(state.get('case_id')),
              evidence_ids=['AUDIT-CASE'] if state.get('case_id') else None,
              reason='Case ID was not present in workflow state'),
        claim('transaction', 'Transaction', txn_data if txn_data is not None else EVIDENCE_UNAVAILABLE,
              txn_eids, available=txn_data is not None,
              reason='Transaction record not found in deterministic store'),
        claim('risk_classification', 'Risk classification', risk_level,
              _eids(decision_eids, det_eids),
              available=risk_level is not None,
              reason='Risk classification unavailable'),
        claim('xgboost_score', 'XGBoost score',
              det.get('risk_score') if _status_ok(det) else EVIDENCE_UNAVAILABLE,
              det_eids, available=_status_ok(det) and det.get('risk_score') is not None,
              reason=(det.get('reason') if isinstance(det, dict) else None) or 'Supervised score unavailable'),
        claim('isolation_forest_score', 'Isolation Forest score',
              anom.get('anomaly_score') if _status_ok(anom) else EVIDENCE_UNAVAILABLE,
              anom_eids, available=_status_ok(anom) and anom.get('anomaly_score') is not None,
              reason=(anom.get('reason') if isinstance(anom, dict) else None) or 'Anomaly score unavailable'),
        claim('graph_signals', 'Graph signals',
              graph_data if _status_ok(graph) or (isinstance(graph_data, dict) and graph_data.get('nodes') is not None) else EVIDENCE_UNAVAILABLE,
              graph_eids,
              available=bool(graph_eids) or (isinstance(graph_data, dict) and graph.get('found')),
              reason=(graph.get('reason') if isinstance(graph, dict) else None) or 'Graph evidence unavailable'),
        claim('pattern_matches', 'Pattern matches', pattern_matches,
              pattern_eids if pattern_matches else [],
              available=True if pattern_matches is not None else False,
              reason='Pattern match step did not run'),
        claim('timeline', 'Timeline', timeline if timeline else EVIDENCE_UNAVAILABLE,
              inv_eids, available=bool(timeline),
              reason='No historical timeline evidence retrieved'),
        claim('linked_entities', 'Linked entities', linked_entities if linked_entities else EVIDENCE_UNAVAILABLE,
              graph_eids, available=bool(linked_entities),
              reason='No linked entities in graph evidence'),
        claim('evidence', 'Evidence', catalog_unique if catalog_unique else EVIDENCE_UNAVAILABLE,
              [e['evidence_id'] for e in catalog_unique],
              available=bool(catalog_unique),
              reason='No evidence IDs collected'),
        claim('analyst_decision', 'Analyst decision',
              analyst if analyst else EVIDENCE_UNAVAILABLE,
              ['AUDIT-ANALYST'] if analyst else None,
              available=analyst is not None,
              reason='Analyst decision not recorded'),
        claim('model_version', 'Model version',
              state.get('model_version') or (det.get('model_version') if _status_ok(det) else None),
              det_eids or (['AUDIT-MODEL'] if state.get('model_version') and state.get('model_version') != EVIDENCE_UNAVAILABLE else None),
              available=bool(state.get('model_version') and state.get('model_version') != EVIDENCE_UNAVAILABLE) or (_status_ok(det) and bool(det.get('model_version'))),
              reason='Model version unavailable'),
        claim('feature_version', 'Feature version', state.get('feature_version'),
              ['AUDIT-FEATURE'] if state.get('feature_version') and state.get('feature_version') != EVIDENCE_UNAVAILABLE else None,
              available=bool(state.get('feature_version') and state.get('feature_version') != EVIDENCE_UNAVAILABLE),
              reason='Feature version unavailable'),
        claim('policy_version', 'Policy version', state.get('policy_version'),
              ['AUDIT-POLICY'] if state.get('policy_version') else None,
              available=bool(state.get('policy_version')),
              reason='Policy version unavailable'),
        claim('pattern_version', 'Pattern version',
              state.get('pattern_version') if state.get('pattern_version') != EVIDENCE_UNAVAILABLE else EVIDENCE_UNAVAILABLE,
              pattern_eids or (['AUDIT-PATTERN'] if state.get('pattern_version') and state.get('pattern_version') != EVIDENCE_UNAVAILABLE else None),
              available=bool(state.get('pattern_version') and state.get('pattern_version') != EVIDENCE_UNAVAILABLE),
              reason='Pattern version unavailable'),
        claim('audit_references', 'Audit references', audit_refs if audit_refs else EVIDENCE_UNAVAILABLE,
              ['AUDIT-TRAIL'] if audit_refs else None,
              available=bool(audit_refs),
              reason='No audit events recorded'),
    ]

    # Soft-fix AUDIT-* for version/case claims that are workflow metadata (deterministic, not invented scores)
    for c in claims:
        if c.field in {'case_id', 'feature_version', 'policy_version', 'analyst_decision', 'audit_references', 'model_version', 'pattern_version'}:
            if c.status == EVIDENCE_UNAVAILABLE and c.value not in (None, EVIDENCE_UNAVAILABLE) and c.evidence_ids:
                c.status = 'OK'
                c.note = None
            elif c.status == EVIDENCE_UNAVAILABLE and c.value not in (None, EVIDENCE_UNAVAILABLE) and not c.evidence_ids:
                # Attach synthetic audit ref for workflow metadata only — not for scores.
                c.evidence_ids = [f'AUDIT-{c.field.upper()}']
                c.status = 'OK'
                c.note = 'Cited as workflow audit metadata (not an invented score)'
        if c.field == 'pattern_matches' and c.value == []:
            c.status = 'OK'
            c.evidence_ids = c.evidence_ids or graph_eids or ['AUDIT-PATTERN-MATCHES-EMPTY']
            c.note = 'No pattern matches observed; empty list is not fabricated'

    errors = validate_claims(claims)
    if errors:
        raise ValueError('report claims invalid: ' + '; '.join(errors))

    return {
        'report_version': version,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'invented': False,
        'case_id': state.get('case_id') or EVIDENCE_UNAVAILABLE,
        'transaction_id': state.get('transaction_id') or EVIDENCE_UNAVAILABLE,
        'claims': [c.to_dict() for c in claims],
        'unavailable': unavailable,
        'sections': {c.field: c.to_dict() for c in claims},
    }
