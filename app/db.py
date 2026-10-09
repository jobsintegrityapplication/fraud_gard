import sqlite3
from .config import DB_PATH, ensure_dirs

TXN_COLUMNS = [
    'id', 'customer_id', 'account_id', 'sim_id', 'imei', 'ip', 'merchant_id', 'location',
    'amount', 'ts', 'label', 'account_age_days', 'imei_account_count', 'imei_sim_count',
    'ip_account_count', 'merchant_fraud_rate', 'txn_velocity_1h', 'location_distance_km',
    'device_change_rate', 'sim_change_rate', 'hour_deviation',
]

_TXN_EXTRA = [
    ('account_age_days', 'REAL'),
    ('imei_account_count', 'REAL'),
    ('imei_sim_count', 'REAL'),
    ('ip_account_count', 'REAL'),
    ('merchant_fraud_rate', 'REAL'),
    ('txn_velocity_1h', 'REAL'),
    ('location_distance_km', 'REAL'),
    ('device_change_rate', 'REAL'),
    ('sim_change_rate', 'REAL'),
    ('hour_deviation', 'REAL'),
]


def conn():
    ensure_dirs()
    c = sqlite3.connect(str(DB_PATH), timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _migrate_transactions(c):
    existing = {row[1] for row in c.execute('PRAGMA table_info(transactions)').fetchall()}
    for name, typ in _TXN_EXTRA:
        if name not in existing:
            c.execute(f'ALTER TABLE transactions ADD COLUMN {name} {typ}')


_PATTERN_EXTRA = [
    ('description', 'TEXT'),
    ('version', 'INTEGER'),
    ('supporting_cases_json', 'TEXT'),
    ('supporting_evidence_json', 'TEXT'),
    ('threshold', 'REAL'),
    ('policy_action', 'TEXT'),
    ('owner', 'TEXT'),
]


def _migrate_patterns(c):
    existing = {row[1] for row in c.execute('PRAGMA table_info(patterns)').fetchall()}
    if not existing:
        return
    for name, typ in _PATTERN_EXTRA:
        if name not in existing:
            c.execute(f'ALTER TABLE patterns ADD COLUMN {name} {typ}')
    c.execute("UPDATE patterns SET version = 1 WHERE version IS NULL")
    c.execute("UPDATE patterns SET threshold = 1 WHERE threshold IS NULL")
    c.execute("UPDATE patterns SET policy_action = 'ALERT' WHERE policy_action IS NULL OR policy_action = ''")
    c.execute("UPDATE patterns SET supporting_cases_json = '[]' WHERE supporting_cases_json IS NULL")
    c.execute("UPDATE patterns SET supporting_evidence_json = evidence_json WHERE supporting_evidence_json IS NULL AND evidence_json IS NOT NULL")


def _migrate_cases(c):
    existing = {row[1] for row in c.execute('PRAGMA table_info(cases)').fetchall()}
    if existing:
        extras = [
            ('title', 'TEXT'),
            ('pattern_id', 'TEXT'),
            ('customer_id', 'TEXT'),
            ('account_id', 'TEXT'),
            ('evidence_version', 'INTEGER'),
        ]
        for name, typ in extras:
            if name not in existing:
                c.execute(f'ALTER TABLE cases ADD COLUMN {name} {typ}')
        c.execute('UPDATE cases SET evidence_version = 1 WHERE evidence_version IS NULL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS case_evidence(
        evidence_id TEXT PRIMARY KEY,
        case_id TEXT,
        evidence_type TEXT,
        source TEXT,
        payload_json TEXT,
        evidence_version INTEGER,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS analyst_decisions(
        decision_id TEXT PRIMARY KEY,
        case_id TEXT,
        decision TEXT,
        reason TEXT,
        reviewer TEXT,
        timestamp TEXT,
        evidence_version INTEGER
    );
    CREATE TABLE IF NOT EXISTS case_audit_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id TEXT,
        actor TEXT,
        action TEXT,
        from_status TEXT,
        to_status TEXT,
        evidence_version INTEGER,
        decision_id TEXT,
        detail TEXT,
        ts TEXT
    );
    CREATE TABLE IF NOT EXISTS training_label_queue(
        queue_id TEXT PRIMARY KEY,
        case_id TEXT,
        transaction_id TEXT,
        decision TEXT,
        label INTEGER,
        reviewer TEXT,
        reason TEXT,
        evidence_version INTEGER,
        status TEXT,
        rejection_reason TEXT,
        created_at TEXT,
        updated_at TEXT,
        metadata_json TEXT
    );
    CREATE TABLE IF NOT EXISTS learning_jobs(
        job_id TEXT PRIMARY KEY,
        status TEXT,
        stages_json TEXT,
        result_json TEXT,
        error TEXT,
        created_at TEXT,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS model_registry(
        model_id TEXT PRIMARY KEY,
        version TEXT,
        algorithm TEXT,
        dataset_version TEXT,
        feature_version TEXT,
        training_timestamp TEXT,
        metrics_json TEXT,
        status TEXT,
        artifact_uri TEXT,
        gate_results_json TEXT,
        created_at TEXT,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS candidate_features(
        candidate_id TEXT PRIMARY KEY,
        name TEXT,
        description TEXT,
        description_source TEXT,
        status TEXT,
        origin TEXT,
        origin_ref TEXT,
        template TEXT,
        params_json TEXT,
        entity_level TEXT,
        validation_json TEXT,
        production INTEGER,
        llm_may_edit_json TEXT,
        created_at TEXT,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS policy_settings(
        policy_key TEXT PRIMARY KEY,
        version INTEGER NOT NULL,
        policy_json TEXT NOT NULL,
        updated_by TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS drift_monitor_runs(
        run_id TEXT PRIMARY KEY,
        overall_status TEXT NOT NULL,
        policy_json TEXT NOT NULL,
        results_json TEXT NOT NULL,
        reference_rows INTEGER NOT NULL,
        current_rows INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS retraining_recommendations(
        recommendation_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        status TEXT NOT NULL,
        policy_json TEXT NOT NULL,
        features_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES drift_monitor_runs(run_id)
    );
    ''')


def init_db():
    c = conn()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS transactions(
        id TEXT PRIMARY KEY,
        customer_id TEXT,
        account_id TEXT,
        sim_id TEXT,
        imei TEXT,
        ip TEXT,
        merchant_id TEXT,
        location TEXT,
        amount REAL,
        ts TEXT,
        label INTEGER DEFAULT NULL,
        account_age_days REAL,
        imei_account_count REAL,
        imei_sim_count REAL,
        ip_account_count REAL,
        merchant_fraud_rate REAL,
        txn_velocity_1h REAL,
        location_distance_km REAL,
        device_change_rate REAL,
        sim_change_rate REAL,
        hour_deviation REAL
    );
    CREATE TABLE IF NOT EXISTS alerts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id TEXT,
        xgb_score REAL,
        anomaly_score REAL,
        graph_score REAL,
        pattern_score REAL,
        risk TEXT,
        reasons TEXT,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS cases(
        id TEXT PRIMARY KEY,
        transaction_id TEXT,
        status TEXT,
        decision TEXT,
        reason TEXT,
        evidence_json TEXT,
        created_at TEXT,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS patterns(
        id TEXT PRIMARY KEY,
        name TEXT,
        state TEXT,
        signature_json TEXT,
        evidence_json TEXT,
        created_at TEXT,
        updated_at TEXT,
        description TEXT,
        version INTEGER DEFAULT 1,
        supporting_cases_json TEXT,
        supporting_evidence_json TEXT,
        threshold REAL DEFAULT 1,
        policy_action TEXT DEFAULT 'ALERT',
        owner TEXT
    );
    CREATE TABLE IF NOT EXISTS audit(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor TEXT,
        action TEXT,
        entity_type TEXT,
        entity_id TEXT,
        detail TEXT,
        ts TEXT
    );
    CREATE TABLE IF NOT EXISTS security_meta(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS models(
        id TEXT PRIMARY KEY,
        algorithm TEXT,
        status TEXT,
        metrics_json TEXT,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS ingestions(
        upload_id TEXT PRIMARY KEY,
        filename TEXT,
        content_type TEXT,
        file_format TEXT,
        byte_size INTEGER,
        checksum_sha256 TEXT,
        stored_path TEXT,
        status TEXT,
        row_count INTEGER,
        valid_rows INTEGER,
        invalid_rows INTEGER,
        duplicate_rows INTEGER,
        quality_json TEXT,
        error_report_json TEXT,
        created_at TEXT,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS ingestion_quarantine(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        upload_id TEXT,
        row_number INTEGER,
        payload_json TEXT,
        reasons_json TEXT,
        created_at TEXT
    );
    ''')
    _migrate_transactions(c)
    _migrate_patterns(c)
    _migrate_cases(c)
    c.executescript('''
    CREATE TABLE IF NOT EXISTS training_label_queue(
        queue_id TEXT PRIMARY KEY,
        case_id TEXT,
        transaction_id TEXT,
        decision TEXT,
        label INTEGER,
        reviewer TEXT,
        reason TEXT,
        evidence_version INTEGER,
        status TEXT,
        rejection_reason TEXT,
        created_at TEXT,
        updated_at TEXT,
        metadata_json TEXT
    );
    CREATE TABLE IF NOT EXISTS learning_jobs(
        job_id TEXT PRIMARY KEY,
        status TEXT,
        stages_json TEXT,
        result_json TEXT,
        error TEXT,
        created_at TEXT,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS model_registry(
        model_id TEXT PRIMARY KEY,
        version TEXT,
        algorithm TEXT,
        dataset_version TEXT,
        feature_version TEXT,
        training_timestamp TEXT,
        metrics_json TEXT,
        status TEXT,
        artifact_uri TEXT,
        gate_results_json TEXT,
        created_at TEXT,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS candidate_features(
        candidate_id TEXT PRIMARY KEY,
        name TEXT,
        description TEXT,
        description_source TEXT,
        status TEXT,
        origin TEXT,
        origin_ref TEXT,
        template TEXT,
        params_json TEXT,
        entity_level TEXT,
        validation_json TEXT,
        production INTEGER,
        llm_may_edit_json TEXT,
        created_at TEXT,
        updated_at TEXT
    );
    ''')
    sanitized = c.execute("SELECT value FROM security_meta WHERE key='audit_redaction_v1'").fetchone()
    if not sanitized:
        from .domain.redact import fingerprint, sanitize_audit_detail
        existing = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, actor_column, detail_column, id_column in (
            ('audit', 'actor', 'detail', 'id'),
            ('case_audit_events', 'actor', 'detail', 'id'),
        ):
            if table not in existing:
                continue
            rows = c.execute(f'SELECT {id_column},{actor_column},{detail_column} FROM {table}').fetchall()
            for row in rows:
                actor = str(row[actor_column] or '')
                safe_actor = actor if actor in {'', 'system'} or actor.startswith(('user:', 'legacy:')) else 'legacy:' + fingerprint(actor).split(':', 1)[-1]
                safe_detail = sanitize_audit_detail(row[detail_column])
                c.execute(f'UPDATE {table} SET {actor_column}=?,{detail_column}=? WHERE {id_column}=?', (safe_actor, safe_detail, row[id_column]))
        c.execute("INSERT INTO security_meta(key,value) VALUES('audit_redaction_v1','complete')")
    c.commit()
    c.close()
