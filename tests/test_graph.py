import pandas as pd

from app.graph.analysis import analyze_entity, detect_shared_identifiers
from app.graph.backend import GraphBackend
from app.graph.factory import create_backend
from app.graph.ingest import ingest_frame
from app.graph.schema import NODE_TYPES, REL_TYPES
from app.services.graph import graph_score
from app.services.patterns import discover
from app.services.synthetic import generate


def test_graph_score_shared_imei():
    df = pd.DataFrame([{'imei': 'I_FRAUD_RING_01', 'ip': 'x', 'sim_id': f'S{i}'} for i in range(10)])
    score, evidence = graph_score({'imei': 'I_FRAUD_RING_01', 'ip': 'x', 'sim_id': 'S0'}, df)
    assert score > 0
    assert any('shared_imei' in e for e in evidence)
    assert all('not proof' in e.lower() or 'not proof of fraud' in e.lower() or 'alleged' in e.lower() or 'correlation' in e.lower() for e in evidence)


def test_shared_sim_and_ip_and_device_links():
    rows = []
    for i in range(6):
        rows.append({
            'transaction_id': f'T{i}',
            'customer_id': f'C{i}',
            'account_id': f'A{i}',
            'sim_id': 'SIM-SHARED',
            'imei': f'IMEI-{i}',
            'ip': '203.0.113.10',
            'merchant_id': 'M1',
            'location': 'DEL',
            'amount': 10,
            'label': 0,
        })
    backend = ingest_frame(pd.DataFrame(rows))
    signals = {s['kind']: s for s in detect_shared_identifiers(backend)}
    assert 'shared_sim' in signals
    assert signals['shared_sim']['account_count'] >= 2
    assert signals['shared_sim']['not_proof_of_fraud'] is True
    assert 'shared_ip' in signals


def test_backend_interface_is_swappable():
    backend = create_backend('networkx')
    assert isinstance(backend, GraphBackend)
    assert backend.name == 'networkx'
    assert 'Customer' in NODE_TYPES and 'LINKED_TO_FRAUD' in REL_TYPES
    backend.upsert_node('Customer', 'C1')
    backend.upsert_node('Account', 'A1')
    backend.upsert_edge('Customer:C1', 'OWNS', 'Account:A1')
    snap = backend.subgraph('Customer:C1', depth=1)
    assert any(n.type == 'Account' for n in snap.nodes)
    assert any(e.type == 'OWNS' for e in snap.edges)


def test_graph_api_neighborhood(client):
    gen = client.post('/api/v1/demo/generate?n=80')
    assert gen.status_code == 200
    tid = gen.json()['sample_ids'][0]
    r = client.get(f'/api/v1/graph/{tid}')
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ('nodes', 'edges', 'risk_signals', 'communities', 'evidence_ids'):
        assert key in body
    assert body['correlation_is_not_proof'] is True
    assert body['risk_signals']['classified_as_fraud'] is False
    assert isinstance(body['nodes'], list)
    assert isinstance(body['edges'], list)


def test_correlation_is_not_classified_as_fraud():
    df = pd.DataFrame([
        {
            'transaction_id': f'T{i}', 'account_id': f'A{i}', 'customer_id': f'C{i}',
            'imei': 'IMEI-SHARE', 'sim_id': f'S{i}', 'ip': '10.0.0.1', 'merchant_id': 'M',
            'location': 'DEL', 'amount': 1, 'label': 0,
        }
        for i in range(8)
    ])
    backend = ingest_frame(df)
    result = analyze_entity(backend, 'IMEI-SHARE', depth=2)
    assert result is not None
    assert result['correlation_is_not_proof'] is True
    assert result['risk_signals']['classified_as_fraud'] is False
    assert 'fraud' not in str(result.get('decision', '')).lower()


def test_discover_does_not_require_all_optional_columns():
    df = generate(80, seed=3)
    pats = discover(df)
    assert isinstance(pats, list)
