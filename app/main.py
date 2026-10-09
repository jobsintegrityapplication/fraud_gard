import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from starlette.responses import Response
from pydantic import BaseModel

from .config import DATA_DIR, FRONTEND_INDEX, ensure_dirs
from .db import TXN_COLUMNS, conn, init_db
from .ml.features import FEATURES
from .ml.pipeline import FraudModels
from .services.graph import graph_score
from .services.patterns import DISCOVER_COLS, discover
from .services.synthetic import generate
from .agents.api import router as agents_router
from .cases.api import router as cases_router
from .decision.api import router as decision_router
from .decision.engine import DecisionInputs, decide, default_active_rules
from .discovery.api import router as discovery_router
from .graph.api import router as graph_router
from .ingestion.api import router as ingestion_router
from .investigation.api import router as investigation_router
from .learning.api import router as learning_router
from .ml.anomaly.api import router as anomaly_router
from .ml.lifecycle.api import router as models_router
from .ml.supervised.api import router as detection_router
from .features.candidates.api import router as candidate_features_router
from .reports.api import router as reports_router
from .monitoring.api import router as monitoring_router
from .dashboard.api import router as dashboard_router
from .registry.api import router as registry_router
from .registry.service import score_with_registry, upsert_pattern
from .security.auth import Principal, TokenValidationError, authenticate_token, require_roles
from .security.redaction import redact_for_roles
from .security.api import router as auth_router
from .security.policy import router as policy_router
from .domain.redact import sanitize_audit_detail
from .observability import (
    API_ERRORS, API_LATENCY, API_REQUESTS, ANOMALIES, FRAUD_ALERTS,
    MODEL_ERRORS, MODEL_PREDICTIONS, MODEL_SCORE, TRANSACTIONS,
    configure_tracing, metrics_asgi_app, tracer, record_model_evaluation,
)

models = FraudModels()
# Request paths can contain handset and network identifiers (for example graph
# entity IDs); disable URL access logs so those identifiers are not persisted.
logging.getLogger('uvicorn.access').disabled = True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_dirs()
    init_db()
    models.load()
    configure_tracing()
    yield


app = FastAPI(
    title='Autonomous Handset Fraud Management Platform',
    version='1.0.0',
    lifespan=lifespan,
)
app.include_router(ingestion_router)
app.include_router(detection_router)
app.include_router(anomaly_router)
app.include_router(discovery_router)
app.include_router(registry_router)
app.include_router(graph_router)
app.include_router(cases_router)
app.include_router(investigation_router)
app.include_router(agents_router)
app.include_router(decision_router)
app.include_router(learning_router)
app.include_router(models_router)
app.include_router(candidate_features_router)
app.include_router(reports_router)
app.include_router(monitoring_router)
app.include_router(dashboard_router)
app.include_router(auth_router)
app.include_router(policy_router)
app.mount('/metrics', metrics_asgi_app(), name='prometheus-metrics')


@app.middleware('http')
async def observe_http(request, call_next):
    """Measure requests without recording raw URLs, query strings, or identities."""
    method = request.method
    started = __import__('time').perf_counter()
    status = 500
    span_name = f'HTTP {method}'
    span = None
    try:
        with tracer().start_as_current_span(span_name) as span:
            response = await call_next(request)
            status = response.status_code
            route = request.scope.get('route')
            template = getattr(route, 'path', 'unmatched')
            span.update_name(f'{method} {template}')
            span.set_attribute('http.request.method', method)
            span.set_attribute('http.route', template)
            span.set_attribute('http.response.status_code', status)
            return response
    except Exception:
        try:
            if span is not None:
                # Do not attach exception messages: they can contain request data.
                span.set_attribute('error', True)
            span.set_attribute('http.response.status_code', 500)
        except Exception:
            pass
        raise
    finally:
        route = request.scope.get('route')
        template = getattr(route, 'path', 'unmatched')
        duration = __import__('time').perf_counter() - started
        API_REQUESTS.labels(method, template, str(status)).inc()
        API_LATENCY.labels(method, template).observe(duration)
        if status >= 400:
            API_ERRORS.labels(method, template, str(status)).inc()


@app.middleware('http')
async def oidc_auth_and_response_redaction(request, call_next):
    """Protect all API routes and mask PII after endpoint serialization."""
    if not request.url.path.startswith('/api/v1/'):
        return await call_next(request)

    auth_enabled = os.getenv('AUTH_ENABLED', 'true').strip().lower() not in {'0', 'false', 'no', 'off'}
    if not auth_enabled:
        role = os.getenv('DEV_AUTH_ROLE', 'FRAUD_ANALYST').upper()
        request.state.principal = Principal('local-development', 'user:local-development', frozenset({role}), {})
    else:
        authorization = request.headers.get('authorization', '')
        scheme, _, token = authorization.partition(' ')
        if scheme.lower() != 'bearer' or not token:
            return JSONResponse({'detail': 'Authentication required'}, status_code=401, headers={'WWW-Authenticate': 'Bearer'})
        try:
            request.state.principal = authenticate_token(token.strip())
        except TokenValidationError as exc:
            config_error = 'not configured' in str(exc) or 'must be configured' in str(exc)
            message = 'Authentication service is not configured' if config_error else 'Invalid bearer token'
            return JSONResponse({'detail': message}, status_code=503 if config_error else 401,
                                headers={'WWW-Authenticate': 'Bearer'} if not config_error else None)

    response = await call_next(request)
    content_type = response.headers.get('content-type', '')
    if 'application/json' not in content_type:
        return response
    body = b''.join([chunk async for chunk in response.body_iterator])
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return Response(body, status_code=response.status_code, headers=dict(response.headers), media_type=content_type, background=response.background)
    masked = redact_for_roles(payload, request.state.principal.roles)
    headers = {key: value for key, value in response.headers.items() if key.lower() not in {'content-length', 'transfer-encoding'}}
    return Response(json.dumps(masked, default=str), status_code=response.status_code, headers=headers, media_type=content_type, background=response.background)


def audit(action, typ, eid, detail=''):
    c = conn()
    c.execute(
        'INSERT INTO audit(actor,action,entity_type,entity_id,detail,ts) VALUES(?,?,?,?,?,?)',
        ('system', action, typ, eid, sanitize_audit_detail(detail), datetime.now(timezone.utc).isoformat()),
    )
    c.commit()
    c.close()


def load_df():
    c = conn()
    rows = c.execute('SELECT * FROM transactions').fetchall()
    c.close()
    if not rows:
        return pd.DataFrame(columns=TXN_COLUMNS)
    return pd.DataFrame([dict(r) for r in rows])


def _pattern_score(tx, patterns_rows=None):
    return score_with_registry(tx)


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.get('/')
def home():
    if not FRONTEND_INDEX.exists():
        raise HTTPException(500, 'frontend/index.html is missing')
    return FileResponse(str(FRONTEND_INDEX))


@app.post('/api/v1/demo/generate')
def demo_generate(n: int = 10000, _principal: Principal = Depends(require_roles('ADMIN'))):
    if n < 1 or n > 100000:
        raise HTTPException(400, 'n must be between 1 and 100000')
    df = generate(n)
    c = conn()
    c.execute('DELETE FROM alerts')
    c.execute('DELETE FROM cases')
    c.execute('DELETE FROM patterns')
    c.execute('DELETE FROM transactions')
    placeholders = ','.join(['?'] * len(TXN_COLUMNS))
    colsql = ','.join(TXN_COLUMNS)
    c.executemany(
        f'INSERT INTO transactions ({colsql}) VALUES ({placeholders})',
        [tuple(r[col] for col in TXN_COLUMNS) for r in df.to_dict('records')],
    )
    c.commit()
    c.close()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(DATA_DIR / 'synthetic_handset_transactions.csv', index=False)
    audit('GENERATE_DATA', 'dataset', 'synthetic', str(n))
    fraud_ids = df.loc[df.label == 1, 'id'].head(5).tolist()
    sample_ids = df['id'].head(5).tolist()
    return {
        'records': len(df),
        'fraud_rate': float(df.label.mean()),
        'sample_ids': sample_ids,
        'sample_fraud_ids': fraud_ids,
    }


@app.post('/api/v1/train')
def train(_principal: Principal = Depends(require_roles('ML_ENGINEER'))):
    df = load_df()
    if df.empty:
        raise HTTPException(400, 'Generate data first')
    try:
        metrics = models.train(df)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    record_model_evaluation('legacy', 'train', metrics)
    c = conn()
    mid = 'MODEL-' + uuid.uuid4().hex[:8]
    c.execute(
        'INSERT INTO models VALUES(?,?,?,?,?)',
        (mid, 'XGBoost+IsolationForest', 'CHALLENGER', json.dumps(metrics), datetime.now(timezone.utc).isoformat()),
    )
    c.commit()
    c.close()
    audit('TRAIN', 'model', mid, json.dumps(metrics))
    return {'model_id': mid, 'metrics': metrics}


class ScoreRequest(BaseModel):
    transaction_id: str


@app.post('/api/v1/score')
def score(req: ScoreRequest, _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST'))):
    if models.xgb is None or models.iforest is None:
        raise HTTPException(400, 'Train model first')
    df = load_df()
    rows = df[df.id == req.transaction_id]
    if rows.empty:
        raise HTTPException(404, 'Transaction not found')
    tx = rows.iloc[0].to_dict()
    try:
        x, a = models.score(tx)
    except Exception:
        MODEL_ERRORS.labels('xgboost').inc()
        MODEL_ERRORS.labels('anomaly').inc()
        raise
    TRANSACTIONS.inc()
    MODEL_PREDICTIONS.labels('xgboost').inc()
    MODEL_PREDICTIONS.labels('anomaly').inc()
    MODEL_SCORE.labels('xgboost', 'risk').observe(float(x))
    MODEL_SCORE.labels('isolation_forest', 'anomaly').observe(float(a))
    if float(a) >= 0.7:
        ANOMALIES.inc()
    gs, ge = graph_score(tx, df)
    ps, pwhy = _pattern_score(tx)
    decision = decide(DecisionInputs(
        xgboost_score=x,
        anomaly_score=a,
        graph_score=gs,
        pattern_score=ps,
        active_rules=default_active_rules(),
        business_policy={},
        evidence_ids=[],
    ))
    risk = decision.risk_level
    reasons = [f'XGBoost={x:.3f}', f'Anomaly={a:.3f}'] + ge
    if pwhy:
        reasons.append(pwhy)
    reasons.append(f"action={decision.recommended_action}")
    c = conn()
    c.execute(
        'INSERT INTO alerts(transaction_id,xgb_score,anomaly_score,graph_score,pattern_score,risk,reasons,created_at) VALUES(?,?,?,?,?,?,?,?)',
        (req.transaction_id, x, a, gs, ps, risk, json.dumps(reasons), datetime.now(timezone.utc).isoformat()),
    )
    aid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.commit()
    c.close()
    FRAUD_ALERTS.labels(str(risk).lower() if str(risk).lower() in {'low', 'medium', 'high', 'critical'} else 'other').inc()
    audit('SCORE', 'alert', str(aid))
    return {
        'alert_id': aid,
        'risk': risk,
        'risk_score': decision.risk_score,
        'risk_level': decision.risk_level,
        'recommended_action': decision.recommended_action,
        'matched_rules': decision.matched_rules,
        'evidence_ids': decision.evidence_ids,
        'scores': {'xgboost': x, 'anomaly': a, 'graph': gs, 'pattern': ps},
        'reasons': reasons,
        'llm_determined': False,
    }


@app.get('/api/v1/transactions')
def list_transactions(limit: int = 20, _principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST', 'ML_ENGINEER'))):
    limit = max(1, min(limit, 200))
    c = conn()
    rows = c.execute(
        'SELECT id, customer_id, account_id, imei, ip, amount, label FROM transactions LIMIT ?',
        (limit,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


@app.get('/api/v1/alerts')
def alerts(_principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'AUDITOR'))):
    c = conn()
    rows = c.execute('SELECT * FROM alerts ORDER BY id DESC LIMIT 100').fetchall()
    c.close()
    return [dict(r) for r in rows]


@app.post('/api/v1/patterns/discover')
def patterns(_principal: Principal = Depends(require_roles('DATA_SCIENTIST', 'ML_ENGINEER'))):
    df = load_df()
    if df.empty:
        raise HTTPException(400, 'No data')
    for col in DISCOVER_COLS + FEATURES:
        if col not in df.columns:
            df[col] = 0
    sort_cols = [c for c in ['txn_velocity_1h', 'imei_account_count', 'ip_account_count'] if c in df.columns]
    cand = df.sort_values(sort_cols, ascending=False).head(min(1000, len(df))) if sort_cols else df.head(min(1000, len(df)))
    pats = discover(cand)
    for q in pats:
        upsert_pattern(
            pattern_id=q['id'],
            name=q['name'],
            state=q.get('state') or 'DISCOVERED',
            description='Discovered candidate; not classified as fraud',
            signature=q.get('signature') or {},
            supporting_evidence=q.get('evidence') or [],
            threshold=1.0,
            policy_action='ALERT',
            owner='discovery',
            actor='system',
            audit_action='PATTERN_DISCOVER',
        )
    audit('DISCOVER_PATTERNS', 'pattern', 'batch', str(len(pats)))
    return pats


@app.get('/api/v1/metrics')
def metrics(_principal: Principal = Depends(require_roles('FRAUD_ANALYST', 'SENIOR_ANALYST', 'DATA_SCIENTIST', 'ML_ENGINEER', 'AUDITOR'))):
    c = conn()
    alerts_n = c.execute('SELECT COUNT(*) FROM alerts').fetchone()[0]
    cases_n = c.execute('SELECT COUNT(*) FROM cases').fetchone()[0]
    pats = c.execute('SELECT COUNT(*) FROM patterns').fetchone()[0]
    confirmed = c.execute("SELECT COUNT(*) FROM cases WHERE decision='CONFIRMED_FRAUD'").fetchone()[0]
    c.close()
    return {'alerts': alerts_n, 'cases': cases_n, 'patterns': pats, 'confirmed_fraud': confirmed}
