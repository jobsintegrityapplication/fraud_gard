import joblib
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from sklearn.ensemble import IsolationForest
from sklearn.metrics import precision_score, recall_score, f1_score, average_precision_score, roc_auc_score
from xgboost import XGBClassifier
from .features import build_features
from ..config import MODEL_DIR, ensure_dirs


class FraudModels:
    def __init__(self):
        self.xgb = None
        self.iforest = None
        self.meta = {}

    def train(self, df):
        if df is None or len(df) == 0:
            raise ValueError('training frame is empty')
        if 'label' not in df.columns:
            raise ValueError('label column is required')
        X = build_features(df)
        y = df['label'].astype(int).values
        self.xgb = XGBClassifier(
            n_estimators=180, max_depth=5, learning_rate=.08, subsample=.85,
            colsample_bytree=.85, eval_metric='logloss', random_state=42,
        )
        self.xgb.fit(X, y)
        legit = X[y == 0] if (y == 0).sum() else X
        self.iforest = IsolationForest(n_estimators=160, contamination='auto', random_state=42).fit(legit)
        prob = self.xgb.predict_proba(X)[:, 1]
        pred = (prob >= .5).astype(int)
        m = {
            'precision': float(precision_score(y, pred, zero_division=0)),
            'recall': float(recall_score(y, pred, zero_division=0)),
            'f1': float(f1_score(y, pred, zero_division=0)),
            'pr_auc': float(average_precision_score(y, prob)) if len(np.unique(y)) > 1 else 0.0,
            'roc_auc': float(roc_auc_score(y, prob)) if len(np.unique(y)) > 1 else 0.0,
        }
        self.meta = {'trained_at': datetime.now(timezone.utc).isoformat(), 'features': list(X.columns), 'metrics': m}
        ensure_dirs()
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, MODEL_DIR / 'fraud_models.joblib')
        return m

    def load(self):
        p = MODEL_DIR / 'fraud_models.joblib'
        if p.exists():
            loaded = joblib.load(p)
            self.__dict__.update(loaded.__dict__)
            return True
        return False

    def score(self, row):
        if self.xgb is None or self.iforest is None:
            raise RuntimeError('models are not trained')
        X = build_features(pd.DataFrame([row]))
        x = float(self.xgb.predict_proba(X)[0, 1])
        raw = float(self.iforest.decision_function(X)[0])
        a = float(1 / (1 + np.exp(4 * raw)))
        return x, a
