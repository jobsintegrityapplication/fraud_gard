"""Prometheus metrics and privacy-conscious OpenTelemetry tracing."""
from __future__ import annotations

import os
import time
from functools import wraps

from prometheus_client import Counter, Gauge, Histogram, make_asgi_app

API_REQUESTS = Counter('api_requests_total', 'HTTP requests handled', ('method', 'route', 'status'))
API_LATENCY = Histogram('api_request_duration_seconds', 'HTTP request latency', ('method', 'route'))
API_ERRORS = Counter('api_errors_total', 'HTTP requests returning an error', ('method', 'route', 'status'))
TRANSACTIONS = Counter('transactions_processed_total', 'Transactions processed by scoring workflows')
FRAUD_ALERTS = Counter('fraud_alerts_total', 'Fraud alerts created', ('risk_level',))
ANOMALIES = Counter('anomalies_detected_total', 'Anomalies detected')
CONFIRMED_FRAUD = Counter('confirmed_fraud_total', 'Confirmed fraud case decisions')
MODEL_PREDICTIONS = Counter('model_predictions_total', 'Model predictions completed', ('model',))
MODEL_ERRORS = Counter('model_errors_total', 'Model prediction or training failures', ('model',))
TRAINING_DURATION = Histogram('model_training_duration_seconds', 'Model training duration', ('model',))
AGENT_DURATION = Histogram('agent_workflow_duration_seconds', 'Agent workflow duration')
AGENT_FAILURES = Counter('agent_failures_total', 'Agent workflow failures')
MODEL_SCORE = Histogram(
    'model_score_distribution', 'Distribution of model scores', ('model', 'score_type'),
    buckets=(0, .05, .1, .2, .3, .4, .5, .6, .7, .8, .9, .95, 1.0),
)
MODEL_PRECISION = Gauge('fraud_model_precision', 'Precision on evaluated data', ('model_version', 'split'))
MODEL_RECALL = Gauge('fraud_model_recall', 'Recall on evaluated data', ('model_version', 'split'))
MODEL_PR_AUC = Gauge('fraud_model_pr_auc', 'PR-AUC on evaluated data', ('model_version', 'split'))
MODEL_FPR = Gauge('fraud_model_false_positive_rate', 'False positive rate on evaluated data', ('model_version', 'split'))
MODEL_FNR = Gauge('fraud_model_false_negative_rate', 'False negative rate on evaluated data', ('model_version', 'split'))
FEATURE_DRIFT = Gauge('fraud_model_feature_drift_stddev', 'Feature mean shift in champion standard deviations', ('model_version', 'feature'))
DATA_DRIFT_PSI = Gauge('fraud_data_drift_psi', 'Population stability index for monitored transaction features', ('feature',))


def record_model_evaluation(model_version: str, split: str, metrics: dict) -> None:
    """Record only bounded model metadata and numeric aggregate metrics."""
    labels = (str(model_version)[:80], split if split in {'train', 'valid', 'test'} else 'other')
    for gauge, key in (
        (MODEL_PRECISION, 'precision'), (MODEL_RECALL, 'recall'),
        (MODEL_PR_AUC, 'pr_auc'), (MODEL_FPR, 'false_positive_rate'),
        (MODEL_FNR, 'false_negative_rate'),
    ):
        value = metrics.get(key)
        if value is not None:
            gauge.labels(*labels).set(float(value))


def record_feature_drift(model_version: str, shifts: dict) -> None:
    version = str(model_version)[:80]
    for feature, value in shifts.items():
        FEATURE_DRIFT.labels(version, str(feature)[:100]).set(float(value))


def configure_tracing() -> None:
    """Initialize a local console exporter by default, or OTLP when configured."""
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, ConsoleSpanExporter
        if getattr(trace.get_tracer_provider(), '_fraud_configured', False):
            return
        provider = TracerProvider(resource=Resource.create({'service.name': os.getenv('OTEL_SERVICE_NAME', 'handset-fraud-platform')}))
        endpoint = os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT')
        exporter = os.getenv('OTEL_TRACES_EXPORTER', 'otlp' if endpoint else 'console').lower()
        if exporter == 'otlp':
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint) if endpoint else OTLPSpanExporter()))
        elif exporter == 'console':
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        provider._fraud_configured = True
        trace.set_tracer_provider(provider)
    except ImportError as exc:
        raise RuntimeError('OpenTelemetry dependencies are required; install requirements.txt') from exc


def tracer():
    from opentelemetry import trace
    return trace.get_tracer('handset-fraud-platform')


def metrics_asgi_app():
    return make_asgi_app()


def start_timer():
    return time.perf_counter()


def observe_training(model: str):
    """Decorator for synchronous training functions; captures duration and errors."""
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            started = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            except Exception:
                MODEL_ERRORS.labels(model).inc()
                raise
            finally:
                TRAINING_DURATION.labels(model).observe(time.perf_counter() - started)
        return wrapped
    return decorate
