# Architecture Review

**Repository:** Autonomous Handset Fraud Management Platform MVP  
**Review type:** Read-only inspection of existing code. No implementation changes were made.  
**Scope:** Backend, frontend, ML, synthetic generator, tests, Docker, README, database, API.

---

## 1. Executive summary

The repository is a **single-process FastAPI demo**, not a multi-agent or production fraud platform. It demonstrates a linear happy path:

1. Generate synthetic transactions.  
2. Train XGBoost + Isolation Forest in memory and persist one `joblib` blob.  
3. Optionally cluster a subset with HDBSCAN and write candidate “patterns” to SQLite.  
4. Score one transaction at a time via HTTP.  
5. Open a case from an alert and record an analyst decision.

That path is enough to prove the *idea* of the product. It is **not** the target architecture (canonical model, agents, graph store, MLOps, Angular UI, PostgreSQL, security, observability, or evidence reports). Several advertised pieces are stubs, unused imports, or broken when used through the documented UI flow.

**Architectural style today:** monolith + SQLite + static HTML + file-backed models.

---

## 2. Repository map (source of truth)

Only application source (excluding `.venv`, `.vs`, and generated artifacts):

| Path | Role |
|------|------|
| `app/main.py` | FastAPI app, all HTTP routes, scoring fusion, audit writes |
| `app/db.py` | SQLite schema + connection helper |
| `app/config.py` | Paths: `data/`, `models/`, `reports/`; `fraud.db` |
| `app/ml/features.py` | Fixed 11-column feature frame (fill missing with 0) |
| `app/ml/pipeline.py` | `FraudModels`: train / load / score XGBoost + Isolation Forest |
| `app/services/synthetic.py` | Seeded generator of labeled handset transactions |
| `app/services/graph.py` | Count-based shared IMEI / IP / SIM evidence |
| `app/services/patterns.py` | HDBSCAN clustering → pattern registry rows |
| `frontend/index.html` | Single-page dashboard (inline JS/CSS) |
| `tests/test_smoke.py` | One generator assertion |
| `Dockerfile` | Python 3.11 slim, uvicorn on 8000 |
| `docker-compose.yml` | One service, bind-mounts `data`, `models`, `reports` |
| `requirements.txt` | Pinned Python packages |
| `README.md` | Run instructions and demo steps |
| `.gitignore` | `__pycache__`, `.venv`, `.env`, `models/*.joblib`, `data/*.db` |

There is **no** package layout for agents, Angular app, Alembic/SQLAlchemy, NetworkX, Neo4j client, LangGraph, MLflow, MinIO, Prometheus, Grafana, OpenTelemetry, or OAuth.

`.github/` exists as an empty directory in this extract. There is **no** `.dockerignore`.

---

## 3. Current runtime architecture

```
Browser (frontend/index.html)
        | HTTP
FastAPI (app.main:app)  ─────────────────────────┐
        |                                        |
        |  generate / train / discover / score   |
        |  alerts / cases / metrics              |
        v                                        v
  SQLite  data/fraud.db              models/fraud_models.joblib
  CSV     data/synthetic_handset_transactions.csv
  (reports/ unused)
```

**Process model:** one Uvicorn worker (Compose) or `--reload` locally. All ML and clustering run **synchronously inside the request**. Generate + train on 10k rows can block the event loop for a long time.

**Data stores:**

- SQLite file at `data/fraud.db` (created on startup).  
- Feature-rich CSV written on generate; **not** the same schema as SQLite.  
- Entire `FraudModels` instance dumped with `joblib`.  
- `reports/` is created but never written.

**Identity / tenancy / auth:** none. Every audit actor is the literal string `'system'`.

---

## 4. Backend

### 4.1 Application shape

`app/main.py` is the entire application layer: routing, persistence SQL, scoring policy, and audit. There is no service layer for cases, no repository pattern, no Pydantic models for persistence entities (only `ScoreRequest` and `Decision`).

Startup (`@app.on_event('startup')`, deprecated in FastAPI):

- `init_db()`  
- `models.load()` if `models/fraud_models.joblib` exists

### 4.2 API surface (implemented)

| Method | Path | Behavior |
|--------|------|----------|
| GET | `/` | Serves `frontend/index.html` |
| POST | `/api/v1/demo/generate?n=10000` | Regenerates synthetic data; **deletes all SQLite transactions**; writes CSV; does **not** clear alerts/cases/patterns |
| POST | `/api/v1/train` | Trains on CSV if present, else SQLite rows; writes `models` row with status `CHALLENGER` |
| POST | `/api/v1/score` | Scores one `transaction_id`; inserts `alerts` |
| GET | `/api/v1/alerts` | Last 100 alerts |
| POST | `/api/v1/patterns/discover` | HDBSCAN on a candidate slice; `INSERT OR REPLACE` into `patterns` |
| GET | `/api/v1/patterns` | All patterns |
| POST | `/api/v1/cases/from-alert/{alert_id}` | Creates `OPEN` case |
| POST | `/api/v1/cases/{case_id}/decision` | `CONFIRMED_FRAUD` / `NOT_FRAUD` / `NEED_MORE_EVIDENCE` / `UNCERTAIN` |
| GET | `/api/v1/metrics` | Counts: alerts, cases, patterns, confirmed fraud |

OpenAPI is available via FastAPI defaults (`/docs`, `/redoc`).

### 4.3 Declared but not implemented

- `UploadFile`, `File` imported; **no upload endpoint**. README “upload data” is not wired.  
- `python-multipart` in requirements; unused.  
- `reportlab` in requirements; unused.  
- `REPORT_DIR` in config; unused.  
- `GET /api/v1/cases`, case list, case detail, assignment, SLA, comments: **absent**.  
- Batch score, stream ingest, webhooks: **absent**.

### 4.4 Scoring policy (decision fusion — not a Decision Engine)

```
combined = max(xgboost, 0.7 * anomaly, graph_score, pattern_score)
risk = CRITICAL ≥ 0.85 | HIGH ≥ 0.65 | MEDIUM ≥ 0.40 | LOW
```

Pattern score is **not** the HDBSCAN registry. It is a hardcoded prefix check: IMEI starts with `I_FRAUD` or IP starts with `IP_FRAUD`.

Graph score is **not** a graph algorithm. It is frequency of the same IMEI / IP / SIM in the loaded DataFrame vs fixed thresholds (8 / 12 / 6).

---

## 5. Database

SQLite schema (`app/db.py`):

| Table | Purpose | Gaps vs target CDM |
|-------|---------|-------------------|
| `transactions` | 11 columns: ids, IMEI, SIM, IP, merchant, location, amount, ts, label | No customer/account/SIM/device master tables; engineered features not stored |
| `alerts` | Per-score snapshot | No status, assignee, dedup key, TTL |
| `cases` | One case per create-from-alert | No analyst, queue, linked entities, versioned evidence |
| `patterns` | Cluster signatures JSON | No lifecycle beyond `DISCOVERED`; no promotion/approval |
| `audit` | Append-only-ish rows | Mutable SQLite; actor always `system`; not immutable |
| `models` | `algorithm`, `status`, `metrics_json` | Status always `CHALLENGER`; never champion, never loaded by id |

**Canonical data model:** absent. There is one denormalized transaction fact table plus operational tables. Entity relationships (customer–account–SIM–IMEI–IP–merchant) exist only as string IDs on the fact row.

**Integrity:** no FKs, no indexes except PKs, no unique constraint on alert `(transaction_id)` (re-score duplicates alerts).

---

## 6. ML code

### 6.1 Feature engineering (`app/ml/features.py`)

Declared features:

`amount`, `account_age_days`, `imei_account_count`, `imei_sim_count`, `ip_account_count`, `merchant_fraud_rate`, `txn_velocity_1h`, `location_distance_km`, `device_change_rate`, `sim_change_rate`, `hour_deviation`

**These are not engineered from raw events.** The generator *samples* them. `build_features` copies columns if present, else **zeros**.

Consequence: train path that reads the CSV has rich features; score path that reads SQLite has only `amount` (and zeros). Models are **not production-scorable against the database**.

### 6.2 Supervised model

- `XGBClassifier` (180 trees, depth 5, lr 0.08), `eval_metric='logloss'`.  
- Fit on **all rows**, labels included. **No train/validation/test split, no time holdout, no calibration.**  
- Metrics (precision, recall, F1, PR-AUC, ROC-AUC) are **in-sample** and will look optimistic.

### 6.3 Anomaly model

- `IsolationForest` fit on **label == 0** rows only (or all X if no negatives).  
- Score: `1 / (1 + exp(4 * decision_function))` (higher IF decision = more normal → lower anomaly score). Reasonable direction, uncalibrated.

### 6.4 Persistence

`joblib.dump(self, MODEL_DIR/'fraud_models.joblib')` serializes the whole `FraudModels` instance. Load mutates `__dict__`. No MLflow run, no signature, no model version API, no shadow/canary artifacts.

### 6.5 Pattern discovery

HDBSCAN on 8 scaled numeric columns. Clusters with `cl >= 0` and size ≥ 5 become `DISCOVERED` patterns. Noise (`-1`) dropped. No mapping of new transactions to existing clusterers (`prediction_data=False`). Registry is not used at score time.

---

## 7. Synthetic data generator

`app/services/synthetic.py` `generate(n=10000, seed=42)`:

- ~8% fraud label.  
- Shared ring artifacts: ~45% of fraud rows get `I_FRAUD_RING_01` / `IP_FRAUD_01`.  
- Inflated amount / young accounts on a subset of fraud.  
- Locations: BLR, MUM, DEL, HYD, CHN.  
- Feature columns sampled with fraud boosts (velocity, distance, device/SIM change, hour deviation, merchant M001).

This is **label-aware feature fabrication**, not a realistic event log from which features can be rebuilt. It is useful for a demo; it is harmful if treated as a training contract for production feature pipelines.

---

## 8. Frontend

Single file `frontend/index.html`:

- Buttons: Generate 10K, Train, Discover Patterns, Refresh Alerts, Metrics.  
- Create Case per alert.  
- **No** transaction picker / score form. README step 4 requires calling `POST /api/v1/score` outside the UI.  
- **No** case list, decision form, pattern approval, model metrics chart, login.  
- Helper `api()` writes **every** JSON response into the Patterns `<pre>`, including generate/train/metrics.

Not Angular. Not TypeScript. No build, routing, or state management.

---

## 9. Tests

`tests/test_smoke.py`:

- Generates 1000 rows.  
- Asserts length and labels ⊆ {0, 1}.

Missing: API tests, schema tests, feature-parity tests, graph tests, pattern tests, decision tests. `pytest` is **not** in `requirements.txt` (may be installed globally). No CI workflow files in this extract.

---

## 10. Docker

- **Dockerfile:** `python:3.11-slim`, `pip install -r requirements.txt`, `COPY . .`, `uvicorn app.main:app --host 0.0.0.0 --port 8000`.  
- **Compose:** one service `fraud-platform:8000`, volume mounts for data/models/reports.  
- **Issues:** no `.dockerignore` (risk of copying `.venv` / `.vs` into the image); no healthcheck; no Postgres/Neo4j/MinIO/MLflow/Prometheus/Grafana sidecars; Python 3.11 in Docker vs 3.12 commonly used locally.

---

## 11. README vs code

README accurately lists the *intended* demo components. It overstates:

- “Graph-style” analysis (counts, not a graph).  
- “Fraud Pattern Registry” (insert-only clusters, unused at score).  
- “Human case/decision API” (API exists; UI incomplete; no HITL workflow).  
- “Challenger” naming in `models` table without a champion lifecycle.  
- Upload path implied by train error message / unused FastAPI imports.

It correctly warns this is not a production telecom deployment.

---

## 12. Agents vs current code

Target agents do not exist as processes, graphs, or modules.

| Target agent | Current analogue |
|--------------|------------------|
| Master Orchestrator | None (operator clicks HTTP) |
| Fraud Detection | `FraudModels.xgb` inside `POST /score` and `/train` |
| Anomaly Discovery | Isolation Forest in same request + HDBSCAN on `/patterns/discover` |
| Investigation | Case JSON blob = copy of alert row |
| Evidence/Graph | `graph_score` counts |
| Decision | `max()` of four floats + risk bands |
| Human Review | `POST .../decision` |
| Learning/Retraining | Manual `/train`; no feedback loop from case labels |
| Report | Unused `reportlab` / `reports/` |

---

## 13. Strengths worth keeping

1. Small, readable demo surface — FastAPI + SQLite is a valid **spike**.  
2. Seeded generator is deterministic (`seed=42`).  
3. Audit table exists as a hook for later immutable logging.  
4. Risk reason strings already mix model and graph snippets — good UX seed.  
5. Docker Compose one-command run is documented.  
6. Decision vocabulary (`CONFIRMED_FRAUD`, etc.) matches a HITL case model.

---

## 14. Architectural risks (priority)

1. **Train/serve skew:** CSV features vs SQLite schema. Scoring and discovery against DB are incorrect or crash.  
2. **Monolithic request-thread ML:** no queue, no timeout, no isolation.  
3. **Leakage:** in-sample metrics; features derived from the label in the generator.  
4. **No security boundary.**  
5. **No source-of-truth CDM** — every later agent will fight the 11-column fact table.  
6. **Pattern registry and models table are decorative** relative to scoring.  
7. **Frontend cannot complete the README demo** without extra API calls.

See `IMPLEMENTATION_GAP.md` for target-architecture comparison, broken items, dependency gaps, recommended order, and files to modify later (not in this review pass).
