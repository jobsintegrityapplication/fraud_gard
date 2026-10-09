import pytest

from app.data.generator import build_dataset
from app.features.windows import FEATURE_SET_VERSION
from app.ml.supervised.explain import (
    FeatureExplanation,
    assert_explanations_consistent,
    build_feature_explanations,
    explain_prediction,
    impact_from_contribution,
)
from app.ml.supervised.model import score_one, train_supervised
from app.ml.supervised.metrics import PromotionThresholds
from app.ml.lifecycle.gates import PromotionGates


def _open_gates():
    return PromotionGates(
        precision_min=0.0, recall_min=0.0, pr_auc_min=0.0,
        fpr_max=1.0, fnr_max=1.0, min_test_rows=1, min_positives=0,
        segment_min_precision=0.0,
    )


def test_impact_matches_contribution_sign():
    assert impact_from_contribution(0.21) == 'increases_risk'
    assert impact_from_contribution(-0.05) == 'decreases_risk'
    assert impact_from_contribution(0.0) == 'neutral'


def test_contradictory_impact_raises():
    bad = [FeatureExplanation('accounts_per_imei', 8, 'decreases_risk', 0.21)]
    import numpy as np
    import pandas as pd
    row = pd.DataFrame([[8.0]], columns=['accounts_per_imei'])
    with pytest.raises(ValueError, match='contradict'):
        assert_explanations_consistent(
            bad,
            shap_vector=np.array([0.21]),
            feature_names=['accounts_per_imei'],
            row=row,
            risk_score=0.9,
        )


def test_shap_explanation_object_on_prediction():
    raw = build_dataset(160, seed=17, persist=False).processed
    art = train_supervised(
        raw, n_estimators=30, max_depth=3, random_state=17,
        thresholds=PromotionThresholds(0.01, 0.01),
        promotion_gates=_open_gates(),
    )
    tid = str(raw['transaction_id'].iloc[-1])
    pred = score_one(raw, tid, art, top_k=5)
    expl = pred['explanation']
    assert expl['model_version'] == art.meta['model_version']
    assert expl['feature_version'] == FEATURE_SET_VERSION
    assert expl['risk_score'] == pred['risk_score']
    assert expl['method'] in {'shap_tree', 'importance_fallback'}
    assert expl['consistency']['ok'] is True
    assert len(expl['top_features']) <= 5
    example = expl['top_features'][0]
    assert set(example) == {'feature', 'value', 'impact', 'contribution'}
    assert example['feature'] in art.feature_names
    if example['contribution'] > 0:
        assert example['impact'] == 'increases_risk'
    elif example['contribution'] < 0:
        assert example['impact'] == 'decreases_risk'
    # Ordered by |contribution|
    abs_vals = [abs(f['contribution']) for f in expl['top_features']]
    assert abs_vals == sorted(abs_vals, reverse=True)


def test_explanation_uses_same_row_as_score():
    raw = build_dataset(120, seed=19, persist=False).processed
    art = train_supervised(
        raw, n_estimators=25, max_depth=3, random_state=19,
        thresholds=PromotionThresholds(0.01, 0.01),
        promotion_gates=_open_gates(),
        register_lifecycle=False,
    )
    from app.features.engine import compute_features
    feats = compute_features(raw)
    row = feats.iloc[[-1]]
    risk = float(art.predict_proba(row)[0])
    explanation = explain_prediction(
        art.model,
        row,
        art.feature_names,
        risk_score=risk,
        model_version=art.meta['model_version'],
        feature_version=art.meta['feature_set_version'],
        top_k=3,
        transaction_id=str(row['transaction_id'].iloc[0]),
    )
    assert explanation.consistency['ok'] is True
    X = row[art.feature_names].fillna(0).astype(float)
    for feat in explanation.top_features:
        assert abs(feat.value - float(X[feat.feature].iloc[0])) < 1e-6
