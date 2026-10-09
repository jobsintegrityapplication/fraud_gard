from __future__ import annotations

from collections import defaultdict
import hashlib

import networkx as nx

from .backend import GraphBackend
from .ingest import ingest_frame
from .schema import DISCLAIMER, GraphSnapshot, split_key


def _evidence_id(kind: str, value: str) -> str:
    digest = hashlib.sha256(f'{kind}:{value}'.encode('utf-8')).hexdigest()[:12].upper()
    return f'EVG-{kind}-{digest}'


def _undirected(snapshot: GraphSnapshot) -> nx.Graph:
    g = nx.Graph()
    for node in snapshot.nodes:
        g.add_node(node.id, type=node.type, **node.properties)
    for edge in snapshot.edges:
        if g.has_edge(edge.source, edge.target):
            rels = g.edges[edge.source, edge.target].setdefault('rel_types', set())
            rels.add(edge.type)
        else:
            g.add_edge(edge.source, edge.target, rel_types={edge.type})
    return g


def _linked_entities(backend: GraphBackend, ident_node: str) -> tuple[set[str], set[str]]:
    accounts: set[str] = set()
    transactions: set[str] = set()
    for edge, nbr in backend.neighbors(ident_node, direction='in'):
        if nbr.type == 'Account':
            accounts.add(nbr.id)
        elif nbr.type == 'Transaction':
            transactions.add(nbr.id)
    for edge, nbr in backend.neighbors(ident_node, direction='out'):
        if nbr.type == 'Account':
            accounts.add(nbr.id)
        elif nbr.type == 'Transaction':
            transactions.add(nbr.id)
    if not accounts:
        for tid in list(transactions):
            for edge, nbr in backend.neighbors(tid):
                if nbr.type == 'Account':
                    accounts.add(nbr.id)
    return accounts, transactions


def detect_shared_identifiers(backend: GraphBackend) -> list[dict]:
    signals = []
    snap = backend.snapshot()
    by_type = defaultdict(list)
    for node in snap.nodes:
        by_type[node.type].append(node)
    specs = (
        ('IMEI', 'shared_imei', 2),
        ('SIM', 'shared_sim', 2),
        ('IP', 'shared_ip', 2),
    )
    for node_type, kind, min_accounts in specs:
        for node in by_type.get(node_type, []):
            accounts, transactions = _linked_entities(backend, node.id)
            txn_count = len(transactions)
            fanout = len(accounts) if accounts else txn_count
            threshold = min_accounts if accounts else {'shared_imei': 8, 'shared_sim': 6, 'shared_ip': 12}[kind]
            if fanout >= threshold:
                signals.append({
                    'kind': kind,
                    'node_id': node.id,
                    'account_count': len(accounts),
                    'transaction_count': txn_count if txn_count else fanout,
                    'evidence_id': _evidence_id(kind, node.id),
                    'not_proof_of_fraud': True,
                })
    return signals


def account_device_relationships(backend: GraphBackend, node_id: str) -> list[dict]:
    rels = []
    for edge, nbr in backend.neighbors(node_id):
        if edge.type == 'USES' or nbr.type in {'IMEI', 'SIM', 'Account'}:
            rels.append({
                'relationship': edge.type,
                'source': edge.source,
                'target': edge.target,
                'neighbor_type': nbr.type,
            })
    return rels


def fraud_neighbor_count(backend: GraphBackend, node_id: str) -> dict:
    fraud_nodes = set()
    for edge, nbr in backend.neighbors(node_id, rel_type='LINKED_TO_FRAUD'):
        if nbr.type == 'FraudCase' or edge.type == 'LINKED_TO_FRAUD':
            fraud_nodes.add(nbr.id if nbr.type == 'FraudCase' else edge.target)
    for edge, nbr in backend.neighbors(node_id):
        for e2, n2 in backend.neighbors(nbr.id, rel_type='LINKED_TO_FRAUD'):
            if n2.type == 'FraudCase':
                fraud_nodes.add(n2.id)
    return {
        'count': len(fraud_nodes),
        'fraud_case_ids': sorted(fraud_nodes),
        'not_proof_of_fraud': True,
    }


def detect_communities(snapshot: GraphSnapshot) -> list[dict]:
    g = _undirected(snapshot)
    if g.number_of_nodes() < 3:
        return []
    try:
        parts = nx.community.greedy_modularity_communities(g)
    except Exception:
        return []
    communities = []
    for i, members in enumerate(parts):
        types = [split_key(m)[0] for m in members]
        communities.append({
            'community_id': i,
            'size': len(members),
            'member_ids': sorted(members),
            'has_fraud_case': any(t == 'FraudCase' for t in types),
            'not_proof_of_fraud': True,
        })
    return communities


def suspicious_components(snapshot: GraphSnapshot, shared_signals: list[dict]) -> list[dict]:
    g = _undirected(snapshot)
    shared_nodes = {s['node_id'] for s in shared_signals}
    out = []
    if g.number_of_nodes() == 0:
        return out
    for i, comp in enumerate(nx.connected_components(g)):
        types = [split_key(n)[0] for n in comp]
        has_fraud = 'FraudCase' in types
        has_shared = bool(comp & shared_nodes)
        n_accounts = types.count('Account')
        if not (has_fraud or has_shared) or len(comp) < 3:
            continue
        out.append({
            'component_id': i,
            'size': len(comp),
            'account_count': n_accounts,
            'has_fraud_case': has_fraud,
            'has_shared_identifier': has_shared,
            'member_ids': sorted(comp)[:50],
            'not_proof_of_fraud': True,
        })
    return out


def graph_risk_score(shared: list[dict], fraud_neighbors: dict, components: list[dict]) -> float:
    score = 0.0
    for sig in shared:
        kind_w = {'shared_imei': 0.35, 'shared_sim': 0.25, 'shared_ip': 0.2}.get(sig['kind'], 0.1)
        fan = max(sig.get('account_count') or 0, sig.get('transaction_count') or 0)
        score += min(kind_w, kind_w * fan / 12.0)
    score += min(0.3, 0.08 * int(fraud_neighbors.get('count') or 0))
    if components:
        score += min(0.2, 0.05 * len(components))
    return float(min(1.0, score))


def analyze_entity(backend: GraphBackend, entity_id: str, depth: int = 2) -> dict | None:
    node = backend.resolve(entity_id)
    if node is None:
        return None
    snap = backend.subgraph(node.id, depth=max(1, min(depth, 4)))
    neigh_ids = {n.id for n in snap.nodes}
    shared = [s for s in detect_shared_identifiers(backend) if s['node_id'] in neigh_ids]
    devices = account_device_relationships(backend, node.id)
    fraud_n = fraud_neighbor_count(backend, node.id)
    communities = detect_communities(snap)
    components = suspicious_components(snap, shared)
    score = graph_risk_score(shared, fraud_n, components)
    evidence_ids = [s['evidence_id'] for s in shared]
    evidence_ids.extend(f"EVG-FRAUDNBR-{hashlib.sha256(cid.encode()).hexdigest()[:8].upper()}" for cid in fraud_n['fraud_case_ids'][:8])
    return {
        'entity_id': node.id,
        'entity_type': node.type,
        'nodes': [n.to_dict() for n in snap.nodes],
        'edges': [e.to_dict() for e in snap.edges],
        'risk_signals': {
            'shared_identifiers': shared,
            'account_device_relationships': devices,
            'fraud_neighbor_count': fraud_n,
            'suspicious_components': components,
            'graph_risk_score': score,
            'classified_as_fraud': False,
            'correlation_is_not_proof': True,
        },
        'communities': communities,
        'evidence_ids': evidence_ids,
        'graph_risk_score': score,
        'correlation_is_not_proof': True,
        'disclaimer': DISCLAIMER,
        'backend': backend.name,
    }


def score_transaction(tx: dict, df) -> tuple[float, list[str]]:
    """Compatibility scorer for the fusion API. Returns a risk signal, not a fraud label."""
    backend = ingest_frame(df)
    entity = tx.get('imei') or tx.get('account_id') or tx.get('transaction_id') or tx.get('id')
    result = analyze_entity(backend, str(entity), depth=2) if entity else None
    if not result:
        return 0.0, []
    score = float(result['graph_risk_score'])
    evidence = []
    for sig in result['risk_signals']['shared_identifiers']:
        evidence.append(f"{sig['kind']}: linkage among {sig.get('account_count') or sig.get('transaction_count')} entities (not proof of fraud)")
    if result['risk_signals']['fraud_neighbor_count']['count']:
        evidence.append(f"fraud_neighbors: {result['risk_signals']['fraud_neighbor_count']['count']} (alleged/confirmed cases, not proof)")
    if not evidence and score > 0:
        evidence.append('graph_risk_signal (correlation only)')
    return score, evidence
