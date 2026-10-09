import pandas as pd

from app.ml.features import FEATURES, build_features
from app.ml.pipeline import FraudModels
from app.services.synthetic import generate


def test_build_features_fills_missing_columns():
    out = build_features(pd.DataFrame([{'amount': 10}]))
    assert list(out.columns) == FEATURES
    assert float(out.iloc[0]['amount']) == 10
    assert float(out.iloc[0]['txn_velocity_1h']) == 0


def test_train_and_score_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr('app.ml.pipeline.MODEL_DIR', tmp_path)
    df = generate(200, seed=7)
    m = FraudModels()
    metrics = m.train(df)
    assert 'f1' in metrics
    x, a = m.score(df.iloc[0].to_dict())
    assert 0 <= x <= 1
    assert 0 <= a <= 1
    loaded = FraudModels()
    assert loaded.load()
    x2, a2 = loaded.score(df.iloc[0].to_dict())
    assert abs(x - x2) < 1e-6
