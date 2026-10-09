from __future__ import annotations

import json
import os
from pathlib import Path

from ... import config as app_config


def mlflow_enabled() -> bool:
    return os.environ.get('MLFLOW_DISABLED', '').lower() not in {'1', 'true', 'yes'}


def log_training_run(
    params: dict,
    metrics: dict,
    artifact_dir: Path,
    tags: dict | None = None,
    *,
    experiment: str | None = None,
):
    if not mlflow_enabled():
        return None
    try:
        import mlflow
    except ImportError:
        return None
    tracking = os.environ.get('MLFLOW_TRACKING_URI', str(app_config.MODEL_DIR / 'mlruns'))
    mlflow.set_tracking_uri(tracking)
    mlflow.set_experiment(experiment or os.environ.get('MLFLOW_EXPERIMENT', 'handset-fraud-supervised'))
    with mlflow.start_run(run_name=params.get('model_version', 'train')) as run:
        mlflow.log_params({k: str(v)[:250] for k, v in params.items()})
        flat = {}
        for split, vals in metrics.items():
            if isinstance(vals, dict):
                for k, v in vals.items():
                    if isinstance(v, (int, float)):
                        flat[f'{split}_{k}'] = float(v)
            elif isinstance(vals, (int, float)):
                flat[str(split)] = float(vals)
        mlflow.log_metrics(flat)
        if tags:
            mlflow.set_tags(tags)
        if artifact_dir.exists():
            mlflow.log_artifacts(str(artifact_dir), artifact_path='model')
        return run.info.run_id
    return None
