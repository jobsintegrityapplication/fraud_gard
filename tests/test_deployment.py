import json

from app.data.generator import build_dataset
from app.ml.lifecycle.deploy import (
    deploy_canary,
    deploy_production,
    deploy_shadow,
    get_deployment,
    in_canary_traffic,
    resolve_decision_route,
    rollback_to_last_known_good,
    score_with_deployment,
    traffic_bucket,
)
from app.ml.lifecycle.gates import PromotionGates
from app.ml.lifecycle.registry import get_champion, get_model, list_models, set_champion, upsert_model_record
from app.ml.supervised.metrics import PromotionThresholds
from app.ml.supervised.model import train_supervised


def _good_metrics():
    return {
        'precision': 0.95, 'recall': 0.93, 'pr_auc': 0.90,
        'false_positive_rate': 0.02, 'false_negative_rate': 0.05,
        'n': 100, 'n_positive': 20,
    }


def _open_gates(**overrides):
    base = dict(
        precision_min=0.0, recall_min=0.0, pr_auc_min=0.0,
        fpr_max=1.0, fnr_max=1.0, min_test_rows=1, min_positives=0,
        segment_min_precision=0.0,
    )
    base.update(overrides)
    return PromotionGates(**base)


def _register(model_id, version, status='PRODUCTION'):
    return upsert_model_record(
        model_id=model_id,
        version=version,
        algorithm='XGBoost',
        dataset_version='ds',
        feature_version='fe-v1.0.0',
        training_timestamp='2026-09-18T00:00:00Z',
        metrics=_good_metrics(),
        status=status,
        artifact_uri=f'models/supervised/{version}/model.joblib',
    )


def test_traffic_bucket_is_sticky_and_canary_percent_simulated():
    tid = 'TXN-sticky-42'
    assert traffic_bucket(tid) == traffic_bucket(tid)
    assert in_canary_traffic(tid, 0) is False
    assert in_canary_traffic(tid, 100) is True
    n = 2000
    hits = sum(1 for i in range(n) if in_canary_traffic(f'T-{i}', 10))
    rate = hits / n
    assert 0.05 < rate < 0.15


def test_shadow_scores_but_does_not_affect_decision_route():
    raw = build_dataset(180, seed=31, persist=False).processed
    train_supervised(
        raw, n_estimators=30, max_depth=3, random_state=31,
        thresholds=PromotionThresholds(0.01, 0.01), promotion_gates=_open_gates(),
    )
    champ = get_champion()
    assert champ is not None
    champ_id = champ['model_id']
    champ_version = champ['version']

    # Challenger fails gates — remains non-production, suitable for shadow.
    train_supervised(
        raw, n_estimators=30, max_depth=3, random_state=32,
        thresholds=PromotionThresholds(0.999, 0.999),
        promotion_gates=_open_gates(precision_min=0.999, recall_min=0.999),
    )
    assert get_champion()['model_id'] == champ_id
    shadow_candidates = [m for m in list_models() if m['model_id'] != champ_id]
    assert shadow_candidates
    shadow_model = shadow_candidates[0]

    out = deploy_shadow(shadow_model['model_id'])
    assert out['affects_decision'] is False
    assert out['deployment_state'] == 'SHADOW'
    assert get_model(shadow_model['model_id'])['status'] == 'SHADOW'

    tid = str(raw['transaction_id'].iloc[-1])
    route = resolve_decision_route(tid)
    assert route['affects_decision'] is True
    assert route['model_id'] == champ_id
    assert route['role'] == 'PRODUCTION'

    scored = score_with_deployment(raw, tid, top_k=3)
    assert scored['deployment']['affects_decision'] is True
    assert scored['deployment']['model_id'] == champ_id
    assert scored['shadow'] is not None
    assert scored['shadow']['affects_decision'] is False
    assert scored['shadow']['role'] == 'SHADOW'
    assert scored['model_version'] == champ_version


def test_canary_routes_configurable_percent():
    _register('MDL-PROD', 'SUP-PROD', 'PRODUCTION')
    _register('MDL-CAN', 'SUP-CAN', 'SHADOW')
    set_champion('MDL-PROD')
    deploy_canary('MDL-CAN', percent=100)
    route = resolve_decision_route('any-txn')
    assert route['role'] == 'CANARY'
    assert route['model_id'] == 'MDL-CAN'
    assert route['affects_decision'] is True

    deploy_canary('MDL-CAN', percent=0)
    route0 = resolve_decision_route('any-txn')
    assert route0['role'] == 'PRODUCTION'
    assert route0['model_id'] == 'MDL-PROD'


def test_rollback_restores_last_known_good_champion():
    _register('MDL-A', 'SUP-A', 'PRODUCTION')
    _register('MDL-B', 'SUP-B', 'SHADOW')
    set_champion('MDL-A')
    deploy_production('MDL-B', as_champion=True)
    assert get_champion()['model_id'] == 'MDL-B'
    assert get_model('MDL-A')['status'] == 'ROLLED_BACK'
    assert 'MDL-A' in get_deployment().champion_history

    rolled = rollback_to_last_known_good()
    assert rolled['model']['model_id'] == 'MDL-A'
    assert get_champion()['model_id'] == 'MDL-A'
    assert get_champion()['status'] == 'PRODUCTION'
    assert get_model('MDL-B')['status'] == 'ROLLED_BACK'
    assert get_deployment().champion_model_id == 'MDL-A'


def test_deployment_api(client):
    _register('MDL-API-P', 'SUP-API-P', 'PRODUCTION')
    _register('MDL-API-S', 'SUP-API-S', 'FAILED')
    set_champion('MDL-API-P')
    r = client.get('/api/v1/models/deployment')
    assert r.status_code == 200
    body = r.json()
    assert 'CHAMPION' in body['states']
    assert 'SHADOW' in body['states']
    assert body['config']['champion_model_id'] == 'MDL-API-P'

    s = client.post('/api/v1/models/deployment/shadow', json={'model_id': 'MDL-API-S'})
    assert s.status_code == 200, s.text
    assert s.json()['affects_decision'] is False

    c = client.post('/api/v1/models/deployment/canary', json={'model_id': 'MDL-API-S', 'percent': 10})
    assert c.status_code == 200
    assert c.json()['canary_percent'] == 10
