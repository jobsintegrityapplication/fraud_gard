from app.db import TXN_COLUMNS, conn, init_db


def test_init_db_creates_tables_and_feature_columns(isolate_storage):
    init_db()
    c = conn()
    names = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {'transactions', 'alerts', 'cases', 'patterns', 'audit', 'models'} <= names
    cols = {row[1] for row in c.execute('PRAGMA table_info(transactions)').fetchall()}
    c.close()
    assert set(TXN_COLUMNS) <= cols
