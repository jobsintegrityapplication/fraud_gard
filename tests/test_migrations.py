import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.domain.database import make_engine
from app.domain.models import Base

ROOT = Path(__file__).resolve().parents[1]


def _alembic_cfg(url: str) -> Config:
    cfg = Config(str(ROOT / 'alembic.ini'))
    cfg.set_main_option('script_location', str(ROOT / 'alembic'))
    cfg.set_main_option('sqlalchemy.url', url)
    os.environ['DATABASE_URL'] = url
    return cfg


def test_alembic_upgrade_downgrade_upgrade(tmp_path):
    url = f'sqlite:///{tmp_path / "migrate.db"}'
    prev = os.environ.get('DATABASE_URL')
    cfg = _alembic_cfg(url)
    try:
        command.upgrade(cfg, 'head')
        engine = make_engine(url)
        tables = set(inspect(engine).get_table_names())
        assert 'alembic_version' in tables
        assert 'canonical_transactions' in tables
        assert 'fraud_cases' in tables
        assert 'audit_events' in tables
        command.downgrade(cfg, 'base')
        tables_after = set(inspect(engine).get_table_names())
        assert 'canonical_transactions' not in tables_after
        command.upgrade(cfg, 'head')
        assert 'policies' in set(inspect(engine).get_table_names())
        with engine.connect() as conn:
            ver = conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        assert ver
    finally:
        if prev is None:
            os.environ.pop('DATABASE_URL', None)
        else:
            os.environ['DATABASE_URL'] = prev


def test_metadata_matches_migrated_schema(tmp_path):
    url = f'sqlite:///{tmp_path / "compare.db"}'
    command.upgrade(_alembic_cfg(url), 'head')
    engine = make_engine(url)
    migrated = set(inspect(engine).get_table_names()) - {'alembic_version'}
    expected = set(Base.metadata.tables)
    assert expected <= migrated


@pytest.mark.integration
def test_postgres_migration_if_available():
    url = os.environ.get('POSTGRES_TEST_URL')
    if not url:
        pytest.skip('Set POSTGRES_TEST_URL to run PostgreSQL migration tests')
    try:
        engine = make_engine(url)
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
    except Exception:
        pytest.skip('PostgreSQL is not available')
    cfg = _alembic_cfg(url)
    command.upgrade(cfg, 'head')
    tables = set(inspect(engine).get_table_names())
    assert 'canonical_transactions' in tables
    assert 'alembic_version' in tables
