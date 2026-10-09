def test_health(client):
    r = client.get('/health')
    assert r.status_code == 200
    assert r.json()['status'] == 'ok'


def test_frontend_served(client):
    r = client.get('/')
    assert r.status_code == 200
    assert 'Autonomous Handset Fraud' in r.text


def test_generate_train_discover_score_case_decision(client):
    gen = client.post('/api/v1/demo/generate?n=250')
    assert gen.status_code == 200, gen.text
    body = gen.json()
    assert body['records'] == 250
    assert body['sample_ids']

    tx = client.get('/api/v1/transactions?limit=20')
    assert tx.status_code == 200
    assert len(tx.json()) > 0

    trained = client.post('/api/v1/train')
    assert trained.status_code == 200, trained.text
    assert 'metrics' in trained.json()

    discovered = client.post('/api/v1/patterns/discover')
    assert discovered.status_code == 200, discovered.text
    assert isinstance(discovered.json(), list)

    tid = (body.get('sample_fraud_ids') or body['sample_ids'])[0]
    scored = client.post('/api/v1/score', json={'transaction_id': tid})
    assert scored.status_code == 200, scored.text
    assert scored.json()['risk'] in {'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'}

    alerts = client.get('/api/v1/alerts')
    assert alerts.status_code == 200
    assert len(alerts.json()) >= 1
    alert_id = alerts.json()[0]['id']

    created = client.post(f'/api/v1/cases/from-alert/{alert_id}')
    assert created.status_code == 200
    case_id = created.json()['case_id']

    cases = client.get('/api/v1/cases')
    assert cases.status_code == 200
    assert any(c['id'] == case_id for c in cases.json())

    decided = client.post(
        f'/api/v1/cases/{case_id}/decision',
        json={
            'decision': 'CONFIRMED_FRAUD',
            'reason': 'test',
            'reviewer': 'analyst-1',
            'timestamp': '2026-09-18T06:00:00Z',
            'evidence_version': 1,
        },
    )
    assert decided.status_code == 200
    assert decided.json()['decision'] == 'CONFIRMED_FRAUD'

    metrics = client.get('/api/v1/metrics')
    assert metrics.status_code == 200
    assert metrics.json()['confirmed_fraud'] >= 1


def test_score_requires_train(client):
    client.post('/api/v1/demo/generate?n=20')
    r = client.post('/api/v1/score', json={'transaction_id': 'missing'})
    assert r.status_code == 400


def test_invalid_decision(client):
    r = client.post(
        '/api/v1/cases/NOPE/decision',
        json={
            'decision': 'YES',
            'reason': 'nope',
            'reviewer': 'r',
            'timestamp': '2026-09-18T06:00:00Z',
            'evidence_version': 1,
        },
    )
    assert r.status_code == 400
