from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / 'data'
REPORT_DIR = BASE_DIR / 'reports'
MODEL_DIR = BASE_DIR / 'models'
FRONTEND_DIR = BASE_DIR / 'frontend'
FRONTEND_INDEX = FRONTEND_DIR / 'index.html'
DB_PATH = DATA_DIR / 'fraud.db'
RAW_DIR = DATA_DIR / 'raw'
GENERATED_DIR = DATA_DIR / 'generated'
PROCESSED_DIR = DATA_DIR / 'processed'
INGEST_DIR = DATA_DIR / 'ingestion'
DATABASE_URL = os.environ.get(
    'DATABASE_URL',
    'postgresql+psycopg://fraud:fraud@localhost:5432/fraud',
)
def ensure_dirs():
    for p in (DATA_DIR, REPORT_DIR, MODEL_DIR, FRONTEND_DIR, RAW_DIR, GENERATED_DIR, PROCESSED_DIR, INGEST_DIR):
        p.mkdir(parents=True, exist_ok=True)


ensure_dirs()
