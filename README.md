# Autonomous Handset Fraud Management Platform MVP

Local fraud management MVP with an Angular Material console, FastAPI API,
model scoring/training workflows, PostgreSQL canonical schema, MLflow tracking,
RustFS S3-compatible artifact storage, and Prometheus/Grafana observability.

## Run the complete stack on Windows

Install Docker Desktop and start it. From this repository directory, run these
commands in PowerShell:

```powershell
docker compose up --build
```

Open the application at <http://localhost:8080>. The API is at
<http://localhost:8000/docs>, MLflow at <http://localhost:5000>, MinIO at
<http://localhost:9001>, Prometheus at <http://localhost:9090>, and Grafana at
<http://localhost:3000>. The local Grafana image uses its standard first-run
admin prompt. The Compose setup provisions the Prometheus datasource and the
Fraud Platform dashboard automatically.

Useful commands, in PowerShell:

```powershell
docker compose down
docker compose logs -f backend
docker compose logs -f frontend
```

`docker compose down` keeps named volumes and their data. To stop the stack,
press `Ctrl+C` in the foreground `up` terminal, then run `docker compose down`.

Compose uses local-only development defaults so the exact `up --build` command
works without setup. To customize ports or those local settings, copy the
placeholder file and edit it:

```powershell
Copy-Item .env.example .env
```

`.env.example` contains placeholders only. The local default disables OIDC and
assigns the development identity the `ADMIN` role so every MVP screen can be
used. Keep this setting limited to local development; use a real OIDC provider
and role claims outside this stack. No production credentials are included.

Optional services can be started with profiles:

```powershell
docker compose --profile neo4j up --build
docker compose --profile local-llm up --build
```

The optional Neo4j profile provisions the graph database; the MVP defaults to
its in-process NetworkX graph backend. The optional Ollama profile starts a
local model server, but does not download a model automatically.

## Local development without Docker

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:AUTH_ENABLED = 'false'
$env:DEV_AUTH_ROLE = 'ADMIN'
uvicorn app.main:app --reload
```

For the Angular frontend without containers, install Node.js 20+, then:

```powershell
Set-Location frontend/fraud-console
npm ci
npm start
```

The Angular dev server uses `proxy.conf.json` to forward `/api` to the local
backend.

## Compose services and persistence

| Service | Purpose | Local URL |
| --- | --- | --- |
| `frontend` | Angular Material console, Nginx SPA hosting and API proxy | `http://localhost:8080` |
| `backend` | FastAPI, model APIs, drift monitoring and Prometheus metrics | `http://localhost:8000` |
| `postgres` | Canonical SQLAlchemy schema and MLflow metadata database | `localhost:5432` |
| `minio` | RustFS S3-compatible MLflow artifact storage and console | `http://localhost:9001` |
| `mlflow` | Experiment tracking UI and API | `http://localhost:5000` |
| `prometheus` | Metrics scraper | `http://localhost:9090` |
| `grafana` | Provisioned observability dashboard | `http://localhost:3000` |

Health checks gate dependent services. Named volumes retain PostgreSQL,
MinIO, application SQLite data, models, reports, Prometheus, Grafana, Neo4j,
and Ollama data. The current operational demo endpoints use the SQLite database
at `data/fraud.db`, stored in the persistent `app-data` volume; PostgreSQL is
initialized with the canonical Alembic schema and is used by MLflow for run
metadata.

## Demo flow

1. Open the console and generate demo data.
2. Train models and discover patterns.
3. Review alerts and create cases.
4. Record an analyst decision and generate an investigation report.
5. Start a drift check at `POST /api/v1/monitoring/drift/run` or inspect the
   Prometheus metrics at `/metrics`.

The default dev identity has the `ADMIN` role. Drift checks create a pending
retraining recommendation when configured thresholds are crossed; they never
automatically train a model.

## Data generation

Writes entity extracts to `data/raw/`, full events and ground truth to
`data/generated/`, and ML-safe tables to `data/processed/`. Ground-truth fraud
reasons live only in `data/generated/ground_truth.csv` and are not model
features.

```powershell
python -m app.data.generate --transactions 10000 --seed 42
python -m app.data.generate --transactions 50000 --seed 42
```

## Documentation

- [Production readiness assessment](PRODUCTION_READINESS.md)
- [Security review](SECURITY_REVIEW.md)
- [Known limitations](KNOWN_LIMITATIONS.md)
- [Observability and local Grafana](docs/OBSERVABILITY.md)
- [Model and data drift policy](docs/MODEL_DATA_DRIFT.md)
- [Security foundation](docs/SECURITY_FOUNDATION.md)
- [Canonical domain ERD](docs/architecture/canonical_domain_erd.md)

This is a runnable reference MVP, not a production telecom fraud deployment.
