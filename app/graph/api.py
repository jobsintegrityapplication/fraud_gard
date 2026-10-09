from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import conn
from .analysis import analyze_entity
from .ingest import ingest_frame
from ..security.auth import Principal, require_roles

router = APIRouter(prefix='/api/v1/graph', tags=['graph'])


def _transactions() -> pd.DataFrame:
    c = conn()
    rows = c.execute('SELECT * FROM transactions').fetchall()
    cases = c.execute('SELECT * FROM cases').fetchall()
    c.close()
    df = pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()
    case_rows = [dict(r) for r in cases] if cases else []
    return df, case_rows


@router.get('/{entity_id}')
def graph_entity(entity_id: str, depth: int = Query(2, ge=1, le=4), _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST'))):
    df, cases = _transactions()
    if df.empty:
        raise HTTPException(400, 'no transactions loaded')
    backend = ingest_frame(df, cases=cases)
    result = analyze_entity(backend, entity_id, depth=depth)
    if result is None:
        raise HTTPException(404, 'entity not found in graph')
    return result
