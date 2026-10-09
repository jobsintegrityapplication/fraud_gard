import json

from app.data.generator import build_dataset
from app.ml.supervised.metrics import PromotionThresholds, classification_metrics, promotion_decision
from app.ml.supervised.model import load_artifact, score_one, train_supervised
from app.ml.supervised.split import temporal_split


def test_promotion_thresholds_are_not_forced():
    observed = {'precision': 0.4, 'recall': 0.5}
    decision = promotion_decision(observed, PromotionThresholds(precision=0.9, recall=0.9))
    assert decision['promoted'] is False
    assert 'below' in decision['reason']


def test_temporal_split_order():
    import pandas as pd
    df = pd.DataFrame({
        'transaction_id': [f'T{i}' for i in range(20)],
        'ts': pd.date_range('2026-01-01', periods=20, freq='h'),
        'label': [0] * 20,
    })
    sp = temporal_split(df)
    assert sp.train['ts'].max() <= sp.valid['ts'].min()
    assert sp.valid['ts'].max() <= sp.test['ts'].min()
    assert len(sp.train) + len(sp.valid) + len(sp.test) == 20


def test_train_evaluate_predict_and_detection_api(client):
    raw = build_dataset(220, seed=11, persist=False).processed
    art = train_supervised(raw, n_estimators=40, max_depth=3, random_state=11)
    assert art.meta['model_version'].startswith('SUP-')
    test_m = art.meta['metrics']['test']
    for key in ('precision', 'recall', 'f1', 'pr_auc', 'roc_auc', 'false_positive_rate', 'false_negative_rate', 'confusion_matrix'):
        assert key in test_m
    assert 'promoted' in art.meta['promotion']
    # Honest gate: defaults are 0.90/0.90 and must not be faked
    if test_m['precision'] < 0.90 or test_m['recall'] < 0.90:
        assert art.meta['promotion']['promoted'] is False
    loaded = load_artifact('CURRENT')
    tid = str(raw['transaction_id'].iloc[-1])
    pred = score_one(raw, tid, loaded, top_k=5)
    assert pred['transaction_id'] == tid
    assert 0 <= pred['risk_score'] <= 1
    assert pred['risk_level'] in {'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'}
    assert pred['model_version'] == art.meta['model_version']
    assert pred['feature_version'] == art.meta['feature_set_version']
    assert pred['top_features']
    feat = pred['top_features'][0]
    for key in ('feature', 'value', 'impact', 'contribution'):
        assert key in feat
    assert feat['impact'] in {'increases_risk', 'decreases_risk', 'neutral'}
    if feat['contribution'] > 0:
        assert feat['impact'] == 'increases_risk'
    elif feat['contribution'] < 0:
        assert feat['impact'] == 'decreases_risk'
    expl = pred['explanation']
    assert expl['model_version'] == pred['model_version']
    assert expl['feature_version'] == pred['feature_version']
    assert expl['consistency']['ok'] is True

    gen = client.post('/api/v1/demo/generate?n=80')
    assert gen.status_code == 200
    sample = gen.json()['sample_ids'][0]
    # Model artifact already in patched MODEL_DIR from train_supervised
    r = client.post('/api/v1/detection/score', json={'transaction_id': sample})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['transaction_id'] == sample
    assert 'risk_score' in body and 'top_features' in body
    assert body['model_version']


def test_classification_metrics_confusion_shape():
    m = classification_metrics([0, 1, 0, 1], [0.1, 0.9, 0.2, 0.8], 0.5)
    assert set(m['confusion_matrix']) == {'tn', 'fp', 'fn', 'tp'}
    assert m['precision'] == 1.0
    assert m['recall'] == 1.0
