from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...db import conn
from .model import load_artifact, score_one
from ...security.auth import Principal, require_roles
from ...observability import MODEL_ERRORS, MODEL_PREDICTIONS, MODEL_SCORE, TRANSACTIONS

router = APIRouter(prefix='/api/v1/detection', tags=['detection'])


class DetectionScoreRequest(BaseModel):
    transaction_id: str
    top_k: int = 5
    use_deployment: bool = True


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


@router.post('/score')
def detection_score(req: DetectionScoreRequest, _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST'))):
    hist = _history()
    if hist.empty:
        raise HTTPException(400, 'no transactions loaded')
    if req.transaction_id not in set(hist['transaction_id'].astype(str)):
        raise HTTPException(404, 'Transaction not found')
    top_k = max(1, min(req.top_k, 20))
    if req.use_deployment:
        try:
            from ..lifecycle.deploy import get_deployment, score_with_deployment
            cfg = get_deployment()
            if cfg.production_model_id or cfg.champion_model_id:
                result = score_with_deployment(hist, req.transaction_id, top_k=top_k)
                _record_prediction(result)
                return result
        except FileNotFoundError:
            pass
        except KeyError as exc:
            raise HTTPException(404, 'Transaction not found') from exc
    try:
        art = load_artifact()
    except FileNotFoundError as exc:
        raise HTTPException(400, 'supervised model is not trained') from exc
    try:
        result = score_one(hist, req.transaction_id, art, top_k=top_k)
        _record_prediction(result)
        return result
    except KeyError as exc:
        raise HTTPException(404, 'Transaction not found') from exc
    except Exception:
        MODEL_ERRORS.labels('xgboost').inc()
        raise


def _record_prediction(result: dict) -> None:
    TRANSACTIONS.inc()
    MODEL_PREDICTIONS.labels('xgboost').inc()
    MODEL_SCORE.labels('xgboost', 'risk').observe(float(result.get('risk_score', 0)))
