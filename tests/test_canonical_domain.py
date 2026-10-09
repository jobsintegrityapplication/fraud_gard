from datetime import datetime, timezone

from sqlalchemy import inspect

from app.domain.database import create_all, make_engine, make_session_factory
from app.domain.models import (
    Account, AnalystDecision, AuditEvent, Customer, Evidence, FeatureDefinition, FraudCase, FraudPattern,
    Handset, Location, Merchant, ModelMetric, ModelVersion, Network, Policy, Sim, Transaction,
)
from app.domain.redact import fingerprint, sanitize_audit_detail
from app.domain.schemas import (
    AnalystDecisionCreate, AuditEventCreate, CustomerRead, EvidenceCreate, FeatureDefinitionCreate,
    FraudCaseCreate, FraudPatternCreate, HandsetCreate, LocationCreate, MerchantCreate, ModelMetricCreate,
    ModelVersionCreate, NetworkCreate, PolicyCreate, SimCreate, TransactionCreate, TransactionRead,
)

EXPECTED_TABLES = {
    'locations', 'customers', 'accounts', 'sims', 'handsets', 'canonical_transactions',
    'merchants', 'networks', 'fraud_cases', 'fraud_patterns', 'evidence', 'analyst_decisions',
    'feature_definitions', 'model_versions', 'model_metrics', 'policies', 'audit_events',
    'case_audit_events', 'training_label_queue', 'learning_jobs',
}


def _engine(tmp_path):
    engine = make_engine(f'sqlite:///{tmp_path / "canonical.db"}')
    create_all(engine)
    return engine


def test_create_all_tables_and_indexes(tmp_path):
    engine = _engine(tmp_path)
    insp = inspect(engine)
    assert EXPECTED_TABLES <= set(insp.get_table_names())
    txn_indexes = {ix['name'] for ix in insp.get_indexes('canonical_transactions')}
    assert 'ix_txn_ts' in txn_indexes
    assert 'ix_txn_account_ts' in txn_indexes
    fks = insp.get_foreign_keys('canonical_transactions')
    referred = {fk['referred_table'] for fk in fks}
    assert {'customers', 'accounts', 'sims', 'handsets', 'merchants', 'networks', 'locations'} <= referred


def test_entity_roundtrip_and_pydantic(tmp_path):
    engine = _engine(tmp_path)
    Session = make_session_factory(engine)
    now = datetime.now(timezone.utc)
    with Session() as s:
        s.add(Location(location_id='LOC-BLR', location_code='BLR', city='Bengaluru', country='IN', latitude=12.97, longitude=77.59))
        s.add(Customer(customer_id='C1', segment='consumer', home_location_id='LOC-BLR', typical_hour=10, tenure_days=100))
        s.add(Sim(sim_id='S1', msisdn='919999000001', customer_id='C1', status='ACTIVE', activated_at=now))
        s.add(Handset(handset_id='H1', imei='123456789012347', manufacturer='Samsung', customer_id='C1'))
        s.add(Account(account_id='A1', customer_id='C1', product_type='prepaid', opened_at=now, primary_sim_id='S1', primary_handset_id='H1'))
        s.add(Merchant(merchant_id='M1', mcc='MECOM', category='ecommerce', country='IN', home_location_id='LOC-BLR', prior_risk=0.1))
        s.add(Network(network_id='N1', ip='10.1.2.3', asn=15169, connection_type='lte', home_location_id='LOC-BLR'))
        s.commit()
        s.add(Transaction(
            transaction_id='T1', customer_id='C1', account_id='A1', sim_id='S1', handset_id='H1',
            merchant_id='M1', network_id='N1', location_id='LOC-BLR', imei='123456789012347',
            amount=12.5, ts=now, channel='APP', label=0,
        ))
        s.add(FraudPattern(pattern_id='FP-001', pattern_code='SHARED_IMEI_ACCOUNTS', name='Shared IMEI', state='DISCOVERED'))
        s.commit()
        s.add(FraudCase(case_id='FC1', transaction_id='T1', pattern_id='FP-001', customer_id='C1', account_id='A1', status='OPEN'))
        s.commit()
        s.add(Evidence(evidence_id='E1', case_id='FC1', evidence_type='GRAPH', source='networkx', payload_json={'shared_accounts': 8}))
        s.add(AnalystDecision(
            decision_id='D1', case_id='FC1', analyst_id='analyst-1', reviewer='analyst-1',
            decision='CONFIRMED_FRAUD', reason='ring', evidence_version=1,
        ))
        s.add(FeatureDefinition(feature_id='F1', name='txn_velocity_1h', dtype='float', entity_level='TRANSACTION', is_leakage=False))
        s.add(ModelVersion(model_version_id='MV1', algorithm='XGBoost', status='CHALLENGER', artifact_uri='models/fraud_models.joblib'))
        s.commit()
        s.add(ModelMetric(metric_id='MM1', model_version_id='MV1', metric_name='pr_auc', metric_value=0.81, split='valid'))
        s.add(Policy(policy_id='P1', name='score-fusion', status='ACTIVE', rules_json={'high': 0.65}, version=1))
        payload = AuditEventCreate(
            actor='system', action='CREATE', entity_type='transaction', entity_id='T1',
            detail={'imei': '123456789012347', 'msisdn': '919999000001', 'ip': '10.1.2.3'},
        )
        s.add(AuditEvent(actor=payload.actor, action=payload.action, entity_type=payload.entity_type, entity_id=payload.entity_id, detail=payload.sanitized_detail()))
        s.commit()

        tx = s.get(Transaction, 'T1')
        cust = s.get(Customer, 'C1')
        audit = s.query(AuditEvent).one()
        TransactionRead.model_validate(tx)
        CustomerRead.model_validate(cust)
        assert tx.account.customer_id == 'C1'
        assert s.get(FraudCase, 'FC1').evidence_items[0].evidence_type == 'GRAPH'
        assert '123456789012347' not in (audit.detail or '')
        assert '919999000001' not in (audit.detail or '')
        assert '10.1.2.3' not in (audit.detail or '')
        assert fingerprint('123456789012347') in audit.detail


def test_soft_delete_and_check_constraints(tmp_path):
    engine = _engine(tmp_path)
    Session = make_session_factory(engine)
    now = datetime.now(timezone.utc)
    with Session() as s:
        s.add(Location(location_id='LOC-X', location_code='XXX', city='X', country='IN', latitude=1, longitude=1))
        s.add(Customer(customer_id='C9', home_location_id='LOC-X'))
        s.commit()
        cust = s.get(Customer, 'C9')
        cust.deleted_at = now
        s.commit()
        assert s.get(Customer, 'C9').deleted_at is not None
        try:
            s.add(Location(location_id='BAD', location_code='BAD', city='B', country='IN', latitude=120, longitude=1))
            s.commit()
            raised = False
        except Exception:
            s.rollback()
            raised = True
        assert raised


def test_sanitize_never_logs_imei():
    text = sanitize_audit_detail({'imei': '490154203237518', 'ok': 'BLR'})
    assert '490154203237518' not in text
    assert 'BLR' in text
    assert 'redacted:' in text


def test_schema_contracts():
    LocationCreate(location_id='L', location_code='A', city='c', country='IN', latitude=0, longitude=0)
    TransactionCreate(
        transaction_id='T', customer_id='C', account_id='A', sim_id='S', handset_id='H',
        merchant_id='M', network_id='N', location_id='L', imei='123456789012345', amount=1, ts=datetime.now(timezone.utc),
    )
    FraudPatternCreate(pattern_id='P', pattern_code='X', name='n')
    FraudCaseCreate(case_id='C')
    EvidenceCreate(evidence_id='E', case_id='C', evidence_type='MODEL', source='xgb')
    AnalystDecisionCreate(
        decision_id='D', case_id='C', analyst_id='a', reviewer='a',
        decision='NOT_FRAUD', reason='clear', timestamp=datetime.now(timezone.utc), evidence_version=1,
    )
    FeatureDefinitionCreate(feature_id='F', name='amount')
    ModelVersionCreate(model_version_id='V', algorithm='IF')
    ModelMetricCreate(metric_id='M', model_version_id='V', metric_name='f1', metric_value=0.5)
    PolicyCreate(policy_id='P', name='p')
    SimCreate(sim_id='S', msisdn='91', customer_id='C')
    HandsetCreate(handset_id='H', imei='123456789012345')
    NetworkCreate(network_id='N', ip='1.1.1.1')
    MerchantCreate(merchant_id='M', mcc='x', category='y', country='IN')
