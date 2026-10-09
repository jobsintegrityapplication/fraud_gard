# Local observability

The FastAPI process exposes Prometheus metrics at `GET /metrics`. Application
HTTP counters, latency histograms, status errors, scoring totals, alert and
anomaly counts, case confirmation decisions, training and agent durations,
model errors, score distributions, evaluation metrics, and feature drift are
recorded without transaction, case, customer, or raw request identifiers.

OpenTelemetry tracing is enabled when the app starts. Local development uses a
console span exporter by default, so no collector or Kubernetes is required.
Set `OTEL_EXPORTER_OTLP_ENDPOINT` to send traces to an OTLP gRPC collector, or
set `OTEL_TRACES_EXPORTER=none` to disable export. Request spans record HTTP
method, matched route template, and response status; they never record query
strings, raw paths, request bodies, credentials, or exception messages.

## Start the app locally

Install dependencies and start the API as usual:

```powershell
pip install -r requirements.txt
$env:AUTH_ENABLED = 'false'
$env:DEV_AUTH_ROLE = 'FRAUD_ANALYST'
uvicorn app.main:app --reload
```

The local auth setting is for development only. Use the application's normal
OIDC settings outside a local environment.
Change `DEV_AUTH_ROLE` to `ADMIN`, `ML_ENGINEER`, or another role as needed for
role-restricted endpoints; the local development identity receives only that
role.

## Start Prometheus and Grafana

In another terminal, from the repository root:

```powershell
docker compose -f observability/docker-compose.yml up -d
```

Open Grafana at `http://localhost:3000` (local default credentials: `admin` /
`admin`) and Prometheus at `http://localhost:9090`. The provisioned dashboard is
**Fraud Platform / Handset Fraud Platform Observability**. Prometheus scrapes
the locally running API at `host.docker.internal:8000`; the compose file adds a
host-gateway mapping for Linux as well. The app remains a normal local process;
only the optional metrics UI and scraper run in containers.

The dashboard includes API request rate, p95 latency and errors; fraud alerts
and anomalies; precision, recall, PR-AUC, FPR and FNR; score distributions;
feature drift (including PSI from `POST /api/v1/monitoring/drift/run`); training and agent workflow duration; and transactions,
confirmed fraud, model errors, and agent failures. Metrics appear after the
corresponding workflow has run. Training metrics are per model version and
evaluation split. Counters are process-local and reset when the API restarts.

## Metric names

- `api_requests_total`, `api_request_duration_seconds`, `api_errors_total`
- `transactions_processed_total`, `fraud_alerts_total`, `anomalies_detected_total`, `confirmed_fraud_total`
- `model_predictions_total`, `model_errors_total`, `model_training_duration_seconds`
- `agent_workflow_duration_seconds`, `agent_failures_total`
- `model_score_distribution`
- `fraud_model_precision`, `fraud_model_recall`, `fraud_model_pr_auc`,
  `fraud_model_false_positive_rate`, `fraud_model_false_negative_rate`
- `fraud_model_feature_drift_stddev`
- `fraud_data_drift_psi`
