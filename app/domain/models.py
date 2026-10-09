from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from .base import ActorMixin, Base, SoftDeleteMixin, TimestampMixin, VersionMixin

PK = String(32)
FK = String(32)


class Location(TimestampMixin, SoftDeleteMixin, ActorMixin, Base):
    __tablename__ = 'locations'
    __table_args__ = (
        CheckConstraint('latitude >= -90 AND latitude <= 90', name='ck_locations_latitude'),
        CheckConstraint('longitude >= -180 AND longitude <= 180', name='ck_locations_longitude'),
        Index('ix_locations_code', 'location_code'),
        Index('ix_locations_country', 'country'),
    )

    location_id: Mapped[str] = mapped_column(PK, primary_key=True)
    location_code: Mapped[str] = mapped_column(String(8), nullable=False, unique=True)
    city: Mapped[str] = mapped_column(String(128), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)


class Customer(TimestampMixin, SoftDeleteMixin, ActorMixin, VersionMixin, Base):
    __tablename__ = 'customers'
    __table_args__ = (
        Index('ix_customers_segment', 'segment'),
        Index('ix_customers_home_location', 'home_location_id'),
        CheckConstraint("status IN ('ACTIVE','INACTIVE','BLOCKED')", name='ck_customers_status'),
    )

    customer_id: Mapped[str] = mapped_column(PK, primary_key=True)
    segment: Mapped[str] = mapped_column(String(32), nullable=False, default='consumer')
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='ACTIVE')
    home_location_id: Mapped[str | None] = mapped_column(ForeignKey('locations.location_id'), nullable=True)
    typical_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tenure_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    home_location: Mapped[Location | None] = relationship()
    accounts: Mapped[list['Account']] = relationship(back_populates='customer', foreign_keys='Account.customer_id')


class Merchant(TimestampMixin, SoftDeleteMixin, ActorMixin, VersionMixin, Base):
    __tablename__ = 'merchants'
    __table_args__ = (
        Index('ix_merchants_category', 'category'),
        Index('ix_merchants_country', 'country'),
        CheckConstraint('prior_risk >= 0 AND prior_risk <= 1', name='ck_merchants_prior_risk'),
        CheckConstraint("status IN ('ACTIVE','SUSPENDED','CLOSED')", name='ck_merchants_status'),
    )

    merchant_id: Mapped[str] = mapped_column(PK, primary_key=True)
    mcc: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    home_location_id: Mapped[str | None] = mapped_column(ForeignKey('locations.location_id'), nullable=True)
    prior_risk: Mapped[float] = mapped_column(Float, nullable=False, default=0.05)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='ACTIVE')

    home_location: Mapped[Location | None] = relationship()


class Network(TimestampMixin, SoftDeleteMixin, ActorMixin, Base):
    __tablename__ = 'networks'
    __table_args__ = (
        Index('ix_networks_ip', 'ip'),
        Index('ix_networks_asn', 'asn'),
    )

    network_id: Mapped[str] = mapped_column(PK, primary_key=True)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    asn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    connection_type: Mapped[str] = mapped_column(String(32), nullable=False, default='lte')
    home_location_id: Mapped[str | None] = mapped_column(ForeignKey('locations.location_id'), nullable=True)

    home_location: Mapped[Location | None] = relationship()


class Sim(TimestampMixin, SoftDeleteMixin, ActorMixin, Base):
    __tablename__ = 'sims'
    __table_args__ = (
        UniqueConstraint('msisdn', name='uq_sims_msisdn'),
        Index('ix_sims_customer', 'customer_id'),
        CheckConstraint("status IN ('ACTIVE','INACTIVE','HOTLINED')", name='ck_sims_status'),
    )

    sim_id: Mapped[str] = mapped_column(PK, primary_key=True)
    msisdn: Mapped[str] = mapped_column(String(20), nullable=False)
    customer_id: Mapped[str] = mapped_column(ForeignKey('customers.customer_id'), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='ACTIVE')
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    customer: Mapped[Customer] = relationship()


class Handset(TimestampMixin, SoftDeleteMixin, ActorMixin, Base):
    __tablename__ = 'handsets'
    __table_args__ = (
        UniqueConstraint('imei', name='uq_handsets_imei'),
        Index('ix_handsets_customer', 'customer_id'),
        CheckConstraint('length(imei) = 15', name='ck_handsets_imei_len'),
    )

    handset_id: Mapped[str] = mapped_column(PK, primary_key=True)
    imei: Mapped[str] = mapped_column(String(15), nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tac: Mapped[str | None] = mapped_column(String(8), nullable=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey('customers.customer_id'), nullable=True)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    customer: Mapped[Customer | None] = relationship()


class Account(TimestampMixin, SoftDeleteMixin, ActorMixin, VersionMixin, Base):
    __tablename__ = 'accounts'
    __table_args__ = (
        Index('ix_accounts_customer', 'customer_id'),
        Index('ix_accounts_status', 'status'),
        CheckConstraint("status IN ('ACTIVE','SUSPENDED','CLOSED')", name='ck_accounts_status'),
        CheckConstraint("product_type IN ('prepaid','postpaid','wallet')", name='ck_accounts_product'),
    )

    account_id: Mapped[str] = mapped_column(PK, primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey('customers.customer_id'), nullable=False)
    product_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='ACTIVE')
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    primary_sim_id: Mapped[str | None] = mapped_column(
        ForeignKey('sims.sim_id', use_alter=True, name='fk_accounts_primary_sim'), nullable=True,
    )
    primary_handset_id: Mapped[str | None] = mapped_column(
        ForeignKey('handsets.handset_id', use_alter=True, name='fk_accounts_primary_handset'), nullable=True,
    )

    customer: Mapped[Customer] = relationship(back_populates='accounts', foreign_keys=[customer_id])
    primary_sim: Mapped[Sim | None] = relationship(foreign_keys=[primary_sim_id])
    primary_handset: Mapped[Handset | None] = relationship(foreign_keys=[primary_handset_id])


class Transaction(TimestampMixin, ActorMixin, Base):
    __tablename__ = 'canonical_transactions'
    __table_args__ = (
        CheckConstraint('amount >= 0', name='ck_txn_amount_nonneg'),
        CheckConstraint('label IS NULL OR label IN (0, 1)', name='ck_txn_label'),
        Index('ix_txn_ts', 'ts'),
        Index('ix_txn_account_ts', 'account_id', 'ts'),
        Index('ix_txn_customer', 'customer_id'),
        Index('ix_txn_sim', 'sim_id'),
        Index('ix_txn_handset', 'handset_id'),
        Index('ix_txn_merchant', 'merchant_id'),
        Index('ix_txn_network', 'network_id'),
        Index('ix_txn_imei', 'imei'),
    )

    transaction_id: Mapped[str] = mapped_column(PK, primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey('customers.customer_id'), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.account_id'), nullable=False)
    sim_id: Mapped[str] = mapped_column(ForeignKey('sims.sim_id'), nullable=False)
    handset_id: Mapped[str] = mapped_column(ForeignKey('handsets.handset_id'), nullable=False)
    merchant_id: Mapped[str] = mapped_column(ForeignKey('merchants.merchant_id'), nullable=False)
    network_id: Mapped[str] = mapped_column(ForeignKey('networks.network_id'), nullable=False)
    location_id: Mapped[str] = mapped_column(ForeignKey('locations.location_id'), nullable=False)
    imei: Mapped[str] = mapped_column(String(15), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default='INR')
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default='APP')
    label: Mapped[int | None] = mapped_column(Integer, nullable=True)

    customer: Mapped[Customer] = relationship()
    account: Mapped[Account] = relationship()
    sim: Mapped[Sim] = relationship()
    handset: Mapped[Handset] = relationship()
    merchant: Mapped[Merchant] = relationship()
    network: Mapped[Network] = relationship()
    location: Mapped[Location] = relationship()


class FraudPattern(TimestampMixin, SoftDeleteMixin, ActorMixin, VersionMixin, Base):
    __tablename__ = 'fraud_patterns'
    __table_args__ = (
        UniqueConstraint('pattern_code', name='uq_fraud_patterns_code'),
        CheckConstraint(
            "state IN ('DISCOVERED','UNDER_REVIEW','CONFIRMED','ACTIVE','MONITORED','DEPRECATED')",
            name='ck_fraud_patterns_state',
        ),
        CheckConstraint(
            "policy_action IN ('ALERT','BLOCK','STEP_UP','REVIEW')",
            name='ck_fraud_patterns_policy_action',
        ),
    )

    pattern_id: Mapped[str] = mapped_column(PK, primary_key=True)
    pattern_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default='DISCOVERED')
    signature_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    supporting_cases_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    supporting_evidence_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    threshold: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default='1')
    policy_action: Mapped[str] = mapped_column(String(16), nullable=False, default='ALERT', server_default='ALERT')
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FraudCase(TimestampMixin, SoftDeleteMixin, ActorMixin, VersionMixin, Base):
    __tablename__ = 'fraud_cases'
    __table_args__ = (
        Index('ix_fraud_cases_status', 'status'),
        Index('ix_fraud_cases_transaction', 'transaction_id'),
        Index('ix_fraud_cases_pattern', 'pattern_id'),
        CheckConstraint(
            "status IN ('OPEN','INVESTIGATING','PENDING_REVIEW','CONFIRMED_FRAUD','NOT_FRAUD','NEED_MORE_EVIDENCE','CLOSED')",
            name='ck_fraud_cases_status',
        ),
    )

    case_id: Mapped[str] = mapped_column(PK, primary_key=True)
    transaction_id: Mapped[str | None] = mapped_column(ForeignKey('canonical_transactions.transaction_id'), nullable=True)
    pattern_id: Mapped[str | None] = mapped_column(ForeignKey('fraud_patterns.pattern_id'), nullable=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey('customers.customer_id'), nullable=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey('accounts.account_id'), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default='OPEN')
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    evidence_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default='1')

    transaction: Mapped[Transaction | None] = relationship()
    pattern: Mapped[FraudPattern | None] = relationship()
    evidence_items: Mapped[list['CaseEvidence']] = relationship(back_populates='case')
    decisions: Mapped[list['AnalystDecision']] = relationship(back_populates='case')
    audit_events: Mapped[list['CaseAuditEvent']] = relationship(back_populates='case')


class CaseEvidence(TimestampMixin, ActorMixin, Base):
    __tablename__ = 'evidence'
    __table_args__ = (
        Index('ix_evidence_case', 'case_id'),
        CheckConstraint(
            "evidence_type IN ('GRAPH','MODEL','PATTERN','DOCUMENT','RULE','ALERT')",
            name='ck_evidence_type',
        ),
    )

    evidence_id: Mapped[str] = mapped_column(PK, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey('fraud_cases.case_id'), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default='1')

    case: Mapped[FraudCase] = relationship(back_populates='evidence_items')


Evidence = CaseEvidence


class AnalystDecision(TimestampMixin, ActorMixin, VersionMixin, Base):
    __tablename__ = 'analyst_decisions'
    __table_args__ = (
        Index('ix_decisions_case', 'case_id'),
        CheckConstraint(
            "decision IN ('CONFIRMED_FRAUD','NOT_FRAUD','NEED_MORE_EVIDENCE','INVESTIGATING','PENDING_REVIEW','CLOSED')",
            name='ck_analyst_decision',
        ),
    )

    decision_id: Mapped[str] = mapped_column(PK, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey('fraud_cases.case_id'), nullable=False)
    analyst_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    evidence_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default='1')

    case: Mapped[FraudCase] = relationship(back_populates='decisions')


class CaseAuditEvent(Base):
    __tablename__ = 'case_audit_events'
    __table_args__ = (
        Index('ix_case_audit_case', 'case_id'),
        Index('ix_case_audit_ts', 'ts'),
    )

    audit_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey('fraud_cases.case_id'), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    evidence_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    case: Mapped[FraudCase] = relationship(back_populates='audit_events')


class FeatureDefinition(TimestampMixin, SoftDeleteMixin, ActorMixin, VersionMixin, Base):
    __tablename__ = 'feature_definitions'
    __table_args__ = (
        UniqueConstraint('name', name='uq_feature_definitions_name'),
        CheckConstraint("entity_level IN ('TRANSACTION','ACCOUNT','CUSTOMER','DEVICE','MERCHANT')", name='ck_feature_level'),
    )

    feature_id: Mapped[str] = mapped_column(PK, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    dtype: Mapped[str] = mapped_column(String(32), nullable=False, default='float')
    entity_level: Mapped[str] = mapped_column(String(16), nullable=False, default='TRANSACTION')
    is_leakage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default='0')


class ModelVersion(TimestampMixin, ActorMixin, VersionMixin, Base):
    __tablename__ = 'model_versions'
    __table_args__ = (
        Index('ix_model_versions_status', 'status'),
        CheckConstraint(
            "status IN ('TRAINING','VALIDATING','FAILED','SHADOW','CANARY','PRODUCTION','ROLLED_BACK','CHALLENGER','CHAMPION','RETIRED')",
            name='ck_model_status',
        ),
    )

    model_version_id: Mapped[str] = mapped_column(PK, primary_key=True)
    algorithm: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default='SHADOW')
    artifact_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    parent_version_id: Mapped[str | None] = mapped_column(ForeignKey('model_versions.model_version_id'), nullable=True)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metrics_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feature_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    parent: Mapped['ModelVersion | None'] = relationship(remote_side='ModelVersion.model_version_id')
    metrics: Mapped[list['ModelMetric']] = relationship(back_populates='model_version')


class ModelMetric(TimestampMixin, Base):
    __tablename__ = 'model_metrics'
    __table_args__ = (
        Index('ix_model_metrics_version', 'model_version_id'),
        UniqueConstraint('model_version_id', 'metric_name', 'split', name='uq_model_metric_name_split'),
        CheckConstraint("split IN ('train','valid','test','production')", name='ck_model_metric_split'),
    )

    metric_id: Mapped[str] = mapped_column(PK, primary_key=True)
    model_version_id: Mapped[str] = mapped_column(ForeignKey('model_versions.model_version_id'), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    split: Mapped[str] = mapped_column(String(16), nullable=False, default='valid')

    model_version: Mapped[ModelVersion] = relationship(back_populates='metrics')


class Policy(TimestampMixin, SoftDeleteMixin, ActorMixin, VersionMixin, Base):
    __tablename__ = 'policies'
    __table_args__ = (
        UniqueConstraint('name', 'version', name='uq_policies_name_version'),
        CheckConstraint("status IN ('DRAFT','ACTIVE','SUPERSEDED')", name='ck_policies_status'),
    )

    policy_id: Mapped[str] = mapped_column(PK, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='DRAFT')
    rules_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = 'audit_events'
    __table_args__ = (
        Index('ix_audit_entity', 'entity_type', 'entity_id'),
        Index('ix_audit_ts', 'ts'),
        Index('ix_audit_actor', 'actor'),
    )

    audit_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class TrainingLabelQueue(TimestampMixin, Base):
    __tablename__ = 'training_label_queue'
    __table_args__ = (
        Index('ix_tlq_status', 'status'),
        Index('ix_tlq_case', 'case_id'),
        CheckConstraint("label IN (0, 1) OR label IS NULL", name='ck_tlq_label'),
        CheckConstraint(
            "status IN ('QUEUED','CLAIMED','QUALITY_FAILED','READY','USED_IN_TRAINING','REJECTED')",
            name='ck_tlq_status',
        ),
    )

    queue_id: Mapped[str] = mapped_column(PK, primary_key=True)
    case_id: Mapped[str] = mapped_column(String(32), nullable=False)
    transaction_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewer: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default='QUEUED')
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class LearningJob(TimestampMixin, Base):
    __tablename__ = 'learning_jobs'
    __table_args__ = (
        Index('ix_learning_jobs_status', 'status'),
    )

    job_id: Mapped[str] = mapped_column(PK, primary_key=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default='QUEUED')
    stages_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
