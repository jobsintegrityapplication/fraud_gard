from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ...db import conn
from .model import load_artifact, score_one, top_anomalies
from ...security.auth import Principal, require_roles
from ...observability import ANOMALIES, MODEL_ERRORS, MODEL_PREDICTIONS, MODEL_SCORE, TRANSACTIONS

router = APIRouter(prefix='/api/v1/anomaly', tags=['anomaly'])


class AnomalyScoreRequest(BaseModel):
    transaction_id: str


def _history() -> pd.DataFrame:
    c = conn()
    rows = c.execute('SELECT * FROM transactions').fetchall()
    c.close()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    if 'id' in df.columns and 'transaction_id' not in df.columns:
        df['transaction_id'] = df['id']
    return df


def _load():
    try:
        return load_artifact()
    except FileNotFoundError as exc:
        raise HTTPException(400, 'anomaly model is not trained') from exc


@router.post('/score')
def anomaly_score(req: AnomalyScoreRequest, _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST'))):
    hist = _history()
    if hist.empty:
        raise HTTPException(400, 'no transactions loaded')
    art = _load()
    try:
        result = score_one(hist, req.transaction_id, art)
        TRANSACTIONS.inc()
        MODEL_PREDICTIONS.labels('isolation_forest').inc()
        MODEL_SCORE.labels('isolation_forest', 'anomaly').observe(float(result['anomaly_score']))
        if float(result['anomaly_score']) >= .7:
            ANOMALIES.inc()
        return result
    except KeyError as exc:
        raise HTTPException(404, 'Transaction not found') from exc
    except Exception:
        MODEL_ERRORS.labels('isolation_forest').inc()
        raise


@router.get('/rank')
def anomaly_rank(limit: int = Query(20, ge=1, le=500), _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST'))):
    hist = _history()
    if hist.empty:
        raise HTTPException(400, 'no transactions loaded')
    art = _load()
    results = top_anomalies(hist, art, limit=limit)
    TRANSACTIONS.inc(len(results))
    MODEL_PREDICTIONS.labels('isolation_forest').inc(len(results))
    for result in results:
        score = float(result['anomaly_score'])
        MODEL_SCORE.labels('isolation_forest', 'anomaly').observe(score)
        if score >= .7:
            ANOMALIES.inc()
    return results
