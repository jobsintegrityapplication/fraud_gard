from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .redact import sanitize_audit_detail


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LocationRead(ORMModel):
    location_id: str
    location_code: str
    city: str
    country: str
    latitude: float
    longitude: float
    version: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None


class LocationCreate(BaseModel):
    location_id: str
    location_code: str
    city: str
    country: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class CustomerCreate(BaseModel):
    customer_id: str
    segment: str = 'consumer'
    status: str = 'ACTIVE'
    home_location_id: str | None = None
    typical_hour: int | None = Field(default=None, ge=0, le=23)
    tenure_days: int | None = None


class CustomerRead(ORMModel):
    customer_id: str
    segment: str
    status: str
    home_location_id: str | None
    typical_hour: int | None
    tenure_days: int | None
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class AccountCreate(BaseModel):
    account_id: str
    customer_id: str
    product_type: str
    status: str = 'ACTIVE'
    opened_at: datetime
    primary_sim_id: str | None = None
    primary_handset_id: str | None = None


class AccountRead(ORMModel):
    account_id: str
    customer_id: str
    product_type: str
    status: str
    opened_at: datetime
    closed_at: datetime | None
    primary_sim_id: str | None
    primary_handset_id: str | None
    version: int
    deleted_at: datetime | None


class SimCreate(BaseModel):
    sim_id: str
    msisdn: str
    customer_id: str
    status: str = 'ACTIVE'
    activated_at: datetime | None = None


class SimRead(ORMModel):
    sim_id: str
    msisdn: str
    customer_id: str
    status: str
    activated_at: datetime | None
    deactivated_at: datetime | None
    deleted_at: datetime | None


class HandsetCreate(BaseModel):
    handset_id: str
    imei: str = Field(min_length=15, max_length=15)
    manufacturer: str | None = None
    model: str | None = None
    tac: str | None = None
    customer_id: str | None = None


class HandsetRead(ORMModel):
    handset_id: str
    imei: str
    manufacturer: str | None
    model: str | None
    customer_id: str | None
    deleted_at: datetime | None


class MerchantCreate(BaseModel):
    merchant_id: str
    mcc: str
    category: str
    country: str
    home_location_id: str | None = None
    prior_risk: float = Field(default=0.05, ge=0, le=1)
    status: str = 'ACTIVE'


class MerchantRead(ORMModel):
    merchant_id: str
    mcc: str
    category: str
    country: str
    prior_risk: float
    status: str
    version: int
    deleted_at: datetime | None


class NetworkCreate(BaseModel):
    network_id: str
    ip: str
    asn: int | None = None
    connection_type: str = 'lte'
    home_location_id: str | None = None


class NetworkRead(ORMModel):
    network_id: str
    ip: str
    asn: int | None
    connection_type: str
    deleted_at: datetime | None


class TransactionCreate(BaseModel):
    transaction_id: str
    customer_id: str
    account_id: str
    sim_id: str
    handset_id: str
    merchant_id: str
    network_id: str
    location_id: str
    imei: str
    amount: float = Field(ge=0)
    ts: datetime
    channel: str = 'APP'
    currency: str = 'INR'
    label: int | None = None


class TransactionRead(ORMModel):
    transaction_id: str
    customer_id: str
    account_id: str
    amount: float
    ts: datetime
    channel: str
    label: int | None
    created_at: datetime


class FraudPatternCreate(BaseModel):
    pattern_id: str
    pattern_code: str
    name: str
    description: str | None = None
    state: str = 'DISCOVERED'
    signature_json: dict[str, Any] | None = None
    supporting_cases_json: list[Any] | None = None
    supporting_evidence_json: list[Any] | None = None
    threshold: float = 1.0
    policy_action: str = 'ALERT'
    owner: str | None = None


class FraudPatternRead(ORMModel):
    pattern_id: str
    pattern_code: str
    name: str
    description: str | None
    state: str
    version: int
    signature_json: dict[str, Any] | None = None
    supporting_cases_json: list[Any] | None = None
    supporting_evidence_json: list[Any] | None = None
    threshold: float
    policy_action: str
    owner: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class FraudCaseCreate(BaseModel):
    case_id: str | None = None
    transaction_id: str | None = None
    pattern_id: str | None = None
    customer_id: str | None = None
    account_id: str | None = None
    status: str = 'OPEN'
    title: str | None = None
    evidence: list[dict[str, Any]] | None = None


class FraudCaseRead(ORMModel):
    case_id: str
    transaction_id: str | None
    pattern_id: str | None
    status: str
    version: int
    evidence_version: int
    deleted_at: datetime | None


class EvidenceCreate(BaseModel):
    evidence_id: str
    case_id: str
    evidence_type: str
    source: str
    payload_json: dict[str, Any] | None = None
    evidence_version: int = 1


class EvidenceRead(ORMModel):
    evidence_id: str
    case_id: str
    evidence_type: str
    source: str
    payload_json: dict[str, Any] | None
    evidence_version: int


class AnalystDecisionCreate(BaseModel):
    decision_id: str | None = None
    case_id: str | None = None
    analyst_id: str | None = None
    reviewer: str
    decision: str
    reason: str
    timestamp: datetime
    evidence_version: int


class AnalystDecisionRead(ORMModel):
    decision_id: str
    case_id: str
    reviewer: str
    decision: str
    reason: str
    decided_at: datetime
    evidence_version: int
    version: int


class FeatureDefinitionCreate(BaseModel):
    feature_id: str
    name: str
    description: str | None = None
    dtype: str = 'float'
    entity_level: str = 'TRANSACTION'
    is_leakage: bool = False


class FeatureDefinitionRead(ORMModel):
    feature_id: str
    name: str
    dtype: str
    entity_level: str
    is_leakage: bool
    version: int
    deleted_at: datetime | None


class ModelVersionCreate(BaseModel):
    model_version_id: str
    algorithm: str
    status: str = 'SHADOW'
    artifact_uri: str | None = None
    parent_version_id: str | None = None
    metrics_summary: dict[str, Any] | None = None
    dataset_version: str | None = None
    feature_version: str | None = None


class ModelVersionRead(ORMModel):
    model_version_id: str
    algorithm: str
    status: str
    version: int
    artifact_uri: str | None
    dataset_version: str | None = None
    feature_version: str | None = None


class ModelMetricCreate(BaseModel):
    metric_id: str
    model_version_id: str
    metric_name: str
    metric_value: float
    split: str = 'valid'


class ModelMetricRead(ORMModel):
    metric_id: str
    model_version_id: str
    metric_name: str
    metric_value: float
    split: str


class PolicyCreate(BaseModel):
    policy_id: str
    name: str
    status: str = 'DRAFT'
    rules_json: dict[str, Any] = Field(default_factory=dict)


class PolicyRead(ORMModel):
    policy_id: str
    name: str
    status: str
    rules_json: dict[str, Any]
    version: int
    deleted_at: datetime | None


class AuditEventCreate(BaseModel):
    actor: str
    action: str
    entity_type: str
    entity_id: str
    detail: Any = None
    request_id: str | None = None

    def sanitized_detail(self) -> str:
        return sanitize_audit_detail(self.detail)


class AuditEventRead(ORMModel):
    audit_id: int
    actor: str
    action: str
    entity_type: str
    entity_id: str
    detail: str | None
    ts: datetime
    request_id: str | None
