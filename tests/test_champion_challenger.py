import json
from pathlib import Path

from app import config
from app.data.generator import build_dataset
from app.ml.lifecycle.gates import PromotionGates, evaluate_promotion_gates
from app.ml.lifecycle.promote import register_challenger_from_artifact
from app.ml.lifecycle.registry import get_champion, list_models, set_champion, upsert_model_record
from app.ml.supervised.metrics import PromotionThresholds
from app.ml.supervised.model import current_version_file, load_artifact, train_supervised


def _good_metrics():
    return {
        'precision': 0.95,
        'recall': 0.93,
        'pr_auc': 0.90,
        'false_positive_rate': 0.02,
        'false_negative_rate': 0.05,
        'n': 100,
        'n_positive': 20,
    }


def _bad_metrics():
    return {
        'precision': 0.40,
        'recall': 0.30,
        'pr_auc': 0.20,
        'false_positive_rate': 0.40,
        'false_negative_rate': 0.50,
        'n': 100,
        'n_positive': 20,
    }


def _open_gates(**overrides):
    base = dict(
        precision_min=0.0,
        recall_min=0.0,
        pr_auc_min=0.0,
        fpr_max=1.0,
        fnr_max=1.0,
        min_test_rows=1,
        min_positives=0,
        segment_min_precision=0.0,
        require_governance=True,
    )
    base.update(overrides)
    return PromotionGates(**base)


def test_promotion_gates_require_all_criteria():
    record = {
        'model_id': 'M1', 'version': 'V1', 'algorithm': 'XGBoost',
        'dataset_version': 'ds-1', 'feature_version': 'fe-v1.0.0',
        'training_timestamp': '2026-09-18T00:00:00Z', 'metrics': _good_metrics(),
    }
    ok = evaluate_promotion_gates(
        metrics=_good_metrics(),
        split_metrics={'train': {}, 'valid': {}, 'test': _good_metrics()},
        split_sizes={'train': 70, 'valid': 15, 'test': 15},
        record=record,
        drift_report={'ok': True, 'max_mean_shift': 0.1},
    )
    assert ok['promoted'] is True
    assert ok['failed_gates'] == []

    bad = evaluate_promotion_gates(
        metrics=_bad_metrics(),
        split_metrics={'train': {}, 'valid': {}, 'test': _bad_metrics()},
        split_sizes={'train': 70, 'valid': 15, 'test': 15},
        record=record,
        drift_report={'ok': True, 'max_mean_shift': 0.1},
        gates=PromotionGates(precision_min=0.90, recall_min=0.90),
    )
    assert bad['promoted'] is False
    assert 'precision' in bad['failed_gates']
    assert 'recall' in bad['failed_gates']


def test_failed_challenger_does_not_replace_champion():
    champ = upsert_model_record(
        model_id='MDL-CHAMP',
        version='SUP-CHAMPION',
        algorithm='XGBoost',
        dataset_version='ds-champ',
        feature_version='fe-v1.0.0',
        training_timestamp='2026-09-01T00:00:00Z',
        metrics=_good_metrics(),
        status='PRODUCTION',
        artifact_uri='models/supervised/SUP-CHAMPION/model.joblib',
    )
    set_champion('MDL-CHAMP')
    Path(config.MODEL_DIR, 'supervised').mkdir(parents=True, exist_ok=True)
    current_version_file().write_text(json.dumps({'current': 'SUP-CHAMPION', 'promoted': True}), encoding='utf-8')

    class FakeArt:
        def __init__(self):
            self.meta = {
                'model_version': 'SUP-CHALLENGER-FAIL',
                'algorithm': 'XGBoost',
                'feature_set_version': 'fe-v1.0.0',
                'trained_at': '2026-09-18T12:00:00Z',
                'metrics': {'train': _bad_metrics(), 'valid': _bad_metrics(), 'test': _bad_metrics()},
                'segment_metrics': {},
                'split_sizes': {'train': 70, 'valid': 15, 'test': 15},
            }

    outcome = register_challenger_from_artifact(
        FakeArt(),
        dataset_version='ds-challenger',
        artifact_uri='models/supervised/SUP-CHALLENGER-FAIL/model.joblib',
        gates=PromotionGates(precision_min=0.90, recall_min=0.90, pr_auc_min=0.70, fpr_max=0.10, fnr_max=0.10),
        auto_promote=True,
    )
    assert outcome['champion_replaced'] is False
    assert outcome['model']['status'] == 'FAILED'
    assert outcome['gates']['promoted'] is False
    after = get_champion()
    assert after is not None
    assert after['model_id'] == champ['model_id']
    assert after['status'] == 'PRODUCTION'
    assert after['version'] == 'SUP-CHAMPION'
    pointer = json.loads(current_version_file().read_text(encoding='utf-8'))
    assert pointer['current'] == 'SUP-CHAMPION'


def test_train_failed_challenger_keeps_existing_champion_current():
    raw = build_dataset(220, seed=21, persist=False).processed
    first = train_supervised(
        raw,
        n_estimators=40,
        max_depth=3,
        random_state=21,
        thresholds=PromotionThresholds(0.01, 0.01),
        promotion_gates=_open_gates(),
    )
    champ = get_champion()
    assert champ is not None
    assert champ['status'] == 'PRODUCTION'
    before_version = json.loads(current_version_file().read_text(encoding='utf-8'))['current']
    assert before_version == first.meta['model_version']

    second = train_supervised(
        raw,
        n_estimators=40,
        max_depth=3,
        random_state=22,
        thresholds=PromotionThresholds(precision=0.999, recall=0.999),
        promotion_gates=_open_gates(precision_min=0.999, recall_min=0.999),
    )
    promo = second.meta.get('promotion') or {}
    assert promo.get('champion_replaced') is False
    assert promo.get('promoted') is False
    after = get_champion()
    assert after['model_id'] == champ['model_id']
    assert after['status'] == 'PRODUCTION'
    pointer = json.loads(current_version_file().read_text(encoding='utf-8'))
    assert pointer['current'] == before_version
    failed = [m for m in list_models() if m['version'] == second.meta['model_version']]
    assert failed
    assert failed[0]['status'] == 'FAILED'
    loaded = load_artifact('CURRENT')
    assert loaded.meta['model_version'] == before_version


def test_models_api_lists_champion(client):
    upsert_model_record(
        model_id='MDL-API',
        version='SUP-API',
        algorithm='XGBoost',
        dataset_version='ds',
        feature_version='fe-v1.0.0',
        training_timestamp='2026-09-18T00:00:00Z',
        metrics=_good_metrics(),
        status='PRODUCTION',
    )
    set_champion('MDL-API')
    r = client.get('/api/v1/models/champion')
    assert r.status_code == 200
    body = r.json()
    for key in ('model_id', 'version', 'algorithm', 'dataset_version', 'feature_version', 'training_timestamp', 'metrics', 'status'):
        assert key in body
    assert body['status'] == 'PRODUCTION'
