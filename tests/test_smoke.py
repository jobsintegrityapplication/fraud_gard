from app.ml.features import FEATURES
from app.services.synthetic import REQUIRED_COLUMNS, generate


def test_generator():
    df = generate(1000)
    assert len(df) == 1000
    assert set([0, 1]).issuperset(set(df.label.unique()))


def test_generator_has_feature_columns():
    df = generate(50, seed=1)
    for col in REQUIRED_COLUMNS:
        assert col in df.columns
    for col in FEATURES:
        assert bool(df[col].notna().all())
    assert df['id'].is_unique
    assert 0 < float(df.label.mean()) < 1
