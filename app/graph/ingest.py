from __future__ import annotations

import pandas as pd

from .backend import GraphBackend
from .factory import create_backend


def _val(row, *keys):
    for key in keys:
        if isinstance(row, dict) and key in row and row[key] is not None:
            text = str(row[key]).strip()
            if text and text.lower() not in {'nan', 'none'}:
                return text
        elif hasattr(row, key):
            value = getattr(row, key)
            if value is not None and str(value).strip() and str(value).lower() not in {'nan', 'none'}:
                return str(value).strip()
    return ''


def _is_fraud_row(row) -> bool:
    label = row.get('label') if isinstance(row, dict) else None
    try:
        return int(label) == 1
    except (TypeError, ValueError):
        return False


def ingest_frame(df: pd.DataFrame | None, cases: list[dict] | None = None, backend: GraphBackend | None = None) -> GraphBackend:
    store = backend or create_backend()
    if df is None or len(df) == 0:
        return store
    work = df.copy()
    for idx, rec in enumerate(work.to_dict('records')):
        txn_id = _val(rec, 'transaction_id', 'id') or f'ROW-{idx}'
        customer = _val(rec, 'customer_id')
        account = _val(rec, 'account_id')
        sim = _val(rec, 'sim_id')
        imei = _val(rec, 'imei')
        ip = _val(rec, 'ip')
        merchant = _val(rec, 'merchant_id')
        location = _val(rec, 'location', 'location_code')
        n_customer = store.upsert_node('Customer', customer) if customer else ''
        n_account = store.upsert_node('Account', account) if account else ''
        n_sim = store.upsert_node('SIM', sim) if sim else ''
        n_imei = store.upsert_node('IMEI', imei) if imei else ''
        n_ip = store.upsert_node('IP', ip) if ip else ''
        n_merch = store.upsert_node('Merchant', merchant) if merchant else ''
        n_loc = store.upsert_node('Location', location) if location else ''
        n_txn = store.upsert_node('Transaction', txn_id, {'amount': rec.get('amount'), 'ts': str(rec.get('ts') or '')}) if txn_id else ''
        store.upsert_edge(n_customer, 'OWNS', n_account)
        store.upsert_edge(n_account, 'USES', n_sim)
        store.upsert_edge(n_account, 'USES', n_imei)
        store.upsert_edge(n_account, 'PURCHASED', n_txn)
        store.upsert_edge(n_txn, 'USES', n_imei)
        store.upsert_edge(n_txn, 'USES', n_sim)
        store.upsert_edge(n_txn, 'CONNECTED_FROM', n_ip)
        store.upsert_edge(n_txn, 'VISITED', n_loc)
        store.upsert_edge(n_txn, 'PAID_TO', n_merch)
        if _is_fraud_row(rec):
            case_id = f'GT-{txn_id or account or imei}'
            n_case = store.upsert_node('FraudCase', case_id, {'status': 'ALLEGED', 'source': 'transaction_label'})
            for node in (n_account, n_imei, n_sim, n_ip, n_customer):
                store.upsert_edge(node, 'LINKED_TO_FRAUD', n_case, {'proof': False})
    for case in cases or []:
        cid = str(case.get('id') or case.get('case_id') or '')
        if not cid:
            continue
        n_case = store.upsert_node('FraudCase', cid, {
            'status': case.get('decision') or case.get('status') or 'OPEN',
            'source': 'case_registry',
        })
        txn_id = str(case.get('transaction_id') or '')
        n_txn = store.upsert_node('Transaction', txn_id) if txn_id else ''
        confirmed = str(case.get('decision') or '') == 'CONFIRMED_FRAUD'
        store.upsert_edge(n_txn, 'LINKED_TO_FRAUD', n_case, {'proof': confirmed, 'status': case.get('decision')})
        if n_txn:
            for edge, nbr in store.neighbors(n_txn):
                if nbr.type in {'Account', 'IMEI', 'SIM', 'IP', 'Customer'}:
                    store.upsert_edge(nbr.id, 'LINKED_TO_FRAUD', n_case, {'proof': confirmed})
    return store
