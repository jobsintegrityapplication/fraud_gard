import os

import pytest
from fastapi.testclient import TestClient

from app import config, db, main
from app.db import init_db
from app.ml import pipeline as ml_pipeline


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path, monkeypatch):
    monkeypatch.setenv('MLFLOW_DISABLED', '1')
    data = tmp_path / 'data'
    models = tmp_path / 'models'
    reports = tmp_path / 'reports'
    ingest = data / 'ingestion'
    for p in (data, models, reports, ingest):
        p.mkdir()
    db_path = data / 'fraud.db'
    monkeypatch.setattr(config, 'DATA_DIR', data)
    monkeypatch.setattr(config, 'MODEL_DIR', models)
    monkeypatch.setattr(config, 'REPORT_DIR', reports)
    monkeypatch.setattr(config, 'INGEST_DIR', ingest)
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    monkeypatch.setattr(db, 'DB_PATH', db_path)
    monkeypatch.setattr(main, 'DATA_DIR', data)
    monkeypatch.setattr(ml_pipeline, 'MODEL_DIR', models)
    main.models.xgb = None
    main.models.iforest = None
    main.models.meta = {}
    init_db()
    yield


@pytest.fixture
def client(isolate_storage):
    with TestClient(main.app) as c:
        yield c
