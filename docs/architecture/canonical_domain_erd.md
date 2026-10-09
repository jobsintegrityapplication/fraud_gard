# Canonical handset fraud domain model

PostgreSQL is the system of record for this model. The MVP SQLite file remains only for the demo API.

Sensitive identifiers (`imei`, `msisdn`, `ip`, `imsi`) are stored as constrained columns for operations. They must **not** be written to plain logs; use `app.domain.redact.sanitize_audit_detail` (SHA-256 fingerprints) for `AuditEvent.detail`.

Ground-truth `fraud_reason` is **not** a Transaction column. Labels, when present, are the supervised target only.

```mermaid
erDiagram
    Location ||--o{ Customer : "home"
    Location ||--o{ Merchant : "home"
    Location ||--o{ Network : "home"
    Location ||--o{ Transaction : "occurs_at"

    Customer ||--o{ Account : owns
    Customer ||--o{ Sim : holds
    Customer ||--o{ Handset : uses
    Customer ||--o{ Transaction : performs
    Customer ||--o{ FraudCase : involved

    Account ||--o{ Transaction : posts
    Account }o--o| Sim : "primary_sim"
    Account }o--o| Handset : "primary_handset"
    Account ||--o{ FraudCase : involved

    Sim ||--o{ Transaction : "used_in"
    Handset ||--o{ Transaction : "used_in"
    Merchant ||--o{ Transaction : "receives"
    Network ||--o{ Transaction : "originates"

    FraudPattern ||--o{ FraudCase : classifies
    Transaction ||--o{ FraudCase : "opens"
    FraudCase ||--o{ Evidence : has
    FraudCase ||--o{ AnalystDecision : decided_by

    ModelVersion ||--o{ ModelVersion : parent
    ModelVersion ||--o{ ModelMetric : records

    FeatureDefinition {
        string feature_id PK
        string name UK
        int version
        boolean is_leakage
    }

    Policy {
        string policy_id PK
        string name
        int version
        string status
    }

    AuditEvent {
        int audit_id PK
        string actor
        string action
        string entity_type
        string entity_id
        string detail "redacted"
        datetime ts
    }

    Customer {
        string customer_id PK
        string home_location_id FK
        int version
        datetime deleted_at
    }

    Account {
        string account_id PK
        string customer_id FK
        string primary_sim_id FK
        string primary_handset_id FK
        int version
        datetime deleted_at
    }

    Sim {
        string sim_id PK
        string msisdn UK
        string customer_id FK
        datetime deleted_at
    }

    Handset {
        string handset_id PK
        string imei UK
        string customer_id FK
        datetime deleted_at
    }

    Transaction {
        string transaction_id PK
        string customer_id FK
        string account_id FK
        string sim_id FK
        string handset_id FK
        string merchant_id FK
        string network_id FK
        string location_id FK
        numeric amount
        int label "target only"
    }

    Merchant {
        string merchant_id PK
        string home_location_id FK
        float prior_risk
        int version
        datetime deleted_at
    }

    Network {
        string network_id PK
        string ip
        string home_location_id FK
        datetime deleted_at
    }

    Location {
        string location_id PK
        string location_code UK
    }

    FraudCase {
        string case_id PK
        string transaction_id FK
        string pattern_id FK
        int version
        datetime deleted_at
    }

    FraudPattern {
        string pattern_id PK
        string pattern_code UK
        int version
        datetime deleted_at
    }

    Evidence {
        string evidence_id PK
        string case_id FK
    }

    AnalystDecision {
        string decision_id PK
        string case_id FK
        int version
    }

    ModelVersion {
        string model_version_id PK
        string parent_version_id FK
        int version
    }

    ModelMetric {
        string metric_id PK
        string model_version_id FK
    }
```

## Constraints and indexes

- Amounts `>= 0`; location coordinates in range; status/decision enumerations as CHECK constraints.
- Unique: `location_code`, `msisdn`, `imei`, `pattern_code`, feature `name`, policy `(name, version)`, model metric `(model_version_id, metric_name, split)`.
- Indexed: transaction `ts`, `(account_id, ts)`, customer/sim/handset/merchant/network, case status, audit `(entity_type, entity_id)` and `ts`.
- Soft delete: `deleted_at` on master and configuration entities. Transactions, evidence, decisions, metrics, and audit events are immutable facts (no soft delete).
- Version: Customer, Account, Merchant, FraudPattern, FraudCase, FeatureDefinition, ModelVersion, AnalystDecision, Policy.

## Migrations

```bash
# PostgreSQL (Compose)
docker compose up -d postgres
set DATABASE_URL=postgresql+psycopg://fraud:fraud@localhost:5432/fraud
alembic upgrade head
```
