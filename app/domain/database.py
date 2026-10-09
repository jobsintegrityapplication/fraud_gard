from __future__ import annotations

import os

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import DATABASE_URL
from .models import Base


def make_engine(url: str | None = None) -> Engine:
    url = url or os.environ.get('DATABASE_URL', DATABASE_URL)
    kwargs: dict = {'future': True}
    if url.startswith('sqlite'):
        kwargs['connect_args'] = {'check_same_thread': False}
    elif url.startswith('postgresql'):
        kwargs['connect_args'] = {'connect_timeout': 2}
    engine = create_engine(url, **kwargs)

    if url.startswith('sqlite'):
        @event.listens_for(engine, 'connect')
        def _enable_fk(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.close()

    return engine


def make_session_factory(engine: Engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, class_=Session)


def create_all(engine: Engine):
    Base.metadata.create_all(engine)


def drop_all(engine: Engine):
    Base.metadata.drop_all(engine)
