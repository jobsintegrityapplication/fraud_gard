from app.db import conn
from app.registry.matching import score_against_pattern
from app.registry.service import REQUIRED_FIELDS, get_pattern, upsert_pattern


AUTH = {'X-Actor': 'pattern-admin', 'X-Actor-Role': 'PATTERN_ADMIN'}


def _seed(**overrides):
    payload = {
        'pattern_id': 'FP-REG-1',
        'name': 'Shared IMEI burst',
        'description': 'Candidate registry pattern',
        'signature': {'imei_prefix': 'I_FRAUD'},
        'supporting_cases': ['CASE-1'],
        'supporting_evidence': [{'transaction_id': 'T-1'}],
        'threshold': 1.0,
        'policy_action': 'ALERT',
        'owner': 'fraud-ops',
        'state': 'DISCOVERED',
        'actor': 'system',
        'audit_action': 'PATTERN_UPSERT',
    }
    payload.update(overrides)
    return upsert_pattern(**payload)


def _audit_actions(pattern_id: str) -> list[str]:
    c = conn()
    rows = c.execute(
        'SELECT action FROM audit WHERE entity_type = ? AND entity_id = ? ORDER BY id',
        ('FraudPattern', pattern_id),
    ).fetchall()
    c.close()
    return [r['action'] for r in rows]


def test_registry_get_list_contains_required_fields(client):
    _seed()
    listed = client.get('/api/v1/patterns')
    assert listed.status_code == 200
    assert listed.json()
    row = listed.json()[0]
    for key in REQUIRED_FIELDS:
        assert key in row
    one = client.get('/api/v1/patterns/FP-REG-1')
    assert one.status_code == 200
    assert one.json()['pattern_id'] == 'FP-REG-1'


def test_unreviewed_patterns_cannot_become_active(client):
    _seed(pattern_id='FP-UNREV')
    r = client.post('/api/v1/patterns/FP-UNREV/activate', headers=AUTH)
    assert r.status_code == 409
    assert get_pattern('FP-UNREV')['state'] == 'DISCOVERED'
    review1 = client.post('/api/v1/patterns/FP-UNREV/review', json={'notes': 'start'}, headers={'X-Actor': 'analyst'})
    assert review1.status_code == 200
    assert review1.json()['state'] == 'UNDER_REVIEW'
    still = client.post('/api/v1/patterns/FP-UNREV/activate', headers=AUTH)
    assert still.status_code == 409
    assert get_pattern('FP-UNREV')['state'] == 'UNDER_REVIEW'
    assert 'PATTERN_REVIEW' in _audit_actions('FP-UNREV')


def test_unauthorized_users_cannot_activate_patterns(client):
    _seed(pattern_id='FP-AUTH')
    client.post('/api/v1/patterns/FP-AUTH/review', json={}, headers={'X-Actor': 'analyst'})
    client.post('/api/v1/patterns/FP-AUTH/review', json={}, headers={'X-Actor': 'analyst'})
    assert get_pattern('FP-AUTH')['state'] == 'CONFIRMED'
    denied = client.post('/api/v1/patterns/FP-AUTH/activate', headers={'X-Actor': 'intern', 'X-Actor-Role': 'VIEWER'})
    assert denied.status_code == 403
    missing = client.post('/api/v1/patterns/FP-AUTH/activate')
    assert missing.status_code == 403
    assert get_pattern('FP-AUTH')['state'] == 'CONFIRMED'
    allowed = client.post('/api/v1/patterns/FP-AUTH/activate', headers=AUTH)
    assert allowed.status_code == 200
    assert allowed.json()['state'] == 'ACTIVE'
    assert 'PATTERN_ACTIVATE' in _audit_actions('FP-AUTH')


def test_deprecated_patterns_cannot_fire(client):
    _seed(pattern_id='FP-FIRE', state='CONFIRMED')
    activated = client.post('/api/v1/patterns/FP-FIRE/activate', headers=AUTH)
    assert activated.status_code == 200
    tx = {'imei': 'I_FRAUD_RING_99', 'ip': '10.0.0.1', 'amount': 50}
    live = get_pattern('FP-FIRE')
    score, why = score_against_pattern(tx, live)
    assert score == 1.0
    assert why
    dep = client.post('/api/v1/patterns/FP-FIRE/deprecate', headers={'X-Actor': 'lead'})
    assert dep.status_code == 200
    assert dep.json()['state'] == 'DEPRECATED'
    dead = get_pattern('FP-FIRE')
    score2, why2 = score_against_pattern(tx, dead)
    assert score2 == 0.0
    assert why2 is None
    assert 'PATTERN_DEPRECATE' in _audit_actions('FP-FIRE')
