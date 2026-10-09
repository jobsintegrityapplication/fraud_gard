# Model and data drift monitoring

`POST /api/v1/monitoring/drift/run` compares an older reference window with the
most recent transaction window. It monitors important numeric transaction and
engineered fields (`amount`, account/device/network counts and rates, velocity,
location distance, and temporal deviation), plus the categorical `merchant_id`
and `location` features. Identifiers such as customer, account, SIM, IMEI, IP,
and transaction IDs are not drift features and are not returned.

The reference window is the oldest 70% of timestamped transaction rows; the
current window is the newest 30%. At least 10 rows are required in each window.
Numeric and categorical feature distributions use PSI. Numeric features also
include a Kolmogorov-Smirnov statistic in the response. Each result has a
warning `threshold` and status; `critical_threshold` is included as well.

Default policy uses PSI warning at `0.10` and critical at `0.25`. Configure
these values with `DRIFT_PSI_WARNING` and `DRIFT_PSI_CRITICAL`. The
`DRIFT_RETRAINING_RECOMMENDATION_POLICY` setting controls recommendations:

- `warning` (default): create a persisted `PENDING_REVIEW` recommendation if
  any feature is WARNING or CRITICAL.
- `critical`: create one only if any feature is CRITICAL.
- `disabled`: record drift results without creating a recommendation.

A recommendation is a human review item, not an executable training job. Drift
monitoring never schedules the learning executor or retrains a model. Runs and
recommendations are persisted in `drift_monitor_runs` and
`retraining_recommendations`; aggregate PSI is also exposed as
`fraud_data_drift_psi` for Prometheus/Grafana.
