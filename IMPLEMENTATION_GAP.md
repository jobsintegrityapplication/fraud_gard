# Implementation Gap

**Purpose:** Compare the current repository to the stated target architecture, agents, and technology list.  
**Constraint honored:** inspection only; no product code rewritten or deleted.

Legend: **Present** (usable), **Partial** (stub / demo-quality), **Missing**, **Broken** (implemented path fails or is misleading).

---

## 1. Pipeline vs current repository

| Target stage | Status | What exists | Gap |
|--------------|--------|-------------|-----|
| Data ingestion | Partial | `POST /api/v1/demo/generate` writes SQLite + CSV | No connectors, files, streams, batch jobs, schemas, idempotency. `UploadFile` imported but unused. |
| Data quality | Missing | None | No completeness, uniqueness, IMEI/Luhn, timestamp order, outlier, referential checks, quarantine. |
| Canonical data model | Missing | One `transactions` fact table | No Customer, Account, SIM, Handset/IMEI, IP session, Merchant, Event, Label, Feature, Alert, Case, Pattern, Model, Decision entities with keys and history. |
| Feature engineering | Broken / Partial | `build_features` + generator-sampled columns | Features are **not computed from events**. Train uses CSV; score uses DB (zeros). No point-in-time correctness, no feature store. |
| XGBoost known-fraud | Partial | `XGBClassifier` train + `predict_proba` | No split/holdout, no calibration, no threshold policy, train/serve skew. |
| Isolation Forest anomaly | Partial | Fit on non-fraud rows; sigmoid of `decision_function` | Unsupervised path mixed into same train job; no separate cohorting or explanation. |
| HDBSCAN pattern discovery | Broken (via API on DB) / Partial (if CSV columns present) | `discover()` clusters numeric columns | `/patterns/discover` loads SQLite rows **without** those columns → `KeyError`. Registry unused at score. |
| Fraud Pattern Registry | Partial | `patterns` table, GET list, INSERT OR REPLACE | No states beyond `DISCOVERED`, no human promote/reject, no signature matching engine. |
| Graph fraud-ring analysis | Partial | `graph_score` frequency thresholds | Not NetworkX, not Neo4j, no ring detection, no community metrics, no entity graph persistence. |
| Investigation | Partial | Case `evidence_json` = alert dict | No timeline, entity resolution, linked txns, analyst notes, evidence packaging. |
| HITL case management | Partial | Create from alert + decision API | No GET cases, queues, assignment, RBAC, UI for decisions, SLA, dual control. |
| Decision Engine | Missing | `max()` fusion + risk bands in `score()` | No rules, policy versioning, suppressions, mandatory evidence, champion/challenger routing. |
| Continuous learning | Missing | Manual `/train` | Case labels never join training set; no drift-triggered retrain. |
| Challenger model | Partial (name only) | `models.status = 'CHALLENGER'` | No champion, no eval harness, no traffic split. New train does not register a comparable run. |
| Model validation | Missing | In-sample precision/recall/F1/PR-AUC/ROC-AUC | No temporal holdout, fairness, stability, PSI, approval gate. |
| Shadow deployment | Missing | — | No silent scoring of production traffic vs champion. |
| Canary deployment | Missing | — | No % rollout, no kill switch. |
| Production serving | Partial | Single `/score` endpoint | Sync, unauthenticated, no SLO, no batch, no model-id pin. |
| Monitoring | Missing | `GET /metrics` is **business counts**, not Prometheus | No model metrics, latency, data quality, alert volume dashboards. |
| Rollback | Missing | — | Single `fraud_models.joblib` overwrite; no prior artifact restore API. |
| Evidence-based report | Missing | `reportlab` + `reports/` unused | No PDF/HTML case or model-validation report. |

---

## 2. Agents vs current repository

| Agent | Status | Notes |
|-------|--------|-------|
| 1. Master Orchestrator | Missing | No LangGraph / workflow. Operator is the orchestrator via HTTP buttons. |
| 2. Fraud Detection | Partial | XGBoost inside FastAPI process. |
| 3. Anomaly Discovery | Partial | Isolation Forest + HDBSCAN; not a separate agent or schedule. |
| 4. Investigation | Partial | Case record only. |
| 5. Evidence/Graph | Partial | Count evidence strings. |
| 6. Decision | Partial | Inline score fusion. |
| 7. Human Review | Partial | Decision enum on cases; no review UI/workbench. |
| 8. Learning/Retraining | Missing | No closed loop. |
| 9. Report | Missing | Dependency present, code absent. |

There is no agent protocol (messages, state, retries, human-in-the-loop interrupt, audit of agent actions distinct from HTTP).

---

## 3. Technology target vs current stack

| Target | Status | Current |
|--------|--------|---------|
| Python | Present | 3.11 in Docker; local extract ran 3.12 |
| FastAPI | Present | 0.116.1 |
| Pydantic | Partial | Only two request models (`ScoreRequest`, `Decision`) |
| Angular + TypeScript | Missing | `frontend/index.html` vanilla JS |
| PostgreSQL | Missing | SQLite `data/fraud.db` |
| XGBoost | Present | 3.0.4 |
| scikit-learn Isolation Forest | Present | 1.7.1 |
| HDBSCAN | Present | 0.8.40 |
| NetworkX (initial) | Missing | Pandas boolean counts |
| Neo4j production interface | Missing | — |
| LangGraph | Missing | — |
| MLflow | Missing | `joblib` file |
| MinIO/S3 | Missing | Local `data/`, `models/`, `reports/` dirs |
| Docker Compose | Partial | One app container |
| Prometheus | Missing | — |
| Grafana | Missing | — |
| OpenTelemetry | Missing | — |
| OAuth2/OIDC/RBAC | Missing | Open API |
| Pytest | Partial | One test file; **pytest not in `requirements.txt`** |

### Dependency issues (requirements and runtime)

1. **`pytest` not declared** — CI/dev install from `requirements.txt` cannot run tests as specified.  
2. **`reportlab` and `python-multipart` unused** — dead weight; multipart implies an unfinished upload API.  
3. **`hdbscan` on Windows** historically needs a wheel or compiler; a wheel exists for cp312 win_amd64 in this environment, but Docker `python:3.11-slim` may need build tools if a wheel is missing.  
4. **No `.dockerignore`** — `COPY . .` can ingest `.venv`, `.vs`, local `data/*.db`, leaking size and host artifacts.  
5. **No lockfile** (`poetry.lock` / `uv.lock` / pip-tools). Transitive versions float except top-level pins.  
6. **Joblib + XGBoost/sklearn version coupling** — models trained on 3.12 locally may fail to load in 3.11 Docker (or the reverse) without a documented matrix.  
7. **Missing target libraries** (to add later, not now): `networkx`, `neo4j`, `langgraph`, `mlflow`, `boto3` or `minio`, `psycopg`/`sqlalchemy`, `prometheus-client`, `opentelemetry-*`, `authlib`/`python-jose`, Angular toolchain.  
8. **Starlette deprecation:** `@app.on_event('startup')` should become lifespan handlers when the app is evolved.  
9. **SQLite + concurrent writes** under Uvicorn reload / multiple workers is unsafe for the intended Compose “production-like” run.  
10. **Frontend `fetch` same-origin only** — fine for FileResponse; an Angular app on another origin will need CORS (absent).

---

## 4. Existing functionality (what actually works)

When used carefully (generate → train on CSV → score by known `transaction_id` via API → create case → decision API):

- Synthetic 10k (or `n`) generation with ~8% fraud and planted ring IDs.  
- Persist transactions to SQLite + CSV.  
- Train XGBoost + Isolation Forest on **CSV feature columns**; persist `fraud_models.joblib`; store in-sample metrics in `models`.  
- Score a transaction **if** the operator supplies `transaction_id` (not in UI). Combined risk + reason list. Alert row written.  
- List alerts; create case; close or leave in `REVIEW` via decision codes.  
- Metrics counts.  
- Audit rows for generate/train/score/discover/case/decision.  
- Serve dashboard at `/`.  
- Docker one-service build (assuming image build succeeds).  
- Generator unit smoke test.

---

## 5. Broken or misleading functionality

These are defects in the *current* MVP, independent of the larger target.

| ID | Issue | Why |
|----|--------|-----|
| B1 | **Feature train/serve skew** | `transactions` SQLite schema omits engineered columns. `score()` → `load_df()` → `build_features` fills zeros except `amount`. Model trained on CSV is not the model being applied in production-like scoring. |
| B2 | **`POST /api/v1/patterns/discover` fails after generate** | `load_df()` then `sort_values(['txn_velocity_1h','imei_account_count','ip_account_count'])` — those columns are not in SQLite. `KeyError`. Dashboard button 3 is broken. |
| B3 | **Pattern score ignores registry** | Score uses `imei.startswith('I_FRAUD') or ip.startswith('IP_FRAUD')`. Synthetic cheat code, not HDBSCAN signatures. |
| B4 | **README demo step 4 cannot be done in UI** | No score control; alerts stay empty until an external `POST /api/v1/score`. |
| B5 | **No case workbench** | Create case returns JSON in `alert()`; no list, no decision form. README step 6 is API-only. |
| B6 | **Generate does not reset dependents** | `DELETE FROM transactions` only. Orphan alerts/cases; patterns remain. |
| B7 | **Re-score duplicates alerts** | No upsert/dedup. |
| B8 | **`api()` clobbers Patterns panel** | Generate/train/metrics JSON overwrites `#patterns`. |
| B9 | **Challenger is a label** | Every train inserts `CHALLENGER`; scoring always uses the last `joblib` file, not `model_id`. |
| B10 | **In-sample metrics** | Train metrics are not a validation result; README/API present them as `metrics`. |
| B11 | **Label leakage in generator** | Fraud flag drives feature magnitudes and special IDs; models learn the fabrication, not causal structure. |
| B12 | **Upload path advertised by imports / train error text** | “Generate or upload data first” but no upload route. |
| B13 | **Reports unused** | `reportlab` never imported in app code. |
| B14 | **Graph is not a graph** | Thresholded counts; shared IMEI across non-fraud noise can inflate. |
| B15 | **Docker copy hygiene** | No `.dockerignore`; image may be huge or non-reproducible. |
| B16 | **Home FileResponse path** | `DATA_DIR.parent/'frontend'/'index.html'` works if cwd/layout match image `WORKDIR /app`; fragile if package installed differently. |

---

## 6. Missing functionality (target, not present)

Grouped for implementation planning.

**Data platform:** ingest adapters, DQ service, canonical schema + migrations (PostgreSQL), raw vs curated layers, PII handling, object storage (MinIO/S3).

**Features:** real aggregations (velocity, device/SIM change, merchant rates) with point-in-time joins; feature view versioning.

**MLOps:** MLflow tracking/registry, validation suite, champion/challenger, shadow, canary, rollback of artifact + config, model cards.

**Graph:** NetworkX in-process graph for MVP rings; Neo4j driver + Cypher interface behind a port.

**Agents:** LangGraph master graph invoking detection, anomaly, investigation, evidence, decision, human review, learning, report subgraphs; durable state.

**HITL:** Angular workbench — queues, case 360, decision with reason codes, dual review, audit viewer.

**Decision / policy:** versioned rules + ML scores + pattern hits + graph evidence; explainability payload.

**Learning:** confirmed/not-fraud labels → training set; scheduled retrain; approval.

**Observability:** OpenTelemetry traces, Prometheus metrics (`/metrics` scrape), Grafana dashboards, model performance monitors.

**Security:** OIDC login, RBAC (analyst, senior, model-ops, admin), secrets, TLS.

**Reports:** evidence PDF/HTML from case + model validation pack.

**Tests:** API, DQ, features, models, graph, agents, auth; pytest in requirements + CI.

---

## 7. Recommended implementation order

Do **not** start with LangGraph, Neo4j, or Angular chrome. Fix the broken demo contract first, then grow toward the target **without deleting** the current FastAPI spike (wrap and extend).

### Phase 0 — Make the existing MVP honest and runnable (stabilize)

1. Persist **all columns** used in training (or stop training from CSV-only fields). Unify `load_df()` / CSV / SQLite.  
2. Fix `patterns/discover` to use the same feature frame as train.  
3. Wire score + case decision into the existing HTML **or** document API-only clearly after B4/B5 are fixed.  
4. Add `.dockerignore`, pytest to requirements, API smoke tests for generate → train → score → case.  
5. Clear or archive child tables on regenerate.  
6. Use registry or remove pattern_score cheat; do not leave both unexplained.

**Outcome:** README demo works; train/serve parity.

### Phase 1 — Canonical data + PostgreSQL + Pydantic

1. Introduce CDM tables (keep SQLite adapter behind a repository so `app/db.py` can remain until cutover).  
2. Alembic/SQLAlchemy; Pydantic schemas for Transaction, Alert, Case, Pattern, ModelRun.  
3. Data-quality checks on ingest.  
4. Compose: `postgres` service; migrate `fraud-platform`.

### Phase 2 — Real features + graph MVP

1. Compute features from stored events (NetworkX graph for shared IMEI/SIM/IP).  
2. Replace count thresholds with graph metrics (degree, shared-entity subgraph).  
3. Pattern registry matching at score time.  
4. Neo4j **interface** module (protocol) with NetworkX implementation first.

### Phase 3 — Decision engine + HITL API completeness

1. Extract fusion/rules from `main.py` into a decision service with versioned config.  
2. Case APIs: list, get, assign, comment, decision, evidence bundle.  
3. Audit actor from identity (even if still a header stub).

### Phase 4 — Angular workbench

1. New `web/` Angular app consuming FastAPI; keep `frontend/index.html` as fallback until cutover.  
2. Screens: ingest status, alerts, case 360, patterns, models, audit.  
3. CORS + environment configs.

### Phase 5 — MLOps (MLflow, MinIO, validation, shadow/canary/rollback)

1. MinIO for artifacts; MLflow tracking + registry.  
2. Validation job gate before `CHAMPION`.  
3. Shadow scoring path; canary %; rollback to previous registry version.  
4. Continuous learning job from `CONFIRMED_FRAUD` / `NOT_FRAUD`.

### Phase 6 — Agents (LangGraph)

1. Master orchestrator graph wrapping existing services (do not rewrite ML).  
2. One agent per target role, same APIs underneath.  
3. Human Review as an interrupt node.

### Phase 7 — Production hardening

1. OAuth2/OIDC/RBAC.  
2. OpenTelemetry + Prometheus + Grafana in Compose.  
3. Evidence reports (`reportlab` or a dedicated renderer).  
4. Horizontal concerns: queues for train/discover, healthchecks, secrets.

---

## 8. Exact files that need modification (later)

No edits in this pass. When implementation starts, these **existing** files will need changes (extend, do not delete wholesale):

| File | Why it must change |
|------|-------------------|
| `app/main.py` | Train/serve data path; discover crash; unused imports; extract routes; lifespan; decision/score policy; case GET; upload or remove claim; CORS; auth hooks; metrics vs Prometheus. |
| `app/db.py` | Schema for missing feature columns / CDM; later PostgreSQL. |
| `app/config.py` | DB URL, object store, MLflow, Neo4j, auth, report paths. |
| `app/ml/features.py` | Real feature computation; versioned feature lists; no silent zeros in prod. |
| `app/ml/pipeline.py` | Splits, validation metrics, MLflow log, separate IF/XGB artifacts, load-by-version. |
| `app/services/synthetic.py` | Optional: generate raw events + labels separately from features to reduce leakage. |
| `app/services/graph.py` | NetworkX; later Neo4j adapter. |
| `app/services/patterns.py` | Persist clusterer; match at score; pattern lifecycle. |
| `frontend/index.html` | Score UI, cases/decisions, stop clobbering patterns panel (until Angular). |
| `tests/test_smoke.py` | Keep; add API/ML/graph tests alongside. |
| `requirements.txt` | pytest; later stack libs; remove or use reportlab/multipart. |
| `Dockerfile` | `.dockerignore` companion; healthcheck; non-root; Python pin aligned with lockfile. |
| `docker-compose.yml` | Postgres, MinIO, MLflow, Prometheus, Grafana, Neo4j as they are introduced. |
| `README.md` | Align demo with real UI/API; document architecture phases. |
| `.gitignore` | Angular `dist`, MLflow, env files, coverage. |

### Files / trees to **add** (not present today)

- `.dockerignore`  
- `app/schemas/` (Pydantic CDM)  
- `app/repositories/`  
- `app/services/quality.py`, `decision.py`, `reports.py`, `learning.py`  
- `app/agents/` (LangGraph)  
- `app/graph/neo4j_client.py` + `networkx_backend.py`  
- `alembic/`  
- `web/` (Angular)  
- `observability/` (Prometheus scrape, Grafana dashboards, OTEL)  
- `tests/api/`, `tests/ml/`, `tests/graph/`, `tests/agents/`  
- `.github/workflows/ci.yml`  
- Optional: `pyproject.toml`

**Do not delete** the current `app/main.py` spike until routes are incrementally extracted. The MVP is the integration test harness for later phases.

---

## 9. Mapping summary

| Question | Answer |
|----------|--------|
| Current architecture | Monolithic FastAPI + SQLite + joblib + static HTML |
| Existing functionality | Generate, train (CSV), score (skewed), alerts, case create/decision API, audit, HDBSCAN write, Docker single service |
| Missing functionality | CDM, DQ, real features, agents, Angular, Postgres, NetworkX/Neo4j, LangGraph, MLflow, MinIO, Prom/Grafana/OTEL, OIDC/RBAC, MLOps lifecycle, reports |
| Broken functionality | Discover-on-DB, score feature zeros, UI demo gap, pattern cheat vs registry, unused upload/report, challenger no-op |
| Dependency issues | pytest missing; unused libs; no dockerignore; 3.11 vs 3.12; hdbscan/slim risk; no target-stack packages |
| Implementation order | Stabilize MVP → CDM/Postgres → features/graph → decision/HITL APIs → Angular → MLOps → LangGraph agents → observability/security/reports |
| Files to modify | Listed in §8; plus new packages. Existing code remains the core until wrapped. |
