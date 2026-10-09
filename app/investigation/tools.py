from __future__ import annotations

import hashlib
from typing import Any

from ..db import TXN_COLUMNS, conn
from .result import ToolResult

# Present stored values only. Never coerce missing identifiers into placeholders.
_NULL = {None, '', 'nan', 'None', 'NULL'}


def evidence_id(tool: str, *parts: Any) -> str:
    payload = tool + '|' + '|'.join('' if p is None else str(p) for p in parts)
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12].upper()
    return f'EVI-{tool}-{digest}'


def _present(value) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return text not in _NULL


def _clean_row(row: dict, columns: list[str] | None = None) -> dict:
    keys = columns or list(row.keys())
    out = {}
    missing = []
    for key in keys:
        if key not in row:
            missing.append(key)
            continue
        val = row[key]
        if not _present(val):
            missing.append(key)
            continue
        out[key] = val
    return out


def _missing(row: dict, columns: list[str]) -> list[str]:
    return [c for c in columns if c not in row or not _present(row.get(c))]


def _fetchall(sql: str, params: tuple = ()) -> list[dict]:
    c = conn()
    rows = c.execute(sql, params).fetchall()
    c.close()
    return [dict(r) for r in rows]


def _fetchone(sql: str, params: tuple = ()) -> dict | None:
    c = conn()
    row = c.execute(sql, params).fetchone()
    c.close()
    return dict(row) if row else None


def _history(column: str, value: str, limit: int = 100) -> list[dict]:
    if column not in TXN_COLUMNS and column != 'id':
        return []
    rows = _fetchall(
        f'SELECT * FROM transactions WHERE {column} = ? ORDER BY ts ASC, id ASC LIMIT ?',
        (value, int(limit)),
    )
    return [_clean_row(r, TXN_COLUMNS) for r in rows]


def _empty(tool: str, question: str) -> ToolResult:
    return ToolResult(
        tool=tool,
        found=False,
        data=None,
        evidence_ids=[],
        unanswered_questions=[question],
        invented=False,
    )


def get_transaction(transaction_id: str | None) -> ToolResult:
    tool = 'get_transaction'
    if not _present(transaction_id):
        return _empty(tool, 'transaction_id was not provided')
    row = _fetchone('SELECT * FROM transactions WHERE id = ?', (str(transaction_id),))
    if row is None:
        return _empty(tool, f'transaction {transaction_id} is not in the store')
    data = _clean_row(row, TXN_COLUMNS)
    missing = _missing(row, TXN_COLUMNS)
    eid = evidence_id(tool, data.get('id'), data.get('ts'), data.get('amount'))
    return ToolResult(
        tool=tool,
        found=True,
        data=data,
        evidence_ids=[eid],
        missing_fields=missing,
        unanswered_questions=[f'transaction field {f} is not stored' for f in missing],
        invented=False,
    )


def _entity_history(tool: str, column: str, value: str | None, limit: int = 100) -> ToolResult:
    if not _present(value):
        return _empty(tool, f'{column} was not provided')
    records = _history(column, str(value), limit=limit)
    eid = evidence_id(tool, column, value, len(records))
    questions = []
    if not records:
        questions.append(f'no stored transactions for {column}={value}')
    return ToolResult(
        tool=tool,
        found=True,
        data={'key': column, 'value': str(value), 'count': len(records), 'records': records},
        evidence_ids=[eid],
        unanswered_questions=questions,
        invented=False,
    )


def get_account_history(account_id: str | None, limit: int = 100) -> ToolResult:
    return _entity_history('get_account_history', 'account_id', account_id, limit)


def get_device_history(imei: str | None, limit: int = 100) -> ToolResult:
    return _entity_history('get_device_history', 'imei', imei, limit)


def get_sim_history(sim_id: str | None, limit: int = 100) -> ToolResult:
    return _entity_history('get_sim_history', 'sim_id', sim_id, limit)


def get_ip_history(ip: str | None, limit: int = 100) -> ToolResult:
    return _entity_history('get_ip_history', 'ip', ip, limit)


def get_merchant_history(merchant_id: str | None, limit: int = 100) -> ToolResult:
    return _entity_history('get_merchant_history', 'merchant_id', merchant_id, limit)


def get_location_history(location: str | None, limit: int = 100) -> ToolResult:
    return _entity_history('get_location_history', 'location', location, limit)


def get_graph_subgraph(entity_id: str | None, depth: int = 2) -> ToolResult:
    tool = 'get_graph_subgraph'
    if not _present(entity_id):
        return _empty(tool, 'entity_id was not provided')
    from ..graph.analysis import analyze_entity
    from ..graph.ingest import ingest_frame
    import pandas as pd

    rows = _fetchall('SELECT * FROM transactions')
    cases = _fetchall('SELECT * FROM cases')
    if not rows:
        return _empty(tool, 'no transactions loaded; subgraph cannot be built')
    df = pd.DataFrame(rows)
    backend = ingest_frame(df, cases=cases)
    result = analyze_entity(backend, str(entity_id), depth=depth)
    if result is None:
        return _empty(tool, f'entity {entity_id} is not present in the graph')
    # Strip classification; keep correlational signals only
    data = {
        'entity_id': result.get('entity_id'),
        'entity_type': result.get('entity_type'),
        'nodes': result.get('nodes') or [],
        'edges': result.get('edges') or [],
        'communities': result.get('communities') or [],
        'correlation_is_not_proof': True,
        'classified_as_fraud': False,
        'graph_risk_score': result.get('graph_risk_score'),
        'risk_signals': result.get('risk_signals') or {},
    }
    eids = list(result.get('evidence_ids') or [])
    eids.append(evidence_id(tool, data['entity_id'], len(data['nodes']), len(data['edges'])))
    return ToolResult(tool=tool, found=True, data=data, evidence_ids=eids, invented=False)


def get_previous_fraud_cases(
    *,
    transaction_id: str | None = None,
    account_id: str | None = None,
    customer_id: str | None = None,
    imei: str | None = None,
) -> ToolResult:
    tool = 'get_previous_fraud_cases'
    filters = {
        'transaction_id': transaction_id,
        'account_id': account_id,
        'customer_id': customer_id,
        'imei': imei,
    }
    if not any(_present(v) for v in filters.values()):
        return _empty(tool, 'no entity identifiers were provided for prior-case lookup')

    cases = _fetchall(
        "SELECT * FROM cases WHERE status = 'CONFIRMED_FRAUD' OR decision = 'CONFIRMED_FRAUD' ORDER BY updated_at ASC, id ASC"
    )
    txn_ids = set()
    if _present(imei) or (_present(account_id) and not _present(transaction_id)):
        clause = []
        params: list = []
        if _present(imei):
            clause.append('imei = ?')
            params.append(imei)
        if _present(account_id):
            clause.append('account_id = ?')
            params.append(account_id)
        if _present(customer_id):
            clause.append('customer_id = ?')
            params.append(customer_id)
        matched = _fetchall(
            f"SELECT id FROM transactions WHERE {' OR '.join(clause)}",
            tuple(params),
        )
        txn_ids = {r['id'] for r in matched}

    matched_cases = []
    for case in cases:
        cid_txn = case.get('transaction_id')
        hit = False
        if _present(transaction_id) and cid_txn == transaction_id:
            hit = True
        if _present(account_id) and case.get('account_id') == account_id:
            hit = True
        if _present(customer_id) and case.get('customer_id') == customer_id:
            hit = True
        if cid_txn in txn_ids:
            hit = True
        if hit:
            matched_cases.append({
                'case_id': case.get('id'),
                'transaction_id': case.get('transaction_id'),
                'status': case.get('status'),
                'decision': case.get('decision'),
                'updated_at': case.get('updated_at'),
            })

    eid = evidence_id(tool, transaction_id, account_id, customer_id, imei, len(matched_cases))
    questions = []
    if not matched_cases:
        questions.append('no confirmed prior fraud cases stored for the provided identifiers')
    return ToolResult(
        tool=tool,
        found=True,
        data={'count': len(matched_cases), 'cases': matched_cases},
        evidence_ids=[eid],
        unanswered_questions=questions,
        invented=False,
    )


def get_active_patterns() -> ToolResult:
    tool = 'get_active_patterns'
    from ..registry.service import fireable_patterns
    patterns = fireable_patterns()
    slim = []
    for p in patterns:
        slim.append({
            'pattern_id': p.get('pattern_id'),
            'name': p.get('name'),
            'state': p.get('state'),
            'version': p.get('version'),
            'threshold': p.get('threshold'),
            'policy_action': p.get('policy_action'),
            'signature': p.get('signature') or {},
        })
    slim.sort(key=lambda x: str(x.get('pattern_id') or ''))
    eid = evidence_id(tool, len(slim), *(p['pattern_id'] for p in slim))
    questions = []
    if not slim:
        questions.append('no ACTIVE patterns are registered')
    return ToolResult(
        tool=tool,
        found=True,
        data={'count': len(slim), 'patterns': slim},
        evidence_ids=[eid],
        unanswered_questions=questions,
        invented=False,
    )


TOOL_REGISTRY = {
    'get_transaction': get_transaction,
    'get_account_history': get_account_history,
    'get_device_history': get_device_history,
    'get_sim_history': get_sim_history,
    'get_ip_history': get_ip_history,
    'get_merchant_history': get_merchant_history,
    'get_location_history': get_location_history,
    'get_graph_subgraph': get_graph_subgraph,
    'get_previous_fraud_cases': get_previous_fraud_cases,
    'get_active_patterns': get_active_patterns,
}


def invoke(name: str, **kwargs) -> ToolResult:
    if name not in TOOL_REGISTRY:
        return _empty(name, f'unknown tool {name}')
    return TOOL_REGISTRY[name](**kwargs)
